"""Kaeru-style window layout calculation and safe App Cloner XML updates.

Layout model
────────────
 ┌──────────────────────────────────────────────────────────────────┐
 │  Left panel (30-40%)              │  Right pane (60-70%)         │
 │  DENG Tool / Termux status        │  Roblox clone windows        │
 │  logo, package table, logs        │  (floating, tiled/cascaded)  │
 └──────────────────────────────────────────────────────────────────┘

Layout rules by package count (right pane only):
  1  → full right pane (single large window)
  2  → landscape: side-by-side  /  portrait: stacked
  3  → 2+1  (two on top ~55%, one full-width on bottom ~45%)
  4  → 2×2 grid
  5–6 → 2-column compact (up to 3 rows)
  7+  → 3-column Kaeru-style with cascaded title-bar offsets

Failure handling: every write path is wrapped in try/except.
No crash, no block to Start.  Details in debug logs only.
"""

from __future__ import annotations

import base64
import logging
import math
import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import android

_log = logging.getLogger("deng.rejoin.window_layout")

# ── Layout constants ─────────────────────────────────────────────────────────

APP_CLONER_KEYS = {
    "app_cloner_current_window_left": "left",
    "app_cloner_current_window_top": "top",
    "app_cloner_current_window_right": "right",
    "app_cloner_current_window_bottom": "bottom",
}

# Left side reserved for DENG Tool / Termux status panel.
TERMUX_LOG_FRACTION = 0.35          # 35 % left → 65 % right
RIGHT_PANE_FRACTION  = 1.0 - TERMUX_LOG_FRACTION

# Estimated title bar height (dp-scale pixels) for each App Cloner window.
# Used by the 7+ cascade algorithm to keep each title bar clickable.
KAERU_TITLE_BAR_H: int = 48

# Minimum dimensions for any individual window (safety floor).
_MIN_WIN_W: int = 180
_MIN_WIN_H: int = 180


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DisplayInfo:
    width:   int
    height:  int
    density: int


@dataclass(frozen=True)
class WindowRect:
    package: str
    left:    int
    top:     int
    right:   int
    bottom:  int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "package": self.package,
            "left":    self.left,
            "top":     self.top,
            "right":   self.right,
            "bottom":  self.bottom,
        }

    def preview_line(self, index: int) -> str:
        w = self.right  - self.left
        h = self.bottom - self.top
        return (
            f"  {index}. {self.package[:36]:<36} "
            f"l={self.left} t={self.top} r={self.right} b={self.bottom}  ({w}×{h})"
        )


# ── Display detection ─────────────────────────────────────────────────────────

def parse_wm_size(output: str) -> tuple[int, int] | None:
    for token in output.replace(":", " ").split():
        if "x" in token:
            left, right = token.lower().split("x", 1)
            if left.isdigit() and right.isdigit():
                return int(left), int(right)
    return None


def parse_wm_density(output: str) -> int | None:
    for token in output.replace(":", " ").split():
        if token.isdigit():
            return int(token)
    return None


def detect_display_info() -> DisplayInfo:
    size_result    = android.run_command(["wm", "size"],    timeout=5)
    density_result = android.run_command(["wm", "density"], timeout=5)
    size    = parse_wm_size(size_result.stdout)     if size_result.ok    else None
    density = parse_wm_density(density_result.stdout) if density_result.ok else None
    width, height = size or (1080, 1920)
    return DisplayInfo(width=width, height=height, density=density or 420)


# ── Legacy grid layout (kept for backward compatibility) ──────────────────────

def calculate_grid_layout(
    packages: Iterable[str],
    width:    int,
    height:   int,
    gap:      int = 8,
) -> list[WindowRect]:
    """Uniform grid layout (no left reservation). Used by legacy callers."""
    return calculate_kaeru_layout(packages, width, height, gap)


# ── Kaeru-style layout ────────────────────────────────────────────────────────

def calculate_kaeru_layout(
    packages:     Iterable[str],
    right_width:  int,
    total_height: int,
    gap:          int = 8,
) -> list[WindowRect]:
    """Kaeru-style layout for Roblox windows in the right pane.

    All coordinates are relative to the top-left of the right pane
    (caller must add the left-pane offset before passing to App Cloner XML).

    Title bar visibility rules:
    - 1–6 packages: each window gets its own non-overlapping cell.
    - 7+ packages:  3-column grid, each column cascaded by KAERU_TITLE_BAR_H
      vertically so every title bar remains above the content of windows behind it.
    """
    pkgs = list(packages)
    n = len(pkgs)
    if n == 0:
        return []

    W   = max(_MIN_WIN_W, int(right_width))
    H   = max(_MIN_WIN_H, int(total_height))
    g   = max(0, int(gap))
    TBH = KAERU_TITLE_BAR_H

    raw: list[tuple[int, int, int, int]] = []

    # ── 1 package: full right pane ────────────────────────────────────────
    if n == 1:
        raw = [(0, 0, W, H)]

    # ── 2 packages: side-by-side (landscape) or stacked (portrait) ───────
    elif n == 2:
        if W >= H:
            hw = max(_MIN_WIN_W, (W - g) // 2)
            raw = [(0, 0, hw, H), (hw + g, 0, W, H)]
        else:
            hh = max(_MIN_WIN_H, (H - g) // 2)
            raw = [(0, 0, W, hh), (0, hh + g, W, H)]

    # ── 3 packages: 2+1 (two on top ~55 %, one full-width on bottom) ─────
    elif n == 3:
        top_h  = max(_MIN_WIN_H, int(H * 0.55))
        bot_y  = top_h + g
        hw     = max(_MIN_WIN_W, (W - g) // 2)
        raw    = [
            (0,      0,     hw, top_h),
            (hw + g, 0,      W, top_h),
            (0,      bot_y,  W, H),
        ]

    # ── 4 packages: 2×2 grid ──────────────────────────────────────────────
    elif n == 4:
        hw = max(_MIN_WIN_W, (W - g) // 2)
        hh = max(_MIN_WIN_H, (H - g) // 2)
        raw = [
            (0,      0,      hw, hh),
            (hw + g, 0,       W, hh),
            (0,      hh + g, hw,  H),
            (hw + g, hh + g,  W,  H),
        ]

    # ── 5–6 packages: 2-column compact (≤3 rows) ─────────────────────────
    elif n <= 6:
        cols = 2
        rows = math.ceil(n / cols)
        cw   = max(_MIN_WIN_W, (W - g * (cols - 1)) // cols)
        ch   = max(_MIN_WIN_H, (H - g * (rows - 1)) // rows)
        for i in range(n):
            row, col = divmod(i, cols)
            x = col * (cw + g)
            y = row * (ch + g)
            raw.append((x, y, min(x + cw, W), min(y + ch, H)))

    # ── 7+ packages: 3-column Kaeru cascade ──────────────────────────────
    else:
        cols        = 3
        rows        = math.ceil(n / cols)
        cw          = max(_MIN_WIN_W, (W - g * (cols - 1)) // cols)
        # Cascade: each column in a row is offset down by TBH so its
        # title bar sits below the content area of the previous column.
        # Total cascade consumed per row = TBH * (cols - 1).
        cascade_per_col = TBH
        cascade_total   = cascade_per_col * (cols - 1)
        available_h     = max(_MIN_WIN_H * rows, H - cascade_total)
        ch              = max(_MIN_WIN_H, (available_h - g * (rows - 1)) // rows)

        for i in range(n):
            row, col = divmod(i, cols)
            x        = col * (cw + g)
            y_base   = row * (ch + g)
            y        = y_base + col * cascade_per_col
            raw.append((
                max(0, x),
                max(0, y),
                min(x + cw, W),
                min(y + ch, H),
            ))

    return [
        WindowRect(pkg, l, t, r, b)
        for pkg, (l, t, r, b) in zip(pkgs, raw)
    ]


# ── Split layout (left pane reserved for Termux) ──────────────────────────────

def calculate_split_layout(
    packages:           Iterable[str],
    width:              int,
    height:             int,
    gap:                int = 8,
    *,
    termux_log_fraction: float = TERMUX_LOG_FRACTION,
) -> list[WindowRect]:
    """Reserve the left ``termux_log_fraction`` for DENG/Termux; use Kaeru layout on the right.

    Returns absolute screen coordinates (ready for App Cloner XML).
    """
    package_list = list(packages)
    if not package_list:
        return []
    width  = max(1, int(width))
    height = max(1, int(height))
    frac   = max(0.1, min(0.9, float(termux_log_fraction)))
    left_offset     = int(width * frac)
    available_width = max(_MIN_WIN_W, width - left_offset)

    rects = calculate_kaeru_layout(package_list, available_width, height, gap)
    # Shift every rect into the right pane (add left_offset to X coords)
    return [
        WindowRect(r.package, r.left + left_offset, r.top, r.right + left_offset, r.bottom)
        for r in rects
    ]


# ── App Cloner XML writers ────────────────────────────────────────────────────

def app_cloner_prefs_path(package: str) -> Path:
    return Path("/data/data") / package / "shared_prefs" / "pkg_preferences.xml"


def update_app_cloner_xml(package: str, rect: WindowRect) -> tuple[bool, str]:
    """Write window position to App Cloner shared_prefs XML (direct file access).

    Works when Termux has read/write access to the file (same UID or permissive).
    Returns (ok, message). Never raises.
    """
    try:
        path = app_cloner_prefs_path(package)
        if not path.exists():
            return False, "pkg_preferences.xml not found (no App Cloner clone?)"
        backup = path.with_suffix(f".xml.bak-{int(time.time())}")
        shutil.copy2(path, backup)
        tree    = ET.parse(path)
        root_el = tree.getroot()
        values  = rect.as_dict()
        changed = 0
        for child in root_el:
            name = child.attrib.get("name")
            key  = APP_CLONER_KEYS.get(name or "")
            if not key:
                continue
            new_val = str(values[key])
            if child.tag == "int":
                child.set("value", new_val)
            else:
                child.text = new_val
            changed += 1
        if changed == 0:
            return False, "App Cloner XML found but window keys missing"
        tree.write(path, encoding="utf-8", xml_declaration=True)
        _log.debug("Direct XML write OK: %s (%d keys)", package, changed)
        return True, f"Updated App Cloner window preferences ({changed} keys)"
    except PermissionError:
        return False, "Permission denied (try with root)"
    except (OSError, ET.ParseError) as exc:
        return False, f"Direct XML write failed: {exc}"


def update_app_cloner_xml_root(
    package:    str,
    rect:       WindowRect,
    root_tool:  str,
    timeout:    int = 10,
) -> tuple[bool, str]:
    """Write App Cloner window position via root (for protected /data/data paths).

    Uses ``base64`` encoding to safely transfer the XML over the shell pipe
    without any quoting issues. Returns (ok, message). Never raises.
    """
    try:
        path_str = f"/data/data/{package}/shared_prefs/pkg_preferences.xml"
        # Read current XML via root
        read_res = android.run_root_command(
            ["sh", "-c", f"test -f '{path_str}' && cat '{path_str}' 2>/dev/null"],
            root_tool=root_tool, timeout=timeout,
        )
        if not read_res.ok or not (read_res.stdout or "").strip():
            return False, "pkg_preferences.xml not accessible via root"

        try:
            root_el = ET.fromstring(read_res.stdout)
        except ET.ParseError as exc:
            return False, f"App Cloner XML parse failed: {exc}"

        values  = rect.as_dict()
        changed = 0
        for child in root_el:
            name = child.attrib.get("name")
            key  = APP_CLONER_KEYS.get(name or "")
            if not key:
                continue
            new_val = str(values[key])
            if child.tag == "int":
                child.set("value", new_val)
            else:
                child.text = new_val
            changed += 1

        if changed == 0:
            return False, "No App Cloner window keys found in XML via root"

        new_xml  = "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
        new_xml += ET.tostring(root_el, encoding="unicode")
        b64_data = base64.b64encode(new_xml.encode("utf-8")).decode("ascii")

        # Write via root: decode base64 and redirect to the target path.
        write_cmd = f"echo '{b64_data}' | base64 -d > '{path_str}'"
        write_res = android.run_root_command(
            ["sh", "-c", write_cmd], root_tool=root_tool, timeout=timeout,
        )
        if write_res.ok:
            _log.debug("Root XML write OK: %s (%d keys)", package, changed)
            return True, f"Window position set via root ({changed} keys)"
        err = (write_res.stderr or "")[:80]
        return False, f"Root write failed: {err}"
    except Exception as exc:  # noqa: BLE001
        _log.debug("update_app_cloner_xml_root error for %s: %s", package, exc)
        return False, f"Root XML writer error: {exc}"


# ── High-level apply function ─────────────────────────────────────────────────

def apply_layout_to_packages(
    packages:         Iterable[str],
    *,
    gap:              int  = 8,
    write_xml:        bool = False,
    use_split_layout: bool = False,
) -> tuple[list[str], list[dict[str, int | str]]]:
    """Calculate Kaeru layout and optionally write App Cloner XML positions.

    When ``use_split_layout`` is True (multiple packages), the left
    ``TERMUX_LOG_FRACTION`` of the screen is reserved for the DENG/Termux
    status panel. All Roblox windows are placed in the right pane using the
    Kaeru-style layout appropriate for the package count.

    Every write path is wrapped in try/except — failures are logged at DEBUG
    level and reported in the returned messages, but never crash Start.

    Returns:
        (messages, preview_list) — human-readable status lines, rect dicts.
    """
    try:
        display = detect_display_info()
    except Exception:  # noqa: BLE001
        display = DisplayInfo(width=1080, height=1920, density=420)

    package_list = list(packages)
    if not package_list:
        return ["No packages to lay out."], []

    # Choose layout
    if use_split_layout and len(package_list) > 1:
        rects = calculate_split_layout(package_list, display.width, display.height, gap)
    else:
        # Single package or explicit full-screen: Kaeru in the right 65 %
        left_offset = int(display.width * TERMUX_LOG_FRACTION) if len(package_list) == 1 else 0
        available_w = max(_MIN_WIN_W, display.width - left_offset)
        raw_rects   = calculate_kaeru_layout(package_list, available_w, display.height, gap)
        rects       = [
            WindowRect(r.package, r.left + left_offset, r.top, r.right + left_offset, r.bottom)
            for r in raw_rects
        ]

    preview  = [rect.as_dict() for rect in rects]
    messages = [rect.preview_line(i) for i, rect in enumerate(rects, 1)]

    if not write_xml:
        return messages, preview

    # ── Write App Cloner XML for each package ─────────────────────────────
    root = android.detect_root()
    root_tool: str | None = root.tool if root.available else None

    write_msgs: list[str] = []
    for rect in rects:
        try:
            ok, msg = update_app_cloner_xml(rect.package, rect)
            if not ok and root_tool:
                ok, msg = update_app_cloner_xml_root(rect.package, rect, root_tool)
            write_msgs.append(f"{rect.package}: {msg}")
        except Exception as exc:  # noqa: BLE001
            _log.debug("Layout write skipped for %s: %s", rect.package, exc)
            write_msgs.append(f"{rect.package}: layout skipped ({exc})")

    return write_msgs, preview


# ── Verification / preview helpers ───────────────────────────────────────────

def build_layout_preview(
    packages: Iterable[str],
    display:  DisplayInfo | None = None,
    gap:      int = 8,
) -> list[str]:
    """Return human-readable preview lines for the Kaeru layout (no write)."""
    disp  = display or detect_display_info()
    rects = calculate_split_layout(list(packages), disp.width, disp.height, gap)
    return [rect.preview_line(i) for i, rect in enumerate(rects, 1)]


def verify_split_layout(
    packages: Iterable[str],
    display:  DisplayInfo | None = None,
    gap:      int = 8,
) -> list[str]:
    """Return human-readable verification lines for the split layout."""
    disp         = display or detect_display_info()
    package_list = list(packages)
    left_end     = int(disp.width * TERMUX_LOG_FRACTION)
    lines        = [
        f"Display: {disp.width}×{disp.height}  density={disp.density}",
        f"Left pane (Termux log): 0–{left_end}px ({int(TERMUX_LOG_FRACTION * 100)}% of width)",
        f"Right pane (Roblox):   {left_end}–{disp.width}px ({int(RIGHT_PANE_FRACTION * 100)}% of width)",
    ]
    if len(package_list) <= 1:
        lines.append("Split layout: single package — large right-pane window")
    else:
        rects = calculate_split_layout(package_list, disp.width, disp.height, gap)
        for i, rect in enumerate(rects, 1):
            w = rect.right  - rect.left
            h = rect.bottom - rect.top
            lines.append(
                f"  {i}. {rect.package}  "
                f"l={rect.left} t={rect.top} r={rect.right} b={rect.bottom}  ({w}×{h})"
            )
    return lines
