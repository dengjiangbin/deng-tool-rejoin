#!/usr/bin/env python3
"""Capture real HTTP webhook JSON payloads via a local listener."""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

_PROJECT = Path(__file__).resolve().parents[1]
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))

from agent import webhook  # noqa: E402

URL = "https://discord.com/api/webhooks/9999999999/live-capture-token"
LOCAL_URL = "http://127.0.0.1:18765/api/webhooks/9999999999/live-capture-token"
PKG = "com.moons.litesc"
USER = "denghub2"
TAG_ID = "123456789012345678"
CAPTURED: list[dict[str, Any]] = []


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {"raw": raw.decode("utf-8", errors="replace")}
        CAPTURED.append(payload)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        body = json.dumps({"id": f"live-{len(CAPTURED)}"}).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _cfg(tag_enabled: bool) -> dict[str, Any]:
    cfg = {
        "webhook_mode": "new_post",
        "webhook_enabled": True,
        "webhook_url": LOCAL_URL,
        "device_name": "LiveCapture",
        "roblox_packages": [{"package": PKG, "account_username": USER}],
        "webhook_tag_enabled": tag_enabled,
        "webhook_tag_user_id": TAG_ID if tag_enabled else "",
    }
    return cfg


def _validate_local(url: str | None) -> str:
    cleaned = str(url or "").strip()
    if cleaned.startswith("http://127.0.0.1"):
        return cleaned
    return webhook.validate_webhook_url(cleaned)


def main() -> int:
    webhook.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lifecycle_path = webhook.DATA_DIR / "package-lifecycle-webhook-state.json"
    lifecycle_path.write_text(
        json.dumps({"packages": {PKG: {"alive_since": time.time() - 192.0}}}),
        encoding="utf-8",
    )
    server = HTTPServer(("127.0.0.1", 18765), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    with patch.object(webhook, "validate_webhook_url", side_effect=_validate_local):
        def _send(cfg: dict[str, Any], event: str, runtime: float | None = None) -> None:
            if event == "package_dead":
                with patch("agent.config.load_config", return_value=dict(cfg)):
                    webhook.send_package_lifecycle_alert(
                        cfg,
                        event=event,
                        package=PKG,
                        username=USER,
                        runtime_seconds=runtime,
                    )
            elif event == "package_recovered":
                with patch("agent.config.load_config", return_value=dict(cfg)):
                    webhook.send_package_lifecycle_alert(
                        cfg,
                        event=event,
                        package=PKG,
                        username=USER,
                    )
            else:
                snapshot = [{
                    "package": PKG,
                    "username": USER,
                    "status": "Online",
                    "online_since": time.time() - 125.0,
                }]
                payload = webhook.build_status_embed_payload(
                    cfg,
                    supervisor_snapshot=snapshot,
                    app_stats={PKG: {"online": True}},
                )
                webhook._discord_json_request(LOCAL_URL, payload, "POST")

        _send(_cfg(True), "package_dead", 45.0)
        _send(_cfg(False), "package_dead", 45.0)
        _send(_cfg(True), "package_recovered")
        _send(_cfg(True), "monitor")

    server.shutdown()
    proof_path = _PROJECT / "data" / "webhook_live_capture_proof.json"
    proof_path.write_text(json.dumps(CAPTURED, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(CAPTURED)} payloads -> {proof_path}")
    for idx, payload in enumerate(CAPTURED, start=1):
        title = (payload.get("embeds") or [{}])[0].get("title", "")
        runtime = webhook._lifecycle_runtime_from_payload(payload)
        title_safe = title.encode("ascii", "backslashreplace").decode("ascii")
        print(
            f"{idx}: title={title_safe!r} content={payload.get('content', '')!r} "
            f"mentions={payload.get('allowed_mentions')} runtime_present={runtime[0]} runtime={runtime[1]!r}"
        )
    return 0 if len(CAPTURED) == 4 else 1


if __name__ == "__main__":
    raise SystemExit(main())
