"""Landscape-block window layout and safe App Cloner XML updates.

Layout model
────────────
 ┌──────────────────────────────────────────────────────────────────┐
 │  Left panel (35%)                 │  Right pane (65%)            │
 │  DENG Tool / Termux status        │  Roblox clone windows        │
 │  logo, package table, logs        │  (freeform, landscape-shaped)│
 └──────────────────────────────────────────────────────────────────┘

Landscape-block rules (all window counts):
  - Every Roblox window MUST be landscape-shaped: width >= height * 1.25.
  - No two windows may touch or overlap; minimum gap is GAP_PX on each side.
  - Windows are stacked vertically inside the right pane at full pane width
    and 16:9 height.  When they do not fit at 16:9, height is compressed
    until either landscape is impossible or 2-column layout is needed.
  - 2-column layout is used only when single-column compression would make
    windows too short to remain landscape.
  - Outer margin separates windows from screen edges.

Failure handling: every write path is wrapped in try/except.
No crash, no block to Start.  All layout details go to debug log.
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

# ── Safety: packages that must NEVER be resized or repositioned ───────────────
_LAYOUT_EXCLUDE_PREFIXES: tuple[str, ...] = (
    "com.termux",
    "com.android.",
    "android",
    "com.google.",
    "com.samsung.",
    "com.huawei.",
    "com.miui.",
    "com.oneplus.",
    "com.oppo.",
    "com.vivo.",
)

_LAYOUT_EXCLUDE_EXACT: frozenset[str] = frozenset({
    "com.termux",
    "com.termux.boot",
    "com.termux.api",
    "com.termux.styling",
    "com.termux.widget",
    "android",
    "com.android.launcher",
    "com.android.launcher3",
    "com.android.systemui",
})


def _is_layout_excluded(package: str) -> bool:
    pkg = package.strip().lower()
    if pkg in _LAYOUT_EXCLUDE_EXACT:
        return True
    return any(pkg.startswith(pfx) for pfx in _LAYOUT_EXCLUDE_PREFIXES)


# ── Layout constants ─────────────────────────────────────────────────────────

APP_CLONER_KEYS = {
    "app_cloner_current_window_left":   "left",
    "app_cloner_current_window_top":    "top",
    "app_cloner_current_window_right":  "right",
    "app_cloner_current_window_bottom": "bottom",
}

# Left side reserved for DENG Tool / Termux status panel.
TERMUX_LOG_FRACTION = 0.35          # 35% left → 65% right
RIGHT_PANE_FRACTION  = 1.0 - TERMUX_LOG_FRACTION

# Landscape aspect ratio: height = width * LANDSCAPE_H_RATIO
# 16:9 → ratio = 9/16 ≈ 0.5625; we use 9:16 as denominator
LANDSCAPE_H_RATIO: float = 9.0 / 16.0   # window height = width × this

# Minimum gap between any two windows (pixels).
GAP_PX: int = 20

# Outer margin between screen edge / pane boundary and nearest window.
OUTER_MARGIN: int = 16

# Minimum window dimension (safety floor — below this layout is abandoned).
_MIN_WIN_W: int = 160
_MIN_WIN_H: int = 90

# Landscape threshold: width must be at least this times height.
LANDSCAPE_MIN_RATIO: float = 1.25

# Legacy cascade constant (kept for backward-compat test references only).
KAERU_TITLE_BAR_H: int = 48

# ── App Cloner extended key aliases ───────────────────────────────────────────
#
# App Cloner / cloud-phone versions disagree on the key names for window
# bounds, the "Set window position" / "Set window size" enable booleans, and
# the per-orientation portrait/landscape variants.  Without the enable booleans
# turned ON, App Cloner ignores the position ints on next launch — that is the
# root cause of "windows still use last manually opened position".
#
# We write ALL known aliases.  Extras are harmless (App Cloner only honors the
# ones it knows; unknown keys are kept in shared_prefs without effect).
APP_CLONER_POSITION_ALIASES: dict[str, tuple[str, ...]] = {
    "left":   (
        "app_cloner_current_window_left",
        "app_cloner_window_position_left",
        "app_cloner_window_position_landscape_left",
        "app_cloner_window_position_x",
        "app_cloner_window_x",
        "app_cloner_saved_bounds_left",
        "app_cloner_saved_bounds_landscape_left",
        "app_cloner_task_bounds_left",
    ),
    "top": (
        "app_cloner_current_window_top",
        "app_cloner_window_position_top",
        "app_cloner_window_position_landscape_top",
        "app_cloner_window_position_y",
        "app_cloner_window_y",
        "app_cloner_saved_bounds_top",
        "app_cloner_saved_bounds_landscape_top",
        "app_cloner_task_bounds_top",
    ),
    "right": (
        "app_cloner_current_window_right",
        "app_cloner_window_position_right",
        "app_cloner_window_position_landscape_right",
        "app_cloner_saved_bounds_right",
        "app_cloner_saved_bounds_landscape_right",
        "app_cloner_task_bounds_right",
    ),
    "bottom": (
        "app_cloner_current_window_bottom",
        "app_cloner_window_position_bottom",
        "app_cloner_window_position_landscape_bottom",
        "app_cloner_saved_bounds_bottom",
        "app_cloner_saved_bounds_landscape_bottom",
        "app_cloner_task_bounds_bottom",
    ),
    "width": (
        "app_cloner_window_width",
        "app_cloner_window_size_width",
        "app_cloner_window_size_landscape_width",
        "app_cloner_custom_screen_size_width",
    ),
    "height": (
        "app_cloner_window_height",
        "app_cloner_window_size_height",
        "app_cloner_window_size_landscape_height",
        "app_cloner_custom_screen_size_height",
    ),
}

# Boolean "Set X" enable flags — these MUST be true for App Cloner to honor the
# corresponding int values on next launch.  Writing the int alone is not enough.
APP_CLONER_ENABLE_ALIASES: tuple[str, ...] = (
    "app_cloner_set_window_position",
    "app_cloner_set_window_position_enabled",
    "app_cloner_window_position_enabled",
    "app_cloner_set_window_size",
    "app_cloner_set_window_size_enabled",
    "app_cloner_window_size_enabled",
    "app_cloner_custom_screen_size",
    "app_cloner_enable_custom_screen_size",
    "app_cloner_enable_resize",
    "app_cloner_force_resize",
    "app_cloner_allow_resize",
    "app_cloner_freeform",
    "app_cloner_freeform_window",
    # Orientation enable flags (force landscape on the cloned window)
    "app_cloner_force_landscape",
    "app_cloner_orientation_landscape",
    "app_cloner_set_orientation_landscape",
    # Auto-DPI landscape flag (Kaeru clue: "look for Set in auto-DPI pot/land")
    "app_cloner_auto_dpi_landscape",
    "app_cloner_set_auto_dpi_landscape",
    "app_cloner_set_dpi_landscape",
)

# Keys that App Cloner / cloud-phone window managers may use to remember the
# LAST manually-positioned window state.  If we leave these populated with a
# stale value, the OS happily restores that stale rect right back on top of
# the one we just wrote.  By overwriting these to the *desired* rect too,
# the "restore last window" code path becomes a no-op (the "last" position
# is what we want).
APP_CLONER_LAST_POSITION_ALIASES: dict[str, tuple[str, ...]] = {
    "left": (
        "last_window_position_x",
        "last_window_x",
        "user_window_position_x",
        "manual_window_position_x",
        "freeform_last_x",
        "freeform_window_last_x",
        "floating_window_last_x",
    ),
    "top": (
        "last_window_position_y",
        "last_window_y",
        "user_window_position_y",
        "manual_window_position_y",
        "freeform_last_y",
        "freeform_window_last_y",
        "floating_window_last_y",
    ),
    "width": (
        "last_window_width",
        "user_window_width",
        "manual_window_width",
        "freeform_last_width",
        "freeform_window_last_width",
        "floating_window_last_width",
    ),
    "height": (
        "last_window_height",
        "user_window_height",
        "manual_window_height",
        "freeform_last_height",
        "freeform_window_last_height",
        "floating_window_last_height",
    ),
}

# Categories whose keys we will always set when writing — even if not
# pre-existing in the file (App Cloner reads them if present, ignores if not).
_WRITE_CATEGORIES_FOR_VALUES: dict[str, tuple[str, ...]] = {
    # category → list of *aliases* to write with the same integer value
    "position_left":   APP_CLONER_POSITION_ALIASES["left"],
    "position_top":    APP_CLONER_POSITION_ALIASES["top"],
    "position_right":  APP_CLONER_POSITION_ALIASES["right"],
    "position_bottom": APP_CLONER_POSITION_ALIASES["bottom"],
    "size_width":      APP_CLONER_POSITION_ALIASES["width"],
    "size_height":     APP_CLONER_POSITION_ALIASES["height"],
}


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

    @property
    def win_w(self) -> int:
        return self.right - self.left

    @property
    def win_h(self) -> int:
        return self.bottom - self.top

    def as_dict(self) -> dict[str, int | str]:
        return {
            "package": self.package,
            "left":    self.left,
            "top":     self.top,
            "right":   self.right,
            "bottom":  self.bottom,
        }

    def preview_line(self, index: int) -> str:
        w = self.win_w
        h = self.win_h
        ratio = f"{w/h:.2f}" if h > 0 else "∞"
        return (
            f"  {index}. {self.package[:36]:<36} "
            f"l={self.left} t={self.top} r={self.right} b={self.bottom}"
            f"  ({w}×{h}, ratio={ratio})"
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
    """Probe the real display size + density of the host.

    Uses :func:`android.run_android_command` so:

    * the bare name ``wm`` is auto-resolved to ``/system/bin/wm`` (Termux's
      ``$PATH`` excludes ``/system/bin`` — this regression was caught on a
      Samsung SM-N9810 cloud phone where every previous build silently
      fell back to the hardcoded ``(1080, 1920)`` default, producing
      off-screen layout bounds);
    * the call is routed through ``su`` on permission denial — some Samsung
      One UI builds reject ``wm size`` from unprivileged callers.

    The fallback ``(1080, 1920)`` is only used as a last-ditch safety net
    when both unprivileged AND root calls fail.
    """
    size_result    = android.run_android_command(["wm", "size"],    timeout=6)
    density_result = android.run_android_command(["wm", "density"], timeout=6)
    size    = parse_wm_size(size_result.stdout)     if size_result.ok    else None
    density = parse_wm_density(density_result.stdout) if density_result.ok else None
    width, height = size or (1080, 1920)
    return DisplayInfo(width=width, height=height, density=density or 420)


# ── Validation helpers ────────────────────────────────────────────────────────

def validate_layout_rects(
    rects: list[WindowRect],
    pane_x0: int,
    pane_y0: int,
    pane_x1: int,
    pane_y1: int,
) -> list[str]:
    """Validate a list of layout rectangles.

    Returns a list of violation strings (empty = all OK).
    Checks: landscape, no-overlap, no-touch, inside pane, unique bounds.
    """
    errors: list[str] = []
    for i, r in enumerate(rects):
        w, h = r.win_w, r.win_h
        if w < _MIN_WIN_W or h < _MIN_WIN_H:
            errors.append(f"rect[{i}] {r.package}: too small ({w}×{h})")
        if w < h * LANDSCAPE_MIN_RATIO:
            errors.append(
                f"rect[{i}] {r.package}: NOT landscape ({w}×{h}, need w>={h*LANDSCAPE_MIN_RATIO:.0f})"
            )
        if r.left < pane_x0 or r.right > pane_x1:
            errors.append(f"rect[{i}] {r.package}: x out of pane ({r.left}-{r.right} vs {pane_x0}-{pane_x1})")
        if r.top < pane_y0 or r.bottom > pane_y1:
            errors.append(f"rect[{i}] {r.package}: y out of pane ({r.top}-{r.bottom} vs {pane_y0}-{pane_y1})")

    # Pairwise: no overlap, no touch
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            # Touch check: requires gap >= 1 between any two windows
            if not (a.right + GAP_PX <= b.left or b.right + GAP_PX <= a.left or
                    a.bottom + GAP_PX <= b.top or b.bottom + GAP_PX <= a.top):
                errors.append(
                    f"rect[{i}] and rect[{j}] overlap or touch too closely "
                    f"(gap required={GAP_PX}px)"
                )

    seen: set[tuple[int, int, int, int]] = set()
    for i, r in enumerate(rects):
        key = (r.left, r.top, r.right, r.bottom)
        if key in seen:
            errors.append(f"rect[{i}] {r.package}: duplicate bounds")
        seen.add(key)

    return errors


# ── Core landscape-block layout engine ───────────────────────────────────────

def calculate_landscape_blocks(
    packages: Iterable[str],
    display_w: int,
    display_h: int,
    *,
    gap: int = GAP_PX,
    outer: int = OUTER_MARGIN,
    left_fraction: float = TERMUX_LOG_FRACTION,
) -> list[WindowRect]:
    """Calculate freeform-window landscape blocks for Roblox packages.

    Algorithm:
    1. Left panel (35%) reserved for DENG/Termux — never touched.
    2. Right pane = remaining 65% minus outer margins.
    3. Target window aspect ratio: 16:9 (width:height).
    4. PHASE 1: All windows at full pane width, 16:9 height, stacked vertically.
       If total height fits → center pack vertically with gap between windows.
    5. PHASE 2: If single column is too tall, compress window height until
       the stack fits. Continue only while the window remains landscape
       (width >= height * LANDSCAPE_MIN_RATIO).
    6. PHASE 3: If even compressed single-column windows would be too short
       to remain landscape, use 2 columns with 16:9 windows.
    7. PHASE 4: 3 columns (for very large N).
    8. Validate all results; fall back to safer layout if validation fails.

    All coordinates are absolute screen coordinates (ready for App Cloner XML).
    """
    pkgs = [p for p in packages]
    n = len(pkgs)
    if n == 0:
        return []

    # Screen bounds
    W = max(1, int(display_w))
    H = max(1, int(display_h))
    g = max(1, int(gap))
    om = max(0, int(outer))

    # Left panel reservation
    left_end = round(W * max(0.1, min(0.9, float(left_fraction))))

    # Right pane absolute bounds (with outer margins)
    px0 = left_end + om       # left edge of pane
    py0 = om                  # top edge of pane
    px1 = W - om              # right edge of pane
    py1 = H - om              # bottom edge of pane
    pane_w = max(_MIN_WIN_W, px1 - px0)
    pane_h = max(_MIN_WIN_H, py1 - py0)

    def _make_rects_single_col(win_h: int) -> list[WindowRect]:
        """Stack N windows in one column with the given height."""
        total_h = n * win_h + (n - 1) * g
        y_start = py0 + max(0, (pane_h - total_h) // 2)
        result = []
        for i, pkg in enumerate(pkgs):
            y = y_start + i * (win_h + g)
            result.append(WindowRect(pkg, px0, y, px0 + pane_w, y + win_h))
        return result

    def _make_rects_grid(cols: int) -> list[WindowRect]:
        """Lay out in a grid of `cols` columns, 16:9 windows."""
        cell_w = (pane_w - g * (cols - 1)) // cols
        win_h_target = round(cell_w * LANDSCAPE_H_RATIO)
        rows = math.ceil(n / cols)
        total_h = rows * win_h_target + (rows - 1) * g
        y_start = py0 + max(0, (pane_h - total_h) // 2)
        result = []
        for i, pkg in enumerate(pkgs):
            row = i // cols
            col = i % cols
            x = px0 + col * (cell_w + g)
            y = y_start + row * (win_h_target + g)
            result.append(WindowRect(pkg, x, y, x + cell_w, y + win_h_target))
        return result

    # ── PHASE 1: ideal 16:9 single column ────────────────────────────────
    ideal_win_h = round(pane_w * LANDSCAPE_H_RATIO)
    total_ideal = n * ideal_win_h + (n - 1) * g

    if total_ideal <= pane_h:
        rects = _make_rects_single_col(ideal_win_h)
        errors = validate_layout_rects(rects, px0, py0, px1, py1)
        if not errors:
            _log.debug("landscape_blocks: phase1 (ideal 16:9 single-col), n=%d", n)
            return rects
        _log.debug("landscape_blocks: phase1 validation failed: %s", errors)

    # ── PHASE 2: compressed single column ────────────────────────────────
    # Maximum compression: make windows shorter until they still fit.
    # Require: win_w >= win_h * LANDSCAPE_MIN_RATIO → min win_h = pane_w / LANDSCAPE_MIN_RATIO
    min_landscape_h = max(_MIN_WIN_H, int(pane_w / LANDSCAPE_MIN_RATIO))
    compressed_win_h = (pane_h - (n - 1) * g) // n if n > 0 else pane_h

    if compressed_win_h >= min_landscape_h:
        rects = _make_rects_single_col(compressed_win_h)
        errors = validate_layout_rects(rects, px0, py0, px1, py1)
        if not errors:
            _log.debug("landscape_blocks: phase2 (compressed single-col h=%d), n=%d", compressed_win_h, n)
            return rects
        _log.debug("landscape_blocks: phase2 validation failed: %s", errors)

    # ── PHASE 3: 2-column grid ────────────────────────────────────────────
    for cols in (2, 3):
        rects = _make_rects_grid(cols)
        if not rects:
            continue
        errors = validate_layout_rects(rects, px0, py0, px1, py1)
        if not errors:
            _log.debug("landscape_blocks: phase3 (%d-col grid), n=%d", cols, n)
            return rects
        _log.debug("landscape_blocks: phase3 %d-col validation failed: %s", cols, errors)

    # ── PHASE 4: emergency fallback — just spread evenly ─────────────────
    # No further validation — at least they're unique and inside pane.
    # Log at DEBUG only — public stdout must never see this.
    _log.debug("landscape_blocks: all phases failed, using emergency spread for n=%d", n)
    rows = math.ceil(math.sqrt(n))
    cols = math.ceil(n / rows)
    cell_w = max(_MIN_WIN_W, (pane_w - g * (cols - 1)) // cols)
    cell_h = max(_MIN_WIN_H, (pane_h - g * (rows - 1)) // rows)
    win_h = min(cell_h, round(cell_w * LANDSCAPE_H_RATIO))
    result = []
    for i, pkg in enumerate(pkgs):
        row = i // cols
        col = i % cols
        x = px0 + col * (cell_w + g)
        y = py0 + row * (cell_h + g)
        result.append(WindowRect(pkg, x, y, x + cell_w, y + win_h))
    return result


# ── Legacy / compat wrappers ──────────────────────────────────────────────────

def calculate_kaeru_layout(
    packages:     Iterable[str],
    right_width:  int,
    total_height: int,
    gap:          int = GAP_PX,
) -> list[WindowRect]:
    """Backward-compatible wrapper.  Delegates to calculate_landscape_blocks.

    Coordinates are relative to the top-left of the right pane
    (caller must add the left-pane offset to X if needed).

    Note: this function no longer applies the old kaeru cascade algorithm.
    It now produces proper landscape windows using the new block engine.
    The `right_width` and `total_height` args are used as the full canvas
    so the block engine can compute its own pane from them using fraction=0.
    """
    pkgs = list(packages)
    if not pkgs:
        return []
    # Treat right_width × total_height as the full right-pane canvas
    # (no further left reservation — caller already passed right-pane dims).
    rects = calculate_landscape_blocks(
        pkgs,
        right_width,
        total_height,
        gap=gap,
        outer=max(0, gap // 2),
        left_fraction=0.0,   # no additional left reservation
    )
    return rects


def calculate_grid_layout(
    packages: Iterable[str],
    width:    int,
    height:   int,
    gap:      int = GAP_PX,
) -> list[WindowRect]:
    """Uniform grid layout.  Delegates to calculate_kaeru_layout for compat."""
    return calculate_kaeru_layout(packages, width, height, gap)


# ── Primary split layout (used by Start) ─────────────────────────────────────

def calculate_split_layout(
    packages:             Iterable[str],
    width:                int,
    height:               int,
    gap:                  int = GAP_PX,
    *,
    termux_log_fraction:  float = TERMUX_LOG_FRACTION,
) -> list[WindowRect]:
    """Reserve the left panel for DENG/Termux; place landscape blocks on the right.

    Returns absolute screen coordinates ready for App Cloner XML.
    """
    package_list = list(packages)
    if not package_list:
        return []
    return calculate_landscape_blocks(
        package_list,
        display_w=max(1, int(width)),
        display_h=max(1, int(height)),
        gap=gap,
        outer=OUTER_MARGIN,
        left_fraction=termux_log_fraction,
    )


# ── App Cloner / generic clone XML writers ───────────────────────────────────
#
# Different clone wrappers store their per-package preferences in different
# files.  App Cloner uses ``pkg_preferences.xml``; Moons multi-clone (real
# probe id p-368a65d699, packages ``com.moons.litesc/d/e``) uses
# ``<package>_preferences.xml`` plus a global ``prefs.xml``; some OEM clone
# managers use ``settings.xml`` or ``cloner_settings.xml``.  We try each
# candidate in turn so a single tool works across all of them.


def app_cloner_prefs_path(package: str) -> Path:
    """Return the canonical App Cloner prefs path for ``package`` (legacy API).

    Kept for backwards-compatibility with callers/tests that still ask for a
    single Path.  New code should use :func:`clone_prefs_candidates`.
    """
    return Path("/data/data") / package / "shared_prefs" / "pkg_preferences.xml"


def clone_prefs_candidates(package: str) -> list[Path]:
    """Return every clone-wrapper prefs file we know how to write.

    Order matters: more-specific names first so we touch the file that's
    most likely to actually steer the clone's window manager.

    Discovered empirically from real device probes:

    * App Cloner            → ``pkg_preferences.xml``
    * Moons multi-clone     → ``<package>_preferences.xml`` (e.g.
      ``com.moons.litesc_preferences.xml``) and ``prefs.xml``
    * Generic OEM wrappers  → ``settings.xml`` / ``cloner_settings.xml``
    """
    base = Path("/data/data") / package / "shared_prefs"
    return [
        base / "pkg_preferences.xml",
        base / f"{package}_preferences.xml",
        base / "prefs.xml",
        base / "cloner_settings.xml",
        base / "settings.xml",
    ]


# ── Multi-alias XML mutators (shared by direct and root writers) ──────────────


def _values_from_rect(rect: WindowRect) -> dict[str, int]:
    """Return position/size values keyed by canonical name."""
    return {
        "left":   int(rect.left),
        "top":    int(rect.top),
        "right":  int(rect.right),
        "bottom": int(rect.bottom),
        "width":  int(rect.win_w),
        "height": int(rect.win_h),
    }


def _ensure_int_child(root_el: ET.Element, name: str, value: int) -> bool:
    """Update or create ``<int name=name value=value/>``.  Returns True if changed."""
    for child in root_el:
        if child.attrib.get("name") == name:
            if child.tag != "int":
                child.tag = "int"
                if child.text:
                    child.text = None
            cur = child.attrib.get("value")
            new_val = str(int(value))
            if cur != new_val:
                child.set("value", new_val)
                return True
            return False
    new = ET.SubElement(root_el, "int")
    new.set("name", name)
    new.set("value", str(int(value)))
    return True


def _ensure_bool_child(root_el: ET.Element, name: str, value: bool) -> bool:
    """Update or create ``<boolean name=name value=value/>``.  Returns True if changed."""
    new_val = "true" if value else "false"
    for child in root_el:
        if child.attrib.get("name") == name:
            if child.tag != "boolean":
                child.tag = "boolean"
                if child.text:
                    child.text = None
            cur = child.attrib.get("value")
            if cur != new_val:
                child.set("value", new_val)
                return True
            return False
    new = ET.SubElement(root_el, "boolean")
    new.set("name", name)
    new.set("value", new_val)
    return True


def _apply_layout_keys_to_root(
    root_el: ET.Element, rect: WindowRect, *, known_keys: Iterable[str] | None = None
) -> int:
    """Apply every layout-relevant key to a parsed XML root.

    Writes:
      * Every alias in :data:`APP_CLONER_POSITION_ALIASES` for left/top/right/
        bottom/width/height.
      * Every flag in :data:`APP_CLONER_ENABLE_ALIASES` set to ``true``.
      * Any pre-existing keys whose name matches one of the position/size
        aliases or the legacy ``app_cloner_current_window_*`` set — even if
        they were stored as ``string`` or ``long`` — are updated.

    Returns the number of keys created/modified.
    """
    values = _values_from_rect(rect)
    changed = 0

    # 1. Legacy compat: update old "current_window" mapping if present (string/int)
    for child in root_el:
        legacy_key = APP_CLONER_KEYS.get(child.attrib.get("name") or "")
        if not legacy_key:
            continue
        new_val = str(values[legacy_key])
        if child.tag == "int":
            if child.attrib.get("value") != new_val:
                child.set("value", new_val)
                changed += 1
        else:
            if (child.text or "") != new_val:
                child.text = new_val
                changed += 1

    # 2. Position/size aliases — write every known alias as <int>.
    for canon, aliases in APP_CLONER_POSITION_ALIASES.items():
        v = values[canon]
        for name in aliases:
            if _ensure_int_child(root_el, name, v):
                changed += 1

    # 3. Critical "Set" enable booleans — these must be true so App Cloner
    #    actually honors the position/size ints on next launch.
    for name in APP_CLONER_ENABLE_ALIASES:
        if _ensure_bool_child(root_el, name, True):
            changed += 1

    # 3b. Override last/manual position keys (Layer 5 from the spec).
    # When App Cloner / the cloud phone "restores the last window position"
    # on relaunch, that path reads keys like ``last_window_position_x``.
    # Make those equal to the rect we just wrote so the restore is a no-op.
    for canon, aliases in APP_CLONER_LAST_POSITION_ALIASES.items():
        v = values[canon]
        for name in aliases:
            if _ensure_int_child(root_el, name, v):
                changed += 1

    # 4. Honor pre-discovered known_keys names by also writing matching
    #    int/bool variants (covers App Cloner versions we have not enumerated).
    if known_keys:
        from .layout_discovery import _classify  # local import (avoids cycle at import time)

        for name in known_keys:
            if not name:
                continue
            if any(name in aliases for aliases in APP_CLONER_POSITION_ALIASES.values()):
                continue  # already handled in step 2
            if name in APP_CLONER_ENABLE_ALIASES:
                continue  # already handled in step 3
            cats = _classify(name)
            target_value: int | None = None
            if "position_left" in cats or "size_width" in cats and "left" in name.lower():
                target_value = values["left"]
            elif "position_top" in cats:
                target_value = values["top"]
            elif "position_right" in cats:
                target_value = values["right"]
            elif "position_bottom" in cats:
                target_value = values["bottom"]
            elif "size_width" in cats:
                target_value = values["width"]
            elif "size_height" in cats:
                target_value = values["height"]
            if target_value is not None:
                if _ensure_int_child(root_el, name, target_value):
                    changed += 1
                continue
            # Boolean-y enable categories
            if any(
                c in cats
                for c in (
                    "set_position_enable",
                    "set_size_enable",
                    "set_dpi_enable",
                    "freeform",
                    "auto_dpi",
                    "orient_landscape",
                )
            ):
                if _ensure_bool_child(root_el, name, True):
                    changed += 1

    return changed


def _serialize_xml(root_el: ET.Element) -> str:
    """Serialize ``root_el`` to a valid Android shared_prefs XML string."""
    body = ET.tostring(root_el, encoding="unicode")
    return "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n" + body


def _validate_xml(text: str) -> bool:
    try:
        ET.fromstring(text)
        return True
    except ET.ParseError:
        return False


def update_app_cloner_xml(
    package: str,
    rect: WindowRect,
    *,
    known_keys: Iterable[str] | None = None,
) -> tuple[bool, str]:
    """Write window position+size+enable flags via direct file access.

    Walks every candidate in :func:`clone_prefs_candidates`; the first one
    that exists and is writable wins.  Returns (ok, message); never raises.
    """
    attempted: list[str] = []
    last_error = ""
    for path in clone_prefs_candidates(package):
        if not path.exists():
            attempted.append(f"{path.name}: missing")
            continue
        try:
            backup = path.with_suffix(f".xml.bak-{int(time.time())}")
            try:
                shutil.copy2(path, backup)
            except OSError:
                pass
            tree    = ET.parse(path)
            root_el = tree.getroot()
            changed = _apply_layout_keys_to_root(root_el, rect, known_keys=known_keys)
            if changed == 0:
                attempted.append(f"{path.name}: no keys changed (already desired)")
                continue
            serialized = _serialize_xml(root_el)
            if not _validate_xml(serialized):
                attempted.append(f"{path.name}: XML invalid after mutation")
                continue
            path.write_text(serialized, encoding="utf-8")
            _log.debug("Direct XML write OK: %s → %s (%d keys)", package, path.name, changed)
            return True, f"Updated {path.name} ({changed} keys)"
        except PermissionError:
            last_error = "Permission denied"
            attempted.append(f"{path.name}: permission denied (try root)")
        except (OSError, ET.ParseError) as exc:
            last_error = str(exc)
            attempted.append(f"{path.name}: {exc}")
    if not attempted:
        return False, "no clone prefs candidates checked"
    return False, "no writable clone prefs file: " + "; ".join(attempted[:5])


def update_app_cloner_xml_root(
    package:    str,
    rect:       WindowRect,
    root_tool:  str,
    timeout:    int = 10,
    *,
    known_keys: Iterable[str] | None = None,
) -> tuple[bool, str]:
    """Write clone-wrapper window prefs via root.

    Iterates every candidate in :func:`clone_prefs_candidates`; for each one,
    reads via ``cat`` (creates empty ``<map>`` if missing), applies the
    multi-alias mutation, and writes via base64-decode to a tmp file then
    atomic ``mv``.  Stops at the first candidate that succeeds.

    Returns (ok, message); never raises.
    """
    attempted: list[str] = []
    for path in clone_prefs_candidates(package):
        path_str = str(path)
        try:
            # Read existing or treat as empty map if missing.  We attempt EVERY
            # candidate even when missing, because creating ``pkg_preferences.xml``
            # is the App-Cloner path; for Moons/OEM we only want to touch files
            # that already exist.  Skip nonexistent unless this is the very last
            # candidate AND nothing else worked yet (then we try the canonical
            # ``pkg_preferences.xml`` as last-ditch).
            exists_res = android.run_root_command(
                ["sh", "-c", f"test -f '{path_str}' && echo Y || echo N"],
                root_tool=root_tool, timeout=timeout,
            )
            exists = (exists_res.stdout or "").strip().startswith("Y")
            if not exists and path.name != "pkg_preferences.xml":
                attempted.append(f"{path.name}: missing")
                continue
            read_res = android.run_root_command(
                ["sh", "-c", f"test -f '{path_str}' && cat '{path_str}' 2>/dev/null"],
                root_tool=root_tool, timeout=timeout,
            )
            if not read_res.ok or not (read_res.stdout or "").strip():
                root_el = ET.Element("map")
            else:
                try:
                    root_el = ET.fromstring(read_res.stdout)
                except ET.ParseError as exc:
                    attempted.append(f"{path.name}: parse error: {exc}")
                    continue
            changed = _apply_layout_keys_to_root(root_el, rect, known_keys=known_keys)
            if changed == 0:
                attempted.append(f"{path.name}: no keys changed (already desired)")
                continue
            new_xml = _serialize_xml(root_el)
            if not _validate_xml(new_xml):
                attempted.append(f"{path.name}: XML invalid after mutation")
                continue
            b64_data = base64.b64encode(new_xml.encode("utf-8")).decode("ascii")
            tmp_path = f"{path_str}.deng-tmp"
            write_cmd = (
                f"mkdir -p '/data/data/{package}/shared_prefs' && "
                f"echo '{b64_data}' | base64 -d > '{tmp_path}' && "
                f"chmod 660 '{tmp_path}' 2>/dev/null; "
                f"mv -f '{tmp_path}' '{path_str}'"
            )
            write_res = android.run_root_command(
                ["sh", "-c", write_cmd], root_tool=root_tool, timeout=timeout,
            )
            if write_res.ok:
                _log.debug("Root XML write OK: %s → %s (%d keys)", package, path.name, changed)
                return True, f"Wrote {path.name} via root ({changed} keys)"
            err = (write_res.stderr or "")[:80]
            attempted.append(f"{path.name}: write failed: {err}")
        except Exception as exc:  # noqa: BLE001
            _log.debug("update_app_cloner_xml_root error for %s @ %s: %s", package, path.name, exc)
            attempted.append(f"{path.name}: writer error: {exc}")
    if not attempted:
        return False, "no clone prefs candidates"
    return False, "Root write failed: " + "; ".join(attempted[:5])


# ── Apply layout (high-level) ─────────────────────────────────────────────────

def apply_layout_to_packages(
    packages:         Iterable[str],
    *,
    gap:              int  = GAP_PX,
    write_xml:        bool = False,
    use_split_layout: bool = False,  # kept for compat; split is always used
) -> tuple[list[str], list[dict[str, int | str]]]:
    """Calculate landscape block layout and optionally write App Cloner XML.

    Returns (messages, preview_list).
    """
    try:
        display = detect_display_info()
    except Exception:  # noqa: BLE001
        display = DisplayInfo(width=1080, height=1920, density=420)

    package_list = [p for p in packages if not _is_layout_excluded(p)]
    if not package_list:
        return ["No Roblox packages to lay out (all excluded or empty)."], []

    rects = calculate_split_layout(package_list, display.width, display.height, gap)

    preview  = [rect.as_dict() for rect in rects]
    messages = [rect.preview_line(i) for i, rect in enumerate(rects, 1)]

    if not write_xml:
        return messages, preview

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
    gap:      int = GAP_PX,
) -> list[str]:
    disp  = display or detect_display_info()
    rects = calculate_split_layout(list(packages), disp.width, disp.height, gap)
    return [rect.preview_line(i) for i, rect in enumerate(rects, 1)]


def verify_split_layout(
    packages: Iterable[str],
    display:  DisplayInfo | None = None,
    gap:      int = GAP_PX,
) -> list[str]:
    disp         = display or detect_display_info()
    package_list = list(packages)
    left_end     = int(disp.width * TERMUX_LOG_FRACTION)
    lines        = [
        f"Display: {disp.width}×{disp.height}  density={disp.density}",
        f"Left pane (Termux): 0–{left_end}px ({int(TERMUX_LOG_FRACTION*100)}%)",
        f"Right pane (Roblox): {left_end}–{disp.width}px ({int(RIGHT_PANE_FRACTION*100)}%)",
    ]
    rects = calculate_split_layout(package_list, disp.width, disp.height, gap)
    errors = validate_layout_rects(
        rects,
        left_end + OUTER_MARGIN,
        OUTER_MARGIN,
        disp.width - OUTER_MARGIN,
        disp.height - OUTER_MARGIN,
    )
    if errors:
        lines.append("VALIDATION FAILURES:")
        lines.extend(f"  ! {e}" for e in errors)
    for i, rect in enumerate(rects, 1):
        lines.append(rect.preview_line(i))
    return lines
