"""Bounded package username display detection.

This module is intentionally narrow: it reads only known, non-secret
Roblox preference keys from the selected package's exact prefs file.
It does not scan cookies, WebView data, databases, or unrelated apps.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from . import root_access
from .config import (
    get_package_display_username,
    validate_account_username,
    validate_package_name,
    validate_username_source,
)

USERNAME_DETECT_TIMEOUT_SECONDS = 2.0
_PREF_MAX_BYTES = 16 * 1024
_PREF_KEYS = ("username", "displayName")


@dataclass(frozen=True)
class PackageUsernameResult:
    username: str
    source: str
    detector_used: bool
    duration_ms: int
    error: str = ""


def _parse_known_pref_username(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    values: dict[str, str] = {}
    for child in root:
        if child.tag != "string":
            continue
        name = str(child.attrib.get("name") or "")
        if name not in _PREF_KEYS:
            continue
        cleaned = validate_account_username(child.text or "")
        if cleaned:
            values[name] = cleaned
    return values.get("username") or values.get("displayName") or ""


def detect_package_username_quick(
    package: str,
    *,
    timeout_seconds: float = USERNAME_DETECT_TIMEOUT_SECONDS,
) -> PackageUsernameResult:
    start = time.monotonic()
    try:
        pkg = validate_package_name(package)
    except Exception as exc:  # noqa: BLE001
        return PackageUsernameResult("", "unknown", False, 0, str(exc)[:80])
    remaining = max(0.5, min(float(timeout_seconds), USERNAME_DETECT_TIMEOUT_SECONDS))
    path = f"/data/data/{pkg}/shared_prefs/prefs.xml"
    try:
        content = root_access.read_root_file(
            path,
            max_bytes=_PREF_MAX_BYTES,
            timeout=max(1, int(remaining)),
            detect_timeout=1,
        )
    except Exception as exc:  # noqa: BLE001
        duration = int((time.monotonic() - start) * 1000)
        return PackageUsernameResult("", "unknown", True, duration, str(exc)[:80])
    duration = int((time.monotonic() - start) * 1000)
    if not content:
        return PackageUsernameResult("", "unknown", True, duration)
    username = _parse_known_pref_username(content)
    if not username:
        return PackageUsernameResult("", "unknown", True, duration)
    return PackageUsernameResult(username, "detected_safe_pref", True, duration)


def resolve_package_display_username(
    entry: dict[str, Any],
    config_data: dict[str, Any],
    *,
    allow_detect: bool = True,
    timeout_seconds: float = USERNAME_DETECT_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], PackageUsernameResult]:
    updated = dict(entry)
    saved = get_package_display_username(updated, config_data)
    source = str(updated.get("username_source") or "not_set")
    if saved != "Unknown":
        return updated, PackageUsernameResult(saved, source, False, 0)
    if not allow_detect:
        return updated, PackageUsernameResult("Unknown", "unknown", False, 0)
    result = detect_package_username_quick(
        str(updated.get("package") or ""),
        timeout_seconds=timeout_seconds,
    )
    if result.username:
        updated["account_username"] = result.username
        updated["username_source"] = validate_username_source(result.source, result.username)
        cache = dict(config_data.get("package_username_cache") or {})
        cache[str(updated["package"])] = result.username
        config_data["package_username_cache"] = cache
    return updated, result if result.username else PackageUsernameResult(
        "Unknown",
        "unknown",
        result.detector_used,
        result.duration_ms,
        result.error,
    )
