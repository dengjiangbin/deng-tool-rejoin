"""Freeform/window layout calculation and safe App Cloner XML updates."""

from __future__ import annotations

import math
import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import android

APP_CLONER_KEYS = {
    "app_cloner_current_window_left": "left",
    "app_cloner_current_window_top": "top",
    "app_cloner_current_window_right": "right",
    "app_cloner_current_window_bottom": "bottom",
}


@dataclass(frozen=True)
class DisplayInfo:
    width: int
    height: int
    density: int


@dataclass(frozen=True)
class WindowRect:
    package: str
    left: int
    top: int
    right: int
    bottom: int

    def as_dict(self) -> dict[str, int | str]:
        return {"package": self.package, "left": self.left, "top": self.top, "right": self.right, "bottom": self.bottom}

    def preview_line(self, index: int) -> str:
        return f"Package {index}: {self.package} left={self.left} top={self.top} right={self.right} bottom={self.bottom}"


def calculate_grid_layout(packages: Iterable[str], width: int, height: int, gap: int = 8) -> list[WindowRect]:
    package_list = list(packages)
    count = len(package_list)
    if count == 0:
        return []
    width = max(1, int(width))
    height = max(1, int(height))
    gap = max(0, int(gap))
    if count == 1:
        cols, rows = 1, 1
    elif count == 2:
        cols, rows = 2, 1
    elif count <= 4:
        cols, rows = 2, 2
    elif count <= 6:
        cols, rows = 3, 2
    elif count <= 9:
        cols, rows = 3, 3
    else:
        cols = math.ceil(math.sqrt(count))
        rows = math.ceil(count / cols)

    cell_w = max(1, (width - gap * (cols - 1)) // cols)
    cell_h = max(1, (height - gap * (rows - 1)) // rows)
    rects: list[WindowRect] = []
    for index, package in enumerate(package_list):
        row = index // cols
        col = index % cols
        left = max(0, col * (cell_w + gap))
        top = max(0, row * (cell_h + gap))
        right = min(width, left + cell_w)
        bottom = min(height, top + cell_h)
        rects.append(WindowRect(package, left, top, right, bottom))
    return rects


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
    size_result = android.run_command(["wm", "size"], timeout=5)
    density_result = android.run_command(["wm", "density"], timeout=5)
    size = parse_wm_size(size_result.stdout) if size_result.ok else None
    density = parse_wm_density(density_result.stdout) if density_result.ok else None
    width, height = size or (1080, 1920)
    return DisplayInfo(width=width, height=height, density=density or 420)


def build_layout_preview(packages: Iterable[str], display: DisplayInfo | None = None, gap: int = 8) -> list[str]:
    display = display or detect_display_info()
    rects = calculate_grid_layout(packages, display.width, display.height, gap)
    return [rect.preview_line(index) for index, rect in enumerate(rects, start=1)]


def app_cloner_prefs_path(package: str) -> Path:
    return Path("/data/data") / package / "shared_prefs" / "pkg_preferences.xml"


def update_app_cloner_xml(package: str, rect: WindowRect) -> tuple[bool, str]:
    """Safely update known App Cloner window XML values when locally accessible."""
    path = app_cloner_prefs_path(package)
    if not path.exists():
        return False, "Window preference file not found for this package. DENG will still launch the app, but cannot resize this clone automatically."
    try:
        backup = path.with_suffix(path.suffix + f".bak-{int(time.time())}")
        shutil.copy2(path, backup)
        tree = ET.parse(path)
        root = tree.getroot()
        values = rect.as_dict()
        changed = 0
        for child in root:
            name = child.attrib.get("name")
            key = APP_CLONER_KEYS.get(name or "")
            if not key:
                continue
            new_value = str(values[key])
            if child.tag == "int":
                child.set("value", new_value)
            else:
                child.text = new_value
            changed += 1
        if changed == 0:
            return False, "App Cloner XML was found, but known window keys were not present."
        tree.write(path, encoding="utf-8", xml_declaration=True)
        return True, f"Updated App Cloner window preferences; backup: {backup}"
    except (OSError, ET.ParseError) as exc:
        return False, f"Could not update App Cloner XML safely: {exc}"


def apply_layout_to_packages(packages: Iterable[str], *, gap: int = 8, write_xml: bool = False) -> tuple[list[str], list[dict[str, int | str]]]:
    display = detect_display_info()
    rects = calculate_grid_layout(packages, display.width, display.height, gap)
    messages = [rect.preview_line(index) for index, rect in enumerate(rects, start=1)]
    if write_xml:
        root = android.detect_root()
        if not root.available:
            messages.append("Auto resize needs root/file access for cloned app preference files. You can still use normal rejoin.")
        for rect in rects:
            ok, message = update_app_cloner_xml(rect.package, rect)
            messages.append(f"{rect.package}: {message}")
    return messages, [rect.as_dict() for rect in rects]
