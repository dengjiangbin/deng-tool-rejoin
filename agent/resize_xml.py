"""Safer App Cloner XML bounds writes with backup and owner restore."""

from __future__ import annotations

import re
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import android
from .resize_pb99 import write_pb99_bounds_root
from .window_layout import (
    WindowRect,
    clone_prefs_candidates,
    update_app_cloner_xml,
    update_app_cloner_xml_root,
)

_BOUNDS_KEY_RE = re.compile(r"(left|right|top|bottom)", re.IGNORECASE)


def _bounds_from_xml_text(text: str) -> dict[str, int] | None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    out: dict[str, int] = {}
    for child in root:
        name = str(child.get("name") or "")
        if not _BOUNDS_KEY_RE.search(name):
            continue
        for side in ("left", "right", "top", "bottom"):
            if side in name.lower():
                try:
                    out[side] = int(str(child.text or "0").strip())
                except ValueError:
                    pass
    if len(out) == 4:
        return out
    return None


def _rect_dict(rect: WindowRect) -> dict[str, int]:
    return {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom}


def _bounds_match(desired: dict[str, int], actual: dict[str, int] | None, tol: int = 2) -> bool:
    if not actual:
        return False
    for key in ("left", "top", "right", "bottom"):
        if abs(int(desired.get(key, 0)) - int(actual.get(key, 0))) > tol:
            return False
    return True


def _read_xml_via_root(path: Path, root_tool: str) -> tuple[str, dict[str, str]]:
    meta: dict[str, str] = {}
    path_str = str(path)
    stat_res = android.run_root_command(
        ["sh", "-c", f"stat -c '%u:%g %a' '{path_str}' 2>/dev/null || echo ''"],
        root_tool=root_tool,
        timeout=8,
    )
    meta["owner_mode"] = (stat_res.stdout or "").strip()
    read_res = android.run_root_command(
        ["sh", "-c", f"test -f '{path_str}' && cat '{path_str}' 2>/dev/null"],
        root_tool=root_tool,
        timeout=8,
    )
    return (read_res.stdout or ""), meta


def safe_write_resize_bounds(
    package: str,
    rect: WindowRect,
    *,
    screen_mode: str,
    known_keys: list[str] | None = None,
    root_tool: str | None = None,
) -> dict[str, Any]:
    """Write bounds with backup, validation, and explicit status."""
    result: dict[str, Any] = {
        "package": package,
        "xml_path": "",
        "before_bounds": None,
        "after_bounds": _rect_dict(rect),
        "bounds_valid": rect.right > rect.left and rect.bottom > rect.top,
        "backup_created": False,
        "owner_restored": False,
        "permission_restored": False,
        "force_stop_ok": False,
        "relaunch_ok": False,
        "timestamp_marker_ok": False,
        "status": "failed",
        "reason": "",
    }
    if not result["bounds_valid"]:
        result["reason"] = "desired_bounds_invalid"
        return result

    mode = str(screen_mode or "landscape").strip().lower()
    if mode == "portrait" and root_tool:
        ok, method = write_pb99_bounds_root(package, rect, root_tool)
        if ok:
            result["xml_path"] = f"/data/data/{package}/shared_prefs/{package}_preferences.xml"
            result["owner_restored"] = True
            result["permission_restored"] = True
            result["force_stop_ok"] = True
            result["status"] = "resized"
            result["reason"] = method or "pb99_ok"
            result["timestamp_marker_ok"] = True
            return result

    candidates = list(clone_prefs_candidates(package))
    if not candidates:
        result["reason"] = "no_xml_candidates"
        return result

    chosen: Path | None = None
    before_text = ""
    owner_mode = ""
    for path in candidates:
        if path.exists():
            try:
                before_text = path.read_text(encoding="utf-8")
                chosen = path
                break
            except OSError:
                continue
    if chosen is None and root_tool:
        for path in candidates:
            text, meta = _read_xml_via_root(path, root_tool)
            if text.strip():
                before_text = text
                owner_mode = meta.get("owner_mode", "")
                chosen = path
                break
            if path.name.endswith("_preferences.xml") or path.name == "pkg_preferences.xml":
                chosen = path
                owner_mode = meta.get("owner_mode", "")
                break

    if chosen is None:
        chosen = candidates[0]

    result["xml_path"] = str(chosen)
    if before_text.strip():
        before_bounds = _bounds_from_xml_text(before_text)
        result["before_bounds"] = before_bounds
        if _bounds_match(_rect_dict(rect), before_bounds):
            result["status"] = "already_correct"
            result["reason"] = "bounds_already_match_xml"
            return result
        if not _bounds_from_xml_text(before_text) and "left" not in before_text.lower():
            result["status"] = "skipped"
            result["reason"] = "xml_missing_bounds_keys"
            return result

    backup_path = chosen.with_suffix(f".xml.bak-{int(time.time())}")
    if chosen.exists():
        try:
            shutil.copy2(chosen, backup_path)
            result["backup_created"] = True
        except OSError:
            if root_tool:
                android.run_root_command(
                    ["sh", "-c", f"cp -f '{chosen}' '{backup_path}'"],
                    root_tool=root_tool,
                    timeout=8,
                )
                result["backup_created"] = True
            else:
                result["reason"] = "backup_failed"
                result["status"] = "skipped"
                return result

    mode = str(screen_mode or "landscape").strip().lower()
    ok = False
    method = ""
    if root_tool:
        ok, method = update_app_cloner_xml_root(
            package, rect, root_tool, known_keys=known_keys, screen_mode=mode,
        )
    if not ok:
        ok, method = update_app_cloner_xml(
            package, rect, known_keys=known_keys, screen_mode=mode,
        )

    if not ok:
        if result["backup_created"] and backup_path.exists():
            try:
                shutil.copy2(backup_path, chosen)
            except OSError:
                if root_tool:
                    android.run_root_command(
                        ["sh", "-c", f"cp -f '{backup_path}' '{chosen}'"],
                        root_tool=root_tool,
                        timeout=8,
                    )
        result["reason"] = method or "write_failed"
        result["status"] = "failed"
        return result

    after_text = ""
    if chosen.exists():
        try:
            after_text = chosen.read_text(encoding="utf-8")
        except OSError:
            if root_tool:
                after_text, _meta = _read_xml_via_root(chosen, root_tool)
    after_bounds = _bounds_from_xml_text(after_text) if after_text else None
    if after_bounds:
        result["after_bounds"] = after_bounds
    result["timestamp_marker_ok"] = bool(after_text.strip())

    if root_tool and owner_mode:
        android.run_root_command(
            ["sh", "-c", f"chown --reference='/data/data/{package}' '{chosen}' 2>/dev/null; chmod 660 '{chosen}'; sync"],
            root_tool=root_tool,
            timeout=8,
        )
        result["owner_restored"] = True
        result["permission_restored"] = True
    else:
        result["permission_restored"] = True

    stop = android.force_stop_package(package, android.detect_root())
    result["force_stop_ok"] = bool(stop.ok)
    result["status"] = "resized"
    result["reason"] = method or "ok"
    return result
