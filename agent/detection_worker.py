"""Loopback detection worker: the in-game Lua push channel receiver.

Why this exists
───────────────
Scraping ``dumpsys`` / ``uiautomator`` / ``logcat`` to learn whether a Roblox
clone is in-game (and *which* server it joined) is slow (minutes per package)
and unreliable for GL-rendered overlays.  A LocalScript running *inside* the
game is the cheapest, most authoritative truth: while the player is genuinely
in a server, the script POSTs a tiny heartbeat with ``placeId`` / ``jobId`` /
``universeId`` every few seconds; the instant the player dies, disconnects, is
kicked, or hits a captcha screen (pre-game), the DataModel unloads and the
heartbeats stop.

This module runs a single, shared, threaded HTTP server bound to
``127.0.0.1`` (loopback — reachable from the Roblox app on the same device but
not from the network).  It keeps the latest heartbeat per Android package in
memory; the supervisor reads it via :func:`get_heartbeat` to short-circuit the
slow detection path.

Design constraints
──────────────────
* **Never crash the caller.**  Every public entry point is best-effort; if the
  port can't bind or anything fails, detection silently falls back to the
  legacy scrape path.
* **Single instance per process.**  ``start_detection_worker`` is idempotent.
* **No outside exposure.**  Loopback bind + optional shared-token check.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

try:  # constants are optional at import time (keeps unit tests light)
    from .constants import (
        DEFAULT_DETECTION_WORKER_PORT,
        DETECTION_WORKER_PORT_PATH,
        DETECTION_WORKER_TOKEN_PATH,
    )
except Exception:  # noqa: BLE001 - fall back to literals if constants unavailable
    DEFAULT_DETECTION_WORKER_PORT = 52789
    DETECTION_WORKER_PORT_PATH = None  # type: ignore[assignment]
    DETECTION_WORKER_TOKEN_PATH = None  # type: ignore[assignment]

_HOST = "127.0.0.1"
_MAX_BODY_BYTES = 8192


def detection_worker_port() -> int:
    """Port the detector.lua heartbeats target.  Same value the agent binds.

    Overridable via ``DENG_REJOIN_DETECTION_PORT`` (must match on both sides).
    """
    raw = (os.environ.get("DENG_REJOIN_DETECTION_PORT") or "").strip()
    if raw:
        try:
            val = int(raw)
            if 1 <= val <= 65535:
                return val
        except ValueError:
            pass
    return int(DEFAULT_DETECTION_WORKER_PORT)


@dataclass
class _Heartbeat:
    package: str
    alive: bool = True
    place_id: int = 0
    root_place_id: int = 0
    universe_id: int = 0
    job_id: str = ""
    user: str = ""
    received_monotonic: float = field(default_factory=time.monotonic)
    received_wall: float = field(default_factory=time.time)

    def as_public(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "alive": self.alive,
            "placeId": self.place_id,
            "rootPlaceId": self.root_place_id,
            "universeId": self.universe_id,
            "jobId": self.job_id,
            "user": self.user,
            "age_seconds": max(0.0, time.monotonic() - self.received_monotonic),
            "received_wall": self.received_wall,
        }


class _Registry:
    """Thread-safe latest-heartbeat-per-package store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._beats: dict[str, _Heartbeat] = {}

    def update(self, hb: _Heartbeat) -> None:
        if not hb.package:
            return
        with self._lock:
            self._beats[hb.package] = hb

    def get(self, package: str) -> dict[str, Any] | None:
        with self._lock:
            hb = self._beats.get(str(package or "").strip())
            return hb.as_public() if hb is not None else None

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {pkg: hb.as_public() for pkg, hb in self._beats.items()}


class _DetectionWorker:
    def __init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port: int | None = None
        self._token: str = ""
        self._registry = _Registry()
        self._logger: Any = None
        self._start_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def start(self, logger: Any = None, *, token: str | None = None) -> int | None:
        with self._start_lock:
            if self._server is not None and self._port is not None:
                return self._port
            self._logger = logger
            self._token = token if token is not None else _get_or_create_token()
            port = detection_worker_port()
            registry = self._registry
            worker_token = self._token

            class _Handler(BaseHTTPRequestHandler):
                # Silence default stderr access logging.
                def log_message(self, *_args: Any) -> None:  # noqa: N802
                    return

                def _send(self, code: int, payload: dict[str, Any]) -> None:
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except Exception:  # noqa: BLE001
                        pass

                def do_GET(self) -> None:  # noqa: N802
                    if self.path.startswith("/ping"):
                        self._send(200, {"ok": True, "service": "deng-detection-worker"})
                    else:
                        self._send(404, {"ok": False})

                def do_POST(self) -> None:  # noqa: N802
                    if not (self.path.startswith("/h") or self.path.startswith("/heartbeat")):
                        self._send(404, {"ok": False})
                        return
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                    except (TypeError, ValueError):
                        length = 0
                    if length <= 0 or length > _MAX_BODY_BYTES:
                        self._send(400, {"ok": False, "error": "bad length"})
                        return
                    try:
                        raw = self.rfile.read(length)
                        data = json.loads(raw.decode("utf-8", "replace"))
                    except Exception:  # noqa: BLE001
                        self._send(400, {"ok": False, "error": "bad json"})
                        return
                    if not isinstance(data, dict):
                        self._send(400, {"ok": False, "error": "bad body"})
                        return
                    if worker_token:
                        supplied = str(data.get("k") or data.get("token") or "")
                        if supplied != worker_token:
                            self._send(403, {"ok": False, "error": "forbidden"})
                            return
                    hb = _heartbeat_from_payload(data)
                    if hb is None:
                        self._send(400, {"ok": False, "error": "no package"})
                        return
                    registry.update(hb)
                    self._send(200, {"ok": True})

            try:
                server = ThreadingHTTPServer((_HOST, port), _Handler)
            except OSError as exc:
                _log(self._logger, "warning", "detection_worker_bind_failed", port=port, error=str(exc))
                return None
            server.daemon_threads = True
            thread = threading.Thread(
                target=server.serve_forever,
                name="deng-detection-worker",
                daemon=True,
            )
            thread.start()
            self._server = server
            self._thread = thread
            self._port = port
            _write_port_file(port)
            _log(self._logger, "info", "detection_worker_started", port=port)
            return port

    def stop(self) -> None:
        with self._start_lock:
            server = self._server
            self._server = None
            self._thread = None
            self._port = None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:  # noqa: BLE001
                pass

    # -- accessors ---------------------------------------------------------
    @property
    def port(self) -> int | None:
        return self._port

    @property
    def token(self) -> str:
        return self._token

    def get_heartbeat(self, package: str) -> dict[str, Any] | None:
        return self._registry.get(package)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return self._registry.snapshot()


_WORKER = _DetectionWorker()


# -- module-level helpers --------------------------------------------------
def _coerce_int(value: Any) -> int:
    try:
        ival = int(value)
    except (TypeError, ValueError):
        return 0
    return ival if ival > 0 else 0


def _heartbeat_from_payload(data: dict[str, Any]) -> _Heartbeat | None:
    package = str(data.get("pkg") or data.get("package") or "").strip()
    if not package:
        return None
    alive_raw = data.get("alive", True)
    alive = bool(alive_raw) if not isinstance(alive_raw, str) else alive_raw.strip().lower() not in {"0", "false", "no", ""}
    return _Heartbeat(
        package=package[:128],
        alive=alive,
        place_id=_coerce_int(data.get("placeId") or data.get("place_id")),
        root_place_id=_coerce_int(data.get("rootPlaceId") or data.get("root_place_id")),
        universe_id=_coerce_int(data.get("universeId") or data.get("gameId") or data.get("universe_id")),
        job_id=str(data.get("jobId") or data.get("job_id") or "").strip()[:64],
        user=str(data.get("user") or "").strip()[:64],
    )


def _get_or_create_token() -> str:
    path = DETECTION_WORKER_TOKEN_PATH
    if path is None:
        return secrets.token_hex(12)
    try:
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
    except OSError:
        pass
    token = secrets.token_hex(12)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
    except OSError:
        pass
    return token


def current_token() -> str:
    """Stable per-install token baked into deng.txt and required on POSTs."""
    if _WORKER.token:
        return _WORKER.token
    return _get_or_create_token()


def _write_port_file(port: int) -> None:
    path = DETECTION_WORKER_PORT_PATH
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(port), encoding="utf-8")
    except OSError:
        pass


def _log(logger: Any, level: str, event: str, **fields: Any) -> None:
    if logger is None:
        return
    try:
        from .logger import log_event

        log_event(logger, level, event, **fields)
    except Exception:  # noqa: BLE001
        pass


# -- public API ------------------------------------------------------------
def start_detection_worker(logger: Any = None, *, token: str | None = None) -> int | None:
    """Start (idempotently) the loopback heartbeat receiver.  Best-effort."""
    try:
        return _WORKER.start(logger, token=token)
    except Exception as exc:  # noqa: BLE001
        _log(logger, "warning", "detection_worker_start_error", error=str(exc))
        return None


def stop_detection_worker() -> None:
    try:
        _WORKER.stop()
    except Exception:  # noqa: BLE001
        pass


def get_heartbeat(package: str) -> dict[str, Any] | None:
    """Latest heartbeat for ``package`` (with ``age_seconds``) or ``None``."""
    try:
        return _WORKER.get_heartbeat(package)
    except Exception:  # noqa: BLE001
        return None


def active_port() -> int | None:
    return _WORKER.port


def heartbeat_snapshot() -> dict[str, dict[str, Any]]:
    try:
        return _WORKER.snapshot()
    except Exception:  # noqa: BLE001
        return {}
