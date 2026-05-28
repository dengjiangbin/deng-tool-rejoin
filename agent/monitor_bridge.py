"""DENG Tool: Rejoin APK — outbound monitor bridge from Termux agent to backend.

This module is opt-in and isolated. It does NOT touch the supervisor or any
launch logic. It runs in its own background thread, periodically asks a
provided ``status_provider`` for a *safe* per-package snapshot, scrubs the
payload of any sensitive fields, and POSTs it to the DENG Tool backend.

Activation
----------
* Disabled by default. Enable by setting env var ``DENG_MONITOR_BRIDGE_ENABLED=1``.
* Requires ``DENG_MONITOR_BRIDGE_URL`` (defaults to ``https://tool.deng.my.id``).
* Requires a bridge token issued by the backend after license verification,
  passed via ``DENG_MONITOR_BRIDGE_TOKEN`` or the constructor.

Safety contract
---------------
* Never sends: license key, raw HWID, private URL, Roblox cookies, tokens,
  Supabase secrets, bot token, monitor bridge secret, stack traces, full
  internal config, filesystem paths.
* Private URL is only reported as ``private_url_configured: bool``.
* All network failures are swallowed; main monitoring keeps running.
* Backoff with jitter prevents log spam when backend is offline.
* HTTPS only in production (HTTP allowed for ``DENG_MONITOR_BRIDGE_INSECURE=1``
  to support local backend testing).

This module has zero hard dependencies beyond the standard library so it
will not break Termux installs that lack ``requests`` or ``websocket-client``.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("deng.monitor_bridge")

# ── Defaults / env tunables ─────────────────────────────────────────────────
DEFAULT_BRIDGE_URL = "https://tool.deng.my.id"
DEFAULT_PUSH_INTERVAL_SECONDS = 2.0
DEFAULT_SNAPSHOT_INTERVAL_SECONDS = 30
MIN_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 120.0
MAX_PAYLOAD_BYTES = 32 * 1024          # 32 KB lightweight status JSON
# v1.0.3 hotfix: raised from 1.5 MB → 3 MB. Real Samsung cloud phones
# (A51 1080×2400) routinely produce 1.8–2.5 MB PNG screencaps; the old
# limit silently dropped every upload, which is why the APK Snapshot
# tab kept showing "No snapshot yet" forever even though screencap was
# succeeding. Backend `MAX_SNAPSHOT_BYTES` is 5 MB so 3 MB is well
# under the server cap.
MAX_SNAPSHOT_BYTES = 3_000_000          # 3 MB image limit
MAX_PACKAGES_PER_PUSH = 64

# Allowed states (mirrors agent.supervisor STATUS_* but kept local on purpose
# so the bridge does not depend on supervisor imports).
# v1.0.4: canonical 5 APK-visible states — Dead, Launching, Joining,
# Online, No Heartbeat. Legacy supervisor vocabulary (Relaunching,
# Reconnecting, Background, etc.) is still accepted because the bridge
# is permissive at the wire level — the autostart mapper in
# `monitor_autostart._SUPERVISOR_TO_PUBLIC_STATE` is what collapses
# everything down to the public 5 before the bytes leave the device.
# "In-Lobby" is intentionally absent everywhere now.
ALLOWED_STATES = frozenset({
    "Online", "Dead", "Relaunching", "No Heartbeat",
    "Launching", "Joining",
    "Unknown", "Offline", "Preparing",
    "Background", "Reconnecting", "Warning", "Failed",
    "Closed", "Launched", "Disconnected",
    "In Server", "Lobby", "Join Unconfirmed",
    "Join Failed", "Wrong Game / Wrong Server",
})

# Sensitive substrings — if a key or value contains any of these (case-
# insensitive), the field is dropped before sending.
_SENSITIVE_KEY_FRAGMENTS = (
    "secret", "token", "password", "passwd", "license_key", "licensekey",
    "key_value", "key_full", "cookie", "roblosecurity", "hwid", "fingerprint",
    "private_url", "private_server", "auth", "bearer", "credential",
    "supabase", "discord_bot", "bot_token",
)


def _is_sensitive_key(name: str) -> bool:
    n = name.lower()
    return any(frag in n for frag in _SENSITIVE_KEY_FRAGMENTS)


def _scrub(value: Any, _depth: int = 0) -> Any:
    """Recursively scrub a value of sensitive content. Returns a JSON-safe copy."""
    if _depth > 6:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, (list, tuple)):
        return [_scrub(v, _depth + 1) for v in value][:64]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                continue
            if _is_sensitive_key(k):
                continue
            out[k] = _scrub(v, _depth + 1)
        return out
    return None


def _coerce_state(state: Any) -> str:
    if not isinstance(state, str):
        return "Unknown"
    if state in ALLOWED_STATES:
        return state
    return "Unknown"


def _safe_package_entry(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Build a safe per-package status dict from a raw provider entry."""
    if not isinstance(raw, dict):
        return None
    package = raw.get("package") or raw.get("package_name")
    if not isinstance(package, str) or not package:
        return None
    if _is_sensitive_key(package):
        return None

    def _int(name: str, default: int = 0, *, lo: int = 0, hi: int = 10_000_000) -> int:
        try:
            n = int(raw.get(name) or 0)
        except (TypeError, ValueError):
            n = default
        return max(lo, min(hi, n))

    def _optstr(name: str, *, limit: int = 64) -> str | None:
        v = raw.get(name)
        if v is None:
            return None
        if not isinstance(v, str):
            return None
        return v[:limit]

    def _opttime(name: str) -> float | None:
        v = raw.get(name)
        if v in (None, 0):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "package": package[:128],
        "display_name": _optstr("display_name", limit=64),
        "username": _optstr("username", limit=64),
        "state": _coerce_state(raw.get("state")),
        "ram_mb": _int("ram_mb", lo=0, hi=65536),
        "runtime_seconds": _int("runtime_seconds", lo=0, hi=60 * 60 * 24 * 30),
        "restart_count": _int("restart_count", lo=0, hi=1_000_000),
        # PID is included only if explicitly safe (small positive int).
        "pid": _int("pid", lo=0, hi=2_000_000) or None,
        "private_url_configured": bool(raw.get("private_url_configured")),
        "safe_error_reason": _optstr("safe_error_reason", limit=200),
        "last_launch_at": _opttime("last_launch_at"),
        "last_heartbeat_at": _opttime("last_heartbeat_at"),
        "last_state_change_at": _opttime("last_state_change_at"),
    }


def build_safe_payload(
    *,
    tool_version: str,
    channel: str,
    packages: list[dict[str, Any]],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Public helper used by tests to validate the safe-payload contract."""
    safe_pkgs: list[dict[str, Any]] = []
    for entry in packages[:MAX_PACKAGES_PER_PUSH]:
        safe = _safe_package_entry(entry)
        if safe is not None:
            safe_pkgs.append(safe)

    payload: dict[str, Any] = {
        "schema": 1,
        "tool_version": str(tool_version or "")[:32],
        "channel": str(channel or "stable")[:16],
        "captured_at": time.time(),
        "packages": safe_pkgs,
    }
    if extra:
        payload["extra"] = _scrub(extra)
    return payload


# ── Bridge runtime ──────────────────────────────────────────────────────────


StatusProvider = Callable[[], dict[str, Any]]
SnapshotProvider = Callable[[], tuple[bytes, str] | None]


@dataclass
class BridgeConfig:
    bridge_url: str = DEFAULT_BRIDGE_URL
    token: str = ""
    push_interval_seconds: float = DEFAULT_PUSH_INTERVAL_SECONDS
    snapshot_interval_seconds: int = DEFAULT_SNAPSHOT_INTERVAL_SECONDS
    insecure: bool = False
    user_agent: str = "DENG-Tool-Monitor-Bridge/1.0"
    enabled: bool = False

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        env = os.environ.get
        return cls(
            bridge_url=(env("DENG_MONITOR_BRIDGE_URL") or DEFAULT_BRIDGE_URL).rstrip("/"),
            token=env("DENG_MONITOR_BRIDGE_TOKEN") or "",
            push_interval_seconds=float(env("DENG_MONITOR_PUSH_INTERVAL") or DEFAULT_PUSH_INTERVAL_SECONDS),
            snapshot_interval_seconds=int(env("DENG_MONITOR_SNAPSHOT_INTERVAL") or DEFAULT_SNAPSHOT_INTERVAL_SECONDS),
            insecure=str(env("DENG_MONITOR_BRIDGE_INSECURE") or "").lower() in {"1", "true", "yes"},
            enabled=str(env("DENG_MONITOR_BRIDGE_ENABLED") or "").lower() in {"1", "true", "yes"},
        )


@dataclass
class BridgeState:
    connected: bool = False
    last_push_at: float | None = None
    last_push_result: str | None = None
    last_error: str | None = None
    backoff: float = MIN_BACKOFF_SECONDS
    consecutive_failures: int = 0
    snapshot_last_sent_at: float = 0.0
    snapshot_last_result: str | None = None
    # v1.0.4 — extra snapshot diagnostics that propagate to the APK via
    # the `bridge_status` block on each /push. Surfacing these is what
    # finally fixed the "Waiting for first snapshot…" forever bug from
    # v1.0.3: the user can now SEE whether the failure is capture
    # (screencap not installed, permission denied) or upload (HTTP 401,
    # timeout) without needing to SSH into the cloud phone.
    snapshot_last_bytes: int = 0
    snapshot_last_error: str | None = None
    snapshot_last_upload_status: str | None = None  # "ok" | "http_NNN" | "network_error"
    snapshot_provider_called_count: int = 0
    screencap_available: bool | None = None  # None until first attempt
    monitor_enabled_remote: bool = True
    lock: threading.Lock = field(default_factory=threading.Lock)

    def to_push_status(self) -> dict[str, Any]:
        """Public, secret-free view of bridge state for the /push payload."""
        return {
            "snapshot_last_result": self.snapshot_last_result,
            "snapshot_last_bytes": int(self.snapshot_last_bytes or 0),
            "snapshot_last_error": (self.snapshot_last_error or None),
            "snapshot_last_upload_status": (self.snapshot_last_upload_status or None),
            "snapshot_provider_called_count": int(self.snapshot_provider_called_count or 0),
            "screencap_available": self.screencap_available,
            "last_push_result": (self.last_push_result or None),
        }


class MonitorBridge:
    """Outbound HTTPS push bridge — runs in a daemon thread.

    Usage::

        bridge = MonitorBridge(
            config=BridgeConfig.from_env(),
            status_provider=my_status_fn,     # returns dict with packages/version/channel
            snapshot_provider=my_snapshot_fn, # returns (bytes, mime) or None
        )
        bridge.start()
        ...
        bridge.stop()

    The bridge never raises; failures are recorded in ``self.state``.
    """

    def __init__(
        self,
        *,
        config: BridgeConfig,
        status_provider: StatusProvider,
        snapshot_provider: SnapshotProvider | None = None,
        on_unauthorized: Callable[[int], None] | None = None,
    ) -> None:
        self.config = config
        self.status_provider = status_provider
        self.snapshot_provider = snapshot_provider
        self.state = BridgeState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_unauthorized = on_unauthorized

    # ── Lifecycle ────────────────────────────────────────────────────────
    def start(self) -> bool:
        if not self.config.enabled:
            logger.info("monitor_bridge disabled (DENG_MONITOR_BRIDGE_ENABLED not set)")
            return False
        if not self.config.token:
            logger.warning("monitor_bridge has no token; not starting")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="deng-monitor-bridge", daemon=True
        )
        self._thread.start()
        logger.info("monitor_bridge started url=%s", self.config.bridge_url)
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=timeout)
        self._thread = None

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── Main loop ────────────────────────────────────────────────────────
    def _run(self) -> None:
        push_interval = max(0.5, float(self.config.push_interval_seconds))
        next_push = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_push:
                next_push = now + push_interval
                try:
                    self._tick()
                except Exception as exc:  # noqa: BLE001
                    self._record_failure(f"tick_error: {exc.__class__.__name__}")
            # Sleep responsively
            self._stop.wait(timeout=0.25)

    def _tick(self) -> None:
        # Build status payload
        try:
            raw = self.status_provider() or {}
        except Exception as exc:  # noqa: BLE001
            self._record_failure(f"status_provider: {exc.__class__.__name__}")
            return

        packages = raw.get("packages") or []
        payload = build_safe_payload(
            tool_version=str(raw.get("tool_version") or ""),
            channel=str(raw.get("channel") or "stable"),
            packages=list(packages) if isinstance(packages, list) else [],
            extra=raw.get("extra") if isinstance(raw.get("extra"), dict) else None,
        )
        # v1.0.4 — attach the bridge's self-view so the APK Snapshot tab
        # can show a real reason ("capture_failed: screencap unavailable")
        # instead of "Waiting for first snapshot…" forever. The block is
        # tiny (~7 string/int fields) so it stays well under
        # MAX_PAYLOAD_BYTES even with the package list.
        try:
            payload["bridge_status"] = self.state.to_push_status()
        except Exception:  # noqa: BLE001
            pass

        ok = self._post_json("/api/monitor/bridge/push", payload)
        if not ok:
            return

        # Snapshot upload (interval-gated, off=0)
        interval = int(self.config.snapshot_interval_seconds)
        if interval > 0 and self.snapshot_provider:
            elapsed = time.time() - self.state.snapshot_last_sent_at
            if elapsed >= interval:
                self.state.snapshot_provider_called_count += 1
                snap: tuple[bytes, str] | None = None
                capture_error: str | None = None
                try:
                    snap = self.snapshot_provider()
                except Exception as exc:  # noqa: BLE001
                    capture_error = f"{exc.__class__.__name__}"
                    logger.debug("snapshot_provider failed: %s", exc)
                if snap:
                    data, mime = snap
                    # We at least got bytes from the provider — screencap works.
                    self.state.screencap_available = True
                    self.state.snapshot_last_bytes = len(data) if data else 0
                    if not data:
                        self.state.snapshot_last_result = "capture_failed"
                        self.state.snapshot_last_error = "empty_bytes"
                        self.state.snapshot_last_sent_at = time.time() - max(0, interval - 5)
                    elif len(data) > MAX_SNAPSHOT_BYTES:
                        self.state.snapshot_last_result = "capture_failed"
                        self.state.snapshot_last_error = (
                            f"image_too_large_{len(data)}_max_{MAX_SNAPSHOT_BYTES}"
                        )
                        self.state.snapshot_last_sent_at = time.time() - max(0, interval - 5)
                    else:
                        upload_ok = self._post_binary(
                            "/api/monitor/bridge/snapshot",
                            data,
                            content_type=mime or "image/png",
                        )
                        if upload_ok:
                            self.state.snapshot_last_sent_at = time.time()
                            self.state.snapshot_last_result = "success"
                            self.state.snapshot_last_error = None
                            self.state.snapshot_last_upload_status = "ok"
                        else:
                            self.state.snapshot_last_result = "upload_failed"
                            self.state.snapshot_last_upload_status = (
                                self.state.last_error or "network_error"
                            )
                else:
                    # Provider returned None — screencap missing / permission
                    # denied / crashed. Surface the reason in bridge_status
                    # so the APK shows it instead of a silent placeholder.
                    self.state.screencap_available = False
                    self.state.snapshot_last_result = "capture_failed"
                    self.state.snapshot_last_error = (
                        capture_error or "screencap_unavailable"
                    )
                    # Don't spin on a broken capture; back off slightly.
                    self.state.snapshot_last_sent_at = time.time() - max(0, interval - 5)

    # ── HTTP helpers ─────────────────────────────────────────────────────
    def _validate_url(self) -> bool:
        url = self.config.bridge_url
        if not url:
            return False
        if not self.config.insecure and not url.startswith("https://"):
            self._record_failure("bridge_url_not_https")
            return False
        return True

    def _post_json(self, path: str, body: dict[str, Any]) -> bool:
        if not self._validate_url():
            return False
        try:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            self._record_failure(f"json_encode: {exc}")
            return False
        if len(data) > MAX_PAYLOAD_BYTES:
            self._record_failure("payload_too_large")
            return False
        return self._send(path, data, "application/json")

    def _post_binary(self, path: str, data: bytes, *, content_type: str) -> bool:
        if not self._validate_url():
            return False
        if len(data) > MAX_SNAPSHOT_BYTES:
            return False
        return self._send(path, data, content_type)

    def _send(self, path: str, data: bytes, content_type: str) -> bool:
        url = f"{self.config.bridge_url}{path}"
        # All HTTPS goes through safe_http.post_raw, which uses curl as a
        # SUBPROCESS on Termux. Why: real-device probe ``p-d1cb86fd89``
        # showed a SIGSEGV in ``EVP_PKEY_generate`` / ``EVP_PKEY_Q_keygen``
        # inside ``libssl.so.3`` when the bridge's daemon thread called
        # ``urllib.request.urlopen`` in-process. With curl-subprocess the
        # OpenSSL crash kills only the curl child and the bridge thread
        # records a controlled failure.
        try:
            from . import safe_http  # local import keeps tests light
            status, body = safe_http.post_raw(
                url,
                data,
                content_type=content_type,
                headers={
                    "Authorization": f"Bearer {self.config.token}",
                    "User-Agent": self.config.user_agent,
                },
                timeout=8,
            )
        except safe_http.SafeHttpNetworkError as exc:
            self._record_failure(f"net_{exc.__class__.__name__}")
            return False
        except Exception as exc:  # noqa: BLE001
            self._record_failure(f"send_{exc.__class__.__name__}")
            return False

        if 200 <= status < 300:
            self._record_success()
            # The /push endpoint echoes the device's current settings
            # (snapshot interval etc.) so the bridge can react without
            # waiting for a Termux restart.
            if path.endswith("/api/monitor/bridge/push") and body:
                try:
                    payload = json.loads(body.decode("utf-8"))
                    settings = payload.get("settings") if isinstance(payload, dict) else None
                    if isinstance(settings, dict):
                        self._apply_remote_settings(settings)
                except (ValueError, UnicodeDecodeError):
                    pass
            return True
        if status in (401, 403):
            # Token revoked / unauthorized: notify listeners so they can
            # reissue and reconnect on the next tick.
            try:
                cb = self._on_unauthorized
                if cb is not None:
                    cb(status)
            except Exception:  # noqa: BLE001
                pass
        self._record_failure(f"http_{status}")
        return False

    # ── Dynamic remote settings ──────────────────────────────────────────
    def _apply_remote_settings(self, settings: dict[str, Any]) -> None:
        """Update the bridge's local snapshot interval from a /push echo.

        The backend stores per-device monitor settings; this lets the APK
        change snapshot interval at runtime without requiring the Termux
        user to relaunch ``deng-rejoin``.
        """
        interval = settings.get("snapshot_interval_seconds")
        if isinstance(interval, (int, float)):
            iv = max(0, min(3600, int(interval)))
            if iv != self.config.snapshot_interval_seconds:
                logger.info("monitor_bridge snapshot_interval updated %s -> %s",
                            self.config.snapshot_interval_seconds, iv)
                self.config.snapshot_interval_seconds = iv
        enabled = settings.get("monitor_enabled")
        if isinstance(enabled, bool):
            self.state.monitor_enabled_remote = enabled

    # ── State bookkeeping ────────────────────────────────────────────────
    def _record_success(self) -> None:
        with self.state.lock:
            self.state.connected = True
            self.state.last_push_at = time.time()
            self.state.last_push_result = "success"
            self.state.last_error = None
            self.state.consecutive_failures = 0
            self.state.backoff = MIN_BACKOFF_SECONDS

    def _record_failure(self, reason: str) -> None:
        with self.state.lock:
            self.state.connected = False
            self.state.last_error = reason
            self.state.last_push_result = "error"
            self.state.consecutive_failures += 1
            # Exponential backoff with jitter, capped
            self.state.backoff = min(
                MAX_BACKOFF_SECONDS,
                self.state.backoff * 2 + random.uniform(0, 1),  # noqa: S311
            )
        # Throttle log to avoid spam
        if self.state.consecutive_failures in (1, 5) or self.state.consecutive_failures % 30 == 0:
            logger.warning(
                "monitor_bridge push failed reason=%s consecutive=%d",
                reason, self.state.consecutive_failures,
            )


__all__ = [
    "ALLOWED_STATES",
    "BridgeConfig",
    "BridgeState",
    "MAX_PACKAGES_PER_PUSH",
    "MAX_PAYLOAD_BYTES",
    "MAX_SNAPSHOT_BYTES",
    "MonitorBridge",
    "build_safe_payload",
]
