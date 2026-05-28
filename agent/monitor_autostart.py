"""DENG Tool: Rejoin APK — Termux-side monitor bridge autostart.

This module wires :mod:`agent.monitor_bridge` into the public ``deng-rejoin``
lifecycle so cloud-phone monitoring works automatically after license
verification — no manual env-var setup required.

Public contract
---------------
Call :func:`ensure_monitor_bridge_started` from any post-license point
(menu, ``cmd_start``, etc.). The call is **idempotent** and **never
raises**:

* Loads a cached bridge token from ``~/.deng-tool/rejoin/.monitor-bridge.json``.
* If the cache is missing/expired/wrong-URL, POSTs to
  ``/api/monitor/bridge/issue-from-license`` using the active license key
  and its install_id_hash. That endpoint validates the license proof,
  upserts the device row, and returns a short-lived bridge token.
* Starts :class:`agent.monitor_bridge.MonitorBridge` in a daemon thread.
* Re-entry: a second call detects the running bridge and does nothing.

Status provider
---------------
:func:`set_active_supervisor` lets ``cmd_start`` register the live
:class:`agent.supervisor.WatchdogSupervisor` so per-package state is
included in pushes. When no supervisor is registered (user is sitting on
the menu or hasn't pressed Start), the bridge still pushes an empty
``packages`` array — the device row is upserted with ``status_connected =
true`` so the APK shows "Connected, no packages reported yet" instead of
"No cloud phone connected".

Security
--------
* The bridge token is cached locally with mode ``0o600`` (when supported).
* The token is the only secret persisted — license keys, install IDs, and
  owner identifiers are NEVER written to the cache file.
* All network failures are swallowed: the agent's primary monitoring
  loop is never interrupted by bridge problems.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .constants import APP_HOME, VERSION
from .monitor_bridge import (
    DEFAULT_BRIDGE_URL,
    BridgeConfig,
    MonitorBridge,
)

logger = logging.getLogger("deng.monitor_autostart")

# ── Cache + state ───────────────────────────────────────────────────────────

BRIDGE_CACHE_PATH = APP_HOME / ".monitor-bridge.json"
"""Local cache of the issued bridge token. Never committed; mode 0o600."""

# Refresh the token at least this long before its server-side expiry, so a
# slow clock doesn't push with an already-expired token.
_TOKEN_REFRESH_SLACK_SECONDS = 10 * 60  # 10 min

_lock = threading.Lock()
_running_bridge: MonitorBridge | None = None
_active_supervisor: Any = None
_status_announced: bool = False


# ── Public API ──────────────────────────────────────────────────────────────


def set_active_supervisor(supervisor: Any) -> None:
    """Register the live supervisor so its package snapshots get pushed.

    Pass ``None`` when the supervisor exits to drop the reference.
    Safe to call from any thread; never raises.
    """
    global _active_supervisor
    try:
        _active_supervisor = supervisor
    except Exception:  # noqa: BLE001
        pass


def ensure_monitor_bridge_started(
    *,
    license_key: str,
    install_id_hash: str,
    tool_version: str | None = None,
    channel: str | None = None,
    device_label: str | None = None,
    bridge_url: str | None = None,
    announce: bool = True,
) -> bool:
    """Idempotent: ensure the monitor bridge is running.

    Returns ``True`` if the bridge is running after the call, ``False`` on
    any soft failure (e.g. backend offline, missing license proof).
    Never raises.

    The first successful call prints a single non-noisy status line.
    Subsequent calls are silent.
    """
    global _running_bridge, _status_announced

    if not (license_key and install_id_hash):
        return False

    url = _resolve_bridge_url(bridge_url)
    label = (device_label or _default_device_label())[:64]
    tv = (tool_version or VERSION or "")[:32]
    ch = (channel or "stable")[:16]

    with _lock:
        # Fast path: already running.
        if _running_bridge is not None and _running_bridge.is_running():
            return True

        token = _load_cached_token_for_url(url)
        if not token:
            issued = _issue_token_from_license(
                bridge_url=url,
                license_key=license_key,
                install_id_hash=install_id_hash,
                device_label=label,
                tool_version=tv,
                channel=ch,
            )
            if not issued:
                if announce and not _status_announced:
                    _announce_status(connected=False)
                    _status_announced = True
                return False
            token = issued.get("bridge_token") or ""
            if not token:
                return False
            _save_cached_token({
                "bridge_url": url,
                "bridge_token": token,
                "device_id": issued.get("device_id"),
                "expires_at": issued.get("expires_at"),
                "expires_at_epoch": _iso_to_epoch(issued.get("expires_at")),
                "issued_at_epoch": time.time(),
            })

        # Build provider closure that always reads the live supervisor.
        def _status_provider() -> dict[str, Any]:
            return _build_status_payload(tool_version=tv, channel=ch)

        try:
            cfg = BridgeConfig(
                bridge_url=url,
                token=token,
                enabled=True,
                insecure=os.environ.get("DENG_MONITOR_BRIDGE_INSECURE", "").lower()
                    in {"1", "true", "yes"},
            )
            bridge = MonitorBridge(
                config=cfg,
                status_provider=_status_provider,
                snapshot_provider=None,  # snapshots are out-of-scope for autostart
            )
            ok = bridge.start()
        except Exception:  # noqa: BLE001
            logger.debug("autostart bridge.start() raised", exc_info=True)
            ok = False

        if ok:
            _running_bridge = bridge
            if announce and not _status_announced:
                _announce_status(connected=True)
                _status_announced = True
            return True

        if announce and not _status_announced:
            _announce_status(connected=False)
            _status_announced = True
        return False


def stop_monitor_bridge() -> None:
    """Best-effort shutdown — safe to call from any phase."""
    global _running_bridge
    with _lock:
        b = _running_bridge
        _running_bridge = None
    if b is not None:
        try:
            b.stop()
        except Exception:  # noqa: BLE001
            pass


def reset_for_tests() -> None:
    """Test-only helper: clear cache + module state. Safe in production
    (no behavior change beyond resetting the in-memory flags).
    """
    global _running_bridge, _active_supervisor, _status_announced
    stop_monitor_bridge()
    _running_bridge = None
    _active_supervisor = None
    _status_announced = False


# ── Status provider ─────────────────────────────────────────────────────────


def _build_status_payload(*, tool_version: str, channel: str) -> dict[str, Any]:
    """Convert the live supervisor snapshot into the bridge's safe payload
    shape. Returns empty packages when no supervisor is active."""
    packages: list[dict[str, Any]] = []
    sup = _active_supervisor
    if sup is not None:
        try:
            snap = sup.get_status_snapshot()
        except Exception:  # noqa: BLE001
            snap = []
        for row in (snap or [])[:64]:
            if not isinstance(row, dict):
                continue
            pkg = row.get("package") or row.get("package_name")
            if not isinstance(pkg, str) or not pkg:
                continue
            # Supervisor uses "status"; the bridge's safe-payload contract
            # uses "state". Pass both so MonitorBridge picks whichever it
            # supports today/tomorrow.
            state = row.get("status") or row.get("state") or "Unknown"
            packages.append({
                "package": pkg,
                "username": row.get("username") or "",
                "state": state,
                "ram_mb": int(row.get("ram_mb") or 0),
                "runtime_seconds": int(row.get("runtime_seconds") or 0),
                "restart_count": int(row.get("revive_count") or row.get("restart_count") or 0),
                "private_url_configured": bool(row.get("private_url_configured")),
            })
    return {
        "tool_version": tool_version,
        "channel": channel,
        "packages": packages,
    }


# ── Token issuance ──────────────────────────────────────────────────────────


def _issue_token_from_license(
    *,
    bridge_url: str,
    license_key: str,
    install_id_hash: str,
    device_label: str,
    tool_version: str,
    channel: str,
    timeout: float = 8.0,
) -> dict[str, Any] | None:
    """POST /api/monitor/bridge/issue-from-license. Returns the response
    dict on success, ``None`` on any failure."""
    url = bridge_url.rstrip("/") + "/api/monitor/bridge/issue-from-license"
    payload = json.dumps({
        "license_key": license_key,
        "install_id_hash": install_id_hash,
        "device_label": device_label,
        "tool_version": tool_version,
        "channel": channel,
    }, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "DENG-Tool-Monitor-Autostart/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            if not (200 <= resp.status < 300):
                logger.debug("issue-from-license: http_%s", resp.status)
                return None
            body = resp.read()
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict) or not data.get("bridge_token"):
                return None
            return data
    except urllib.error.HTTPError as exc:
        logger.debug("issue-from-license: http_%d", exc.code)
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    except Exception:  # noqa: BLE001
        logger.debug("issue-from-license: unexpected", exc_info=True)
        return None


# ── Cache helpers ───────────────────────────────────────────────────────────


def _load_cached_token_for_url(bridge_url: str) -> str:
    """Return the cached bridge token if it is for this URL and unexpired.

    Returns empty string on any failure (file missing, malformed, expired,
    URL mismatch).
    """
    try:
        if not BRIDGE_CACHE_PATH.exists():
            return ""
        data = json.loads(BRIDGE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(data, dict):
        return ""
    if str(data.get("bridge_url") or "").rstrip("/") != bridge_url.rstrip("/"):
        return ""
    token = str(data.get("bridge_token") or "")
    if not token:
        return ""
    expires = data.get("expires_at_epoch")
    try:
        expires_f = float(expires)
    except (TypeError, ValueError):
        return ""
    if expires_f - _TOKEN_REFRESH_SLACK_SECONDS <= time.time():
        return ""
    return token


def _save_cached_token(payload: dict[str, Any]) -> None:
    try:
        BRIDGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = BRIDGE_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        try:
            os.replace(tmp, BRIDGE_CACHE_PATH)
        except OSError:
            # Fallback for filesystems without atomic replace.
            BRIDGE_CACHE_PATH.write_text(
                json.dumps(payload, separators=(",", ":")),
                encoding="utf-8",
            )
        try:
            os.chmod(BRIDGE_CACHE_PATH, 0o600)
        except OSError:
            pass
    except Exception:  # noqa: BLE001
        logger.debug("monitor token cache save failed", exc_info=True)


def clear_cached_token() -> None:
    """Force the next ``ensure_monitor_bridge_started`` to re-issue a token.

    Call this when a push response says the token was revoked / expired.
    """
    try:
        if BRIDGE_CACHE_PATH.exists():
            BRIDGE_CACHE_PATH.unlink()
    except Exception:  # noqa: BLE001
        pass


# ── Misc helpers ────────────────────────────────────────────────────────────


def _resolve_bridge_url(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    env_url = os.environ.get("DENG_MONITOR_BRIDGE_URL")
    if env_url:
        return env_url.rstrip("/")
    return DEFAULT_BRIDGE_URL


def _default_device_label() -> str:
    # Privacy-safe default: never leak hostnames or user names.
    return "Termux on Android"


def _iso_to_epoch(iso: Any) -> float:
    if not isinstance(iso, str) or not iso:
        return time.time() + 6 * 3600  # fallback ~6h
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except Exception:  # noqa: BLE001
        return time.time() + 6 * 3600


def _announce_status(*, connected: bool) -> None:
    """Print a single, non-noisy status line after the first attempt.

    Uses plain ASCII so it works on every Termux color setting. Swallows
    any IO error.
    """
    try:
        if connected:
            print("  [\u2713] Rejoin APK monitor connected")
        else:
            print("  [!] Rejoin APK monitor offline \u2014 retrying in background")
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "BRIDGE_CACHE_PATH",
    "clear_cached_token",
    "ensure_monitor_bridge_started",
    "reset_for_tests",
    "set_active_supervisor",
    "stop_monitor_bridge",
]
