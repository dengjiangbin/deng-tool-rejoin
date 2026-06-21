"""Bounded package username display detection.

This module is intentionally narrow: it reads only known, non-secret
Roblox preference keys from the selected package's exact prefs file.
It does not scan cookies, WebView data, databases, or unrelated apps.

NEVER imported or called from the live supervisor Start loop.  NEVER
calls any legacy ``account_mapping`` / Refresh Mapping helpers.  Every
subprocess invocation has a strict timeout — failure or timeout always
falls back to ``Unknown`` so the caller's UI stays responsive.
"""

from __future__ import annotations

import logging
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

USERNAME_DETECT_TIMEOUT_SECONDS = 2.0
SAFE_DETECT_DEFAULT_TIMEOUT_SECONDS = 1.5
_PREF_MAX_BYTES = 16 * 1024
_PREF_KEYS = ("username", "displayName")


@dataclass(frozen=True)
class PackageUsernameResult:
    username: str
    source: str
    detector_used: bool
    duration_ms: int
    error: str = ""


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


def _read_prefs_via_root(pkg: str, *, timeout_seconds: float) -> tuple[str, str]:
    """Return (username, error). Tries prefs.xml then package-specific prefs."""
    remaining = max(1, min(float(timeout_seconds), USERNAME_DETECT_TIMEOUT_SECONDS))
    paths = (
        f"/data/data/{pkg}/shared_prefs/prefs.xml",
        f"/data/data/{pkg}/shared_prefs/{pkg}_preferences.xml",
        f"/data/data/{pkg}/shared_prefs/pkg_preferences.xml",
    )
    last_err = ""
    for path in paths:
        try:
            content = root_access.read_root_file(
                path,
                max_bytes=_PREF_MAX_BYTES,
                timeout=max(2, int(remaining)),
                detect_timeout=max(2, int(min(remaining, 3))),
            )
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)[:120]
            continue
        if not content:
            last_err = f"unreadable or missing: {path}"
            continue
        username = _parse_known_pref_username(content)
        if username:
            return username, ""
        last_err = f"no username/displayName keys in {path.split('/')[-1]}"
    return "", last_err


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
    username, err = _read_prefs_via_root(pkg, timeout_seconds=timeout_seconds)
    duration = int((time.monotonic() - start) * 1000)
    if username:
        return PackageUsernameResult(username, "detected_safe_pref", True, duration)
    if err:
        return PackageUsernameResult("", "unknown", True, duration, err[:120])
    return PackageUsernameResult("", "unknown", True, duration)


def scan_package_username(
    package: str,
    config_data: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = USERNAME_DETECT_TIMEOUT_SECONDS,
) -> UsernameScanReport:
    """Scan one package and report username source or an honest Android limitation."""
    start = time.monotonic()
    methods: list[str] = []
    try:
        pkg = validate_package_name(package)
    except Exception as exc:  # noqa: BLE001
        return UsernameScanReport(
            package=str(package or "")[:120],
            username="",
            source="unknown",
            supported=False,
            reason=str(exc)[:160],
            methods_attempted=tuple(methods),
            duration_ms=0,
        )

    app_label = ""
    try:
        from . import android

        app_label = str(android.get_application_label(pkg) or "")[:120]
    except Exception:  # noqa: BLE001
        pass

    if config_data:
        from .config import get_package_display_username

        for entry in config_data.get("roblox_packages") or ():
            if not isinstance(entry, dict) or str(entry.get("package") or "") != pkg:
                continue
            saved = get_package_display_username(entry, config_data)
            source = str(entry.get("username_source") or "not_set")
            if saved != "Unknown" and source == "manual":
                methods.append("config_manual")
                return UsernameScanReport(
                    package=pkg,
                    username=saved,
                    source="manual",
                    supported=True,
                    reason="",
                    methods_attempted=tuple(methods),
                    app_label=app_label,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            if saved != "Unknown" and source.startswith("detected"):
                methods.append("config_cached_auto")
                return UsernameScanReport(
                    package=pkg,
                    username=saved,
                    source=source,
                    supported=True,
                    reason="",
                    methods_attempted=tuple(methods),
                    app_label=app_label,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

    cache = {}
    if config_data and isinstance(config_data.get("package_username_cache"), dict):
        cache = config_data["package_username_cache"]
    cached = validate_account_username(str(cache.get(pkg) or ""))
    if cached:
        methods.append("package_username_cache")
        return UsernameScanReport(
            package=pkg,
            username=cached,
            source="cache",
            supported=True,
            reason="",
            methods_attempted=tuple(methods),
            app_label=app_label,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    methods.append("root_shared_prefs")
    root_cap = root_access.detect()
    if not root_cap.available:
        methods.append("root_unavailable")
        reason = (
            "username unavailable: Android private app data is not readable from "
            "Termux without root/debug/exported source"
        )
        if root_cap.detail:
            reason += f" ({root_cap.detail[:80]})"
        return UsernameScanReport(
            package=pkg,
            username="",
            source="unknown",
            supported=False,
            reason=reason,
            methods_attempted=tuple(methods),
            app_label=app_label,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    quick = detect_package_username_quick(pkg, timeout_seconds=timeout_seconds)
    if quick.username:
        return UsernameScanReport(
            package=pkg,
            username=quick.username,
            source="detected_safe_pref",
            supported=True,
            reason="",
            methods_attempted=tuple(methods),
            app_label=app_label,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    reason = quick.error or (
        "username unavailable: prefs.xml readable via root but username/displayName not present"
    )
    return UsernameScanReport(
        package=pkg,
        username="",
        source="unknown",
        supported=bool(root_cap.available),
        reason=reason[:200],
        methods_attempted=tuple(methods),
        app_label=app_label,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


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


def safe_detect_username_for_package(
    package_name: str,
    *,
    timeout_seconds: float = SAFE_DETECT_DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Best-effort per-package username detector.

    Strictly per-package: never scans other apps, never lists installed
    packages, never invokes the legacy Refresh Mapping flow.  Wraps the
    bounded ``detect_package_username_quick`` so callers (Auto Detect and
    Manual Add Package) can populate display labels without risking a
    Termux freeze.

    Returns the detected username or ``"Unknown"`` on any failure,
    timeout, permission denial, missing file, or parse error.
    """
    safe_pkg = str(package_name or "").strip()
    if not safe_pkg:
        return "Unknown"
    bounded = max(0.5, min(float(timeout_seconds), USERNAME_DETECT_TIMEOUT_SECONDS))
    started = time.monotonic()
    try:
        result = detect_package_username_quick(
            safe_pkg,
            timeout_seconds=bounded,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.monotonic() - started) * 1000)
        _log.debug(
            "safe_detect_username_for_package failed for %s after %dms: %s",
            safe_pkg[:64],
            elapsed,
            str(exc)[:120],
        )
        return "Unknown"
    if result.error:
        _log.debug(
            "safe_detect_username_for_package timeout/error %s after %dms: %s",
            safe_pkg[:64],
            result.duration_ms,
            result.error[:120],
        )
    name = validate_account_username(result.username or "")
    if name:
        return name
    if result.error and "unavailable" in result.error.lower():
        return "Unknown"
    return "Unknown"


def collect_safe_usernames_for_packages(
    packages: list[str],
    *,
    per_package_timeout_seconds: float = SAFE_DETECT_DEFAULT_TIMEOUT_SECONDS,
    total_deadline_seconds: float = 5.0,
) -> dict[str, str]:
    """Run :func:`safe_detect_username_for_package` for each package.

    Stops early once the global ``total_deadline_seconds`` budget is hit
    so the caller (Auto Detect / Manual Add post-processing) cannot stall
    Termux when there are many packages or root is slow.

    The returned mapping always contains every requested package: those
    that hit the deadline or fail simply map to ``"Unknown"``.
    """
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
