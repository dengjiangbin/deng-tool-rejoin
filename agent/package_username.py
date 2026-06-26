"""Root-first package username detection for cloud-phone clones."""

from __future__ import annotations

import logging
import re
import threading
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
MENU_SCAN_TIMEOUT_SECONDS = 3.0
MENU_SCAN_CACHE_TTL_SECONDS = 2.0
_ROOT_OP_TIMEOUT_SECONDS = 3
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


NO_ACCOUNT_LABEL = "No Account"
SCANNER_ERROR_PREFIX = "Scanner Error:"


@dataclass(frozen=True)
class UsernameDisplayRow:
    package: str
    username_display: str
    account_status: str
    username_source: str
    reason: str = ""


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
        f"/data/user/0/{pkg}/shared_prefs/*.xml",
        f"/data/user/0/{pkg}/shared_prefs/*preferences*.xml",
        f"/data/user_de/0/{pkg}/shared_prefs/*.xml",
        f"/data_mirror/data_ce/null/0/{pkg}/shared_prefs/*.xml",
    )
    for pattern in pref_patterns:
        if time.monotonic() >= deadline:
            break
        methods.append("root_shared_prefs_glob")
        paths = root_access.list_root_glob(
            pattern, timeout=_ROOT_OP_TIMEOUT_SECONDS, max_results=24,
        )
        for path in paths:
            content = root_access.read_root_file(
                path, max_bytes=_PREF_MAX_BYTES, timeout=_ROOT_OP_TIMEOUT_SECONDS,
            )
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
        f"/data/user/0/{pkg}/files/*",
        f"/data/user/0/{pkg}/files/**/*",
        f"/sdcard/Android/data/{pkg}/files/*",
        f"/storage/emulated/0/Android/data/{pkg}/files/*",
        f"/storage/emulated/0/Android/data/{pkg}/**/*",
        f"/mnt/user/0/emulated/0/Android/data/{pkg}/files/*",
    )
    for pattern in file_patterns:
        if time.monotonic() >= deadline:
            break
        methods.append("root_file_glob")
        for path in root_access.list_root_glob(
            pattern, timeout=_ROOT_OP_TIMEOUT_SECONDS, max_results=16,
        ):
            if path.endswith((".png", ".jpg", ".webp", ".so", ".apk")):
                continue
            content = root_access.read_root_file(
                path, max_bytes=_FILE_MAX_BYTES, timeout=_ROOT_OP_TIMEOUT_SECONDS,
            )
            if not content:
                continue
            text_user, text_key = _grep_username_in_text(content)
            if text_user:
                status_bits.append(f"ok:file:{path.split('/')[-1]}:{text_key}")
                return text_user, "root_file", "medium", tuple(methods), "; ".join(status_bits[-3:])

    db_patterns = (
        f"/data/data/{pkg}/databases/*.db",
        f"/data/user/0/{pkg}/databases/*.db",
    )
    for pattern in db_patterns:
        if time.monotonic() >= deadline:
            break
        methods.append("root_database_glob")
        for path in root_access.list_root_glob(
            pattern, timeout=_ROOT_OP_TIMEOUT_SECONDS, max_results=8,
        ):
            safe_path = path.replace("'", "'\"'\"'")
            probe = root_access.run_root(
                f"command -v sqlite3 >/dev/null 2>&1 && sqlite3 '{safe_path}' "
                "\"SELECT username FROM sqlite_master LIMIT 0;\" 2>/dev/null; "
                f"sqlite3 '{safe_path}' \"SELECT username,userName,displayName,accountName,playerName "
                "FROM users LIMIT 5\" 2>/dev/null | head -5",
                timeout=_ROOT_OP_TIMEOUT_SECONDS,
            )
            if probe.ok and probe.stdout.strip():
                for line in probe.stdout.splitlines():
                    for token in re.split(r"[|,]", line):
                        candidate = validate_account_username(token.strip())
                        if candidate:
                            status_bits.append(f"ok:db:{path.split('/')[-1]}")
                            return candidate, "root_database", "medium", tuple(methods), "; ".join(status_bits[-3:])
            content = root_access.read_root_file(path, max_bytes=8192, timeout=_ROOT_OP_TIMEOUT_SECONDS)
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
        source="root_scan_no_account",
        supported=True,
        reason=f"no_logged_in_account_found_in_root_readable_data ({root_status})",
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


def username_display_for_package(
    package: str,
    *,
    timeout_seconds: float = SAFE_DETECT_DEFAULT_TIMEOUT_SECONDS,
) -> UsernameDisplayRow:
    """Root-first username display row — never returns Unknown."""
    pkg = str(package or "").strip()
    if not pkg:
        return UsernameDisplayRow(
            package="",
            username_display=f"{SCANNER_ERROR_PREFIX} invalid package",
            account_status="scanner_error",
            username_source="invalid",
            reason="empty package",
        )
    try:
        validate_package_name(pkg)
    except Exception as exc:  # noqa: BLE001
        return UsernameDisplayRow(
            package=pkg[:120],
            username_display=f"{SCANNER_ERROR_PREFIX} {exc}",
            account_status="scanner_error",
            username_source="invalid",
            reason=str(exc)[:160],
        )

    try:
        pre = root_access.root_required_preflight(timeout=max(3, int(min(timeout_seconds, 6))))
    except Exception as exc:  # noqa: BLE001
        return UsernameDisplayRow(
            package=pkg,
            username_display=f"{SCANNER_ERROR_PREFIX} {exc}",
            account_status="scanner_error",
            username_source="root_preflight_failed",
            reason=str(exc)[:160],
        )
    if not pre.ok:
        return UsernameDisplayRow(
            package=pkg,
            username_display=f"{SCANNER_ERROR_PREFIX} {pre.public_error()}",
            account_status="scanner_error",
            username_source="root_preflight_failed",
            reason=pre.detail[:160],
        )

    report = scan_package_username_root(pkg, timeout_seconds=timeout_seconds)
    if report.username:
        from .package_identity import record_package_identity

        record_package_identity(
            pkg,
            report.username,
            source=report.source or "root_scan",
            confidence=report.confidence or "high",
        )
        return UsernameDisplayRow(
            package=pkg,
            username_display=report.username,
            account_status="logged_in",
            username_source=report.source or "root_shared_prefs",
            reason="",
        )
    reason = report.reason or "no_logged_in_account_found_in_root_readable_data"
    if "package data" in reason.lower() or "no username key" in reason.lower():
        reason = "no_logged_in_account_found_in_root_readable_data"
    return UsernameDisplayRow(
        package=pkg,
        username_display=NO_ACCOUNT_LABEL,
        account_status="no_account",
        username_source="root_scan_no_account",
        reason=reason[:160],
    )


def scan_all_username_displays(
    packages: list[str],
    *,
    per_package_timeout_seconds: float = SAFE_DETECT_DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, UsernameDisplayRow]:
    out: dict[str, UsernameDisplayRow] = {}
    for raw in packages or ():
        pkg = str(raw or "").strip()
        if not pkg:
            continue
        out[pkg] = username_display_for_package(pkg, timeout_seconds=per_package_timeout_seconds)
    return out


def username_display_text(
    entry: dict[str, Any] | None = None,
    config_data: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = SAFE_DETECT_DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Display username for menus/tables — root scan only."""
    del config_data  # manual mapping must not override root scan
    package = ""
    if isinstance(entry, dict):
        package = str(entry.get("package") or "").strip()
    if not package:
        return f"{SCANNER_ERROR_PREFIX} missing package"
    return username_display_for_package(package, timeout_seconds=timeout_seconds).username_display


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
    """Scan one package using root evidence only."""
    del config_data  # legacy manual mapping must not override root scan
    start = time.monotonic()
    try:
        pkg = validate_package_name(package)
    except Exception as exc:  # noqa: BLE001
        return UsernameScanReport(
            package=str(package or "")[:120],
            username="",
            source="invalid",
            supported=False,
            reason=str(exc)[:160],
            duration_ms=0,
        )

    root_report = scan_package_username_root(pkg, timeout_seconds=timeout_seconds)
    root_report = UsernameScanReport(
        **{
            **root_report.__dict__,
            "duration_ms": int((time.monotonic() - start) * 1000),
        }
    )
    return root_report


_menu_scan_cache_lock = threading.Lock()
_menu_scan_cache: dict[str, tuple[float, UsernameScanReport]] = {}


def _timeout_scan_report(package: str, *, duration_ms: int) -> UsernameScanReport:
    return UsernameScanReport(
        package=package,
        username="",
        source="scanner_timeout",
        supported=True,
        reason=f"{SCANNER_ERROR_PREFIX} Timeout",
        methods_attempted=("root_scan_budget",),
        duration_ms=duration_ms,
        root_used=True,
        root_read_status="timed out",
    )


def scan_package_username_for_menu(
    package: str,
    config_data: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = MENU_SCAN_TIMEOUT_SECONDS,
) -> UsernameScanReport:
    """Fast, bounded username scan for package settings submenu.

    Uses a brief in-memory cache (2s) so re-rendering the menu does not
    re-run root I/O.  Each package scan is capped at ``timeout_seconds``
    (default 3s).  On budget overrun returns ``Scanner Error: Timeout``.
    """
    del config_data
    try:
        pkg = validate_package_name(package)
    except Exception as exc:  # noqa: BLE001
        return UsernameScanReport(
            package=str(package or "")[:120],
            username="",
            source="invalid",
            supported=False,
            reason=f"{SCANNER_ERROR_PREFIX} {exc}",
            duration_ms=0,
        )

    now = time.monotonic()
    with _menu_scan_cache_lock:
        cached = _menu_scan_cache.get(pkg)
        if cached and (now - cached[0]) <= MENU_SCAN_CACHE_TTL_SECONDS:
            return cached[1]

    started = time.monotonic()
    budget = min(max(0.25, float(timeout_seconds)), MENU_SCAN_TIMEOUT_SECONDS)
    report = scan_package_username_root(pkg, timeout_seconds=budget)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if (time.monotonic() - started) >= budget or elapsed_ms >= int(budget * 1000):
        report = _timeout_scan_report(pkg, duration_ms=elapsed_ms)

    with _menu_scan_cache_lock:
        _menu_scan_cache[pkg] = (time.monotonic(), report)
    return report


def menu_username_display(
    report: UsernameScanReport,
    entry: dict[str, Any],
    config_data: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Return ``(display_username, source_label)`` for menu rows."""
    del entry, config_data  # menu uses scan report only — never re-scan via cache
    if report.reason.startswith(SCANNER_ERROR_PREFIX):
        return report.reason[:80], "scanner_error"
    if report.username:
        return report.username, report.source or "root_scan"
    return NO_ACCOUNT_LABEL, report.source or "root_scan_no_account"


def resolve_package_display_username(
    entry: dict[str, Any],
    config_data: dict[str, Any],
    *,
    allow_detect: bool = True,
    timeout_seconds: float = USERNAME_DETECT_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], PackageUsernameResult]:
    updated = dict(entry)
    pkg = str(updated.get("package") or "").strip()
    if not allow_detect:
        row = username_display_for_package(pkg, timeout_seconds=timeout_seconds)
        return updated, PackageUsernameResult(
            row.username_display,
            row.username_source,
            False,
            0,
            row.reason[:120],
            root_used=True,
        )

    row = username_display_for_package(pkg, timeout_seconds=timeout_seconds)
    if row.account_status == "logged_in":
        from .package_identity import record_package_identity

        record_package_identity(
            pkg,
            row.username_display,
            source=row.username_source or "root_scan",
            confidence="high",
        )
        updated["account_username"] = row.username_display
        updated["username_source"] = validate_username_source(row.username_source, row.username_display)
        cache = dict(config_data.get("package_username_cache") or {})
        cache[pkg] = row.username_display
        config_data["package_username_cache"] = cache
    return updated, PackageUsernameResult(
        row.username_display,
        row.username_source,
        True,
        0,
        row.reason[:120],
        root_used=True,
        confidence="high" if row.account_status == "logged_in" else "",
    )


def safe_detect_username_for_package(
    package_name: str,
    *,
    timeout_seconds: float = SAFE_DETECT_DEFAULT_TIMEOUT_SECONDS,
) -> str:
    safe_pkg = str(package_name or "").strip()
    if not safe_pkg:
        return f"{SCANNER_ERROR_PREFIX} invalid package"
    return username_display_for_package(
        safe_pkg,
        timeout_seconds=max(0.5, min(float(timeout_seconds), USERNAME_DETECT_TIMEOUT_SECONDS)),
    ).username_display


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
            out.setdefault(pkg, NO_ACCOUNT_LABEL)
            continue
        remaining = max(0.5, deadline - time.monotonic())
        budget = min(per_package_timeout_seconds, remaining)
        out[pkg] = safe_detect_username_for_package(pkg, timeout_seconds=budget)
    return out
