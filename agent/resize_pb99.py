"""PB99 / rz.txt auto-resize grid and bounds write (portrait-first source of truth)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from . import android
from .window_layout import WindowRect, _layout_tmp_dir, _serialize_xml

# Reserve screen space for the Rejoin table / Termux UI (pb99 grid origin).
_PB99_LANDSCAPE_LEFT_OFFSET_PERCENT = 40
_PB99_PORTRAIT_TOP_OFFSET_PERCENT = 40
_MCURRENT_ORIENTATION_RE = re.compile(
    r"mCurrentOrientation\s*=\s*(\d+)",
    re.IGNORECASE,
)


def read_display_rotation() -> int:
    """Return ``mCurrentOrientation`` from dumpsys display (pb99 uses 1/3 = landscape)."""
    res = android.run_android_command(["dumpsys", "display"], timeout=8, prefer_root=True)
    text = res.stdout or ""
    m = _MCURRENT_ORIENTATION_RE.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    try:
        state = android.get_display_orientation_state()
        rot = state.get("rotation")
        if rot != "" and rot is not None:
            return int(rot)
    except (TypeError, ValueError):
        pass
    return 0


def pb99_mode_from_rotation(rotation: int) -> str:
    return "LANDSCAPE" if int(rotation) in (1, 3) else "PORTRAIT"


def pb99_columns(mode: str, package_count: int) -> int:
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


def pb99_prefs_paths(package: str) -> list[str]:
    return [
        f"/data/data/{package}/shared_prefs/{package}_preferences.xml",
        f"/data/user/0/{package}/shared_prefs/{package}_preferences.xml",
    ]


def calculate_pb99_grid(
    packages: list[str],
    *,
    wm_width: int,
    wm_height: int,
    rotation: int | None = None,
) -> tuple[list[WindowRect], dict[str, Any]]:
    """Compute window rects using the pb99/rz.txt RS shell algorithm."""
    pkgs = [p for p in packages if p]
    rot = read_display_rotation() if rotation is None else int(rotation)
    w = max(1, int(wm_width))
    h = max(1, int(wm_height))
    mx = max(w, h)
    mn = min(w, h)
    mod = pb99_mode_from_rotation(rot)
    if mod == "LANDSCAPE":
        sw, sh = mx, mn
    else:
        sw, sh = mn, mx
    num = len(pkgs)
    col = pb99_columns(mod, num) if num else 0
    if mod == "LANDSCAPE":
        ox = sw * _PB99_LANDSCAPE_LEFT_OFFSET_PERCENT // 100
        oy = 0
    else:
        ox = 0
        oy = sh * _PB99_PORTRAIT_TOP_OFFSET_PERCENT // 100
    aw = sw - ox
    ah = sh - oy
    mt = mx * 40 // 1000
    ms = sw * 2 // 1000
    mb = sh * 2 // 1000
    ww = max(1, aw - ms * 2)
    wh = max(1, ah - mt - mb)
    rows = (num + col - 1) // col if col else 0
    ch = max(1, wh // rows) if rows else 0
    empty = rows * col - num if col else 0

    rects: list[WindowRect] = []
    a = 0
    s = 0
    for r in range(rows):
        c = 0
        while c < col:
            if a >= num:
                break
            pkg = pkgs[a]
            if a == 0 and empty > 0:
                span = min(empty + 1, col)
                cell_w = ww // col * span
                left = ox + ms
                right = left + cell_w
                top = oy + mt + r * ch
                bottom = top + ch
                c += span - 1
                s += span - 1
            else:
                cell_w = ww // col
                left = ox + ms + (s % col) * cell_w
                right = left + cell_w
                top = oy + mt + (s // col) * ch
                bottom = top + ch
            rects.append(WindowRect(pkg, left, top, right, bottom))
            a += 1
            c += 1
            s += 1

    layout = {
        "algorithm": "pb99",
        "mode": mod,
        "rotation": rot,
        "screen_width": sw,
        "screen_height": sh,
        "columns": col,
        "rows": rows,
        "left_offset": ox,
        "top_offset": oy,
        "top_margin": mt,
        "side_margin": ms,
        "bottom_margin": mb,
        "usable_width": ww,
        "usable_height": wh,
        "empty_slots": empty,
        "wm_width": w,
        "wm_height": h,
    }
    return rects, layout


def _ensure_pb99_bounds_keys(root_el: ET.Element, rect: WindowRect) -> bool:
    """Ensure simple left/right/top/bottom int keys exist (pb99 sed targets)."""
    changed = False
    desired = {
        "left": rect.left,
        "right": rect.right,
        "top": rect.top,
        "bottom": rect.bottom,
    }
    present = {str(child.get("name") or ""): child for child in root_el}
    for name, value in desired.items():
        child = present.get(name)
        val_s = str(int(value))
        if child is None:
            ET.SubElement(root_el, "int", {"name": name, "value": val_s})
            changed = True
        elif child.tag == "int" and child.get("value") != val_s:
            child.set("value", val_s)
            changed = True
    for child in root_el:
        name = str(child.get("name") or "")
        lower = name.lower()
        for side, value in desired.items():
            if side in lower and name not in desired:
                val_s = str(int(value))
                if child.tag == "int":
                    if child.get("value") != val_s:
                        child.set("value", val_s)
                        changed = True
                elif (child.text or "") != val_s:
                    child.text = val_s
                    changed = True
    return changed


def write_pb99_bounds_root(
    package: str,
    rect: WindowRect,
    root_tool: str,
    *,
    timeout: int = 12,
) -> tuple[bool, str]:
    """Write bounds to ``{package}_preferences.xml`` using pb99 sed + root (rz.txt)."""
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return False, "invalid rect"
    l, t, r, b = rect.left, rect.top, rect.right, rect.bottom
    last_err = ""
    for path in pb99_prefs_paths(package):
        exists_res = android.run_root_command(
            ["sh", "-c", f"test -f '{path}' && echo Y || echo N"],
            root_tool=root_tool,
            timeout=timeout,
        )
        exists = (exists_res.stdout or "").strip().startswith("Y")
        if not exists:
            mkdir_cmd = (
                f"mkdir -p '$(dirname \"{path}\")' && "
                f"printf '%s\\n' "
                f"'<?xml version=\"1.0\" encoding=\"utf-8\" standalone=\"yes\" ?>' "
                f"'<map/>' > '{path}'"
            )
            mk = android.run_root_command(["sh", "-c", mkdir_cmd], root_tool=root_tool, timeout=timeout)
            if not mk.ok:
                last_err = f"{path}: create failed"
                continue
        sed_cmd = (
            f"sed -i "
            f"\"s/left\\\" value=\\\"[^\\\"]*\\\"/left\\\" value=\\\"{l}\\\"/g;"
            f"s/right\\\" value=\\\"[^\\\"]*\\\"/right\\\" value=\\\"{r}\\\"/g;"
            f"s/top\\\" value=\\\"[^\\\"]*\\\"/top\\\" value=\\\"{t}\\\"/g;"
            f"s/bottom\\\" value=\\\"[^\\\"]*\\\"/bottom\\\" value=\\\"{b}\\\"/g\" "
            f"'{path}'"
        )
        sed_res = android.run_root_command(["sh", "-c", sed_cmd], root_tool=root_tool, timeout=timeout)
        read_res = android.run_root_command(
            ["sh", "-c", f"cat '{path}' 2>/dev/null"],
            root_tool=root_tool,
            timeout=timeout,
        )
        text = read_res.stdout or ""
        if text.strip():
            try:
                root_el = ET.fromstring(text)
                if _ensure_pb99_bounds_keys(root_el, rect):
                    new_xml = _serialize_xml(root_el)
                    safe_pkg = package.replace(".", "_")
                    local_tmp = _layout_tmp_dir() / f"pb99_{safe_pkg}.xml"
                    local_tmp.write_text(new_xml, encoding="utf-8")
                    cp_cmd = (
                        f"cp -f '{local_tmp}' '{path}' && "
                        f"chown --reference='/data/data/{package}' '{path}' 2>/dev/null || "
                        f"chown $(stat -c '%u:%g' '/data/data/{package}') '{path}' 2>/dev/null; "
                        f"chmod 660 '{path}'; sync"
                    )
                    android.run_root_command(["sh", "-c", cp_cmd], root_tool=root_tool, timeout=timeout)
                    try:
                        local_tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
            except ET.ParseError:
                pass
        own_cmd = (
            f"u=$(stat -c %u '/data/data/{package}' 2>/dev/null); "
            f"[ -n \"$u\" ] && chown $u:$u '{path}' 2>/dev/null; "
            f"chmod 660 '{path}'; sync"
        )
        android.run_root_command(["sh", "-c", own_cmd], root_tool=root_tool, timeout=timeout)
        if sed_res.ok or exists:
            try:
                root_info = android.detect_root()
                android.force_stop_package(package, root_info)
            except Exception:  # noqa: BLE001
                pass
            return True, f"pb99 root write {path}"
        last_err = (sed_res.stderr or sed_res.stdout or last_err or "sed failed")[:120]
    return False, last_err or "pb99 write failed"
