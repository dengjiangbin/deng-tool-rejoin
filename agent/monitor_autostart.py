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
* If the cache is missing/expired/wrong-URL, calls
  ``/api/monitor/bridge/issue-from-license`` via :mod:`agent.safe_http`
  (curl subprocess on Termux). The endpoint validates the license proof,
  upserts the device row, and returns a short-lived bridge token.
* Starts :class:`agent.monitor_bridge.MonitorBridge` in a daemon thread.
* Re-entry: a second call detects the running bridge and does nothing.

Status provider
---------------
:func:`set_active_supervisor` lets ``cmd_start`` register the live
:class:`agent.supervisor.WatchdogSupervisor` so per-package state is
included in pushes. When no supervisor is registered (user is sitting on
the menu or hasn't pressed Start), the bridge instead reports each
**configured/enabled package** with ``state="Dead"`` and ``runtime=0`` so
the APK shows the rows immediately (username title, package_name
subtitle, Dead badge) — see :func:`set_config`.

Snapshot provider
-----------------
On Termux, the bridge captures screenshots via :func:`agent.snapshot.capture_snapshot`
and uploads PNG bytes to ``/api/monitor/bridge/snapshot`` on the
snapshot interval requested by the device's monitor settings. The
interval is updated dynamically from the ``/push`` response so the user
can change it in the APK without relaunching ``deng-rejoin``.

Security
--------
* The bridge token is cached locally with mode ``0o600`` (when supported).
* The token is the only secret persisted — license keys, install IDs, and
  owner identifiers are NEVER written to the cache file.
* All HTTPS calls run via :mod:`agent.safe_http`, which uses curl as a
  subprocess on Termux. This is essential: real-device probe
  ``p-d1cb86fd89`` showed a SIGSEGV inside libssl3's
  ``EVP_PKEY_generate`` when the previous (in-process) urllib path was
  used. With curl-subprocess the crash only kills the curl child.
* All network failures are swallowed: the agent's primary monitoring
  loop is never interrupted by bridge problems.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .constants import APP_HOME, VERSION
from .license import get_public_device_model
from .monitor_bridge import (
    ALLOWED_STATES,
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
_active_config: dict[str, Any] | None = None
_status_announced: bool = False
_last_issue_result: str | None = None


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


def set_config(cfg: dict[str, Any] | None) -> None:
    """Register the saved config so the bridge can report configured
    packages even when no supervisor is active (Termux is sitting on the
    main menu). Safe to call from any thread; never raises.
    """
    global _active_config
    try:
        _active_config = cfg if isinstance(cfg, dict) else None
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
    config: dict[str, Any] | None = None,
) -> bool:
    """Idempotent: ensure the monitor bridge is running.

    Returns ``True`` if the bridge is running after the call, ``False`` on
    any soft failure (e.g. backend offline, missing license proof).
    Never raises.

    The first successful call prints a single non-noisy status line.
    Subsequent calls are silent.
    """
    global _running_bridge, _status_announced, _last_issue_result

    if config is not None:
        set_config(config)

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
                _last_issue_result = "error"
                if announce and not _status_announced:
                    _announce_status(connected=False)
                    _status_announced = True
                return False
            _last_issue_result = "success"
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
        else:
            _last_issue_result = "cached"

        # Closures that always read the live supervisor / config.
        def _status_provider() -> dict[str, Any]:
            return _build_status_payload(tool_version=tv, channel=ch)

        def _on_unauthorized(_status: int) -> None:
            # Token revoked → drop cache so next ensure() reissues.
            clear_cached_token()
            logger.info("monitor_bridge token rejected (HTTP %s); cleared cache", _status)

        try:
            cfg_obj = BridgeConfig(
                bridge_url=url,
                token=token,
                enabled=True,
                insecure=os.environ.get("DENG_MONITOR_BRIDGE_INSECURE", "").lower()
                    in {"1", "true", "yes"},
            )
            bridge = MonitorBridge(
                config=cfg_obj,
                status_provider=_status_provider,
                snapshot_provider=_default_snapshot_provider,
                on_unauthorized=_on_unauthorized,
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
    global _running_bridge, _active_supervisor, _active_config
    global _status_announced, _last_issue_result
    stop_monitor_bridge()
    _running_bridge = None
    _active_supervisor = None
    _active_config = None
    _status_announced = False
    _last_issue_result = None


def get_monitor_status_summary() -> dict[str, Any]:
    """Return a redacted dict describing current monitor state.

    Used by ``deng-rejoin monitor status``. Contains **no** secrets:
    license keys, bridge tokens, app session tokens, and raw install IDs
    are never included.
    """
    bridge = _running_bridge
    state = bridge.state if bridge is not None else None
    cfg = _active_config
    configured_count = 0
    try:
        if isinstance(cfg, dict):
            pkgs = cfg.get("roblox_packages") or []
            if isinstance(pkgs, list):
                configured_count = sum(
                    1 for p in pkgs
                    if isinstance(p, dict) and p.get("enabled", True) and p.get("package")
                )
    except Exception:  # noqa: BLE001
        configured_count = 0

    reported_count = 0
    try:
        if bridge is not None:
            raw = bridge.status_provider() or {}
            pkgs = raw.get("packages") or []
            reported_count = len(pkgs) if isinstance(pkgs, list) else 0
    except Exception:  # noqa: BLE001
        reported_count = 0

    snapshot_interval = 0
    if bridge is not None:
        try:
            snapshot_interval = int(bridge.config.snapshot_interval_seconds)
        except Exception:  # noqa: BLE001
            snapshot_interval = 0

    cache_summary: dict[str, Any] = {"present": False}
    try:
        if BRIDGE_CACHE_PATH.exists():
            data = json.loads(BRIDGE_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cache_summary = {
                    "present": True,
                    "expires_at": data.get("expires_at"),
                    "issued_at_epoch": data.get("issued_at_epoch"),
                }
    except Exception:  # noqa: BLE001
        cache_summary = {"present": False, "error": "read_failed"}

    push_interval = 0.0
    if bridge is not None:
        try:
            push_interval = float(bridge.config.push_interval_seconds)
        except Exception:  # noqa: BLE001
            push_interval = 0.0

    return {
        "bridge_url": _resolve_bridge_url(None),
        "autostart_enabled": True,
        "bridge_running": bool(bridge and bridge.is_running()),
        "connected": bool(state and state.connected),
        "push_interval_seconds": push_interval,
        "last_push_at": (state.last_push_at if state else None),
        "last_push_result": (state.last_push_result if state else None),
        "last_error": (state.last_error if state else None),
        "consecutive_failures": (state.consecutive_failures if state else 0),
        "snapshot_interval_seconds": snapshot_interval,
        "snapshot_last_sent_at": (state.snapshot_last_sent_at if state else 0.0),
        "snapshot_last_result": (state.snapshot_last_result if state else None),
        # v1.0.4 diagnostics — what `deng-rejoin monitor status` prints
        # so the user can debug the snapshot pipeline without secrets.
        "snapshot_last_bytes": (state.snapshot_last_bytes if state else 0),
        "snapshot_last_error": (state.snapshot_last_error if state else None),
        "snapshot_last_upload_status": (state.snapshot_last_upload_status if state else None),
        "snapshot_provider_called_count": (state.snapshot_provider_called_count if state else 0),
        "screencap_available": (state.screencap_available if state else None),
        # v1.0.6 capture-provider diagnostics.
        "snapshot_provider": (state.snapshot_provider if state else None),
        "snapshot_png_valid": (state.snapshot_png_valid if state else None),
        "snapshot_root_granted": (state.snapshot_root_granted if state else None),
        "snapshot_su_available": (state.snapshot_su_available if state else None),
        "snapshot_attempts": (list(state.snapshot_attempts) if state else []),
        "configured_packages": configured_count,
        "reported_packages": reported_count,
        "supervisor_active": _active_supervisor is not None,
        "token_cache": cache_summary,
        "last_issue_result": _last_issue_result,
    }


# ── Status provider ─────────────────────────────────────────────────────────


# v1.0.4: APK-visible state vocabulary is now exactly five values:
#
#   Dead          — process not running, or in lobby/not playing (so the
#                   tool should relaunch / rejoin).
#   Launching     — open-package command issued, process is starting.
#                   This is BEFORE the private-server join intent.
#   Joining       — private-server URL join intent has been issued; the
#                   package is trying to enter that server.
#   Online        — process confirmed in-game / healthy heartbeat.
#   No Heartbeat  — process likely alive but no healthy heartbeat — will
#                   trigger relaunch/rejoin after cooldown.
#
# `In-Lobby` is intentionally NOT in this set anymore. Per user feedback,
# treating "Lobby" as a distinct state was blocking relaunch/rejoin —
# it now collapses to `Dead` so the supervisor's recovery loop kicks in.
_SUPERVISOR_TO_PUBLIC_STATE: dict[str, str] = {
    # Healthy / in-game.
    "Online": "Online",
    "In Server": "Online",
    # Process started but not yet in-game and not yet joining the
    # private server. `Preparing` and the deprecated `Launched` collapse
    # here too — they're all "process is coming up".
    "Launching": "Launching",
    "Launched": "Launching",
    "Preparing": "Launching",
    "Relaunching": "Launching",
    # Process is in the join-private-URL phase. `Join Unconfirmed`
    # collapses here because the supervisor uses it for "deep link
    # opened but no in-game proof yet" — same user-visible meaning.
    "Joining": "Joining",
    "Join Unconfirmed": "Joining",
    # Heartbeat lost on a known-good process. Reconnecting / Background
    # collapse here because they all describe "process alive, gameplay
    # uncertain" and the supervisor's relaunch cooldown handles them.
    "No Heartbeat": "No Heartbeat",
    "Reconnecting": "No Heartbeat",
    "Background": "No Heartbeat",
    # Genuinely not running, OR returned to lobby / not-playing. The
    # user explicitly does NOT want `In-Lobby` as a separate state —
    # lobby must map to Dead so the watchdog re-issues a launch/rejoin.
    "Dead": "Dead",
    "In-Lobby": "Dead",
    "Lobby": "Dead",
    "Closed": "Dead",
    "Disconnected": "Dead",
    "Failed": "Dead",
    "Stopped": "Dead",
    "Unknown": "Dead",
    "Offline": "Dead",
    "Warning": "Dead",
    "Join Failed": "Dead",
    "Wrong Game / Wrong Server": "Dead",
}

# Public allow-list for cross-checking: exactly five values.
APK_VISIBLE_STATES: frozenset[str] = frozenset(
    {"Dead", "Launching", "Joining", "Online", "No Heartbeat"}
)


def _coerce_public_state(raw: Any) -> str:
    """Collapse any supervisor state string to the 5 APK-visible states.

    Anything outside the canonical 5 — including blank/None — is mapped
    to ``Dead`` so the watchdog's recovery loop owns the recovery
    decision rather than the APK silently rendering an unfamiliar label.
    """
    if not isinstance(raw, str) or not raw:
        return "Dead"
    if raw in _SUPERVISOR_TO_PUBLIC_STATE:
        return _SUPERVISOR_TO_PUBLIC_STATE[raw]
    # If the supervisor introduces a brand-new state name later and we
    # haven't wired it into the map, default to "Dead" — never silently
    # leak the unknown vocabulary to the APK.
    return "Dead"


def _build_status_payload(*, tool_version: str, channel: str) -> dict[str, Any]:
    """Build the bridge payload.

    Order of preference:
      1. Live ``WatchdogSupervisor`` snapshot (post-Start).
      2. Saved-config enabled packages (pre-Start / on menu).
      3. Empty list (no config available).

    All paths are exception-safe and never touch root / subprocesses.
    """
    try:
        device_ram = read_device_ram()
    except Exception:  # noqa: BLE001
        device_ram = None

    sup = _active_supervisor
    if sup is not None:
        packages = _packages_from_supervisor(sup)
        if packages is not None:
            payload: dict[str, Any] = {
                "tool_version": tool_version,
                "channel": channel,
                "packages": packages,
            }
            if device_ram is not None:
                payload["device_ram"] = device_ram
            return payload

    cfg = _active_config
    payload = {
        "tool_version": tool_version,
        "channel": channel,
        "packages": _packages_from_config(cfg) if cfg is not None else [],
    }
    if device_ram is not None:
        payload["device_ram"] = device_ram
    return payload


def _packages_from_supervisor(sup: Any) -> list[dict[str, Any]] | None:
    try:
        snap = sup.get_status_snapshot()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(snap, list):
        return None

    now = time.time()
    out: list[dict[str, Any]] = []
    for row in snap[:64]:
        if not isinstance(row, dict):
            continue
        pkg = row.get("package") or row.get("package_name")
        if not isinstance(pkg, str) or not pkg:
            continue
        raw_state = row.get("status") or row.get("state") or "Unknown"
        public_state = _coerce_public_state(raw_state)
        # Compute runtime from the optional epoch the supervisor tracks
        # (``online_since`` / ``last_seen_at``). 0 means "not running yet".
        runtime_seconds = 0
        try:
            online_since = row.get("online_since") or row.get("last_seen_at")
            if online_since and public_state == "Online":
                runtime_seconds = max(0, int(now - float(online_since)))
        except (TypeError, ValueError):
            runtime_seconds = 0
        # RAM — supervisor may or may not include it. Never call dumpsys
        # from this thread (idle-safety rule); use the cached value the
        # supervisor publishes if present, else 0.
        ram_mb = 0
        try:
            ram_mb = max(0, int(row.get("ram_mb") or 0))
        except (TypeError, ValueError):
            ram_mb = 0
        out.append({
            "package": pkg,
            "username": row.get("username") or "",
            "state": public_state,
            "ram_mb": ram_mb,
            "runtime_seconds": runtime_seconds,
            "restart_count": int(row.get("revive_count") or row.get("restart_count") or 0),
            "private_url_configured": bool(row.get("private_url_configured")),
        })
    return out


def _packages_from_config(cfg: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Build per-package rows from the saved Termux config alone.

    Never reads root, never scans installed apps, never queries dumpsys —
    this is exactly the "idle-safe" data source required by the
    no-segfault-while-AFK contract.
    """
    if not isinstance(cfg, dict):
        return []
    try:
        raw_pkgs = cfg.get("roblox_packages") or []
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw_pkgs, list):
        return []

    # Optional read-only username cache (kept in cfg by package_username
    # module). Falls back to ``account_username`` field on the entry.
    username_cache = cfg.get("package_username_cache") if isinstance(cfg, dict) else None
    if not isinstance(username_cache, dict):
        username_cache = {}
    account_cache = cfg.get("account_username_cache") if isinstance(cfg, dict) else None
    if not isinstance(account_cache, dict):
        account_cache = {}

    out: list[dict[str, Any]] = []
    for entry in raw_pkgs[:64]:
        if not isinstance(entry, dict):
            continue
        if not entry.get("enabled", True):
            continue
        pkg = entry.get("package") or entry.get("package_name")
        if not isinstance(pkg, str) or not pkg:
            continue
        username = (
            entry.get("account_username")
            or username_cache.get(pkg)
            or account_cache.get(pkg)
            or ""
        )
        if not isinstance(username, str):
            username = ""
        display_name = entry.get("app_name") if isinstance(entry.get("app_name"), str) else None
        private_url = entry.get("private_server_url")
        out.append({
            "package": pkg,
            "display_name": display_name or None,
            "username": username,
            "state": "Dead",
            "ram_mb": 0,
            "runtime_seconds": 0,
            "restart_count": 0,
            "private_url_configured": bool(private_url),
        })
    return out


# ── Snapshot provider ───────────────────────────────────────────────────────


def _default_snapshot_provider() -> Any:
    """Capture a fullscreen screenshot for the bridge to upload.

    v1.0.6: returns a rich :class:`agent.snapshot.SnapshotCapture` so the
    bridge can surface real per-provider diagnostics (which rung worked,
    PNG validity, root grant) to the APK and probe. The bridge duck-types
    the return value, so returning the object — or ``None`` — is safe.

    Implementation rules:
      * Never runs unless the bridge calls it (snapshot_interval > 0).
      * Uses ``agent.snapshot.capture_snapshot_detailed`` which walks the
        full provider ladder (normal/system/root stdout + root file) with
        a strict per-attempt timeout and PNG validation.
      * On any failure returns the capture object with ``data=None`` and a
        precise ``result``/``error`` so the bridge keeps running.
      * Never raises.
    """
    try:
        from . import snapshot as _snap  # local import keeps cold-start light
    except Exception:  # noqa: BLE001
        return None
    try:
        return _snap.capture_snapshot_detailed()
    except Exception:  # noqa: BLE001
        return None


def _parse_meminfo(text: str) -> dict[str, Any] | None:
    """Pure parser for ``/proc/meminfo`` text. Returns RAM dict or None.

    Returns the dashboard contract requested by the user:
    ``available_mb / total_mb / available_percent``.
    """
    total_kb = 0
    avail_kb = -1
    free_kb = 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key = parts[0].rstrip(":")
        try:
            val = int(parts[1])  # kB
        except (TypeError, ValueError):
            continue
        if key == "MemTotal":
            total_kb = val
        elif key == "MemAvailable":
            avail_kb = val
        elif key == "MemFree":
            free_kb = val
    if total_kb <= 0:
        return None
    if avail_kb < 0:
        avail_kb = free_kb
    total_mb = total_kb // 1024
    available_mb = max(0, avail_kb) // 1024
    percent = int(round((avail_kb / total_kb) * 100)) if total_kb else 0
    percent = max(0, min(100, percent))
    return {
        "available_mb": int(available_mb),
        "total_mb": int(total_mb),
        "percent": percent,
    }


def read_device_ram() -> dict[str, Any] | None:
    """Read device-level RAM from ``/proc/meminfo`` (root-free, idle-safe).

    Returns ``{"available_mb", "total_mb", "percent"}`` or ``None`` when
    the file is unavailable (e.g. non-Linux dev machine). Never raises.
    """
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    try:
        return _parse_meminfo(text)
    except Exception:  # noqa: BLE001
        return None


# ── Token issuance (curl subprocess via safe_http) ──────────────────────────


def _issue_token_from_license(
    *,
    bridge_url: str,
    license_key: str,
    install_id_hash: str,
    device_label: str,
    tool_version: str,
    channel: str,
    timeout: float = 12.0,
) -> dict[str, Any] | None:
    """POST /api/monitor/bridge/issue-from-license via :mod:`agent.safe_http`.

    Returns the parsed response dict on success or ``None`` on any
    failure. **Always uses curl-subprocess on Termux** so OpenSSL
    crashes inside libssl cannot kill the agent process.
    """
    try:
        from . import safe_http  # local import — safe_http has no heavy deps
    except Exception:  # noqa: BLE001
        return None

    url = bridge_url.rstrip("/") + "/api/monitor/bridge/issue-from-license"
    payload = {
        "license_key": license_key,
        "install_id_hash": install_id_hash,
        "device_label": device_label,
        "tool_version": tool_version,
        "channel": channel,
    }
    try:
        data = safe_http.post_json(url, payload, timeout=int(max(5, timeout)))
    except safe_http.SafeHttpStatusError as exc:
        logger.debug("issue-from-license: http_%d", exc.status_code)
        return None
    except safe_http.SafeHttpNetworkError as exc:
        logger.debug("issue-from-license: net error %s", exc)
        return None
    except safe_http.SafeHttpJsonError as exc:
        logger.debug("issue-from-license: bad json %s", exc)
        return None
    except Exception:  # noqa: BLE001
        logger.debug("issue-from-license: unexpected", exc_info=True)
        return None

    if not isinstance(data, dict) or not data.get("bridge_token"):
        return None
    return data


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
    label = (get_public_device_model() or "").strip()
    if label and label.lower() != "unknown":
        return label[:64]
    return "Android device"


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
    "APK_VISIBLE_STATES",
    "BRIDGE_CACHE_PATH",
    "clear_cached_token",
    "ensure_monitor_bridge_started",
    "get_monitor_status_summary",
    "reset_for_tests",
    "set_active_supervisor",
    "set_config",
    "stop_monitor_bridge",
]
