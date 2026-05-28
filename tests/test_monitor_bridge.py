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
    # The user-facing app expects these specific labels.
    for required in {"Online", "Dead", "Relaunching", "No Heartbeat", "Launching", "Unknown"}:
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
