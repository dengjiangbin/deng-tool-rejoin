"""Local in-game Lua heartbeat receiver for DENG Rejoin.

Roblox executor scripts POST/GET a lightweight ping to this server so the
watchdog knows the game client is alive without polling external APIs.
"""

from __future__ import annotations

import copy
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import validate_package_name


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9999
HEARTBEAT_TTL_SECONDS = 30.0

DEFAULT_HEARTBEAT_RECORD: dict[str, float | int] = {
    "timestamp": 0.0,
    "count": 0,
    "total_count": 0,
}


def normalize_heartbeat_record(raw: Any) -> dict[str, float | int]:
    """Return a safe heartbeat record with numeric timestamp/count fields."""
    if not isinstance(raw, dict):
        return copy.copy(DEFAULT_HEARTBEAT_RECORD)
    try:
        timestamp = float(raw.get("timestamp") or 0.0)
    except (TypeError, ValueError):
        timestamp = 0.0
    try:
        count = int(raw.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    try:
        total_count = int(raw.get("total_count") or 0)
    except (TypeError, ValueError):
        total_count = 0
    return {
        "timestamp": max(0.0, timestamp),
        "count": max(0, count),
        "total_count": max(0, total_count),
    }


class LuaHeartbeatServer:
    """Background HTTP server recording per-package Lua heartbeat timestamps."""

    __slots__ = (
        "_allowed_packages",
        "_ever_seen",
        "_host",
        "_httpd",
        "_lock",
        "_port",
        "_records",
        "_thread",
        "_ttl",
    )

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        allowed_packages: set[str] | frozenset[str] | None = None,
        ttl_seconds: float = HEARTBEAT_TTL_SECONDS,
    ) -> None:
        self._host = str(host or DEFAULT_HOST)
        self._port = int(port)
        self._ttl = float(ttl_seconds)
        self._allowed_packages = {
            str(p).strip() for p in (allowed_packages or ()) if str(p).strip()
        }
        self._records: dict[str, dict[str, float | int]] = {}
        self._ever_seen: set[str] = set()
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        if self._httpd is not None:
            return int(self._httpd.server_address[1])
        return self._port

    @property
    def heartbeats(self) -> dict[str, float]:
        """Read-only snapshot of last heartbeat monotonic timestamps."""
        with self._lock:
            return {
                pkg: float(rec["timestamp"])
                for pkg, rec in self._records.items()
                if float(rec.get("timestamp") or 0.0) > 0.0
            }

    def get_record(self, package: str) -> dict[str, float | int]:
        """Return a copy of the heartbeat record for ``package``."""
        pkg = str(package or "").strip()
        with self._lock:
            return normalize_heartbeat_record(self._records.get(pkg))

    def record_heartbeat(self, package: str) -> bool:
        """Record a heartbeat for ``package``. Returns False when rejected."""
        try:
            pkg = validate_package_name(str(package or "").strip())
        except Exception:  # noqa: BLE001
            return False
        if self._allowed_packages and pkg not in self._allowed_packages:
            return False
        now = time.monotonic()
        with self._lock:
            rec = self._records.setdefault(pkg, copy.copy(DEFAULT_HEARTBEAT_RECORD))
            rec["timestamp"] = now
            rec["count"] = int(rec.get("count") or 0) + 1
            rec["total_count"] = int(rec.get("total_count") or 0) + 1
            self._ever_seen.add(pkg)
        return True

    def ping_count(self, package: str, *, window: bool = True) -> int:
        """Return heartbeat ping count for ``package`` (window = current execution)."""
        rec = self.get_record(package)
        if window:
            return int(rec["count"])
        return int(rec["total_count"])

    def reset_window_ping_count(self, package: str) -> None:
        """Reset per-window ping counter when a clone relaunches."""
        pkg = str(package or "").strip()
        if not pkg:
            return
        with self._lock:
            if pkg in self._records:
                self._records[pkg]["count"] = 0

    def is_fresh(self, package: str, *, ttl_seconds: float | None = None) -> bool:
        ttl = float(self._ttl if ttl_seconds is None else ttl_seconds)
        rec = self.get_record(package)
        ts = float(rec["timestamp"])
        if ts <= 0.0:
            return False
        return (time.monotonic() - ts) < ttl

    def ever_seen(self, package: str) -> bool:
        with self._lock:
            return str(package or "").strip() in self._ever_seen

    def age_seconds(self, package: str) -> float | None:
        rec = self.get_record(package)
        ts = float(rec["timestamp"])
        if ts <= 0.0:
            return None
        return max(0.0, time.monotonic() - ts)

    def running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def start(self) -> None:
        if self.running():
            return

        server_ref = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path or "")
                if parsed.path not in {"/heartbeat", "/heartbeat/"}:
                    self.send_response(404)
                    self.end_headers()
                    return
                params = parse_qs(parsed.query or "")
                pkg_values = params.get("package") or params.get("pkg") or []
                pkg = str(pkg_values[0] if pkg_values else "").strip()
                if not server_ref.record_heartbeat(pkg):
                    self.send_response(400)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"invalid package")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                del format, args

        bind_port = self._port
        last_err: Exception | None = None
        for _ in range(5):
            try:
                self._httpd = ThreadingHTTPServer((self._host, bind_port), _Handler)
                break
            except OSError as exc:
                last_err = exc
                if bind_port == 0:
                    raise
                bind_port = 0
        else:
            raise last_err or OSError("failed to bind lua heartbeat server")

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            kwargs={"poll_interval": 0.5},
            name="deng-lua-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        httpd = self._httpd
        thread = self._thread
        self._httpd = None
        self._thread = None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
