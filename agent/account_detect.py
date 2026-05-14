"""Safe account-display-name detection for Roblox package rows.

This module only returns narrow, display-oriented values. It never returns
cookies, tokens, passwords, or raw preference content.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from . import android
from .config import validate_package_name

SAFE_USERNAME_KEYS = {
    "account_name",
    "current_username",
    "display_name",
    "last_username",
    "player_name",
    "roblox_username",
    "user_name",
    "username",
}

FORBIDDEN_KEY_MARKERS = {
    "auth",
    "cookie",
    "credential",
    "pass",
    "password",
    "roblosecurity",
    "secret",
    "security",
    "session",
    "token",
}

SAFE_PREF_FILE_HINTS = {
    "account",
    "profile",
    "settings",
    "user",
    "username",
    "pkg_preferences",
}

GENERIC_APP_LABELS = {"roblox", "roblox player", "roblox app"}
MAX_PREF_FILE_BYTES = 64_000


@dataclass(frozen=True)
class AccountDetectionResult:
    username: str
    source: str


def _is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in FORBIDDEN_KEY_MARKERS)


def is_safe_username_value(value: str | None) -> bool:
    if value is None:
        return False
    cleaned = str(value).strip()
    if not cleaned or len(cleaned) > 40:
        return False
    lowered = cleaned.lower()
    if any(marker in lowered for marker in FORBIDDEN_KEY_MARKERS):
        return False
    if re.search(r"https?://|[=/\\{}\[\]<>]|[A-Za-z0-9_-]{32,}", cleaned):
        return False
    return re.fullmatch(r"[A-Za-z0-9_. -]{1,40}", cleaned) is not None


def _clean_username(value: str) -> str:
    return " ".join(str(value).strip().split())


def username_from_pref_xml(xml_text: str) -> str | None:
    """Extract a safe username from allowlisted XML preference keys only."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    for child in root:
        key = (child.attrib.get("name") or "").strip().lower()
        if not key or _is_forbidden_key(key):
            continue
        if key not in SAFE_USERNAME_KEYS:
            continue
        raw_value = child.attrib.get("value")
        if raw_value is None:
            raw_value = child.text
        if is_safe_username_value(raw_value):
            return _clean_username(str(raw_value))
    return None


def _candidate_pref_files(package: str) -> list[Path]:
    base = Path("/data/data") / validate_package_name(package) / "shared_prefs"
    if not base.exists() or not base.is_dir():
        return []
    candidates: list[Path] = []
    for path in sorted(base.glob("*.xml")):
        name = path.name.lower()
        if any(hint in name for hint in SAFE_PREF_FILE_HINTS):
            candidates.append(path)
    return candidates[:20]


def detect_username_from_safe_prefs(package: str) -> str | None:
    for path in _candidate_pref_files(package):
        try:
            if path.stat().st_size > MAX_PREF_FILE_BYTES:
                continue
            username = username_from_pref_xml(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if username:
            return username
    return None


def detect_android_app_label(package: str) -> str | None:
    package = validate_package_name(package)
    result = android.run_command(["dumpsys", "package", package], timeout=8)
    if not result.ok:
        return None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if not lowered.startswith("application-label"):
            continue
        _, _, raw_label = stripped.partition(":")
        label = raw_label.strip().strip("'\"")
        if label.lower() in GENERIC_APP_LABELS:
            continue
        if is_safe_username_value(label):
            return _clean_username(label)
    return None


def detect_account_username_for_package(package: str) -> AccountDetectionResult | None:
    validate_package_name(package)
    label = detect_android_app_label(package)
    if label:
        return AccountDetectionResult(label, "android_app_label")
    username = detect_username_from_safe_prefs(package)
    if username:
        return AccountDetectionResult(username, "detected_safe_pref")
    return None
