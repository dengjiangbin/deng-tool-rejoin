"""Safe account-display-name detection for Roblox package rows.

Read-only. Does not modify app data. Logs only package + outcome — never raw
file contents, tokens, or cookies.
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import android
from .config import validate_account_username, validate_package_name

_log = logging.getLogger("deng_tool_rejoin")

_USERNAME_CACHE: dict[str, str] = {}

FORBIDDEN_KEY_MARKERS = frozenset(
    (
        "auth",
        "bearer",
        "cookie",
        "credential",
        "csrf",
        "pass",
        "password",
        "roblosecurity",
        "secret",
        "security",
        "session",
        "ticket",
        "token",
    )
)

# Prefer exact login username keys over display-oriented keys.
# Keys with score 0 are numeric IDs (not usernames) — they are skipped by the parser.
# Keys not present default to 0 (skipped) unless they contain user-hint substrings (see _pref_key_score).
_PREF_KEY_SCORES: dict[str, int] = {
    "username": 100,
    "userid": 0,
    "user_id": 0,
    "username_lower": 95,
    "user_name": 100,
    "account_name": 98,
    "accountname": 98,
    "display_name": 80,
    "displayname": 80,
    "name": 50,
    "player_name": 92,
    "roblox_username": 100,
    "current_username": 90,
    "last_username": 85,
    # Roblox-specific patterns seen in the wild
    "rbx_username": 100,
    "rbx_user": 100,
    "rbxusername": 100,
    "robloxusername": 100,
    "playerusername": 92,
    "player_username": 92,
    "accountusername": 98,
    "account_username": 98,
    "login_name": 95,
    "loginname": 95,
    "screenname": 88,
    "screen_name": 88,
    "nickname": 75,
    "nick": 70,
    "handle": 70,
    "login": 85,
}

_ROBLOX_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
# Display names: printable, reasonable length; no URLs or token shapes
_MAX_DISPLAY_LEN = 32
_MAX_USERNAME_LEN = 20

# Hook for tests: optional (db_path) -> username or None
_sqlite_username_hook: Callable[[str], str | None] | None = None


def set_sqlite_username_hook(cb: Callable[[str], str | None] | None) -> None:
    """Test-only: register a callback to simulate SQLite username extraction."""
    global _sqlite_username_hook
    _sqlite_username_hook = cb


@dataclass(frozen=True)
class AccountDetectionResult:
    username: str
    source: str


def is_sensitive_key_name(name: str) -> bool:
    if not name or not isinstance(name, str):
        return True
    lowered = name.lower().strip()
    if ".roblosecurity" in lowered or "roblosecurity" in lowered:
        return True
    return any(marker in lowered for marker in FORBIDDEN_KEY_MARKERS)


def is_sensitive_value(value: str | None) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    low = s.lower()
    if "roblosecurity" in low or ".roblosecurity" in low:
        return True
    if any(x in low for x in ("sessionid", "session_id", "cookie:", "bearer ", "authorization:")):
        return True
    if re.search(r"https?://", s):
        return True
    if re.fullmatch(r"[A-Za-z0-9+/=._-]{40,}", s):
        return True
    return False


def sanitize_detected_username(value: str | None) -> str:
    if value is None:
        return ""
    s = " ".join(str(value).strip().split())
    if len(s) > 80:
        s = s[:80].rstrip()
    return s


def get_cached_account_username(package_name: str) -> str | None:
    try:
        pkg = validate_package_name(package_name)
    except Exception:
        return None
    return _USERNAME_CACHE.get(pkg)


def set_cached_account_username(package_name: str, username: str) -> None:
    pkg = validate_package_name(package_name)
    cleaned = sanitize_detected_username(username)
    if cleaned:
        _USERNAME_CACHE[pkg] = cleaned
    elif pkg in _USERNAME_CACHE:
        del _USERNAME_CACHE[pkg]


def _settings(config: dict[str, Any] | None) -> dict[str, Any]:
    from .config import default_config

    base = default_config()["account_detection"]
    if not config:
        return dict(base)
    raw = config.get("account_detection")
    if isinstance(raw, dict):
        merged = dict(base)
        merged.update({k: v for k, v in raw.items() if k in merged})
        return merged
    return dict(base)


def is_safe_username_value(value: str | None) -> bool:
    """True if value looks like a Roblox-style login username (strict)."""
    if value is None:
        return False
    cleaned = str(value).strip()
    if not cleaned or len(cleaned) > _MAX_USERNAME_LEN:
        return False
    if is_sensitive_value(cleaned):
        return False
    return _ROBLOX_USERNAME_RE.fullmatch(cleaned) is not None


def is_safe_display_name_value(value: str | None) -> bool:
    """Looser check for display names (spaces allowed, capped length)."""
    if value is None:
        return False
    cleaned = str(value).strip()
    if not cleaned or len(cleaned) > _MAX_DISPLAY_LEN:
        return False
    if is_sensitive_value(cleaned):
        return False
    if re.search(r"[=/\\{}\[\]<>]", cleaned):
        return False
    return re.fullmatch(r"[A-Za-z0-9_ .'-]{1,32}", cleaned) is not None


def _pref_key_score(key: str) -> int:
    k = (key or "").strip().lower()
    if k in _PREF_KEY_SCORES:
        return _PREF_KEY_SCORES[k]
    if is_sensitive_key_name(k):
        return -999
    # Fallback: keys whose name contains username-like substrings get a low positive score
    # so Roblox-specific or app-specific key names don't silently block detection.
    _USER_HINT_SUBSTRINGS = ("username", "user_name", "display_name", "account_name", "playername", "player_name")
    if any(sub in k for sub in _USER_HINT_SUBSTRINGS):
        return 10
    return 0


def _xml_element_text(el: ET.Element) -> str | None:
    raw = el.attrib.get("value")
    if raw is not None:
        return raw
    if el.text and str(el.text).strip():
        return str(el.text).strip()
    return None


def username_from_pref_xml(xml_text: str) -> str | None:
    """Extract the best-scoring safe username from Android shared_pref XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    best_key = (-1, -1)  # (field_score, type_weight) type_weight: 2=login, 1=display
    best: str | None = None
    for child in root:
        key = (child.attrib.get("name") or "").strip()
        if not key or is_sensitive_key_name(key):
            continue
        raw_val = _xml_element_text(child)
        if raw_val is None or is_sensitive_value(raw_val):
            continue
        candidate = sanitize_detected_username(raw_val)
        if not candidate:
            continue
        score = _pref_key_score(key)
        if score <= 0:
            continue
        if is_safe_username_value(candidate):
            tw = 2
        elif is_safe_display_name_value(candidate):
            tw = 1
        else:
            continue
        if key.lower() == "name" and tw != 2:
            continue
        rank = (score, tw)
        if rank > best_key:
            best_key = rank
            best = candidate
    return best


def _clean_username(value: str) -> str:
    return sanitize_detected_username(value)


def _candidate_pref_files(package: str) -> list[Path]:
    base = Path("/data/data") / validate_package_name(package) / "shared_prefs"
    try:
        if not base.exists() or not base.is_dir():
            return []
    except PermissionError:
        _log.debug(
            "Permission denied checking %s — root may be needed for username auto-detect", base
        )
        return []
    hints = ("account", "profile", "settings", "user", "username", "pkg_preferences", "roblox")
    candidates: list[Path] = []
    try:
        for path in sorted(base.glob("*.xml")):
            name = path.name.lower()
            if any(h in name for h in hints):
                candidates.append(path)
        if not candidates:
            candidates = sorted(base.glob("*.xml"))[:12]
    except PermissionError:
        _log.debug("Permission denied listing %s — skipping", base)
    return candidates[:24]


def detect_username_from_safe_prefs(package: str, *, max_bytes: int | None = None) -> str | None:
    limit = max_bytes or 64_000
    for path in _candidate_pref_files(package):
        try:
            if path.stat().st_size > limit:
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
    generic = {"roblox", "roblox player", "roblox app"}
    for line in result.stdout.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if not lowered.startswith("application-label"):
            continue
        _, _, raw_label = stripped.partition(":")
        label = raw_label.strip().strip("'\"")
        if label.lower() in generic:
            continue
        if is_safe_username_value(label) or is_safe_display_name_value(label):
            return _clean_username(label)
    return None


def _root_read_file_capped(abs_path: str, max_bytes: int, timeout: int, root_tool: str) -> str | None:
    if max_bytes <= 0:
        return None
    # Read-only: head -c only. Paths come from our validated package tree.
    q = abs_path.replace("'", "'\"'\"'")
    inner = f"test -r '{q}' && head -c {int(max_bytes)} '{q}' 2>/dev/null"
    res = android.run_root_command(["sh", "-c", inner], root_tool=root_tool, timeout=timeout)
    if not res.ok or res.timed_out:
        return None
    out = (res.stdout or "").strip()
    return out if out else None


def _root_read_shared_prefs_for_username(
    package: str, max_bytes: int, timeout: int, root_tool: str
) -> str | None:
    """Targeted root-based shared_prefs reader.

    Faster than a full find scan because it directly lists the shared_prefs directory and
    reads hint-matching XML files in priority order. Used as a first-pass before the full
    find-based scan in _root_scan_package_data.
    """
    pkg = validate_package_name(package)
    base = f"/data/data/{pkg}/shared_prefs"
    # List .xml files in the shared_prefs directory using root
    list_inner = f"test -d '{base}' && ls '{base}/' 2>/dev/null | grep '\\.xml$' | head -32"
    list_res = android.run_root_command(["sh", "-c", list_inner], root_tool=root_tool, timeout=timeout)
    if list_res.timed_out or not list_res.stdout:
        return None
    filenames = [f.strip() for f in (list_res.stdout or "").splitlines() if f.strip().endswith(".xml")]
    if not filenames:
        return None
    _hints = ("account", "profile", "settings", "user", "username", "pkg_preferences", "roblox", "rbx")
    priority = [f for f in filenames if any(h in f.lower() for h in _hints)]
    fallback = [f for f in filenames if f not in priority]
    ordered = (priority + fallback)[:24]
    for fname in ordered:
        abs_path = f"{base}/{fname}"
        content = _root_read_file_capped(abs_path, max_bytes, timeout, root_tool)
        if not content:
            continue
        username = username_from_pref_xml(content)
        if username:
            _log.debug("Root shared_prefs found username in %s", fname)
            return username
    return None


def _root_list_scan_files(package: str, max_kb: int, timeout: int, root_tool: str) -> list[str]:
    pkg = validate_package_name(package)
    base = f"/data/data/{pkg}"
    # find: only under this package, caps count and file size
    inner = (
        f"find '{base}' -maxdepth 6 -type f -size -{int(max_kb)}k "
        f"\\( -path '*/shared_prefs/*.xml' -o -name '*.json' -o -path '*/databases/*.db' \\) "
        f"2>/dev/null | head -80"
    )
    res = android.run_root_command(["sh", "-c", inner], root_tool=root_tool, timeout=timeout)
    if not res.ok or res.timed_out:
        return []
    lines = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip().startswith("/")]
    return lines


def _username_from_json_text(text: str) -> str | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return _username_from_json_obj(data)


def _json_key_score(key: str) -> int:
    k = str(key or "").strip().lower().replace("-", "_")
    if k in _PREF_KEY_SCORES:
        return _PREF_KEY_SCORES[k]
    if is_sensitive_key_name(k):
        return -999
    return 0


def _username_from_json_obj(data: Any, depth: int = 0) -> str | None:
    if depth > 3:
        return None
    if isinstance(data, dict):
        best_key = (-1, -1)
        best: str | None = None
        for key, val in data.items():
            if not isinstance(key, str):
                continue
            if is_sensitive_key_name(key):
                continue
            sk = _json_key_score(key)
            if sk == 0:
                continue
            if isinstance(val, dict):
                nested = _username_from_json_obj(val, depth + 1)
                if nested:
                    return nested
                continue
            if not isinstance(val, str):
                continue
            if is_sensitive_value(val):
                continue
            cand = sanitize_detected_username(val)
            if not cand:
                continue
            if is_safe_username_value(cand):
                tw = 2
            elif is_safe_display_name_value(cand):
                tw = 1
            else:
                continue
            if key.lower() == "name" and tw != 2:
                continue
            rank = (sk, tw)
            if rank > best_key:
                best_key = rank
                best = cand
        return best
    return None


def _try_sqlite_roblox_user(db_abs: str) -> str | None:
    if _sqlite_username_hook is not None:
        try:
            u = _sqlite_username_hook(db_abs)
        except Exception:
            return None
        if u and sanitize_detected_username(u):
            c = sanitize_detected_username(u)
            if is_safe_username_value(c) or is_safe_display_name_value(c):
                return c
        return None
    return None


def _root_scan_package_data(package: str, settings: dict[str, Any]) -> tuple[str | None, str | None]:
    timeout = int(settings.get("scan_timeout_seconds", 8))
    max_kb = int(settings.get("max_file_size_kb", 512))
    max_bytes = max_kb * 1024
    root = android.detect_root()
    if not root.available or not root.tool:
        _log.info("Root unavailable, skipped package data scan")
        return None, None

    # Fast first pass: directly read shared_prefs XML via root (no find needed).
    # This covers the common case where the username is in a shared_prefs XML file.
    fast_user = _root_read_shared_prefs_for_username(package, max_bytes, timeout, root.tool)
    if fast_user:
        u = sanitize_detected_username(fast_user)
        if u:
            return u, "root_shared_prefs"

    # Full scan: find XML/JSON/db files anywhere under the package data directory.
    paths = _root_list_scan_files(package, max_kb=max_kb, timeout=timeout, root_tool=root.tool)
    best: str | None = None
    best_src: str | None = None
    best_rank: tuple[int, int] = (-1, -1)
    for abs_path in paths:
        if not abs_path.startswith(f"/data/data/{validate_package_name(package)}/"):
            continue
        if abs_path.endswith(".db"):
            user = _try_sqlite_roblox_user(abs_path)
            if user:
                cand = sanitize_detected_username(user)
                rank = (100, 2 if is_safe_username_value(cand) else 1)
                if rank > best_rank:
                    best, best_src, best_rank = cand, "root_sqlite", rank
            continue
        raw = _root_read_file_capped(abs_path, min(max_bytes, 262_144), timeout, root.tool)
        if not raw:
            continue
        if abs_path.endswith(".xml"):
            u = username_from_pref_xml(raw)
            if u:
                cand = sanitize_detected_username(u)
                rank = (95, 2 if is_safe_username_value(cand) else 1)
                if rank > best_rank:
                    best, best_src, best_rank = cand, "root_pref", rank
        elif abs_path.endswith(".json"):
            u = _username_from_json_text(raw)
            if u:
                cand = sanitize_detected_username(u)
                rank = (90, 2 if is_safe_username_value(cand) else 1)
                if rank > best_rank:
                    best, best_src, best_rank = cand, "root_json", rank
    if best:
        return best, best_src or "root_scan"
    return None, None


def detect_account_username(
    package_name: str,
    *,
    entry: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    use_root: bool = True,
    respect_config_manual: bool = True,
) -> AccountDetectionResult | None:
    """Resolve display username for one package (config → label → prefs → root scan)."""
    package_name = validate_package_name(package_name)
    settings = _settings(config)
    if not settings.get("enabled", True):
        return None

    if respect_config_manual and entry:
        src = str(entry.get("username_source") or "").strip().lower()
        manual_user = validate_account_username(entry.get("account_username", ""))
        if src == "manual" and manual_user:
            u = sanitize_detected_username(manual_user)
            return AccountDetectionResult(u, "config_manual") if u else None

    label = detect_android_app_label(package_name)
    if label:
        u = sanitize_detected_username(label)
        if u:
            if settings.get("cache_detected_usernames", True):
                set_cached_account_username(package_name, u)
            _log.info("Detected username for %s: %s", package_name, u)
            return AccountDetectionResult(u, "android_app_label")

    pref_user = detect_username_from_safe_prefs(package_name, max_bytes=settings.get("max_file_size_kb", 512) * 1024)
    if pref_user:
        u = sanitize_detected_username(pref_user)
        if u:
            if settings.get("cache_detected_usernames", True):
                set_cached_account_username(package_name, u)
            _log.info("Detected username for %s: %s", package_name, u)
            return AccountDetectionResult(u, "detected_safe_pref")

    if use_root and settings.get("use_root", True):
        try:
            root_user, rsrc = _root_scan_package_data(package_name, settings)
        except OSError:
            root_user, rsrc = None, None
        if root_user:
            u = sanitize_detected_username(root_user)
            if u:
                if settings.get("cache_detected_usernames", True):
                    set_cached_account_username(package_name, u)
                _log.info("Detected username for %s: %s", package_name, u)
                return AccountDetectionResult(u, rsrc or "root_scan")

    _log.info("No username found for %s", package_name)
    return None


def detect_account_usernames_for_packages(
    packages: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
    use_root: bool = True,
    respect_config_manual: bool = False,
) -> list[tuple[dict[str, Any], AccountDetectionResult | None]]:
    """Run detection per package entry. Returns (entry, result) pairs in order."""
    out: list[tuple[dict[str, Any], AccountDetectionResult | None]] = []
    for entry in packages:
        if not isinstance(entry, dict):
            continue
        pkg = str(entry.get("package") or "").strip()
        if not pkg:
            continue
        try:
            validate_package_name(pkg)
        except Exception:
            out.append((entry, None))
            continue
        res = detect_account_username(
            pkg,
            entry=entry,
            config=config,
            use_root=use_root,
            respect_config_manual=respect_config_manual,
        )
        out.append((entry, res))
    return out


def detect_account_username_for_package(package: str) -> AccountDetectionResult | None:
    """Backward-compatible helper: no config entry (setup wizard)."""
    return detect_account_username(package, entry=None, config=None, use_root=True, respect_config_manual=False)
