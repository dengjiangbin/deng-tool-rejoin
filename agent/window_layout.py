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
    size_result    = android.run_command(["wm", "size"],    timeout=5)
    density_result = android.run_command(["wm", "density"], timeout=5)
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
    _log.warning("landscape_blocks: all phases failed, using emergency spread for n=%d", n)
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


# ── App Cloner XML writers ────────────────────────────────────────────────────

def app_cloner_prefs_path(package: str) -> Path:
    return Path("/data/data") / package / "shared_prefs" / "pkg_preferences.xml"


def update_app_cloner_xml(package: str, rect: WindowRect) -> tuple[bool, str]:
    """Write window position to App Cloner shared_prefs XML (direct file access).

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
    """Write App Cloner window position via root. Returns (ok, message). Never raises."""
    try:
        path_str = f"/data/data/{package}/shared_prefs/pkg_preferences.xml"
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
