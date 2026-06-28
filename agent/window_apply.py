"""Real-window apply layer for landscape-block layout.

Pipeline (per package)
──────────────────────
1. DISCOVER  — scan shared_prefs and identify real layout keys (cached).
2. WRITE     — write every known position/size alias AND every known "Set X"
               enable boolean to pkg_preferences.xml (direct → root fallback).
3. STOP      — force-stop the package so prefs are re-read on next launch.
4. RELAUNCH  — optionally relaunch via ``launcher.perform_rejoin`` so the new
               clone window is created with the new bounds.
5. READBACK  — wait until a task/window for the package is visible, then parse
               actual bounds out of ``dumpsys window windows`` /
               ``dumpsys activity activities``.  We pick the bounds from the
               window/task that actually belongs to *this* package, never a
               random first match.
6. RESIZE    — if the actual bounds differ from desired by more than the
               tolerance, try ``cmd activity resize-task <id> l t r b``,
               ``am task resize``, and ``am stack resize`` via root.
7. RETRY     — re-write keys, re-stop, re-launch, re-readback up to N times.
8. STATUS    — mark each package one of:
                  Layout Applied     — actual bounds verified within tolerance
                  Layout Unverified  — write succeeded but bounds unreadable
                  Layout Failed      — bounds wrong after every fallback

All output goes to the ``deng.rejoin.window_apply`` logger (file only).  This
module NEVER prints to stdout/stderr.  Public dashboard reads the ``status``
field on each :class:`ApplyResult`.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import android
from .window_layout import (
    WindowRect,
    _is_layout_excluded,
    parse_wm_density,
    parse_wm_size,
    rect_center,
    rect_inside_bounds,
    rects_overlap,
    update_app_cloner_xml,
    update_app_cloner_xml_root,
)

_log = logging.getLogger("deng.rejoin.window_apply")


# ── Public status labels (read by supervisor / dashboard) ─────────────────────

LAYOUT_APPLIED    = "Layout Applied"
LAYOUT_UNVERIFIED = "Layout Unverified"
LAYOUT_FAILED     = "Layout Failed"
LAYOUT_SKIPPED    = "Layout Skipped"


@dataclass
class ApplyResult:
    package: str
    desired: WindowRect
    pre_write_ok: bool = False
    pre_write_method: str = ""
    actual_bounds: tuple[int, int, int, int] | None = None  # (l, t, r, b)
    actual_method: str = ""
    direct_resize_ok: bool = False
    touch_probe_ok: bool | None = None
    touch_probe_center: tuple[int, int] | None = None
    touch_probe_detail: str = ""
    task_bounds: tuple[int, int, int, int] | None = None
    surface_bounds: tuple[int, int, int, int] | None = None
    input_region: tuple[int, int, int, int] | None = None
    touchable_region: tuple[int, int, int, int] | None = None
    window_frame: tuple[int, int, int, int] | None = None
    content_frame: tuple[int, int, int, int] | None = None
    stable_frame: tuple[int, int, int, int] | None = None
    visible_frame: tuple[int, int, int, int] | None = None
    title_bar_height: int = 0
    task_id: int | None = None
    task_package_expected: bool = True
    corrected_task_bounds: tuple[int, int, int, int] | None = None
    density_info: dict[str, Any] = field(default_factory=dict)
    mismatch_classification: list[str] = field(default_factory=list)
    layer_readback: dict[str, Any] = field(default_factory=dict)
    validation: list[str] = field(default_factory=list)
    final_ok: bool = False
    status: str = LAYOUT_FAILED   # one of LAYOUT_APPLIED/UNVERIFIED/FAILED/SKIPPED
    detail: str = ""
    attempts: list[str] = field(default_factory=list)


# ── Capability probes ─────────────────────────────────────────────────────────

def _capability_probes() -> dict[str, bool]:
    """Detect which apply methods are available on this device.  Never raises."""
    caps = {
        "root":             False,
        "cmd_activity":     False,
        "am_stack":         False,
        "dumpsys_activity": False,
        "dumpsys_window":   False,
        "wm_size":          False,
    }
    try:
        caps["root"] = android.detect_root().available
    except Exception:  # noqa: BLE001
        pass
    for cmd, key in (
        (["cmd", "activity", "-h"], "cmd_activity"),
        (["am", "-h"],              "am_stack"),
        (["dumpsys", "activity"],   "dumpsys_activity"),
        (["dumpsys", "window"],     "dumpsys_window"),
        (["wm", "size"],            "wm_size"),
    ):
        try:
            res = android.run_command(cmd, timeout=3)
            caps[key] = bool(res.ok or (res.stdout and "Usage" in res.stdout))
        except Exception:  # noqa: BLE001
            pass
    return caps


# ── Read actual bounds (package-correct) ──────────────────────────────────────

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_RECT_RE = re.compile(r"Rect\(([-\d]+),\s*([-\d]+)\s*-\s*([-\d]+),\s*([-\d]+)\)")
_REGION_RECT_RE = re.compile(r"\[([-\d]+),([-\d]+)\]\[([-\d]+),([-\d]+)\]")
_TASK_ID_RE = re.compile(r"#(\d+)\s")
_WINDOW_BLOCK_HEADER_RE = re.compile(r"Window\s*\{[^}]*\b([\w.]+)/[\w.]+\b")


def _parse_bounds_line(text: str) -> tuple[int, int, int, int] | None:
    """Extract first ``[l,t][r,b]`` rect from a text blob."""
    m = _BOUNDS_RE.search(text)
    if not m:
        return None
    try:
        l, t, r, b = (int(g) for g in m.groups())
        return l, t, r, b
    except Exception:  # noqa: BLE001
        return None


def _rect_from_match(match: re.Match[str]) -> tuple[int, int, int, int]:
    return tuple(int(g) for g in match.groups())  # type: ignore[return-value]


def _parse_rect_after_key(block: str, key: str) -> tuple[int, int, int, int] | None:
    idx = block.find(key)
    if idx < 0:
        return None
    slice_ = block[idx : idx + 260]
    m = _BOUNDS_RE.search(slice_)
    if m:
        return _rect_from_match(m)
    m = _RECT_RE.search(slice_)
    if m:
        return _rect_from_match(m)
    return None


def _parse_region_after_key(block: str, key: str) -> tuple[int, int, int, int] | None:
    idx = block.find(key)
    if idx < 0:
        return None
    slice_ = block[idx : idx + 420]
    # Most dumps use Region([l,t][r,b]) for simple rectangular touch areas.
    rects = [_rect_from_match(m) for m in _REGION_RECT_RE.finditer(slice_)]
    if rects:
        left = min(r[0] for r in rects)
        top = min(r[1] for r in rects)
        right = max(r[2] for r in rects)
        bottom = max(r[3] for r in rects)
        return (left, top, right, bottom)
    m = _BOUNDS_RE.search(slice_)
    if m:
        return _rect_from_match(m)
    m = _RECT_RE.search(slice_)
    if m:
        return _rect_from_match(m)
    return None


def _bounds_to_list(bounds: tuple[int, int, int, int] | None) -> list[int] | None:
    return list(bounds) if bounds is not None else None


@dataclass
class _WindowEntry:
    package: str
    bounds:  tuple[int, int, int, int] | None
    has_surface: bool
    is_focused: bool
    task_id: int | None
    frame: tuple[int, int, int, int] | None = None
    content_frame: tuple[int, int, int, int] | None = None
    stable_frame: tuple[int, int, int, int] | None = None
    visible_frame: tuple[int, int, int, int] | None = None
    surface_bounds: tuple[int, int, int, int] | None = None
    input_region: tuple[int, int, int, int] | None = None
    touchable_region: tuple[int, int, int, int] | None = None
    raw_block: str = ""


_WINDOW_HEADER_RE = re.compile(r"^\s*(?:Window\b.*|.*\bWindow\s*\{).*$")


def _is_window_header_line(line: str) -> bool:
    """Heuristic: does this line look like the start of a window block?

    Matches both real Android dumpsys lines (``Window{abc1234 ...}``) and the
    looser sample used in tests (``Window foo {``).
    """
    stripped = line.lstrip()
    if stripped.startswith(("mCurrentFocus=", "mFocusedApp=", "mInputMethodTarget=")):
        return False
    return ("Window{" in line) or ("Window {" in line) or (
        stripped.startswith("Window") and "{" in line
    )


def _parse_window_dumpsys(text: str, package: str) -> list[_WindowEntry]:
    """Parse ``dumpsys window windows`` and yield candidate entries for ``package``.

    A "block" is the text between consecutive ``Window ... {`` headers.  We
    only keep blocks whose body mentions the package.
    """
    entries: list[_WindowEntry] = []
    if not text:
        return entries
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not _is_window_header_line(line):
            i += 1
            continue
        block_lines = [line]
        j = i + 1
        while j < len(lines) and not _is_window_header_line(lines[j]):
            block_lines.append(lines[j])
            j += 1
        block = "\n".join(block_lines)
        i = j
        if package not in block:
            continue
        frame = _parse_rect_after_key(block, "mFrame=")
        content_frame = _parse_rect_after_key(block, "mContentFrame=")
        stable_frame = _parse_rect_after_key(block, "mStableFrame=")
        visible_frame = (
            _parse_rect_after_key(block, "mVisibleFrame=")
            or _parse_rect_after_key(block, "mVisibleInsets")
        )
        surface_bounds = (
            _parse_rect_after_key(block, "mSurfacePosition=")
            or _parse_rect_after_key(block, "surfacePosition=")
            or _parse_rect_after_key(block, "mSurfaceFrame=")
        )
        input_region = (
            _parse_region_after_key(block, "touchableRegion=")
            or _parse_region_after_key(block, "mTouchableRegion=")
            or _parse_region_after_key(block, "Touchable region=")
        )
        touchable_region = input_region
        bounds = None
        for key in ("mFrame=", "containingFrame=", "mBounds=", "Bounds="):
            idx = block.find(key)
            if idx >= 0:
                slice_ = block[idx : idx + 200]
                m = _BOUNDS_RE.search(slice_)
                if m:
                    bounds = (int(m.group(1)), int(m.group(2)),
                              int(m.group(3)), int(m.group(4)))
                    break
        if bounds is None:
            bounds = _parse_bounds_line(block)
        has_surface = "mHasSurface=true" in block
        is_focused = ("mCurrentFocus" in text and
                      package in text.split("mCurrentFocus", 1)[-1][:160])
        task_id = None
        m = re.search(r"taskId=(\d+)", block)
        if m:
            task_id = int(m.group(1))
        entries.append(_WindowEntry(
            package=package,
            bounds=bounds,
            has_surface=has_surface,
            is_focused=is_focused,
            task_id=task_id,
            frame=frame,
            content_frame=content_frame,
            stable_frame=stable_frame,
            visible_frame=visible_frame,
            surface_bounds=surface_bounds,
            input_region=input_region,
            touchable_region=touchable_region,
            raw_block=block[:1000],
        ))
    # Fallback: if no real Window{} block was found but the text mentions the
    # package + has bounds, return a single weak candidate.  This keeps the
    # bounds parser working on the loose sample format used in tests.
    if not entries and package in text:
        bounds = _parse_bounds_line(text)
        if bounds:
            entries.append(_WindowEntry(
                package=package,
                bounds=bounds,
                has_surface="mHasSurface=true" in text,
                is_focused=False,
                task_id=None,
                frame=_parse_rect_after_key(text, "mFrame="),
                content_frame=_parse_rect_after_key(text, "mContentFrame="),
                stable_frame=_parse_rect_after_key(text, "mStableFrame="),
                visible_frame=_parse_rect_after_key(text, "mVisibleFrame="),
                surface_bounds=_parse_rect_after_key(text, "mSurfaceFrame="),
                input_region=(
                    _parse_region_after_key(text, "touchableRegion=")
                    or _parse_region_after_key(text, "mTouchableRegion=")
                ),
                touchable_region=(
                    _parse_region_after_key(text, "touchableRegion=")
                    or _parse_region_after_key(text, "mTouchableRegion=")
                ),
                raw_block=text[:1000],
            ))
    return entries


@dataclass
class _TaskEntry:
    package: str
    bounds: tuple[int, int, int, int] | None
    task_id: int | None
    visible: bool
    raw_block: str


def _parse_activity_dumpsys(text: str, package: str) -> list[_TaskEntry]:
    """Parse ``dumpsys activity activities`` for task records mentioning ``package``."""
    entries: list[_TaskEntry] = []
    if not text:
        return entries
    lines = text.splitlines()
    cur_task_id: int | None = None
    cur_block_lines: list[str] = []
    cur_pkg_in_block = False

    def _flush():
        nonlocal cur_block_lines, cur_pkg_in_block, cur_task_id
        if cur_pkg_in_block and cur_block_lines:
            block = "\n".join(cur_block_lines)
            bounds = None
            for key in ("Bounds=", "mBounds=", "mLastNonFullscreenBounds=", "userBounds="):
                idx = block.find(key)
                if idx >= 0:
                    slice_ = block[idx : idx + 200]
                    m = _BOUNDS_RE.search(slice_)
                    if m:
                        bounds = (int(m.group(1)), int(m.group(2)),
                                  int(m.group(3)), int(m.group(4)))
                        break
            if bounds is None:
                bounds = _parse_bounds_line(block)
            visible = "visible=true" in block or "mResumedActivity" in block
            entries.append(_TaskEntry(
                package=package,
                bounds=bounds,
                task_id=cur_task_id,
                visible=visible,
                raw_block=block[:1000],
            ))
        cur_block_lines.clear()
        cur_pkg_in_block = False

    for line in lines:
        m = re.search(r"TaskRecord\{[^}]*\s+#(\d+)\b", line)
        if m:
            _flush()
            cur_task_id = int(m.group(1))
        cur_block_lines.append(line)
        if package in line:
            cur_pkg_in_block = True
    _flush()
    return entries


# Minimum surface size that counts as "the main activity window".
# Probe ``p-1239f2b5f9`` showed our parser picking up status-bar windows
# like ``mFrame=[0,0][1280,25]`` (25 px tall) and ``[0,25][1,26]`` (1×1 px)
# as the package's bounds — both belonged to the same UID but were not
# the activity surface.  Anything under this threshold is definitely
# chrome (status bar / IME stub / nav bar) — skip it.
_MIN_REAL_WINDOW_W = 200
_MIN_REAL_WINDOW_H = 100


def _is_real_activity_bounds(
    bounds: tuple[int, int, int, int] | None,
) -> bool:
    """Heuristic: is this a real activity surface, or a chrome sliver?"""
    if bounds is None:
        return False
    l, t, r, b = bounds
    w, h = r - l, b - t
    return w >= _MIN_REAL_WINDOW_W and h >= _MIN_REAL_WINDOW_H


def read_actual_bounds(package: str) -> tuple[tuple[int, int, int, int] | None, str]:
    """Read the actual on-screen bounds for ``package``.

    Selection priority (highest → lowest):
        1. Real activity window (size ≥ 200×100), surface alive AND focused.
        2. Real activity window (size ≥ 200×100), surface alive.
        3. Real activity window (size ≥ 200×100), any.
        4. Legacy fallback — any window with bounds (kept for unit-test
           fixtures that don't model the full dumpsys layout).

    The size threshold filters out status-bar / IME / nav-bar windows
    that share the package's UID and would otherwise mask the activity
    surface (probe ``p-1239f2b5f9`` — every clone "fits" in 25 px).
    Source labels: ``dumpsys_window``, ``dumpsys_activity``, ``unavailable``.
    Never raises.
    """
    # 1. dumpsys window windows — prefer windows with mHasSurface=true.
    try:
        res = android.run_command(["dumpsys", "window", "windows"], timeout=6)
        if res.ok and res.stdout:
            cands = _parse_window_dumpsys(res.stdout, package)
            for c in cands:
                if (c.has_surface and c.is_focused
                        and _is_real_activity_bounds(c.bounds)):
                    return c.bounds, "dumpsys_window"
            for c in cands:
                if c.has_surface and _is_real_activity_bounds(c.bounds):
                    return c.bounds, "dumpsys_window"
            for c in cands:
                if _is_real_activity_bounds(c.bounds):
                    return c.bounds, "dumpsys_window"
            for c in cands:
                if c.has_surface and c.bounds:
                    return c.bounds, "dumpsys_window"
            for c in cands:
                if c.is_focused and c.bounds:
                    return c.bounds, "dumpsys_window"
            for c in cands:
                if c.bounds:
                    return c.bounds, "dumpsys_window"
    except Exception:  # noqa: BLE001
        pass

    # 2. dumpsys activity activities — fall back to task bounds.
    try:
        res = android.run_command(["dumpsys", "activity", "activities"], timeout=6)
        if res.ok and res.stdout:
            cands = _parse_activity_dumpsys(res.stdout, package)
            for c in cands:
                if c.visible and _is_real_activity_bounds(c.bounds):
                    return c.bounds, "dumpsys_activity"
            for c in cands:
                if _is_real_activity_bounds(c.bounds):
                    return c.bounds, "dumpsys_activity"
            for c in cands:
                if c.visible and c.bounds:
                    return c.bounds, "dumpsys_activity"
            for c in cands:
                if c.bounds:
                    return c.bounds, "dumpsys_activity"
    except Exception:  # noqa: BLE001
        pass

    return None, "unavailable"


def _select_window_entry(package: str) -> _WindowEntry | None:
    try:
        res = android.run_command(["dumpsys", "window", "windows"], timeout=6)
    except Exception:  # noqa: BLE001
        return None
    if not res.ok or not res.stdout:
        return None
    cands = _parse_window_dumpsys(res.stdout, package)
    for predicate in (
        lambda c: c.has_surface and c.is_focused and _is_real_activity_bounds(c.bounds),
        lambda c: c.has_surface and _is_real_activity_bounds(c.bounds),
        lambda c: _is_real_activity_bounds(c.bounds),
        lambda c: c.has_surface and c.bounds,
        lambda c: c.is_focused and c.bounds,
        lambda c: c.bounds,
    ):
        for c in cands:
            if predicate(c):
                return c
    return None


def _select_task_entry(package: str) -> _TaskEntry | None:
    try:
        res = android.run_command(["dumpsys", "activity", "activities"], timeout=6)
    except Exception:  # noqa: BLE001
        return None
    if not res.ok or not res.stdout:
        return None
    cands = _parse_activity_dumpsys(res.stdout, package)
    for predicate in (
        lambda c: c.visible and _is_real_activity_bounds(c.bounds),
        lambda c: _is_real_activity_bounds(c.bounds),
        lambda c: c.visible and c.bounds,
        lambda c: c.bounds,
    ):
        for c in cands:
            if predicate(c):
                return c
    return None


_SURFACE_RECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:crop|bounds|frame|destination|source|layerStackSpace)\s*=\s*(\[[^\n]+?\]|\([^\n]+?\)|Rect\([^\n]+?\))", re.I),
    re.compile(r"\b(?:pos|position)\s*=\s*\(([-\d.]+),\s*([-\d.]+)\).*?\bsize\s*=\s*\((\d+),\s*(\d+)\)", re.I),
)


def _parse_surface_bounds(text: str, package: str) -> tuple[int, int, int, int] | None:
    if not text or package not in text:
        return None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if package not in line:
            continue
        block = "\n".join(lines[i : min(len(lines), i + 10)])
        for pattern in _SURFACE_RECT_PATTERNS:
            m = pattern.search(block)
            if not m:
                continue
            if len(m.groups()) == 1:
                blob = m.group(1)
                bm = _BOUNDS_RE.search(blob)
                if bm:
                    return _rect_from_match(bm)
                rm = _RECT_RE.search(blob)
                if rm:
                    return _rect_from_match(rm)
            elif len(m.groups()) == 4:
                x, y = int(float(m.group(1))), int(float(m.group(2)))
                w, h = int(m.group(3)), int(m.group(4))
                return (x, y, x + w, y + h)
        b = _parse_bounds_line(block)
        if b:
            return b
    return None


def read_surface_bounds(package: str) -> tuple[tuple[int, int, int, int] | None, str]:
    try:
        res = android.run_android_command(["dumpsys", "SurfaceFlinger"], timeout=6)
    except Exception:  # noqa: BLE001
        return None, "unavailable"
    if not res.ok or not res.stdout:
        return None, "unavailable"
    bounds = _parse_surface_bounds(res.stdout, package)
    return (bounds, "dumpsys_surfaceflinger") if bounds else (None, "unavailable")


def _read_density_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "wm_size": "",
        "wm_density": "",
        "wm_physical_size": None,
        "wm_override_size": None,
        "wm_physical_density": None,
        "wm_override_density": None,
        "display_rect": None,
        "rotation": "",
    }
    try:
        size_res = android.run_android_command(["wm", "size"], timeout=4)
        info["wm_size"] = size_res.stdout or ""
        physical = re.search(r"Physical size:\s*(\d+x\d+)", size_res.stdout or "")
        override = re.search(r"Override size:\s*(\d+x\d+)", size_res.stdout or "")
        if physical:
            info["wm_physical_size"] = parse_wm_size(physical.group(1))
        if override:
            info["wm_override_size"] = parse_wm_size(override.group(1))
    except Exception:  # noqa: BLE001
        pass
    try:
        density_res = android.run_android_command(["wm", "density"], timeout=4)
        info["wm_density"] = density_res.stdout or ""
        physical_d = re.search(r"Physical density:\s*(\d+)", density_res.stdout or "")
        override_d = re.search(r"Override density:\s*(\d+)", density_res.stdout or "")
        if physical_d:
            info["wm_physical_density"] = int(physical_d.group(1))
        if override_d:
            info["wm_override_density"] = int(override_d.group(1))
        if not info["wm_physical_density"]:
            info["wm_physical_density"] = parse_wm_density(density_res.stdout or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        display = android.get_display_orientation_state()
        info["display_rect"] = [0, 0, int(display.get("width") or 0), int(display.get("height") or 0)]
        info["rotation"] = display.get("rotation", "")
    except Exception:  # noqa: BLE001
        pass
    return info


def _content_title_bar_height(
    frame: tuple[int, int, int, int] | None,
    content_frame: tuple[int, int, int, int] | None,
) -> int:
    if not frame or not content_frame:
        return 0
    return max(0, int(content_frame[1]) - int(frame[1]))


def _offset_bounds(
    bounds: tuple[int, int, int, int],
    *,
    top_offset: int = 0,
) -> tuple[int, int, int, int]:
    return (bounds[0], bounds[1] + top_offset, bounds[2], bounds[3] + top_offset)


def _classify_layer_readback(
    *,
    desired: WindowRect,
    task_bounds: tuple[int, int, int, int] | None,
    surface_bounds: tuple[int, int, int, int] | None,
    input_region: tuple[int, int, int, int] | None,
    content_frame: tuple[int, int, int, int] | None,
    display_bounds: tuple[int, int, int, int],
    title_bar_height: int,
    tolerance: int,
) -> list[str]:
    classes: list[str] = []
    desired_tuple = (desired.left, desired.top, desired.right, desired.bottom)
    task_ok = task_bounds is not None and _bounds_close_enough(task_bounds, desired, tolerance)
    surface_ok = surface_bounds is not None and _bounds_close_enough(surface_bounds, desired, tolerance)
    input_ok = input_region is not None and _bounds_close_enough(input_region, desired, tolerance)
    if task_bounds == display_bounds or surface_bounds == display_bounds or input_region == display_bounds:
        classes.append("fullscreen_readback")
    for axis in _clamp_axes(task_bounds, desired, tolerance):
        classes.append(f"android_clamped_{axis}")
    if surface_ok and input_region is not None and not input_ok:
        classes.append("visual_correct_input_wrong")
    if task_ok and surface_bounds is not None and not surface_ok:
        classes.append("task_correct_surface_wrong")
    if task_ok and input_region is not None and not input_ok:
        classes.append("task_correct_input_wrong")
    if title_bar_height > 0:
        corrected = _offset_bounds(desired_tuple, top_offset=title_bar_height)
        if input_region and _bounds_close_enough(input_region, _rect_from_bounds(desired.package, corrected), tolerance):
            classes.append("decor_title_bar_offset")
        elif content_frame and not _bounds_close_enough(content_frame, desired, tolerance):
            classes.append("decor_content_frame_offset")
    if task_bounds and input_region and not _bounds_close_enough(input_region, _rect_from_bounds(desired.package, task_bounds), tolerance):
        tw = max(1, task_bounds[2] - task_bounds[0])
        th = max(1, task_bounds[3] - task_bounds[1])
        iw = max(1, input_region[2] - input_region[0])
        ih = max(1, input_region[3] - input_region[1])
        sx = iw / tw
        sy = ih / th
        if abs(sx - 1.0) > 0.08 or abs(sy - 1.0) > 0.08:
            classes.append("density_scale_mismatch")
    if not classes and (task_bounds or surface_bounds or input_region):
        if all(
            b is None or _bounds_close_enough(b, desired, tolerance)
            for b in (task_bounds, surface_bounds, input_region)
        ):
            classes.append("match")
        else:
            classes.append("bounds_mismatch")
    return classes


def _clamp_axes(
    actual: tuple[int, int, int, int] | None,
    desired: WindowRect,
    tolerance: int,
) -> list[str]:
    if actual is None:
        return []
    desired_tuple = (desired.left, desired.top, desired.right, desired.bottom)
    labels = ("x", "y", "width", "height")
    actual_values = (
        actual[0],
        actual[1],
        actual[2] - actual[0],
        actual[3] - actual[1],
    )
    desired_values = (
        desired_tuple[0],
        desired_tuple[1],
        desired_tuple[2] - desired_tuple[0],
        desired_tuple[3] - desired_tuple[1],
    )
    return [
        label for label, got, want in zip(labels, actual_values, desired_values)
        if abs(int(got) - int(want)) > tolerance
    ]


def collect_portrait_layer_readback(
    package: str,
    desired: WindowRect,
    *,
    tolerance: int = 32,
) -> dict[str, Any]:
    """Collect task/window/surface/input evidence for portrait click alignment."""
    window_entry = _select_window_entry(package)
    task_entry = _select_task_entry(package)
    surface_bounds, surface_method = read_surface_bounds(package)
    task_bounds = task_entry.bounds if task_entry else None
    window_bounds = window_entry.bounds if window_entry else None
    if surface_bounds is None and window_entry is not None:
        surface_bounds = window_entry.surface_bounds or window_entry.bounds
        surface_method = "dumpsys_window"
    input_region = None
    touchable_region = None
    if window_entry is not None:
        input_region = window_entry.input_region
        touchable_region = window_entry.touchable_region
    frame = window_entry.frame if window_entry else None
    content = window_entry.content_frame if window_entry else None
    stable = window_entry.stable_frame if window_entry else None
    visible = window_entry.visible_frame if window_entry else None
    title_h = _content_title_bar_height(frame or window_bounds, content)
    display_bounds = _display_bounds("portrait")
    density_info = _read_density_info()
    classes = _classify_layer_readback(
        desired=desired,
        task_bounds=task_bounds,
        surface_bounds=surface_bounds,
        input_region=input_region,
        content_frame=content,
        display_bounds=display_bounds,
        title_bar_height=title_h,
        tolerance=tolerance,
    )
    return {
        "package": package,
        "configured_package_expected": True,
        "desired_bounds": _bounds_to_list((desired.left, desired.top, desired.right, desired.bottom)),
        "task_bounds": _bounds_to_list(task_bounds),
        "window_bounds": _bounds_to_list(window_bounds),
        "surface_bounds": _bounds_to_list(surface_bounds),
        "surface_method": surface_method,
        "input_region": _bounds_to_list(input_region),
        "touchable_region": _bounds_to_list(touchable_region),
        "window_frame": _bounds_to_list(frame),
        "content_frame": _bounds_to_list(content),
        "stable_frame": _bounds_to_list(stable),
        "visible_frame": _bounds_to_list(visible),
        "title_bar_height": title_h,
        "corrected_task_bounds": _bounds_to_list(
            _offset_bounds((desired.left, desired.top, desired.right, desired.bottom), top_offset=title_h)
            if title_h else None
        ),
        "density": density_info,
        "mismatch_classification": classes,
        "clamped_axes": _clamp_axes(task_bounds, desired, tolerance),
        "surface_clamped_axes": _clamp_axes(surface_bounds, desired, tolerance),
        "input_clamped_axes": _clamp_axes(input_region, desired, tolerance),
        "task_id": task_entry.task_id if task_entry else (window_entry.task_id if window_entry else None),
        "task_package": package if task_entry or window_entry else "",
        "task_package_expected": bool(task_entry or window_entry),
        "window_has_surface": bool(window_entry.has_surface) if window_entry else False,
        "window_focused": bool(window_entry.is_focused) if window_entry else False,
    }


def _get_task_id(package: str) -> int | None:
    """Best-effort: find the task id for ``package``."""
    task_entry = _select_task_entry(package)
    if task_entry is not None and task_entry.task_id is not None:
        return task_entry.task_id
    window_entry = _select_window_entry(package)
    if window_entry is not None and window_entry.task_id is not None:
        return window_entry.task_id
    try:
        res = android.run_command(["dumpsys", "activity", "activities"], timeout=6)
        if res.ok:
            text = res.stdout or ""
            for m in re.finditer(r"TaskRecord\{[^}]*?#(\d+)[^}]*?A=([\w.]+)", text):
                if m.group(2) == package:
                    return int(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


def _get_stack_id(package: str) -> int | None:
    """Find the stack id currently holding ``package``'s task.

    On Android 10 (SDK 29) the working resize verb is::

        am stack resize <STACK_ID> <LEFT,TOP,RIGHT,BOTTOM>

    which operates on the STACK rather than the task, so we need to know
    the stack the task currently belongs to.  Probe ``p-1239f2b5f9``
    (SM-N9810 / Android 10) showed task records like::

        * TaskRecord{3169de3 #78 A=com.moons.litesc U=0 StackId=3 sz=1}

    where the trailing ``StackId=3`` is what we need.  Returns ``None``
    when the stack id cannot be discovered.  Never raises.
    """
    try:
        res = android.run_command(["dumpsys", "activity", "activities"], timeout=8)
        if not res.ok or not res.stdout:
            return None
        text = res.stdout
        # The TaskRecord line contains both #<task_id> and StackId=<n>.
        for m in re.finditer(
            r"TaskRecord\{[^}]*?#(\d+)[^}]*?A=([\w.]+)[^}]*?StackId=(\d+)",
            text,
        ):
            tid_str, pkg_in_block, stack_str = m.group(1), m.group(2), m.group(3)
            if pkg_in_block == package:
                return int(stack_str)
        # Fallback: any "Stack #N" header whose body contains the package.
        # Stack blocks are delimited by "Stack #" headers in dumpsys output.
        blocks = re.split(r"\n(?=Stack #\d+:)", text)
        for blk in blocks:
            head = blk.split("\n", 1)[0]
            sm = re.match(r"Stack #(\d+)", head)
            if not sm:
                continue
            if package in blk:
                return int(sm.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


def _wait_for_window(package: str, timeout: float) -> bool:
    """Poll until ``package`` has any task/window evidence.  Best-effort.

    Returns True if evidence appeared; False on timeout.  Never raises.
    """
    deadline = time.time() + max(0.5, float(timeout))
    while time.time() < deadline:
        try:
            ev = android.get_package_alive_evidence(package)
            if ev.get("window") or ev.get("running") or ev.get("surface") or ev.get("foreground"):
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


# Android 10 (SDK 29) freeform workspace stack id — confirmed by AOSP
# source ``WindowConfiguration.WINDOWING_MODE_FREEFORM = 5`` and by Samsung
# One UI 5 behavior.  On most Android 10 builds the freeform workspace
# stack is dynamically allocated, so callers should prefer
# ``move-task <tid> <stack> true`` over hard-coding 5.
ANDROID10_FREEFORM_WINDOWING_MODE = 5
_DIRECT_RESIZE_SETTLE_SEC = 0.5


def _direct_resize_via_root(
    package: str,
    rect: WindowRect,
    root_tool: str,
    *,
    tolerance: int = 32,
) -> tuple[bool, str]:
    """Try direct-resize commands and return True only after bounds readback verifies.

    Android 10 ``am stack resize`` often returns rc=0 even when the stack id
    is wrong or the window stays fullscreen.  We therefore treat a command as
    successful only when ``read_actual_bounds`` confirms the package landed
    within ``tolerance`` px of ``rect``.  Never raises.
    """
    task_id = _get_task_id(package)
    stack_id = _get_stack_id(package)
    if task_id is None:
        return False, "no task id"
    l, t, r, b = rect.left, rect.top, rect.right, rect.bottom
    bounds_comma = f"{l},{t},{r},{b}"
    bounds_args = [str(l), str(t), str(r), str(b)]

    flipped = False
    try:
        res = android.run_root_command(
            ["cmd", "activity", "set-task-windowing-mode",
             str(task_id), str(ANDROID10_FREEFORM_WINDOWING_MODE)],
            root_tool=root_tool, timeout=4,
        )
        flipped = bool(res.ok)
    except Exception:  # noqa: BLE001
        pass
    if not flipped:
        try:
            android.run_root_command(
                ["am", "stack", "move-task", str(task_id),
                 str(ANDROID10_FREEFORM_WINDOWING_MODE), "true"],
                root_tool=root_tool, timeout=4,
            )
        except Exception:  # noqa: BLE001
            pass

    new_stack = _get_stack_id(package)
    target_stack = new_stack if new_stack is not None else stack_id

    candidates: list[list[str]] = []
    if target_stack is not None:
        candidates.append(
            ["am", "stack", "resize", str(target_stack), bounds_comma]
        )
        candidates.append(
            ["am", "stack", "resize-animated", str(target_stack), bounds_comma]
        )
    candidates.extend([
        ["cmd", "activity", "resize-task", str(task_id), *bounds_args],
        ["cmd", "activity", "resize-task", str(task_id), *bounds_args, "1"],
        ["am", "task", "resize", str(task_id), *bounds_args],
    ])
    if target_stack is not None:
        candidates.append(
            ["am", "stack", "resize", str(target_stack), *bounds_args]
        )
    candidates.append(
        ["wm", "task", "resize", str(task_id), *bounds_args]
    )

    last_err = ""
    for cmd_args in candidates:
        try:
            res = android.run_root_command(
                cmd_args, root_tool=root_tool, timeout=4,
            )
            if not res.ok:
                last_err = (res.stderr or res.stdout or "command failed").strip()[:120]
                continue
            time.sleep(_DIRECT_RESIZE_SETTLE_SEC)
            bounds, source = read_actual_bounds(package)
            if bounds and _bounds_close_enough(bounds, rect, tolerance):
                return True, (
                    f"verified via {' '.join(cmd_args[:3])} "
                    f"bounds={bounds} ({source})"
                )
            last_err = (
                f"{' '.join(cmd_args[:3])} rc=0 but bounds={bounds} "
                f"(want {l},{t},{r},{b})"
            )[:160]
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)[:120]
            continue
    return False, (
        f"all direct-resize variants failed (task #{task_id} "
        f"stack #{target_stack}) — {last_err}"
    )


# ── High-level apply ─────────────────────────────────────────────────────────

def _bounds_close_enough(
    actual: tuple[int, int, int, int],
    desired: WindowRect,
    tolerance: int = 32,
) -> bool:
    return (
        abs(actual[0] - desired.left)   <= tolerance and
        abs(actual[1] - desired.top)    <= tolerance and
        abs(actual[2] - desired.right)  <= tolerance and
        abs(actual[3] - desired.bottom) <= tolerance
    )


def _write_one_package(
    rect: WindowRect,
    *,
    root_tool: str | None,
    known_keys: Iterable[str] | None,
    result: ApplyResult,
    screen_mode: str = "landscape",
) -> bool:
    """Pre-write the XML for one rect.  Updates ``result`` in place.

    Returns True if at least one write method succeeded.
    """
    mode = str(screen_mode or "landscape").strip().lower()
    if mode == "portrait" and root_tool:
        try:
            from .resize_pb99 import write_pb99_bounds_root

            ok, msg = write_pb99_bounds_root(rect.package, rect, root_tool)
            result.attempts.append(f"pb99-root: {msg}")
            if ok:
                result.pre_write_ok = True
                result.pre_write_method = "pb99-root"
                return True
        except Exception as exc:  # noqa: BLE001
            result.attempts.append(f"pb99-root-error: {exc}")
    try:
        ok, msg = update_app_cloner_xml(
            rect.package, rect, known_keys=known_keys, screen_mode=screen_mode,
        )
        result.attempts.append(f"xml-direct: {msg}")
        if ok:
            result.pre_write_ok = True
            result.pre_write_method = "xml-direct"
            return True
    except Exception as exc:  # noqa: BLE001
        result.attempts.append(f"xml-direct-error: {exc}")
    if root_tool:
        try:
            ok, msg = update_app_cloner_xml_root(
                rect.package, rect, root_tool,
                known_keys=known_keys,
                screen_mode=screen_mode,
            )
            result.attempts.append(f"xml-root: {msg}")
            if ok:
                result.pre_write_ok = True
                result.pre_write_method = "xml-root"
                return True
        except Exception as exc:  # noqa: BLE001
            result.attempts.append(f"xml-root-error: {exc}")
    return False


def _discover_known_keys(packages: Sequence[str], root_tool: str | None) -> dict[str, list[str]]:
    """Best-effort discovery wrapper: returns ``{package: [key_names...]}``."""
    try:
        from .layout_discovery import get_cached_or_discover
        discs = get_cached_or_discover(list(packages), root_tool=root_tool)
        out: dict[str, list[str]] = {}
        for pkg, d in discs.items():
            out[pkg] = [k.name for k in d.keys]
        return out
    except Exception as exc:  # noqa: BLE001
        _log.debug("_discover_known_keys error: %s", exc)
        return {pkg: [] for pkg in packages}


def _rect_from_bounds(package: str, bounds: tuple[int, int, int, int]) -> WindowRect:
    return WindowRect(package, bounds[0], bounds[1], bounds[2], bounds[3])


def _display_bounds(screen_mode: str = "landscape") -> tuple[int, int, int, int]:
    try:
        from .window_layout import detect_display_info, resolve_layout_mode
        display = detect_display_info()
        mode = str(screen_mode or "landscape").strip().lower()
        resolved = resolve_layout_mode(display.width, display.height, mode)
        return (0, 0, int(resolved.normalized_width), int(resolved.normalized_height))
    except Exception:  # noqa: BLE001
        return (0, 0, 99999, 99999)


def _validate_actual_layout(
    results: Sequence[ApplyResult],
    *,
    screen_mode: str,
) -> None:
    mode = str(screen_mode or "landscape").strip().lower()
    display_bounds = _display_bounds(mode)
    actual_rects: list[tuple[ApplyResult, WindowRect]] = []
    for result in results:
        if result.actual_bounds is None:
            continue
        rect = _rect_from_bounds(result.package, result.actual_bounds)
        actual_rects.append((result, rect))
        if not rect_inside_bounds(rect, display_bounds):
            result.validation.append("Offscreen")
        if mode == "portrait":
            desired_w = max(1, result.desired.win_w)
            desired_h = max(1, result.desired.win_h)
            if rect.win_w < max(160, int(desired_w * 0.85)):
                result.validation.append("Too Small")
            if rect.win_h < max(180, int(desired_h * 0.85)):
                result.validation.append("Too Small")
    for i in range(len(actual_rects)):
        result_i, rect_i = actual_rects[i]
        for j in range(i + 1, len(actual_rects)):
            result_j, rect_j = actual_rects[j]
            if rects_overlap(rect_i, rect_j):
                result_i.validation.append(f"Overlap:{result_j.package}")
                result_j.validation.append(f"Overlap:{result_i.package}")
            if (
                mode == "portrait"
                and result_i.actual_bounds is not None
                and result_i.actual_bounds == result_j.actual_bounds
            ):
                result_i.validation.append(f"duplicate_final_bounds:{result_j.package}")
                result_j.validation.append(f"duplicate_final_bounds:{result_i.package}")
    if mode == "portrait":
        seen_task_ids: dict[int, str] = {}
        for result in results:
            if result.task_id is None:
                continue
            other = seen_task_ids.get(result.task_id)
            if other and other != result.package:
                result.validation.append(f"duplicate_task_id:{other}")
                for prev in results:
                    if prev.package == other:
                        prev.validation.append(f"duplicate_task_id:{result.package}")
                        break
            else:
                seen_task_ids[result.task_id] = result.package


def _tap_center_probe(
    package: str,
    bounds: tuple[int, int, int, int],
    root_tool: str | None,
) -> tuple[bool, tuple[int, int], str]:
    rect = _rect_from_bounds(package, bounds)
    center = rect_center(rect)
    if not root_tool:
        return False, center, "no root"
    try:
        cx, cy = center
        res = android.run_root_command(
            ["input", "tap", str(cx), str(cy)],
            root_tool=root_tool,
            timeout=3,
        )
        if res.ok:
            return True, center, "input tap ok"
        detail = (res.stderr or res.stdout or "input tap failed").strip()[:120]
        return False, center, detail
    except Exception as exc:  # noqa: BLE001
        return False, center, str(exc)[:120]


def _tuple_from_readback(value: Any) -> tuple[int, int, int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return tuple(int(v) for v in value)  # type: ignore[return-value]
        except (TypeError, ValueError):
            return None
    return None


def _record_portrait_layer_readback(result: ApplyResult, *, tolerance: int) -> None:
    try:
        readback = collect_portrait_layer_readback(
            result.package,
            result.desired,
            tolerance=tolerance,
        )
    except Exception as exc:  # noqa: BLE001
        result.attempts.append(f"portrait-layer-readback-error: {exc}")
        return
    result.layer_readback = readback
    result.task_bounds = _tuple_from_readback(readback.get("task_bounds"))
    result.surface_bounds = _tuple_from_readback(readback.get("surface_bounds"))
    result.input_region = _tuple_from_readback(readback.get("input_region"))
    result.touchable_region = _tuple_from_readback(readback.get("touchable_region"))
    result.window_frame = _tuple_from_readback(readback.get("window_frame"))
    result.content_frame = _tuple_from_readback(readback.get("content_frame"))
    result.stable_frame = _tuple_from_readback(readback.get("stable_frame"))
    result.visible_frame = _tuple_from_readback(readback.get("visible_frame"))
    result.corrected_task_bounds = _tuple_from_readback(readback.get("corrected_task_bounds"))
    result.title_bar_height = int(readback.get("title_bar_height") or 0)
    try:
        result.task_id = int(readback.get("task_id")) if readback.get("task_id") is not None else None
    except (TypeError, ValueError):
        result.task_id = None
    result.task_package_expected = bool(readback.get("task_package_expected", True))
    density = readback.get("density")
    result.density_info = density if isinstance(density, dict) else {}
    classes = readback.get("mismatch_classification")
    result.mismatch_classification = [str(c) for c in classes] if isinstance(classes, list) else []
    result.attempts.append(
        "portrait-layer-readback: "
        f"task_id={result.task_id} task={result.task_bounds} surface={result.surface_bounds} "
        f"input={result.input_region} title_bar={result.title_bar_height} "
        f"class={','.join(result.mismatch_classification) or 'none'}"
    )


def _portrait_layer_validation_errors(result: ApplyResult, *, tolerance: int) -> list[str]:
    errors: list[str] = []
    desired = result.desired
    display_bounds = _display_bounds("portrait")
    corrected_task = result.corrected_task_bounds
    if not result.task_package_expected:
        errors.append("wrong task package")
    for label, bounds in (
        ("task", result.task_bounds),
        ("surface", result.surface_bounds),
        ("input", result.input_region),
    ):
        if bounds is None:
            continue
        if bounds == display_bounds:
            errors.append(f"{label} fullscreen")
        target = corrected_task if label in {"task", "surface"} and corrected_task else None
        if target is not None:
            target_ok = _bounds_close_enough(bounds, _rect_from_bounds(desired.package, target), tolerance)
        else:
            target_ok = _bounds_close_enough(bounds, desired, tolerance)
        if not target_ok:
            errors.append(f"{label} mismatch")
    if result.title_bar_height > 0 and result.content_frame is not None:
        # Title bars are allowed only when the exposed content frame still
        # tracks the desired visible slot closely enough for tap coordinates.
        if not _bounds_close_enough(result.content_frame, desired, tolerance):
            errors.append("decor/titlebar content mismatch")
    bad_classes = {
        "visual_correct_input_wrong",
        "task_correct_surface_wrong",
        "task_correct_input_wrong",
        "fullscreen_readback",
        "density_scale_mismatch",
        "decor_title_bar_offset",
        "decor_content_frame_offset",
        "bounds_mismatch",
        "android_clamped_x",
        "android_clamped_y",
        "android_clamped_width",
        "android_clamped_height",
    }
    for cls in result.mismatch_classification:
        if cls in bad_classes and cls not in {"match"}:
            readable = cls.replace("_", " ")
            if readable not in errors:
                errors.append(readable)
    return errors


def _titlebar_corrected_rect(result: ApplyResult) -> WindowRect | None:
    if result.title_bar_height <= 0:
        return None
    if "decor_title_bar_offset" not in result.mismatch_classification:
        return None
    display = _display_bounds("portrait")
    shift = int(result.title_bar_height)
    left = result.desired.left
    top = max(display[1], result.desired.top - shift)
    right = result.desired.right
    bottom = max(top + 1, result.desired.bottom - shift)
    if bottom > display[3]:
        delta = bottom - display[3]
        top = max(display[1], top - delta)
        bottom = display[3]
    return WindowRect(result.package, left, top, right, bottom)


def apply_window_layout(
    rects: Sequence[WindowRect],
    *,
    force_stop_before: bool = False,
    relaunch_after: bool = False,
    verify_after: bool = True,
    pre_write: bool = True,
    allow_direct_resize: bool = True,
    retries: int = 1,
    tolerance: int = 32,
    wait_for_window_seconds: float = 6.0,
    screen_mode: str = "landscape",
    touch_probe: bool = False,
) -> list[ApplyResult]:
    """Apply landscape-block layout to a list of WindowRect.

    Pipeline:
      1. Discover known keys (cached, package-specific).
      2. For each rect:
         a. Pre-write XML (direct → root fallback) with EVERY alias and the
            "Set X" enable booleans.
         b. (optional) Force-stop package so XML is honored on next launch.
      3. After launch grace, read actual bounds.  Pick the right window/task
         for the package (surface, focus, visibility).
      4. If actual bounds differ, try direct resize via root.
      5. Retry: re-write, re-stop, re-readback.
      6. Set result.status = LAYOUT_APPLIED / UNVERIFIED / FAILED.

    Returns one :class:`ApplyResult` per rect.  Never raises.  Never prints.
    """
    mode = str(screen_mode or "landscape").strip().lower()
    if mode not in ("landscape", "portrait"):
        mode = "landscape"
    caps = _capability_probes()
    _log.debug("apply_window_layout caps=%s", caps)

    results: list[ApplyResult] = []
    root_info = android.detect_root()
    root_tool = root_info.tool if root_info.available else None

    # ── Layer 1: enable freeform / resizable-activity capabilities ──────────
    # Without enable_freeform_support=1 and force_resizable_activities=1,
    # the system refuses to honor non-fullscreen launch bounds or
    # ``cmd activity resize-task`` for Roblox.  This is the missing
    # foundation that App Cloner XML alone cannot replace.
    try:
        from .freeform_enable import setup_freeform_capabilities
        freeform_result = setup_freeform_capabilities()
        _log.debug(
            "freeform_setup: root=%s enabled=%s already=%s failed=%s",
            freeform_result.root_available,
            freeform_result.enabled_keys,
            freeform_result.already_enabled_keys,
            freeform_result.failed_keys,
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("freeform setup error: %s", exc)
        freeform_result = None

    packages = [r.package for r in rects if not _is_layout_excluded(r.package)]
    known_keys_map = _discover_known_keys(packages, root_tool)

    for rect in rects:
        result = ApplyResult(package=rect.package, desired=rect)

        if _is_layout_excluded(rect.package):
            result.status = LAYOUT_SKIPPED
            result.detail = "excluded (Termux/system)"
            result.attempts.append("skip-excluded")
            result.final_ok = True   # excluded packages are not "failures"
            results.append(result)
            continue

        # Step 1: pre-write (optional — skipped for read-only post-launch verify)
        if pre_write:
            _write_one_package(
                rect,
                root_tool=root_tool,
                known_keys=known_keys_map.get(rect.package, []),
                result=result,
                screen_mode=mode,
            )
        else:
            result.attempts.append("verify-only: pre-write skipped")
            result.pre_write_ok = True

        # Step 2: force-stop so prefs reload on next launch
        if pre_write and force_stop_before and result.pre_write_ok:
            try:
                android.force_stop_package(rect.package)
                result.attempts.append("force-stop ok")
            except Exception as exc:  # noqa: BLE001
                result.attempts.append(f"force-stop error: {exc}")

        results.append(result)

    if not verify_after:
        for r in results:
            if r.status == LAYOUT_SKIPPED:
                continue
            if r.pre_write_ok:
                r.final_ok = True
                r.status = LAYOUT_UNVERIFIED
                r.detail = r.detail or "pre-write succeeded; verification skipped"
            else:
                r.final_ok = False
                r.status = LAYOUT_FAILED
                r.detail = r.detail or "pre-write failed; verification skipped"
        return results

    # Step 3-5: post-launch verification + direct resize + retry
    for attempt in range(max(1, retries + 1)):
        all_ok = True
        for result in results:
            if result.status == LAYOUT_SKIPPED:
                continue

            # Make sure a window/task actually exists before we judge bounds.
            _wait_for_window(result.package, timeout=wait_for_window_seconds)

            bounds, source = read_actual_bounds(result.package)
            result.actual_bounds = bounds
            result.actual_method = source
            result.attempts.append(f"verify-attempt-{attempt}: {source}={bounds}")

            if bounds is None:
                # Could not verify — keep current status, mark Unverified.
                result.final_ok = result.pre_write_ok
                result.status = LAYOUT_UNVERIFIED if result.pre_write_ok else LAYOUT_FAILED
                all_ok = False
                continue

            if _bounds_close_enough(bounds, result.desired, tolerance):
                result.final_ok = True
                result.status = LAYOUT_APPLIED
                layer_errors: list[str] = []
                if mode == "portrait":
                    _record_portrait_layer_readback(result, tolerance=tolerance)
                    layer_errors = _portrait_layer_validation_errors(result, tolerance=tolerance)
                    if layer_errors:
                        result.validation.extend(layer_errors)
                        result.final_ok = False
                        result.status = LAYOUT_FAILED
                        all_ok = False
                        corrected_rect = _titlebar_corrected_rect(result)
                        if allow_direct_resize and root_tool and corrected_rect is not None:
                            ok, detail = _direct_resize_via_root(
                                result.package, corrected_rect, root_tool
                            )
                            result.direct_resize_ok = ok
                            result.attempts.append(f"titlebar-corrected-resize: {detail}")
                            if ok:
                                time.sleep(0.6)
                                bounds, source = read_actual_bounds(result.package)
                                result.actual_bounds = bounds
                                result.actual_method = source
                                _record_portrait_layer_readback(result, tolerance=tolerance)
                                layer_errors = _portrait_layer_validation_errors(result, tolerance=tolerance)
                                if bounds and _bounds_close_enough(bounds, corrected_rect, tolerance) and not layer_errors:
                                    result.validation = []
                                    result.final_ok = True
                                    result.status = LAYOUT_APPLIED
                                    continue
                if not layer_errors:
                    continue

            all_ok = False

            if not allow_direct_resize:
                result.attempts.append("verify-only: direct resize skipped")
                result.final_ok = False
                result.status = LAYOUT_UNVERIFIED
                continue

            # Step 4: direct resize via root
            if root_tool:
                ok, detail = _direct_resize_via_root(
                    result.package, result.desired, root_tool
                )
                result.direct_resize_ok = ok
                result.attempts.append(f"direct-resize: {detail}")
                if ok:
                    time.sleep(0.6)
                    bounds, source = read_actual_bounds(result.package)
                    result.actual_bounds = bounds
                    result.actual_method = source
                    if bounds and _bounds_close_enough(bounds, result.desired, tolerance):
                        result.final_ok = True
                        result.status = LAYOUT_APPLIED
                        layer_errors = []
                        if mode == "portrait":
                            _record_portrait_layer_readback(result, tolerance=tolerance)
                            layer_errors = _portrait_layer_validation_errors(result, tolerance=tolerance)
                            if layer_errors:
                                result.validation.extend(layer_errors)
                                result.final_ok = False
                                result.status = LAYOUT_FAILED
                                all_ok = False
                                corrected_rect = _titlebar_corrected_rect(result)
                                if root_tool and corrected_rect is not None:
                                    ok, detail = _direct_resize_via_root(
                                        result.package, corrected_rect, root_tool
                                    )
                                    result.direct_resize_ok = ok
                                    result.attempts.append(f"titlebar-corrected-resize: {detail}")
                                    if ok:
                                        time.sleep(0.6)
                                        bounds, source = read_actual_bounds(result.package)
                                        result.actual_bounds = bounds
                                        result.actual_method = source
                                        _record_portrait_layer_readback(result, tolerance=tolerance)
                                        layer_errors = _portrait_layer_validation_errors(result, tolerance=tolerance)
                                        if bounds and _bounds_close_enough(bounds, corrected_rect, tolerance) and not layer_errors:
                                            result.validation = []
                                            result.final_ok = True
                                            result.status = LAYOUT_APPLIED
                                            continue
                                continue
                        if not layer_errors:
                            continue

            # Step 5: re-write keys then force-stop again so next launch
            # picks up the corrected prefs.
            if allow_direct_resize and attempt + 1 < max(1, retries + 1):
                rewrote = _write_one_package(
                    result.desired,
                    root_tool=root_tool,
                    known_keys=known_keys_map.get(result.package, []),
                    result=result,
                    screen_mode=mode,
                )
                if rewrote:
                    result.attempts.append("retry-rewrite ok")
            # Default to FAILED for now — the next attempt may upgrade it.
            result.final_ok = False
            result.status = LAYOUT_FAILED

        if all_ok:
            break

    _validate_actual_layout(results, screen_mode=mode)

    if mode == "portrait" and touch_probe:
        for r in results:
            if r.status == LAYOUT_SKIPPED or r.actual_bounds is None:
                continue
            ok, center, detail = _tap_center_probe(r.package, r.actual_bounds, root_tool)
            r.touch_probe_ok = ok
            r.touch_probe_center = center
            r.touch_probe_detail = detail
            r.attempts.append(
                f"touch-probe: center={center} result={'ok' if ok else 'fail'} detail={detail}"
            )
            if not ok:
                r.final_ok = False
                r.status = LAYOUT_FAILED
                r.validation.append("touch probe failed")
            _log.info(
                "[DENG_REJOIN_TOUCH_PROBE] package=%s center=%s result=%s detail=%s",
                r.package, center, "ok" if ok else "fail", detail,
            )

    for r in results:
        if r.status != LAYOUT_SKIPPED and r.validation:
            r.final_ok = False
            r.status = LAYOUT_FAILED

    # Finalize details
    for r in results:
        if r.status == LAYOUT_SKIPPED:
            continue
        if r.status == LAYOUT_APPLIED:
            r.detail = (
                f"applied via {r.pre_write_method}, verified by {r.actual_method}"
            )
        elif r.status == LAYOUT_UNVERIFIED:
            r.detail = (
                f"pre-write OK via {r.pre_write_method}; bounds not readable"
            )
        else:
            r.detail = (
                f"layout not honored "
                f"(pre_write={r.pre_write_ok}, actual_bounds={r.actual_bounds}, "
                f"validation={','.join(r.validation) or 'Mismatch'})"
            )

    return results


def apply_window_layout_silent(
    rects: Iterable[WindowRect],
    *,
    force_stop_before: bool = False,
    relaunch_after: bool = False,
    verify_after: bool = True,
    pre_write: bool = True,
    allow_direct_resize: bool = True,
    retries: int = 1,
    screen_mode: str = "landscape",
) -> tuple[int, int]:
    """Silent wrapper: returns (success_count, total_count).  Never raises.

    "Success" means status is ``LAYOUT_APPLIED`` OR ``LAYOUT_SKIPPED``.
    """
    try:
        results = apply_window_layout(
            list(rects),
            force_stop_before=force_stop_before,
            relaunch_after=relaunch_after,
            verify_after=verify_after,
            pre_write=pre_write,
            allow_direct_resize=allow_direct_resize,
            retries=retries,
            screen_mode=screen_mode,
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("apply_window_layout_silent error: %s", exc)
        return 0, 0
    ok = sum(
        1 for r in results
        if r.status in (LAYOUT_APPLIED, LAYOUT_SKIPPED)
    )
    return ok, len(results)


def force_resize_package(package: str, rect: WindowRect) -> tuple[bool, str]:
    """One-shot resize for a single package — used during supervisor recovery.

    Pipeline:
      1. Ensure freeform/resizable system settings are on.
      2. Direct resize via root (cmd activity resize-task / am task resize /
         am stack resize / wm task resize / windowing-mode flip).
      3. Read back bounds; return whether they match desired ± 32 px.

    Never raises.  Returns ``(ok, detail)``.

    Recovery note (probe p-6c644c4708): this used to call
    ``setup_freeform_capabilities()`` on every recovery.  That re-wrote the
    global/secure freeform flags and made WindowManager recreate the whole
    activity stack — force-closing every app + Termux ("root bound window"
    mass close).  Freeform is already ensured once at Start (and re-ensured,
    now session-guarded, by the ``apply_window_layout_silent`` call that
    precedes this in supervisor recovery), so we only do the per-task resize
    here and never touch global window settings while clones are live.
    """
    root_info = android.detect_root()
    if not root_info.available or not root_info.tool:
        return False, "no root"
    try:
        return _direct_resize_via_root(package, rect, root_info.tool)
    except Exception as exc:  # noqa: BLE001
        return False, f"resize error: {exc}"
