"""Root-first package username detection for cloud-phone clones."""

from __future__ import annotations

import logging
import re
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

_log = logging.getLogger("deng.rejoin.package_username")

USERNAME_DETECT_TIMEOUT_SECONDS = 8.0
SAFE_DETECT_DEFAULT_TIMEOUT_SECONDS = 2.0
_PREF_MAX_BYTES = 32 * 1024
_FILE_MAX_BYTES = 64 * 1024

_USERNAME_XML_KEYS = (
    "username",
    "userName",
    "displayName",
    "accountName",
    "authenticatedUser",
    "playerName",
)

_USERNAME_TEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("username", re.compile(r'(?i)(?:^|[\s"\'<>])username[\s"\'=:>]+([A-Za-z0-9_]{3,20})')),
    ("displayName", re.compile(r'(?i)(?:^|[\s"\'<>])displayName[\s"\'=:>]+([A-Za-z0-9_]{3,20})')),
    ("accountName", re.compile(r'(?i)(?:^|[\s"\'<>])accountName[\s"\'=:>]+([A-Za-z0-9_]{3,20})')),
    ("playerName", re.compile(r'(?i)(?:^|[\s"\'<>])playerName[\s"\'=:>]+([A-Za-z0-9_]{3,20})')),
)

_SECRET_SUB = re.compile(
    r"(?i)(roblosecurity|cookie|token|session|password|authheader|deviceid)\s*[=:]\s*\S+"
)


@dataclass(frozen=True)
class PackageUsernameResult:
    username: str
    source: str
    detector_used: bool
    duration_ms: int
    error: str = ""
    root_used: bool = False
    confidence: str = ""


@dataclass(frozen=True)
class UsernameScanReport:
    package: str
    username: str
    source: str
    supported: bool
    reason: str
    methods_attempted: tuple[str, ...] = ()
    app_label: str = ""
    duration_ms: int = 0
    root_used: bool = False
    confidence: str = ""
    root_read_status: str = ""
    launcher_activity: str = ""
    installed: bool | None = None
    enabled: bool | None = None


def _redact_secrets(text: str) -> str:
    return _SECRET_SUB.sub("<redacted>", text or "")


def _parse_known_pref_username(xml_text: str) -> tuple[str, str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return "", ""
    values: dict[str, str] = {}
    for child in root:
        if child.tag not in ("string", "int"):
            continue
        name = str(child.attrib.get("name") or "")
        if name not in _USERNAME_XML_KEYS:
            continue
        cleaned = validate_account_username(child.text or "")
        if cleaned:
            values[name] = cleaned
    if values.get("username"):
        return values["username"], "username"
    if values.get("userName"):
        return values["userName"], "userName"
    for key in ("displayName", "accountName", "authenticatedUser", "playerName"):
        if values.get(key):
            return values[key], key
    return "", ""


def _grep_username_in_text(text: str) -> tuple[str, str]:
    safe = _redact_secrets(text or "")
    for key, pattern in _USERNAME_TEXT_PATTERNS:
        match = pattern.search(safe)
        if not match:
            continue
        candidate = validate_account_username(match.group(1))
        if candidate:
            return candidate, key
    return "", ""


def _normalize_source(raw: str) -> str:
    mapping = {
        "detected_safe_pref": "root_shared_prefs",
        "manual": "manual_mapping",
        "cache": "manual_mapping",
        "detected_file": "root_file",
        "detected_database": "root_database",
    }
    return mapping.get(raw, raw or "unknown")


def _root_scan_paths(pkg: str, *, budget_seconds: float) -> tuple[str, str, str, tuple[str, ...], str]:
    """Return (username, source, confidence, methods, root_read_status)."""
    methods: list[str] = []
    deadline = time.monotonic() + max(1.0, budget_seconds)
    status_bits: list[str] = []

    pref_patterns = (
        f"/data/data/{pkg}/shared_prefs/*.xml",
        f"/data/data/{pkg}/shared_prefs/*preferences*.xml",
    )
    for pattern in pref_patterns:
        if time.monotonic() >= deadline:
            break
        methods.append("root_shared_prefs_glob")
        paths = root_access.list_root_glob(pattern, timeout=6, max_results=24)
        for path in paths:
            content = root_access.read_root_file(path, max_bytes=_PREF_MAX_BYTES, timeout=6)
            if not content:
                status_bits.append(f"unreadable:{path.split('/')[-1]}")
                continue
            username, key = _parse_known_pref_username(content)
            if username:
                status_bits.append(f"ok:{path.split('/')[-1]}:{key}")
                return username, "root_shared_prefs", "high", tuple(methods), "; ".join(status_bits[-3:])
            text_user, text_key = _grep_username_in_text(content)
            if text_user:
                status_bits.append(f"ok:{path.split('/')[-1]}:{text_key}")
                return text_user, "root_shared_prefs", "medium", tuple(methods), "; ".join(status_bits[-3:])

    file_patterns = (
        f"/data/data/{pkg}/files/*",
        f"/data/data/{pkg}/files/**/*",
        f"/data/data/{pkg}/app_*/*",
        f"/sdcard/Android/data/{pkg}/files/*",
        f"/storage/emulated/0/Android/data/{pkg}/files/*",
    )
    for pattern in file_patterns:
        if time.monotonic() >= deadline:
            break
        methods.append("root_file_glob")
        for path in root_access.list_root_glob(pattern, timeout=6, max_results=16):
            if path.endswith((".png", ".jpg", ".webp", ".so", ".apk")):
                continue
            content = root_access.read_root_file(path, max_bytes=_FILE_MAX_BYTES, timeout=6)
            if not content:
                continue
            text_user, text_key = _grep_username_in_text(content)
            if text_user:
                status_bits.append(f"ok:file:{path.split('/')[-1]}:{text_key}")
                return text_user, "root_file", "medium", tuple(methods), "; ".join(status_bits[-3:])

    db_patterns = (f"/data/data/{pkg}/databases/*.db",)
    for pattern in db_patterns:
        if time.monotonic() >= deadline:
            break
        methods.append("root_database_glob")
        for path in root_access.list_root_glob(pattern, timeout=6, max_results=8):
            safe_path = path.replace("'", "'\"'\"'")
            probe = root_access.run_root(
                f"command -v sqlite3 >/dev/null 2>&1 && sqlite3 '{safe_path}' "
                "\"SELECT username FROM sqlite_master LIMIT 0;\" 2>/dev/null; "
                f"sqlite3 '{safe_path}' \"SELECT username,userName,displayName,accountName,playerName "
                "FROM users LIMIT 5\" 2>/dev/null | head -5",
                timeout=8,
            )
            if probe.ok and probe.stdout.strip():
                for line in probe.stdout.splitlines():
                    for token in re.split(r"[|,]", line):
                        candidate = validate_account_username(token.strip())
                        if candidate:
                            status_bits.append(f"ok:db:{path.split('/')[-1]}")
                            return candidate, "root_database", "medium", tuple(methods), "; ".join(status_bits[-3:])
            content = root_access.read_root_file(path, max_bytes=8192, timeout=6)
            if content:
                text_user, text_key = _grep_username_in_text(content)
                if text_user:
                    status_bits.append(f"ok:dbscan:{path.split('/')[-1]}:{text_key}")
                    return text_user, "root_database", "low", tuple(methods), "; ".join(status_bits[-3:])

    if not status_bits:
        status_bits.append("no username key found in root-readable data")
    return "", "unknown", "", tuple(methods), "; ".join(status_bits[:4])


def scan_package_username_root(
    package: str,
    *,
    timeout_seconds: float = USERNAME_DETECT_TIMEOUT_SECONDS,
) -> UsernameScanReport:
    """Root-required username scan with explicit evidence."""
    start = time.monotonic()
    try:
        pkg = validate_package_name(package)
    except Exception as exc:  # noqa: BLE001
        return UsernameScanReport(
            package=str(package or "")[:120],
            username="",
            source="unknown",
            supported=False,
            reason=str(exc)[:160],
            root_used=False,
            confidence="",
            root_read_status="invalid package",
        )

    pre = root_access.root_required_preflight(timeout=max(3, int(min(timeout_seconds, 6))))
    if not pre.ok:
        return UsernameScanReport(
            package=pkg,
            username="",
            source="unknown",
            supported=False,
            reason=pre.public_error(),
            methods_attempted=("root_required_preflight",),
            duration_ms=int((time.monotonic() - start) * 1000),
            root_used=False,
            confidence="",
            root_read_status=pre.detail[:160],
        )

    app_label = ""
    launcher_activity = ""
    installed = None
    enabled = None
    try:
        from . import android

        app_label = str(android.get_application_label(pkg) or "")[:120]
        installed = android.package_installed(pkg)
        from . import launch_verify

        component, launchable, _ = launch_verify.resolve_launcher_activity(pkg)
        launcher_activity = component or ""
        enabled = launchable
    except Exception:  # noqa: BLE001
        pass

    username, source, confidence, methods, root_status = _root_scan_paths(
        pkg,
        budget_seconds=max(1.0, timeout_seconds - 1.0),
    )
    duration = int((time.monotonic() - start) * 1000)
    if username:
        return UsernameScanReport(
            package=pkg,
            username=username,
            source=source,
            supported=True,
            reason="",
            methods_attempted=methods,
            app_label=app_label,
            duration_ms=duration,
            root_used=True,
            confidence=confidence,
            root_read_status=root_status,
            launcher_activity=launcher_activity,
            installed=installed,
            enabled=enabled,
        )
    return UsernameScanReport(
        package=pkg,
        username="",
        source="unknown",
        supported=True,
        reason=f"no username key found in root-readable data ({root_status})",
        methods_attempted=methods,
        app_label=app_label,
        duration_ms=duration,
        root_used=True,
        confidence="",
        root_read_status=root_status,
        launcher_activity=launcher_activity,
        installed=installed,
        enabled=enabled,
    )


def _read_prefs_via_root(pkg: str, *, timeout_seconds: float) -> tuple[str, str]:
    report = scan_package_username_root(pkg, timeout_seconds=timeout_seconds)
    if report.username:
        return report.username, ""
    return "", report.reason[:120]


def detect_package_username_quick(
    package: str,
    *,
    timeout_seconds: float = USERNAME_DETECT_TIMEOUT_SECONDS,
) -> PackageUsernameResult:
    start = time.monotonic()
    report = scan_package_username_root(package, timeout_seconds=timeout_seconds)
    duration = int((time.monotonic() - start) * 1000)
    if report.username:
        return PackageUsernameResult(
            report.username,
            _normalize_source(report.source),
            True,
            duration,
            root_used=True,
            confidence=report.confidence or "high",
        )
    return PackageUsernameResult(
        "",
        "unknown",
        report.root_used,
        duration,
        report.reason[:120],
        root_used=report.root_used,
    )


def scan_package_username(
    package: str,
    config_data: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = USERNAME_DETECT_TIMEOUT_SECONDS,
) -> UsernameScanReport:
    """Scan one package; manual mapping is fallback only after root scan."""
    start = time.monotonic()
    try:
        pkg = validate_package_name(package)
    except Exception as exc:  # noqa: BLE001
        return UsernameScanReport(
            package=str(package or "")[:120],
            username="",
            source="unknown",
            supported=False,
            reason=str(exc)[:160],
            duration_ms=0,
        )

    if config_data:
        for entry in config_data.get("roblox_packages") or ():
            if not isinstance(entry, dict) or str(entry.get("package") or "") != pkg:
                continue
            saved = get_package_display_username(entry, config_data)
            source = str(entry.get("username_source") or "not_set")
            if saved != "Unknown" and source == "manual":
                return UsernameScanReport(
                    package=pkg,
                    username=saved,
                    source="manual_mapping",
                    supported=True,
                    reason="",
                    methods_attempted=("config_manual",),
                    duration_ms=int((time.monotonic() - start) * 1000),
                    root_used=False,
                    confidence="high",
                    root_read_status="manual map",
                )

    root_report = scan_package_username_root(pkg, timeout_seconds=timeout_seconds)
    if root_report.username:
        return root_report
    return root_report


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
        return updated, PackageUsernameResult(saved, _normalize_source(source), False, 0, root_used=False)
    if not allow_detect:
        return updated, PackageUsernameResult("Unknown", "unknown", False, 0, root_used=False)
    result = detect_package_username_quick(
        str(updated.get("package") or ""),
        timeout_seconds=timeout_seconds,
    )
    if result.username:
        updated["account_username"] = result.username
        updated["username_source"] = validate_username_source(
            _normalize_source(result.source),
            result.username,
        )
        cache = dict(config_data.get("package_username_cache") or {})
        cache[str(updated["package"])] = result.username
        config_data["package_username_cache"] = cache
    return updated, result if result.username else PackageUsernameResult(
        "Unknown",
        "unknown",
        result.detector_used,
        result.duration_ms,
        result.error,
        root_used=result.root_used,
    )


def safe_detect_username_for_package(
    package_name: str,
    *,
    timeout_seconds: float = SAFE_DETECT_DEFAULT_TIMEOUT_SECONDS,
) -> str:
    safe_pkg = str(package_name or "").strip()
    if not safe_pkg:
        return "Unknown"
    bounded = max(0.5, min(float(timeout_seconds), USERNAME_DETECT_TIMEOUT_SECONDS))
    try:
        result = detect_package_username_quick(safe_pkg, timeout_seconds=bounded)
    except Exception as exc:  # noqa: BLE001
        _log.debug("safe_detect failed for %s: %s", safe_pkg[:64], str(exc)[:120])
        return "Unknown"
    name = validate_account_username(result.username or "")
    return name or "Unknown"


def collect_safe_usernames_for_packages(
    packages: list[str],
    *,
    per_package_timeout_seconds: float = SAFE_DETECT_DEFAULT_TIMEOUT_SECONDS,
    total_deadline_seconds: float = 8.0,
) -> dict[str, str]:
    deadline = time.monotonic() + max(0.5, float(total_deadline_seconds))
    out: dict[str, str] = {}
    for raw in packages or ():
        pkg = str(raw or "").strip()
        if not pkg:
            continue
        if time.monotonic() >= deadline:
            out.setdefault(pkg, "Unknown")
            continue
        remaining = max(0.5, deadline - time.monotonic())
        budget = min(per_package_timeout_seconds, remaining)
        out[pkg] = safe_detect_username_for_package(pkg, timeout_seconds=budget)
    return out
