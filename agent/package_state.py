"""Authoritative root-powered per-package state scanner."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import launch_verify, root_access
from .config import validate_package_name

LAUNCHING_TTL_SECONDS = 30

STATE_ONLINE = "online"
STATE_OFFLINE = "offline"
STATE_LAUNCHING = "launching"
STATE_NO_ACCOUNT = "no_account"
STATE_NOT_LAUNCHABLE = "not_launchable"
STATE_CRASHED = "crashed"
STATE_BLOCKED = "blocked"


@dataclass
class PackageLaunchMeta:
    last_launch_at: float = 0.0
    last_launch_rc: int = -1
    last_launch_command: str = ""
    launch_lock_until: float = 0.0
    last_failure_reason: str = ""
    crash_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class PackageStateRow:
    package: str
    state: str
    root_alive: bool
    foreground: bool
    reason: str
    last_launch_age: float | None = None
    launch_lock_active: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)


_launch_meta: dict[str, PackageLaunchMeta] = {}


def get_launch_meta(package: str) -> PackageLaunchMeta:
    pkg = str(package or "").strip()
    if pkg not in _launch_meta:
        _launch_meta[pkg] = PackageLaunchMeta()
    return _launch_meta[pkg]


def record_launch_attempt(
    package: str,
    *,
    command: str = "",
    rc: int = -1,
    ok: bool | None = None,
    failure_reason: str = "",
) -> None:
    """Record a launch attempt and set a per-package TTL lock."""
    try:
        pkg = validate_package_name(package)
    except Exception:  # noqa: BLE001
        return
    now = time.time()
    meta = get_launch_meta(pkg)
    meta.last_launch_at = now
    meta.last_launch_rc = int(rc)
    meta.last_launch_command = str(command or "")[:500]
    meta.launch_lock_until = now + float(LAUNCHING_TTL_SECONDS)
    meta.last_failure_reason = str(failure_reason or "")[:200]
    if ok is False and failure_reason:
        meta.launch_lock_until = min(meta.launch_lock_until, now + 5.0)


def clear_launch_lock(package: str, reason: str = "") -> None:
    pkg = str(package or "").strip()
    meta = _launch_meta.get(pkg)
    if not meta:
        return
    meta.launch_lock_until = 0.0
    if reason:
        meta.last_failure_reason = str(reason)[:200]


def launch_lock_blocks_relaunch(package: str, *, row: PackageStateRow | None = None) -> bool:
    """Return True only while an active launch lock still applies."""
    pkg = str(package or "").strip()
    meta = _launch_meta.get(pkg)
    if not meta or meta.launch_lock_until <= 0:
        return False
    now = time.time()
    current = row or scan_package_state_root(pkg)
    if current.root_alive or current.foreground:
        clear_launch_lock(pkg, "process_alive")
        return False
    if current.state != STATE_LAUNCHING:
        clear_launch_lock(pkg, f"state_{current.state}")
        return False
    if now >= meta.launch_lock_until:
        clear_launch_lock(pkg, "ttl_expired")
        return False
    return True


def _crash_evidence(package: str, meta: PackageLaunchMeta) -> tuple[bool, str, tuple[str, ...]]:
    if meta.last_launch_at <= 0:
        return False, "", ()
    age = time.time() - meta.last_launch_at
    if age > LAUNCHING_TTL_SECONDS * 2:
        return False, "", ()
    lines = launch_verify._recent_logcat_for_package(package, limit=6)  # noqa: SLF001
    if not lines:
        return False, "", ()
    for line in lines:
        low = line.lower()
        if package in line and ("fatal" in low or "androidruntime" in low or "crash" in low):
            return True, "androidruntime_fatal_after_launch", tuple(lines[:4])
    return False, "", tuple(lines[:4])


def scan_package_state_root(
    package: str,
    *,
    meta: PackageLaunchMeta | None = None,
    skip_username: bool = False,
) -> PackageStateRow:
    """Compute one package state from root evidence only."""
    del skip_username  # reserved for callers that batch username separately
    try:
        pkg = validate_package_name(package)
    except Exception as exc:  # noqa: BLE001
        return PackageStateRow(
            package=str(package or "")[:120],
            state=STATE_BLOCKED,
            root_alive=False,
            foreground=False,
            reason=str(exc)[:160],
        )

    meta = meta or get_launch_meta(pkg)
    now = time.time()
    last_launch_age = round(now - meta.last_launch_at, 1) if meta.last_launch_at > 0 else None

    pre = root_access.root_required_preflight(timeout=4)
    if not pre.ok:
        return PackageStateRow(
            package=pkg,
            state=STATE_BLOCKED,
            root_alive=False,
            foreground=False,
            reason=pre.public_error(),
            last_launch_age=last_launch_age,
            launch_lock_active=False,
            evidence={"root_preflight": pre.detail[:160]},
        )

    _, launchable, not_launchable_reason = launch_verify.resolve_launcher_activity(pkg)
    if not launchable:
        clear_launch_lock(pkg, "not_launchable")
        return PackageStateRow(
            package=pkg,
            state=STATE_NOT_LAUNCHABLE,
            root_alive=False,
            foreground=False,
            reason=not_launchable_reason or "no_launcher_activity",
            last_launch_age=last_launch_age,
            evidence={},
        )

    evidence = launch_verify.collect_process_evidence(pkg)
    root_alive = bool(evidence.get("root_running"))
    foreground = bool(evidence.get("foreground"))
    crashed, crash_reason, crash_lines = _crash_evidence(pkg, meta)
    if crash_lines:
        meta.crash_lines = crash_lines

    base_evidence = {
        "root_pidof": evidence.get("root_pidof", ""),
        "root_pgrep": evidence.get("root_pgrep", ""),
        "root_ps": evidence.get("root_ps", ""),
        "resumed_line": evidence.get("resumed_line", ""),
        "window_line": evidence.get("window_line", ""),
    }

    if root_alive or foreground:
        clear_launch_lock(pkg, "alive")
        return PackageStateRow(
            package=pkg,
            state=STATE_ONLINE,
            root_alive=root_alive,
            foreground=foreground,
            reason="root_process_or_foreground",
            last_launch_age=last_launch_age,
            launch_lock_active=False,
            evidence=base_evidence,
        )

    if crashed:
        clear_launch_lock(pkg, crash_reason)
        return PackageStateRow(
            package=pkg,
            state=STATE_CRASHED,
            root_alive=False,
            foreground=False,
            reason=crash_reason,
            last_launch_age=last_launch_age,
            launch_lock_active=False,
            evidence={**base_evidence, "crash_lines": list(crash_lines)},
        )

    in_launch_window = bool(
        meta.last_launch_at > 0
        and (now - meta.last_launch_at) <= LAUNCHING_TTL_SECONDS
        and now < meta.launch_lock_until
    )
    if in_launch_window:
        return PackageStateRow(
            package=pkg,
            state=STATE_LAUNCHING,
            root_alive=False,
            foreground=False,
            reason="launch_window_active",
            last_launch_age=last_launch_age,
            launch_lock_active=True,
            evidence=base_evidence,
        )

    if meta.launch_lock_until > 0 and now >= meta.launch_lock_until:
        clear_launch_lock(pkg, "ttl_expired")

    from . import package_username as _pu

    username_row = _pu.username_display_for_package(pkg, timeout_seconds=3.0)
    if username_row.account_status == "no_account":
        return PackageStateRow(
            package=pkg,
            state=STATE_NO_ACCOUNT,
            root_alive=False,
            foreground=False,
            reason=username_row.reason or "no_logged_in_account_found_in_root_readable_data",
            last_launch_age=last_launch_age,
            launch_lock_active=False,
            evidence=base_evidence,
        )

    return PackageStateRow(
        package=pkg,
        state=STATE_OFFLINE,
        root_alive=False,
        foreground=False,
        reason="no_root_process_or_foreground",
        last_launch_age=last_launch_age,
        launch_lock_active=False,
        evidence=base_evidence,
    )


def scan_all_package_states_root(packages: list[str]) -> dict[str, PackageStateRow]:
    out: dict[str, PackageStateRow] = {}
    for raw in packages or ():
        pkg = str(raw or "").strip()
        if not pkg:
            continue
        out[pkg] = scan_package_state_root(pkg)
    return out


def row_to_probe_dict(row: PackageStateRow) -> dict[str, Any]:
    return {
        "package": row.package,
        "state": row.state,
        "root_alive": row.root_alive,
        "foreground": row.foreground,
        "reason": row.reason,
        "last_launch_age": row.last_launch_age,
        "launch_lock_active": row.launch_lock_active,
    }


def map_row_to_supervisor_status(row: PackageStateRow) -> str:
    """Map scanner state to WatchdogSupervisor public status labels."""
    from .supervisor import (
        STATUS_DEAD,
        STATUS_FAILED,
        STATUS_JOIN_FAILED,
        STATUS_LAUNCHING,
        STATUS_ONLINE,
    )

    mapping = {
        STATE_ONLINE: STATUS_ONLINE,
        STATE_OFFLINE: STATUS_DEAD,
        STATE_LAUNCHING: STATUS_LAUNCHING,
        STATE_NO_ACCOUNT: STATUS_DEAD,
        STATE_NOT_LAUNCHABLE: STATUS_FAILED,
        STATE_CRASHED: STATUS_JOIN_FAILED,
        STATE_BLOCKED: STATUS_FAILED,
    }
    return mapping.get(row.state, STATUS_DEAD)
