"""Tests for agent/monitor_bridge.py — safe-payload contract + bridge runtime.

These tests deliberately do NOT touch the supervisor or any package launch
code. They isolate the bridge: payload shape, sensitive-field scrubbing,
state-vocabulary clamp, payload-size guard, and offline-resilience.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import pytest

from agent.monitor_bridge import (
    ALLOWED_STATES,
    MAX_PACKAGES_PER_PUSH,
    MAX_PAYLOAD_BYTES,
    BridgeConfig,
    MonitorBridge,
    build_safe_payload,
)


def _raw_pkg(**overrides):
    base = {
        "package": "com.litec.client",
        "display_name": "Litec",
        "username": "deng1629",
        "state": "Online",
        "ram_mb": 642,
        "runtime_seconds": 8073,
        "restart_count": 2,
        "pid": 1234,
        "private_url_configured": True,
        "last_launch_at": time.time() - 1000,
        "last_heartbeat_at": time.time() - 5,
        "last_state_change_at": time.time() - 1000,
    }
    base.update(overrides)
    return base


# ── Payload shape & scrubbing ───────────────────────────────────────────────

def test_payload_includes_required_fields():
    payload = build_safe_payload(
        tool_version="1.0.0",
        channel="stable",
        packages=[_raw_pkg()],
    )
    assert payload["schema"] == 1
    assert payload["tool_version"] == "1.0.0"
    assert payload["channel"] == "stable"
    assert len(payload["packages"]) == 1
    pkg = payload["packages"][0]
    for key in (
        "package", "display_name", "username", "state",
        "ram_mb", "runtime_seconds", "restart_count", "pid",
        "private_url_configured", "safe_error_reason",
        "last_launch_at", "last_heartbeat_at", "last_state_change_at",
    ):
        assert key in pkg, f"missing field: {key}"


def test_private_url_is_only_a_boolean():
    payload = build_safe_payload(
        tool_version="1.0.0", channel="stable",
        packages=[_raw_pkg(
            private_url_configured=True,
            private_url="https://secret/server",  # sneak attempt
        )],
    )
    pkg = payload["packages"][0]
    assert pkg["private_url_configured"] is True
    assert "private_url" not in pkg


@pytest.mark.parametrize("sensitive_key,sensitive_value", [
    ("license_key", "DENG-XXXX-YYYY-ZZZZ-WWWW"),
    ("hwid", "raw-hwid-1234"),
    ("private_server_url", "https://private/server"),
    ("roblosecurity", "_|RBX_TOKEN_|_"),
    ("auth_token", "Bearer abc"),
    ("bot_token", "discord.bot.token"),
    ("supabase_key", "service-role-key"),
])
def test_sensitive_fields_are_dropped(sensitive_key, sensitive_value):
    payload = build_safe_payload(
        tool_version="1.0.0", channel="stable",
        packages=[_raw_pkg(**{sensitive_key: sensitive_value})],
        extra={sensitive_key: sensitive_value},
    )
    serialized = repr(payload)
    assert sensitive_value not in serialized, (
        f"sensitive value leaked through key {sensitive_key}: {serialized}"
    )


def test_state_vocabulary_clamps_to_allowed_set():
    payload = build_safe_payload(
        tool_version="1.0.0", channel="stable",
        packages=[_raw_pkg(state="MalformedStateThatDoesNotExist")],
    )
    assert payload["packages"][0]["state"] == "Unknown"

    payload2 = build_safe_payload(
        tool_version="1.0.0", channel="stable",
        packages=[_raw_pkg(state="Online")],
    )
    assert payload2["packages"][0]["state"] == "Online"


def test_state_vocabulary_includes_required_public_states():
    # The user-facing app expects these specific labels — including the
    # v1.0.4 additions Launching + Joining + No Heartbeat.
    for required in {
        "Online", "Dead", "Launching", "Joining", "No Heartbeat",
        "Relaunching", "Unknown",
    }:
        assert required in ALLOWED_STATES


def test_numeric_fields_are_clamped_and_coerced():
    payload = build_safe_payload(
        tool_version="1.0.0", channel="stable",
        packages=[_raw_pkg(
            ram_mb=9_999_999,       # > max 65536 → clamp
            runtime_seconds=-10,     # negative → 0
            restart_count="garbage", # non-int → 0
            pid=-1,                  # invalid → None
        )],
    )
    pkg = payload["packages"][0]
    assert pkg["ram_mb"] == 65_536
    assert pkg["runtime_seconds"] == 0
    assert pkg["restart_count"] == 0
    assert pkg["pid"] is None


def test_packages_without_name_are_dropped():
    payload = build_safe_payload(
        tool_version="1.0.0", channel="stable",
        packages=[
            _raw_pkg(package=""),                 # empty
            {"state": "Online"},                  # missing package
            _raw_pkg(package="com.real.app"),     # keeper
        ],
    )
    names = [p["package"] for p in payload["packages"]]
    assert names == ["com.real.app"]


def test_too_many_packages_are_truncated():
    pkgs = [_raw_pkg(package=f"com.pkg.{i}") for i in range(MAX_PACKAGES_PER_PUSH + 50)]
    payload = build_safe_payload(tool_version="1.0.0", channel="stable", packages=pkgs)
    assert len(payload["packages"]) == MAX_PACKAGES_PER_PUSH


def test_payload_stays_within_size_limit():
    import json
    pkgs = [_raw_pkg(package=f"com.pkg.{i}", display_name="x" * 60) for i in range(40)]
    payload = build_safe_payload(tool_version="1.0.0", channel="stable", packages=pkgs)
    raw = json.dumps(payload).encode("utf-8")
    assert len(raw) < MAX_PAYLOAD_BYTES, f"payload {len(raw)} exceeds limit {MAX_PAYLOAD_BYTES}"


# ── BridgeConfig from env ───────────────────────────────────────────────────

def test_bridge_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DENG_MONITOR_BRIDGE_ENABLED", raising=False)
    monkeypatch.delenv("DENG_MONITOR_BRIDGE_TOKEN", raising=False)
    cfg = BridgeConfig.from_env()
    assert cfg.enabled is False


def test_bridge_enabled_when_env_set(monkeypatch):
    monkeypatch.setenv("DENG_MONITOR_BRIDGE_ENABLED", "1")
    monkeypatch.setenv("DENG_MONITOR_BRIDGE_TOKEN", "test-token")
    monkeypatch.setenv("DENG_MONITOR_BRIDGE_URL", "https://example.com")
    cfg = BridgeConfig.from_env()
    assert cfg.enabled is True
    assert cfg.token == "test-token"
    assert cfg.bridge_url == "https://example.com"


# ── Runtime: offline-safe (no exceptions escape) ────────────────────────────

def test_bridge_does_not_crash_on_offline_backend(monkeypatch):
    cfg = BridgeConfig(
        bridge_url="https://127.0.0.1:1",  # nothing listening
        token="t-test-token",
        enabled=True,
        push_interval_seconds=0.05,
        snapshot_interval_seconds=0,
    )

    calls = {"status": 0}

    def status_provider():
        calls["status"] += 1
        return {
            "tool_version": "1.0.0",
            "channel": "stable",
            "packages": [_raw_pkg()],
        }

    bridge = MonitorBridge(config=cfg, status_provider=status_provider)
    assert bridge.start() is True
    time.sleep(0.3)
    bridge.stop()

    # Status provider was still called at least once → the loop survived.
    assert calls["status"] >= 1
    # Failure mode recorded but no exception escaped.
    assert bridge.state.connected is False
    assert bridge.state.last_error is not None


def test_bridge_refuses_to_start_without_token():
    cfg = BridgeConfig(enabled=True, token="", bridge_url="https://example.com")
    bridge = MonitorBridge(config=cfg, status_provider=lambda: {"packages": []})
    assert bridge.start() is False
    assert bridge.is_running() is False


def test_bridge_refuses_http_without_insecure_flag():
    cfg = BridgeConfig(
        enabled=True, token="t", bridge_url="http://insecure-host",
        push_interval_seconds=0.05, insecure=False,
    )
    bridge = MonitorBridge(config=cfg, status_provider=lambda: {"packages": [_raw_pkg()]})
    bridge.start()
    time.sleep(0.2)
    bridge.stop()
    assert bridge.state.last_error == "bridge_url_not_https"


# ── v1.0.2 — segfault-fix invariants ────────────────────────────────────────


def test_bridge_send_routes_through_safe_http_post_raw(monkeypatch):
    """All bridge HTTPS MUST go through agent.safe_http.post_raw so the
    OpenSSL ``EVP_PKEY_generate`` SIGSEGV captured in probe ``p-d1cb86fd89``
    only kills a curl child, never the agent process.

    Regression guard: if a future refactor brings back in-process
    ``urllib.request.urlopen``, this test fails.
    """
    cfg = BridgeConfig(
        enabled=True, token="abc", bridge_url="https://example.com",
        push_interval_seconds=10,  # we'll drive _tick directly
        snapshot_interval_seconds=0,
    )
    bridge = MonitorBridge(config=cfg, status_provider=lambda: {"packages": [_raw_pkg()]})

    captured: dict[str, object] = {}
    def _spy(url, body_bytes, *, content_type, headers, timeout):
        captured["url"] = url
        captured["content_type"] = content_type
        captured["headers"] = dict(headers)
        captured["body_len"] = len(body_bytes)
        return 200, b'{"ok":true,"accepted":1,"settings":null}'

    monkeypatch.setattr("agent.safe_http.post_raw", _spy)
    # Drive a single tick by hand (avoids racing with the daemon loop).
    bridge._tick()

    assert captured["url"].endswith("/api/monitor/bridge/push")
    assert captured["content_type"] == "application/json"
    assert captured["headers"]["Authorization"] == "Bearer abc"
    assert bridge.state.connected is True
    assert bridge.state.last_push_result == "success"


def test_bridge_applies_settings_echoed_from_push_response(monkeypatch):
    """When /push echoes settings, the bridge updates its local snapshot
    interval. That's how an APK settings change reaches Termux without a
    relaunch."""
    cfg = BridgeConfig(
        enabled=True, token="abc", bridge_url="https://example.com",
        snapshot_interval_seconds=0,
    )
    bridge = MonitorBridge(config=cfg, status_provider=lambda: {"packages": []})

    monkeypatch.setattr(
        "agent.safe_http.post_raw",
        lambda *a, **kw: (
            200,
            b'{"ok":true,"accepted":0,"settings":{"snapshot_interval_seconds":30,"monitor_enabled":true}}',
        ),
    )
    bridge._tick()
    assert bridge.config.snapshot_interval_seconds == 30
    assert bridge.state.monitor_enabled_remote is True


def test_bridge_unauthorized_triggers_on_unauthorized_callback(monkeypatch):
    seen: list[int] = []

    def _on_unauth(status):
        seen.append(status)

    cfg = BridgeConfig(
        enabled=True, token="revoked", bridge_url="https://example.com",
        snapshot_interval_seconds=0,
    )
    bridge = MonitorBridge(
        config=cfg, status_provider=lambda: {"packages": []},
        on_unauthorized=_on_unauth,
    )
    monkeypatch.setattr("agent.safe_http.post_raw", lambda *a, **kw: (401, b"{}"))
    bridge._tick()
    assert seen == [401]
    assert bridge.state.connected is False
    assert bridge.state.last_error == "http_401"


def test_snapshot_skipped_when_interval_is_zero(monkeypatch):
    cfg = BridgeConfig(
        enabled=True, token="t", bridge_url="https://example.com",
        snapshot_interval_seconds=0,
    )
    called = {"snap": 0}
    def _snap():
        called["snap"] += 1
        return (b"FAKEPNG", "image/png")
    bridge = MonitorBridge(
        config=cfg, status_provider=lambda: {"packages": []},
        snapshot_provider=_snap,
    )
    monkeypatch.setattr("agent.safe_http.post_raw", lambda *a, **kw: (200, b'{"ok":true}'))
    bridge._tick()
    assert called["snap"] == 0, "snapshot must NOT run when interval is 0"


def test_snapshot_capture_failure_marks_result_and_does_not_crash(monkeypatch):
    cfg = BridgeConfig(
        enabled=True, token="t", bridge_url="https://example.com",
        snapshot_interval_seconds=15,
    )
    def _broken_snap():
        raise RuntimeError("screencap exploded")
    bridge = MonitorBridge(
        config=cfg, status_provider=lambda: {"packages": []},
        snapshot_provider=_broken_snap,
    )
    monkeypatch.setattr("agent.safe_http.post_raw", lambda *a, **kw: (200, b'{"ok":true}'))
    # First tick: cooldown is initialized to 0, so snapshot will run.
    bridge._tick()
    # Should not raise; the result is recorded as a failure.
    assert bridge.state.snapshot_last_result in (None, "capture_failed")


def test_safe_http_post_raw_uses_curl_backend_on_termux(monkeypatch):
    """When TERMUX_VERSION is set, safe_http.post_raw must NOT use
    urllib.request — it must shell out to curl. That's the segfault
    isolation guarantee.
    """
    monkeypatch.setenv("TERMUX_VERSION", "0.118.0")
    monkeypatch.delenv("DENG_HTTP_BACKEND", raising=False)
    from agent import safe_http

    captured = {}
    def _fake_run_curl(args, *, stdin_bytes=None, timeout=30):
        captured["args"] = args
        captured["stdin_len"] = len(stdin_bytes or b"")
        return 200, b'{"ok":true}'

    monkeypatch.setattr(safe_http, "_run_curl", _fake_run_curl)
    # If the implementation regressed and went through urllib, curl_available
    # would never be consulted and this monkeypatch would be a no-op — make
    # the test loud by asserting our spy was hit.
    status, body = safe_http.post_raw(
        "https://example.com/x",
        b'{"hello":"world"}',
        content_type="application/json",
        headers={"Authorization": "Bearer abc"},
        timeout=5,
    )
    assert status == 200
    assert body == b'{"ok":true}'
    assert "args" in captured, "post_raw on Termux must route through _run_curl"
    # Auth + content-type were forwarded.
    args = captured["args"]
    assert any("Authorization: Bearer abc" in str(a) for a in args)
    assert any("Content-Type: application/json" in str(a) for a in args)


# ── v1.0.4 — bridge_status push payload + snapshot diagnostics ────────────────


def test_push_payload_embeds_bridge_status_block(monkeypatch):
    """v1.0.4: every /push payload now carries a `bridge_status` object
    so the backend can persist real snapshot pipeline diagnostics on the
    device row. Without this, the APK Snapshot tab can't tell the user
    WHY a snapshot is missing — that was the v1.0.3 silent-failure bug.
    """
    cfg = BridgeConfig(
        enabled=True, token="abc", bridge_url="https://example.com",
        snapshot_interval_seconds=0,
    )
    bridge = MonitorBridge(config=cfg, status_provider=lambda: {"packages": []})
    bridge.state.snapshot_last_result = "capture_failed"
    bridge.state.snapshot_last_error = "screencap_unavailable"
    bridge.state.snapshot_provider_called_count = 3
    bridge.state.screencap_available = False

    captured = {}
    def _spy(url, body_bytes, *, content_type, headers, timeout):
        captured["body"] = body_bytes
        return 200, b'{"ok":true,"accepted":0,"settings":null}'

    monkeypatch.setattr("agent.safe_http.post_raw", _spy)
    bridge._tick()

    import json as _json
    pushed = _json.loads(captured["body"].decode("utf-8"))
    assert "bridge_status" in pushed
    bs = pushed["bridge_status"]
    assert bs["snapshot_last_result"] == "capture_failed"
    assert bs["snapshot_last_error"] == "screencap_unavailable"
    assert bs["snapshot_provider_called_count"] == 3
    assert bs["screencap_available"] is False


def test_snapshot_provider_none_records_screencap_unavailable(monkeypatch):
    """If the snapshot provider returns None (screencap missing / perm
    denied), the bridge must record a precise reason — that's what the
    APK shows in the Snapshot tab instead of "Waiting forever".
    """
    cfg = BridgeConfig(
        enabled=True, token="t", bridge_url="https://example.com",
        snapshot_interval_seconds=15,
    )
    bridge = MonitorBridge(
        config=cfg, status_provider=lambda: {"packages": []},
        snapshot_provider=lambda: None,
    )
    monkeypatch.setattr("agent.safe_http.post_raw", lambda *a, **kw: (200, b'{"ok":true}'))
    bridge._tick()
    assert bridge.state.snapshot_last_result == "capture_failed"
    assert bridge.state.snapshot_last_error == "screencap_unavailable"
    assert bridge.state.screencap_available is False
    assert bridge.state.snapshot_provider_called_count == 1


def test_snapshot_capture_success_records_bytes_and_upload_ok(monkeypatch):
    """Happy path: provider returns PNG bytes, upload succeeds. The
    bridge must record byte count + upload status so the APK can show
    them in the diagnostics line.
    """
    cfg = BridgeConfig(
        enabled=True, token="t", bridge_url="https://example.com",
        snapshot_interval_seconds=1,
    )
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 1024
    bridge = MonitorBridge(
        config=cfg, status_provider=lambda: {"packages": []},
        snapshot_provider=lambda: (png, "image/png"),
    )
    monkeypatch.setattr("agent.safe_http.post_raw", lambda *a, **kw: (200, b'{"ok":true}'))
    # Tick once for the push, then again to trigger the snapshot.
    bridge._tick()
    time.sleep(1.1)
    bridge._tick()
    assert bridge.state.snapshot_last_result == "success"
    assert bridge.state.snapshot_last_bytes == len(png)
    assert bridge.state.snapshot_last_upload_status == "ok"
    assert bridge.state.screencap_available is True


class _FakeAttempt:
    def __init__(self, provider, png_valid=False):
        self.provider = provider
        self.png_valid = png_valid
    def to_safe_dict(self):
        return {"provider": self.provider, "png_valid": self.png_valid}


class _FakeCapture:
    """Duck-typed stand-in for agent.snapshot.SnapshotCapture."""
    def __init__(self, *, data=None, result="failed_unknown", provider=None,
                 png_valid=False, error=None, byte_length=0,
                 screencap_found=False, su_available=False, root_granted=None,
                 attempts=None, mime="image/png"):
        self.data = data
        self.result = result
        self.provider = provider
        self.png_valid = png_valid
        self.error = error
        self.byte_length = byte_length or (len(data) if data else 0)
        self.screencap_found = screencap_found
        self.su_available = su_available
        self.root_granted = root_granted
        self.attempts = attempts or []
        self.mime = mime


def test_rich_capture_success_ingests_provider_diagnostics(monkeypatch):
    """v1.0.6: a SnapshotCapture with valid PNG uploads and records WHICH
    provider worked (e.g. root_screencap_file) + png validity + root grant.
    """
    cfg = BridgeConfig(
        enabled=True, token="t", bridge_url="https://example.com",
        snapshot_interval_seconds=1,
    )
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 20_000
    cap = _FakeCapture(
        data=png, result="success", provider="root_screencap_file",
        png_valid=True, screencap_found=True, su_available=True, root_granted=True,
        attempts=[_FakeAttempt("normal_screencap"), _FakeAttempt("root_screencap_file", True)],
    )
    bridge = MonitorBridge(
        config=cfg, status_provider=lambda: {"packages": []},
        snapshot_provider=lambda: cap,
    )
    monkeypatch.setattr("agent.safe_http.post_raw", lambda *a, **kw: (200, b'{"ok":true}'))
    bridge._tick()
    time.sleep(1.1)
    bridge._tick()
    assert bridge.state.snapshot_last_result == "success"
    assert bridge.state.snapshot_provider == "root_screencap_file"
    assert bridge.state.snapshot_png_valid is True
    assert bridge.state.snapshot_root_granted is True
    assert bridge.state.snapshot_su_available is True
    assert bridge.state.snapshot_last_bytes == len(png)


def test_rich_capture_failure_records_result_without_crash(monkeypatch):
    cfg = BridgeConfig(
        enabled=True, token="t", bridge_url="https://example.com",
        snapshot_interval_seconds=15,
    )
    cap = _FakeCapture(
        data=None, result="failed_root_denied", error="root screencap denied",
        screencap_found=True, su_available=True, root_granted=False,
        attempts=[_FakeAttempt("root_screencap_stdout")],
    )
    bridge = MonitorBridge(
        config=cfg, status_provider=lambda: {"packages": []},
        snapshot_provider=lambda: cap,
    )
    monkeypatch.setattr("agent.safe_http.post_raw", lambda *a, **kw: (200, b'{"ok":true}'))
    bridge._tick()
    assert bridge.state.snapshot_last_result == "failed_root_denied"
    assert bridge.state.snapshot_last_error == "root screencap denied"
    assert bridge.state.snapshot_root_granted is False


def test_upload_http_failure_marks_failed_upload_http(monkeypatch):
    cfg = BridgeConfig(
        enabled=True, token="t", bridge_url="https://example.com",
        snapshot_interval_seconds=1,
    )
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 1024
    # push succeeds (200) but snapshot upload returns 503.
    def _post(url, *a, **kw):
        if url.endswith("/api/monitor/bridge/snapshot"):
            return 503, b'{"error":"busy"}'
        return 200, b'{"ok":true}'
    bridge = MonitorBridge(
        config=cfg, status_provider=lambda: {"packages": []},
        snapshot_provider=lambda: (png, "image/png"),
    )
    monkeypatch.setattr("agent.safe_http.post_raw", _post)
    bridge._tick()
    time.sleep(1.1)
    bridge._tick()
    assert bridge.state.snapshot_last_result == "failed_upload_http"


def test_device_ram_embedded_in_push_bridge_status(monkeypatch):
    """v1.0.6: device_ram from the status provider must ride along in the
    bridge_status block so the dashboard can render per-device RAM."""
    cfg = BridgeConfig(
        enabled=True, token="t", bridge_url="https://example.com",
        snapshot_interval_seconds=0,
    )
    def _status():
        return {
            "packages": [],
            "device_ram": {"used_mb": 2048, "total_mb": 4096, "percent": 50},
        }
    bridge = MonitorBridge(config=cfg, status_provider=_status)
    captured = {}
    def _spy(url, body_bytes, *, content_type, headers, timeout):
        captured["body"] = body_bytes
        return 200, b'{"ok":true,"settings":null}'
    monkeypatch.setattr("agent.safe_http.post_raw", _spy)
    bridge._tick()
    import json as _json
    pushed = _json.loads(captured["body"].decode("utf-8"))
    assert pushed["bridge_status"]["device_ram"] == {"used_mb": 2048, "total_mb": 4096, "percent": 50}


def test_to_push_status_redacts_no_secrets():
    """BridgeState.to_push_status must never include tokens, URLs, or
    license keys — only the small snapshot/push diagnostic block.
    """
    from agent.monitor_bridge import BridgeState
    s = BridgeState()
    out = s.to_push_status()
    assert set(out.keys()) == {
        "snapshot_last_result",
        "snapshot_last_bytes",
        "snapshot_last_error",
        "snapshot_last_upload_status",
        "snapshot_provider_called_count",
        "screencap_available",
        # v1.0.6 capture-provider diagnostics.
        "snapshot_provider",
        "snapshot_png_valid",
        "snapshot_root_granted",
        "snapshot_su_available",
        "last_push_result",
    }
    # None of these may carry a secret: assert only diagnostic primitives.
    for v in out.values():
        assert v is None or isinstance(v, (str, int, bool)), f"unexpected type: {v!r}"
