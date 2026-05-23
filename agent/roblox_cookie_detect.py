"""Automatic .ROBLOSECURITY detection for authenticated Roblox Presence.

Reads protected Roblox app data via root to find the session cookie the app
already stored locally.  Never logs or prints cookie values.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from typing import Any

from . import root_access
from .config import validate_package_name, validate_roblosecurity_cookie

_log = logging.getLogger("deng_tool_rejoin")

_ROBLOX_COOKIE_PREFIX = "_|WARNING:-DO-NOT-SHARE-THIS"
_COOKIE_KEY_NAMES = frozenset({".roblosecurity", "roblosecurity"})
_WEBVIEW_COOKIE_PATHS = (
    "app_webview/Default/Cookies",
    "app_webview/Cookies",
    "app_webview/Network/Cookies",
    "databases/Cookies",
)
_COOKIE_INLINE_RE = re.compile(
    r"(?:\.?ROBLOSECURITY=)?(\_\|WARNING:-DO-NOT-SHARE-THIS\.[^\s\"'<>;]{16,})",
    re.IGNORECASE,
)


def _normalize_cookie_value(raw: str | None) -> str:
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    if text.lower().startswith(".roblosecurity="):
        text = text.split("=", 1)[1].strip()
    try:
        return validate_roblosecurity_cookie(text)
    except Exception:  # noqa: BLE001
        return ""


def looks_like_roblox_cookie(raw: str | None) -> bool:
    cookie = _normalize_cookie_value(raw)
    if not cookie:
        return False
    if cookie.startswith(_ROBLOX_COOKIE_PREFIX):
        return True
    return len(cookie) >= 64 and " " not in cookie and "\n" not in cookie


def cookie_from_pref_xml(xml_text: str) -> str:
    """Extract .ROBLOSECURITY from Android shared_prefs XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    for child in root:
        key = (child.attrib.get("name") or "").strip()
        if not key:
            continue
        key_low = key.lower()
        raw_val = child.attrib.get("value")
        if raw_val is None and child.text:
            raw_val = str(child.text).strip()
        if key_low in _COOKIE_KEY_NAMES or ".roblosecurity" in key_low:
            cookie = _normalize_cookie_value(raw_val)
            if cookie:
                return cookie
        if raw_val and looks_like_roblox_cookie(str(raw_val)):
            return _normalize_cookie_value(str(raw_val))
    match = _COOKIE_INLINE_RE.search(xml_text or "")
    if match:
        return _normalize_cookie_value(match.group(1))
    return ""


def _cookie_from_webview_db(tmp_db_path: str) -> str:
    try:
        conn = sqlite3.connect(f"file:{tmp_db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return ""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT value FROM cookies WHERE lower(name) IN ('.roblosecurity', 'roblosecurity') "
            "ORDER BY creation_utc DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row and row[0]:
            cookie = _normalize_cookie_value(str(row[0]))
            if cookie:
                return cookie
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return ""


def _root_scan_shared_prefs(package: str, *, timeout: int, max_bytes: int) -> str:
    base = f"/data/data/{package}/shared_prefs"
    files = root_access.list_root_glob(f"{base}/*.xml", timeout=timeout, max_results=32)
    hints = ("cookie", "auth", "session", "roblox", "account", "user", "pkg_preferences")
    priority = [path for path in files if any(h in path.lower() for h in hints)]
    ordered = (priority + [path for path in files if path not in priority])[:24]
    for abs_path in ordered:
        content = root_access.read_root_file(abs_path, max_bytes=max_bytes, timeout=timeout)
        if not content:
            continue
        cookie = cookie_from_pref_xml(content)
        if cookie:
            _log.info("Auto-detected ROBLOSECURITY for %s via shared_prefs", package)
            return cookie
    return ""


def _root_scan_webview_cookies(package: str, *, timeout: int) -> str:
    if not root_access.has_root():
        return ""
    for rel in _WEBVIEW_COOKIE_PATHS:
        abs_path = f"/data/data/{package}/{rel}"
        tmp = tempfile.mktemp(suffix=".cookies.db", prefix="deng_roblox_")
        try:
            copied = root_access.run_root_command(["cp", abs_path, tmp], timeout=timeout)
            if copied.returncode != 0:
                continue
            cookie = _cookie_from_webview_db(tmp)
            if cookie:
                _log.info("Auto-detected ROBLOSECURITY for %s via webview cookies", package)
                return cookie
        except Exception as exc:  # noqa: BLE001
            _log.debug("WebView cookie scan failed for %s (%s): %s", package, rel, exc)
        finally:
            try:
                import os

                if os.path.isfile(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
    return ""


def detect_roblox_cookie(
    package_name: str,
    *,
    entry: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    use_root: bool = True,
    force_rescan: bool = False,
) -> str:
    """Detect .ROBLOSECURITY for one package. Returns '' when unavailable."""
    try:
        package_name = validate_package_name(package_name)
    except Exception:
        return ""

    if entry and not force_rescan:
        existing = str(entry.get("roblox_cookie") or "").strip()
        if existing:
            try:
                return validate_roblosecurity_cookie(existing)
            except Exception:  # noqa: BLE001
                pass

    if not use_root or not root_access.has_root():
        return ""

    settings = {}
    if isinstance(config, dict):
        raw = config.get("account_detection")
        if isinstance(raw, dict):
            settings = raw
    if settings.get("enabled", True) is False:
        return ""

    timeout = int(settings.get("scan_timeout_seconds", 8) or 8)
    max_bytes = int(settings.get("max_file_size_kb", 512) or 512) * 1024

    try:
        cookie = _root_scan_shared_prefs(package_name, timeout=timeout, max_bytes=max_bytes)
        if cookie:
            return cookie
        cookie = _root_scan_webview_cookies(package_name, timeout=timeout)
        if cookie:
            return cookie
    except Exception as exc:  # noqa: BLE001
        _log.debug("ROBLOSECURITY auto-detect failed for %s: %s", package_name, exc)
    return ""
