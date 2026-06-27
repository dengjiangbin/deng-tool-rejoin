"""Effective screen mode detection for resize layout.

Raw ``wm size`` often stays in physical portrait (e.g. 1080x1920) while the
effective UI coordinate space is landscape after rotation.  Resize must use
the effective mode, not sensor order alone.
"""

from __future__ import annotations

import re
from typing import Any

from . import android
from .window_layout import _detect_display_from_dumpsys

_FOCUS_BOUNDS_RE = re.compile(
    r"mCurrentFocus=Window\{[^}]*\b([\w.]+)/[\w.]+\b[^}]*\}",
    re.IGNORECASE,
)
_RECT_RE = re.compile(r"Rect\(([-\d]+),\s*([-\d]+)\s*-\s*([-\d]+),\s*([-\d]+)\)")


def _parse_wm_raw() -> tuple[int, int, str]:
    wm = android.get_wm_size()
    w = int(wm.get("width") or 0)
    h = int(wm.get("height") or 0)
    raw = str(wm.get("raw") or "").strip()
    if w <= 0 or h <= 0:
        return 0, 0, raw
    return w, h, raw


def _logical_size_from_dumpsys() -> tuple[int, int, str]:
    block = _detect_display_from_dumpsys()
    if not block:
        return 0, 0, ""
    w, h, _density = block
    return int(w), int(h), f"{w}x{h}"


def _display_state() -> dict[str, Any]:
    return android.get_display_orientation_state()


def _home_launcher_landscape() -> tuple[bool, str]:
    try:
        launcher = android.get_home_launcher_package()
        info = android.get_home_launcher_bounds(launcher)
        bounds = info.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            return False, str(launcher or "")
        bw = max(0, int(bounds[2]) - int(bounds[0]))
        bh = max(0, int(bounds[3]) - int(bounds[1]))
        return bool(bw > bh and bw > 0), str(launcher or "")
    except Exception:  # noqa: BLE001
        return False, ""


def _current_focus_size() -> tuple[int, int, str]:
    try:
        res = android.run_android_command(["dumpsys", "window", "windows"], timeout=6, prefer_root=True)
        text = res.stdout or ""
        if not text:
            return 0, 0, ""
        focus_idx = text.find("mCurrentFocus=")
        if focus_idx < 0:
            return 0, 0, ""
        block = text[focus_idx: focus_idx + 2500]
        pkg = ""
        m = _FOCUS_BOUNDS_RE.search(block)
        if m:
            pkg = m.group(1)
        rect_m = _RECT_RE.search(block)
        if not rect_m:
            return 0, 0, pkg
        l, t, r, b = (int(g) for g in rect_m.groups())
        w = max(0, r - l)
        h = max(0, b - t)
        return w, h, pkg
    except Exception:  # noqa: BLE001
        return 0, 0, ""


def _rotation_bucket(rotation: Any) -> str:
    try:
        rot = int(rotation)
    except (TypeError, ValueError):
        return "unknown"
    if rot in (1, 3):
        return "landscape"
    if rot in (0, 2):
        return "portrait"
    return "unknown"


def detect_effective_resize_mode(
    *,
    previous_mode: str | None = None,
) -> dict[str, Any]:
    """Return effective PORTRAIT/LANDSCAPE mode for resize coordinate space."""
    wm_w, wm_h, wm_raw = _parse_wm_raw()
    major = max(wm_w, wm_h) if wm_w > 0 and wm_h > 0 else 0
    minor = min(wm_w, wm_h) if wm_w > 0 and wm_h > 0 else 0

    display = _display_state()
    logical_w, logical_h, logical_label = _logical_size_from_dumpsys()
    if logical_w <= 0 or logical_h <= 0:
        logical_w = int(display.get("width") or 0)
        logical_h = int(display.get("height") or 0)
        logical_label = f"{logical_w}x{logical_h}" if logical_w and logical_h else ""

    app_w, app_h, _ = _logical_size_from_dumpsys()
    app_label = f"{app_w}x{app_h}" if app_w and app_h else logical_label

    focus_w, focus_h, focus_pkg = _current_focus_size()
    home_landscape, launcher_pkg = _home_launcher_landscape()

    rotation = display.get("rotation", "")
    rot_bucket = _rotation_bucket(rotation)

    wm_portrait = wm_w > 0 and wm_h > wm_w
    logical_landscape = logical_w > logical_h and logical_w > 0
    logical_portrait = logical_h > logical_w and logical_h > 0
    focus_landscape = focus_w > focus_h and focus_w > 0
    focus_portrait = focus_h > focus_w and focus_h > 0

    home_landscape_wm_portrait_conflict = bool(
        wm_portrait and (home_landscape or logical_landscape or focus_landscape or rot_bucket == "landscape")
    )

    conflicts: list[str] = []
    votes_landscape = 0
    votes_portrait = 0

    if logical_landscape:
        votes_landscape += 3
    elif logical_portrait:
        votes_portrait += 3

    if focus_landscape:
        votes_landscape += 2
    elif focus_portrait:
        votes_portrait += 2

    if home_landscape:
        votes_landscape += 2

    if rot_bucket == "landscape":
        votes_landscape += 1
    elif rot_bucket == "portrait":
        votes_portrait += 1

    if wm_portrait and not home_landscape_wm_portrait_conflict:
        votes_portrait += 1
    elif not wm_portrait and wm_w > 0:
        votes_landscape += 1

    if home_landscape_wm_portrait_conflict:
        mode = "LANDSCAPE"
        confidence = "HIGH"
        basis = "effective window/home layout is landscape; raw wm size is portrait"
        conflicts.append("home_landscape_wm_portrait_conflict")
    elif votes_landscape > votes_portrait:
        mode = "LANDSCAPE"
        if logical_landscape or home_landscape:
            confidence = "HIGH"
            basis = "logical or home window width exceeds height"
        elif rot_bucket == "landscape":
            confidence = "MEDIUM"
            basis = "display rotation indicates landscape"
        else:
            confidence = "MEDIUM"
            basis = "majority orientation signals landscape"
    elif votes_portrait > votes_landscape:
        mode = "PORTRAIT"
        if logical_portrait:
            confidence = "HIGH"
            basis = "logical display height exceeds width"
        elif rot_bucket == "portrait":
            confidence = "MEDIUM"
            basis = "display rotation indicates portrait"
        else:
            confidence = "MEDIUM"
            basis = "majority orientation signals portrait"
    else:
        prev = str(previous_mode or "").strip().upper()
        if prev in {"LANDSCAPE", "PORTRAIT"}:
            mode = prev
            confidence = "LOW"
            basis = f"conflicting signals; fallback to previous successful mode {prev}"
        elif rot_bucket == "landscape":
            mode = "LANDSCAPE"
            confidence = "LOW"
            basis = "conflicting signals; weak rotation fallback landscape"
        else:
            mode = "PORTRAIT"
            confidence = "LOW"
            basis = "conflicting signals; weak fallback portrait"
        conflicts.append("signal_tie")

    if votes_landscape > 0 and votes_portrait > 0 and not home_landscape_wm_portrait_conflict:
        conflicts.append("mixed_orientation_signals")

    return {
        "mode": mode,
        "confidence": confidence,
        "basis": basis,
        "conflicts": conflicts,
        "signals": {
            "wm_size_raw": wm_raw or (f"{wm_w}x{wm_h}" if wm_w and wm_h else ""),
            "physical_size_normalized": {"major": major, "minor": minor},
            "rotation": str(rotation),
            "logical_size": logical_label,
            "app_size": app_label,
            "current_focus": focus_pkg or "",
            "current_focus_size": f"{focus_w}x{focus_h}" if focus_w and focus_h else "",
            "home_or_launcher_landscape": home_landscape,
            "home_landscape_wm_portrait_conflict": home_landscape_wm_portrait_conflict,
            "launcher_package": launcher_pkg,
        },
    }
