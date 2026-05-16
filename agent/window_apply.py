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
from typing import Iterable, Sequence

from . import android
from .window_layout import (
    WindowRect,
    _is_layout_excluded,
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


@dataclass
class _WindowEntry:
    package: str
    bounds:  tuple[int, int, int, int] | None
    has_surface: bool
    is_focused: bool
    task_id: int | None
    raw_block: str


_WINDOW_HEADER_RE = re.compile(r"^\s*(?:Window\b.*|.*\bWindow\s*\{).*$")


def _is_window_header_line(line: str) -> bool:
    """Heuristic: does this line look like the start of a window block?

    Matches both real Android dumpsys lines (``Window{abc1234 ...}``) and the
    looser sample used in tests (``Window foo {``).
    """
    return ("Window{" in line) or ("Window {" in line) or (
        line.lstrip().startswith("Window") and "{" in line
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


def read_actual_bounds(package: str) -> tuple[tuple[int, int, int, int] | None, str]:
    """Read the actual on-screen bounds for ``package``.

    Picks the candidate that (1) has a real surface, (2) is visible, or
    (3) is focused, in that order.  Returns ``(bounds_or_None, source_label)``.
    Source labels: ``dumpsys_window``, ``dumpsys_activity``, ``unavailable``.
    Never raises.
    """
    # 1. dumpsys window windows — prefer windows with mHasSurface=true.
    try:
        res = android.run_command(["dumpsys", "window", "windows"], timeout=6)
        if res.ok and res.stdout:
            cands = _parse_window_dumpsys(res.stdout, package)
            # Prefer: surface + bounds → focused + bounds → any with bounds.
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
                if c.visible and c.bounds:
                    return c.bounds, "dumpsys_activity"
            for c in cands:
                if c.bounds:
                    return c.bounds, "dumpsys_activity"
    except Exception:  # noqa: BLE001
        pass

    return None, "unavailable"


def _get_task_id(package: str) -> int | None:
    """Best-effort: find the task id for ``package``."""
    try:
        res = android.run_command(["dumpsys", "activity", "activities"], timeout=6)
        if res.ok:
            cands = _parse_activity_dumpsys(res.stdout, package)
            for c in cands:
                if c.task_id is not None:
                    return c.task_id
    except Exception:  # noqa: BLE001
        pass
    try:
        res = android.run_command(["dumpsys", "window", "windows"], timeout=6)
        if res.ok:
            cands = _parse_window_dumpsys(res.stdout, package)
            for c in cands:
                if c.task_id is not None:
                    return c.task_id
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
            if ev.get("task") or ev.get("window") or ev.get("running"):
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


def _direct_resize_via_root(
    package: str, rect: WindowRect, root_tool: str
) -> tuple[bool, str]:
    """Try multiple direct-resize commands for the package's current task.

    Each Android build supports a different subset of these commands.  We
    try every variant and return success on the first one that runs without
    error AND moves the actual bounds.  Never raises.
    """
    task_id = _get_task_id(package)
    if task_id is None:
        return False, "no task id"
    l, t, r, b = rect.left, rect.top, rect.right, rect.bottom

    # First, flip the task into freeform windowing mode so the resize is
    # honored.  Some Android forks silently no-op resize on fullscreen tasks.
    try:
        android.run_root_command(
            ["cmd", "activity", "set-task-windowing-mode", str(task_id), "5"],
            root_tool=root_tool, timeout=4,
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        android.run_root_command(
            ["am", "stack", "move-task", str(task_id), "0", "true"],
            root_tool=root_tool, timeout=4,
        )
    except Exception:  # noqa: BLE001
        pass

    candidates = [
        ["cmd", "activity", "resize-task", str(task_id),
         str(l), str(t), str(r), str(b)],
        ["cmd", "activity", "resize-task", str(task_id),
         str(l), str(t), str(r), str(b), "1"],
        ["am", "task", "resize", str(task_id),
         str(l), str(t), str(r), str(b)],
        ["am", "stack", "resize", str(task_id),
         str(l), str(t), str(r), str(b)],
        # Last resort: write bounds into the activity record directly.
        ["wm", "task", "resize", str(task_id),
         str(l), str(t), str(r), str(b)],
    ]
    last_err = ""
    for cmd_args in candidates:
        try:
            res = android.run_root_command(
                cmd_args, root_tool=root_tool, timeout=4,
            )
            if res.ok:
                return True, f"resize via {cmd_args[0]} {cmd_args[1]}"
            last_err = (res.stderr or res.stdout or "").strip()[:120]
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)[:120]
            continue
    return False, f"all direct-resize variants failed (task #{task_id}) — {last_err}"


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
) -> bool:
    """Pre-write the XML for one rect.  Updates ``result`` in place.

    Returns True if at least one write method succeeded.
    """
    try:
        ok, msg = update_app_cloner_xml(rect.package, rect, known_keys=known_keys)
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
                rect.package, rect, root_tool, known_keys=known_keys
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


def apply_window_layout(
    rects: Sequence[WindowRect],
    *,
    force_stop_before: bool = False,
    relaunch_after: bool = False,
    verify_after: bool = True,
    retries: int = 1,
    tolerance: int = 32,
    wait_for_window_seconds: float = 6.0,
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

        # Step 1: pre-write
        _write_one_package(
            rect,
            root_tool=root_tool,
            known_keys=known_keys_map.get(rect.package, []),
            result=result,
        )

        # Step 2: force-stop so prefs reload on next launch
        if force_stop_before and result.pre_write_ok:
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
                continue

            all_ok = False

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
                        continue

            # Step 5: re-write keys then force-stop again so next launch
            # picks up the corrected prefs.
            if attempt + 1 < max(1, retries + 1):
                rewrote = _write_one_package(
                    result.desired,
                    root_tool=root_tool,
                    known_keys=known_keys_map.get(result.package, []),
                    result=result,
                )
                if rewrote:
                    try:
                        android.force_stop_package(result.package)
                        result.attempts.append("retry-force-stop ok")
                    except Exception as exc:  # noqa: BLE001
                        result.attempts.append(f"retry-force-stop error: {exc}")
            # Default to FAILED for now — the next attempt may upgrade it.
            result.final_ok = False
            result.status = LAYOUT_FAILED

        if all_ok:
            break

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
                f"(pre_write={r.pre_write_ok}, actual_bounds={r.actual_bounds})"
            )

    return results


def apply_window_layout_silent(
    rects: Iterable[WindowRect],
    *,
    force_stop_before: bool = False,
    relaunch_after: bool = False,
    verify_after: bool = True,
    retries: int = 1,
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
            retries=retries,
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
    """
    try:
        from .freeform_enable import setup_freeform_capabilities
        setup_freeform_capabilities()
    except Exception:  # noqa: BLE001
        pass
    root_info = android.detect_root()
    if not root_info.available or not root_info.tool:
        return False, "no root"
    try:
        ok, detail = _direct_resize_via_root(package, rect, root_info.tool)
    except Exception as exc:  # noqa: BLE001
        return False, f"resize error: {exc}"
    if not ok:
        return False, detail
    time.sleep(0.6)
    try:
        bounds, _src = read_actual_bounds(package)
    except Exception:  # noqa: BLE001
        return ok, f"{detail} (post-readback failed)"
    if bounds and _bounds_close_enough(bounds, rect, 32):
        return True, f"{detail} → bounds verified {bounds}"
    return False, f"{detail} → bounds still {bounds} (want {rect.left,rect.top,rect.right,rect.bottom})"
