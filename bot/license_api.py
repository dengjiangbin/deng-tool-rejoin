"""DENG Tool: Rejoin — Lightweight License Check API Server.

Provides HTTP endpoints so the Android/Termux client can verify and bind
device licenses against Supabase WITHOUT exposing the Supabase service-role key
to the client.

Architecture
────────────
• Bot server (this module) knows the service-role key.
• Android client sends only: key, install_id_hash, device_model, app_version.
• The client NEVER sees Supabase credentials.
• All sensitive operations happen server-side here.

Endpoints
─────────
  GET  /api/license/health
       Returns {"status": "ok", "version": "...", "store": "..."}.

  POST /api/license/check
       Body: {"key": "DENG-...", "install_id_hash": "...",
               "device_model": "...", "app_version": "...",
               "device_label": ""}
       Returns: {"result": "active|wrong_device|not_found|...", "message": "..."}

  POST /api/license/heartbeat
       Same body as /check.
       Just updates last_seen_at if the device is already bound.
       Returns: {"result": "active|wrong_device|not_found|...", "message": "..."}

Environment variables
─────────────────────
  LICENSE_API_ENABLED        Set to "true" to enable (default: false).
  LICENSE_API_HOST           Bind host (default: 127.0.0.1).
  LICENSE_API_PORT           Port (default: 8787).
  LICENSE_API_SHARED_SECRET  Optional bearer token for client → server auth.
                             If set, every request must include:
                             Authorization: Bearer <secret>
                             If not set, any client can call the API
                             (only safe behind a firewall/VPN/NAT).

Security notes
──────────────
• Do NOT expose LICENSE_API_PORT to the public internet without
  setting LICENSE_API_SHARED_SECRET and using HTTPS.
• The service-role key is never returned in any response.
• Full license keys are never logged.
• install_id_hash values are already hashes — not persisted raw.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("deng.rejoin.license_api")

_RESULT_MESSAGES: dict[str, str] = {
    "active": "License active.",
    "wrong_device": "This key is bound to a different device. Use HWID Reset in the Discord panel.",
    "not_found": "Key not found. Check the key and try again.",
    "revoked": "This key has been revoked.",
    "expired": "This key has expired.",
    "inactive": "License inactive.",
    "missing_key": "No license key provided.",
    "server_unavailable": "License server temporarily unavailable.",
}


def _mask_key(key: str) -> str:
    """Return a masked version of the key for safe logging."""
    parts = (key or "").split("-")
    if len(parts) >= 5:
        return f"{parts[0]}-{parts[1]}...{parts[-1]}"
    return key[:8] + "..." if len(key) > 8 else "***"


def _hash_install_id(raw_id: str) -> str:
    """SHA-256 hash of a raw install_id (if the client sends unhashed; defensive)."""
    return hashlib.sha256(raw_id.encode("utf-8")).hexdigest()


def _build_response(result: str, status: int = 200) -> tuple[bytes, int]:
    message = _RESULT_MESSAGES.get(result, result)
    payload = json.dumps({"result": result, "message": message}).encode("utf-8")
    return payload, status


def _is_authorized(environ: dict) -> bool:
    """Check Bearer token if LICENSE_API_SHARED_SECRET is set."""
    secret = os.environ.get("LICENSE_API_SHARED_SECRET", "").strip()
    if not secret:
        return True  # No secret configured — open
    auth_header = environ.get("HTTP_AUTHORIZATION", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:] == secret
    return False


def _read_json_body(environ: dict) -> dict | None:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if length <= 0 or length > 8192:
            return None
        body = environ["wsgi.input"].read(length)
        return json.loads(body)
    except (ValueError, KeyError, UnicodeDecodeError):
        return None


def _wsgi_app(environ: dict, start_response):  # noqa: ANN001
    """Minimal WSGI application — no external framework required."""
    path = environ.get("PATH_INFO", "")
    method = environ.get("REQUEST_METHOD", "GET")

    def respond(body: bytes, status: int = 200, content_type: str = "application/json") -> list[bytes]:
        status_str = {200: "200 OK", 400: "400 Bad Request", 401: "401 Unauthorized", 405: "405 Method Not Allowed", 500: "500 Internal Server Error"}.get(status, f"{status} Error")
        headers = [("Content-Type", content_type), ("Content-Length", str(len(body)))]
        start_response(status_str, headers)
        return [body]

    # ── Health check ──────────────────────────────────────────────────────────
    if path == "/api/license/health":
        from agent.license_store import get_default_store
        store_mode = os.environ.get("DENG_LICENSE_STORE", "local")
        try:
            from agent.constants import VERSION
        except ImportError:
            VERSION = "unknown"
        payload = json.dumps({"status": "ok", "version": VERSION, "store": store_mode}).encode()
        return respond(payload)

    # ── Auth check (all other endpoints) ─────────────────────────────────────
    if not _is_authorized(environ):
        return respond(json.dumps({"error": "Unauthorized"}).encode(), 401)

    # ── Check / Heartbeat ─────────────────────────────────────────────────────
    if path in ("/api/license/check", "/api/license/heartbeat"):
        if method != "POST":
            return respond(json.dumps({"error": "POST required"}).encode(), 405)

        body = _read_json_body(environ)
        if not body:
            return respond(json.dumps({"error": "Invalid JSON body"}).encode(), 400)

        raw_key = (body.get("key") or "").strip()
        install_id_hash = (body.get("install_id_hash") or "").strip()
        device_model = (body.get("device_model") or "unknown")[:120]
        app_version = (body.get("app_version") or "unknown")[:40]

        if not raw_key:
            payload, status = _build_response("missing_key", 400)
            return respond(payload, status)

        # If client sends a raw (unhashed) install_id, hash it here.
        # Clients should send the hash directly; this is a fallback.
        if len(install_id_hash) != 64:  # SHA-256 hex = 64 chars
            install_id_hash = _hash_install_id(install_id_hash)

        log.info(
            "License %s for key %s device_model=%s",
            "check" if path.endswith("check") else "heartbeat",
            _mask_key(raw_key),
            device_model,
        )

        try:
            from agent.license_store import get_default_store
            store = get_default_store()
            result = store.bind_or_check_device(
                raw_key, install_id_hash, device_model, app_version
            )
        except Exception as exc:  # noqa: BLE001
            log.error("License check error: %s", exc)
            payload, status = _build_response("server_unavailable", 500)
            return respond(payload, status)

        log.info("License result: %s for key %s", result, _mask_key(raw_key))
        payload, status = _build_response(result)
        return respond(payload, status)

    # ── 404 ───────────────────────────────────────────────────────────────────
    return respond(json.dumps({"error": "Not found"}).encode(), 404 if False else 404)


def start_api_server(host: str, port: int) -> None:
    """Start the license API server using wsgiref (stdlib only, no extra deps)."""
    from wsgiref.simple_server import make_server, WSGIRequestHandler

    class _QuietHandler(WSGIRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:  # noqa: ARG002
            pass  # wsgiref logs suppressed; we use our own logger

    log.info("License API server starting on %s:%d", host, port)
    with make_server(host, port, _wsgi_app, handler_class=_QuietHandler) as httpd:
        log.info("License API server ready — http://%s:%d/api/license/health", host, port)
        httpd.serve_forever()


def maybe_start_api_thread() -> None:
    """Start the license API in a daemon thread if LICENSE_API_ENABLED=true.

    Called from bot/main.py on startup.  Does nothing if not enabled.
    """
    enabled = os.environ.get("LICENSE_API_ENABLED", "").strip().lower()
    if enabled not in ("1", "true", "yes"):
        log.debug("License API disabled (set LICENSE_API_ENABLED=true to enable).")
        return

    host = os.environ.get("LICENSE_API_HOST", "127.0.0.1").strip()
    try:
        port = int(os.environ.get("LICENSE_API_PORT", "8787"))
    except ValueError:
        log.error("LICENSE_API_PORT is not a valid integer — API not started.")
        return

    import threading
    t = threading.Thread(
        target=start_api_server,
        args=(host, port),
        daemon=True,
        name="license-api",
    )
    t.start()
    log.info("License API thread started (daemon) on %s:%d.", host, port)
