"""Grid bounds calculation for auto-detect resize."""

from __future__ import annotations

import math
from typing import Any

from .window_layout import WindowRect, _detect_status_bar_height


def columns_for_mode(mode: str, package_count: int) -> int:
    m = str(mode or "").strip().upper()
    n = max(0, int(package_count))
    if m == "LANDSCAPE":
        if n <= 6:
            return 2
        if n <= 9:
            return 3
        return 4
    if n <= 4:
        return 1
    if n <= 10:
        return 2
    return 3


def normalize_screen_dimensions(
    major: int,
    minor: int,
    mode: str,
) -> tuple[int, int]:
    if major <= 0 or minor <= 0:
        return 1080, 1920
    m = str(mode or "").strip().upper()
    if m == "LANDSCAPE":
        return major, minor
    return minor, major


def calculate_resize_grid(
    packages: list[str],
    *,
    mode: str,
    major: int,
    minor: int,
    left_offset: int = 0,
    top_margin: int | None = None,
    side_margin: int = 0,
    bottom_margin: int = 0,
) -> tuple[list[WindowRect], dict[str, Any]]:
    """Compute one WindowRect per package inside the effective screen space."""
    pkgs = [p for p in packages if p]
    screen_width, screen_height = normalize_screen_dimensions(major, minor, mode)
    if not pkgs:
        layout = {
            "screen_width": screen_width,
            "screen_height": screen_height,
            "columns": 0,
            "rows": 0,
            "left_offset": left_offset,
            "top_margin": 0,
            "side_margin": side_margin,
            "bottom_margin": bottom_margin,
            "usable_width": 0,
            "usable_height": 0,
            "empty_slots": 0,
        }
        return [], layout

    cols = columns_for_mode(mode, len(pkgs))
    rows = int(math.ceil(len(pkgs) / cols))
    top_margin = _detect_status_bar_height() if top_margin is None else max(0, int(top_margin))

    usable_left = max(0, int(left_offset))
    usable_right = max(usable_left + 1, screen_width - max(0, side_margin))
    usable_top = max(0, top_margin)
    usable_bottom = max(usable_top + 1, screen_height - max(0, bottom_margin))
    usable_width = max(1, usable_right - usable_left)
    usable_height = max(1, usable_bottom - usable_top)

    cell_w = max(1, usable_width // cols)
    cell_h = max(1, usable_height // rows)
    empty_slots = cols * rows - len(pkgs)

    rects: list[WindowRect] = []
    for index, pkg in enumerate(pkgs):
        row = index // cols
        col = index % cols
        left = usable_left + col * cell_w
        top = usable_top + row * cell_h
        right = screen_width - side_margin if col == cols - 1 else min(screen_width - side_margin, left + cell_w)
        bottom = usable_bottom if row == rows - 1 else min(usable_bottom, top + cell_h)
        if right <= left:
            right = min(screen_width - side_margin, left + cell_w)
        if bottom <= top:
            bottom = min(usable_bottom, top + cell_h)
        rects.append(WindowRect(pkg, left, top, right, bottom))

    layout = {
        "screen_width": screen_width,
        "screen_height": screen_height,
        "columns": cols,
        "rows": rows,
        "left_offset": usable_left,
        "top_margin": usable_top,
        "side_margin": side_margin,
        "bottom_margin": bottom_margin,
        "usable_width": usable_width,
        "usable_height": usable_height,
        "empty_slots": empty_slots,
    }
    return rects, layout


def validate_grid_bounds(rects: list[WindowRect], screen_width: int, screen_height: int) -> list[str]:
    errors: list[str] = []
    for i, r in enumerate(rects):
        if r.left < 0 or r.top < 0:
            errors.append(f"rect[{i}] {r.package}: negative origin")
        if r.right <= r.left or r.bottom <= r.top:
            errors.append(f"rect[{i}] {r.package}: invalid size")
        if r.right > screen_width or r.bottom > screen_height:
            errors.append(
                f"rect[{i}] {r.package}: offscreen ({r.left},{r.top},{r.right},{r.bottom}) "
                f"vs {screen_width}x{screen_height}"
            )
    return errors
