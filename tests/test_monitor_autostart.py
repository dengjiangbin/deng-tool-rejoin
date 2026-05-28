"""Tests for agent/monitor_autostart.py — Termux-side monitor bridge wiring.

These tests verify the public contract that the Rejoin APK fix depends on:

* ``ensure_monitor_bridge_started`` is idempotent and never raises.
* Cached bridge tokens are reused (no extra HTTP call).
* Expired / wrong-URL caches force re-issue.
* Backend offline → returns False, no crash, no token written.
* Active supervisor is mirrored into the status_provider payload.
* Cache file is locked down (mode 0600 on POSIX).
* No license keys / install IDs / owner identifiers leak into the cache file.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import pytest

# Force APP_HOME into a per-test temp dir BEFORE importing monitor_autostart,
# so the cache path is sandboxed.
@pytest.fixture(autouse=True)
def _sandbox_app_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DENG_REJOIN_HOME", str(tmp_path / "rejoin"))
    # Reload constants + autostart so they see the new APP_HOME.
    for mod in [
        "agent.monitor_autostart",
        "agent.constants",
    ]:
        sys.modules.pop(mod, None)
    yield


def _fresh():
    """Import a clean copy of the module after APP_HOME sandboxing."""
    from agent import monitor_autostart
    monitor_autostart.reset_for_tests()
    return monitor_autostart


# ── Cache path is inside the sandboxed APP_HOME ─────────────────────────────


def test_cache_path_is_inside_app_home(tmp_path):
    autostart = _fresh()
    assert str(autostart.BRIDGE_CACHE_PATH).startswith(str(tmp_path))
    assert autostart.BRIDGE_CACHE_PATH.name == ".monitor-bridge.json"


# ── Idempotency + missing inputs ────────────────────────────────────────────


def test_missing_license_returns_false_without_network():
    autostart = _fresh()
    with mock.patch.object(autostart, "_issue_token_from_license") as issue:
        result = autostart.ensure_monitor_bridge_started(
            license_key="", install_id_hash="a" * 64,
            tool_version="1.0.0", channel="stable",
        )
    assert result is False
    issue.assert_not_called()


def test_missing_install_id_returns_false_without_network():
    autostart = _fresh()
    with mock.patch.object(autostart, "_issue_token_from_license") as issue:
        result = autostart.ensure_monitor_bridge_started(
            license_key="DENG-1A2B-3C4D-5E6F-7890", install_id_hash="",
            tool_version="1.0.0", channel="stable",
        )
    assert result is False
    issue.assert_not_called()


# ── Backend offline is non-fatal ────────────────────────────────────────────


def test_backend_offline_returns_false_and_never_raises():
    autostart = _fresh()
    def _fail(*_a, **_kw):
        raise urllib.error.URLError("connection refused")
    with mock.patch.object(autostart.urllib.request, "urlopen", side_effect=_fail):
        result = autostart.ensure_monitor_bridge_started(
            license_key="DENG-1A2B-3C4D-5E6F-7890",
            install_id_hash="a" * 64,
            tool_version="1.0.0", channel="stable",
            announce=False,
        )
    assert result is False
    # Cache must NOT be created when issue fails.
    assert not autostart.BRIDGE_CACHE_PATH.exists()


def test_backend_5xx_returns_false_and_caches_nothing():
    autostart = _fresh()

    class _Resp:
        status = 500
        def read(self): return b""
        def __enter__(self): return self
        def __exit__(self, *a): return False

    with mock.patch.object(autostart.urllib.request, "urlopen", return_value=_Resp()):
        result = autostart.ensure_monitor_bridge_started(
            license_key="DENG-1A2B-3C4D-5E6F-7890",
            install_id_hash="a" * 64,
            announce=False,
        )
    assert result is False
    assert not autostart.BRIDGE_CACHE_PATH.exists()


# ── Successful issue path ───────────────────────────────────────────────────


def _ok_response(token="bridge-tok-abc", device_id="dev-1", ttl_sec=12 * 3600):
    expires_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + ttl_sec)
    )
    body = json.dumps({
        "bridge_token": token, "device_id": device_id, "expires_at": expires_at,
    }).encode("utf-8")

    class _Resp:
        status = 200
        def read(self): return body
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return _Resp(), expires_at


def test_success_issues_token_and_starts_bridge():
    autostart = _fresh()
    resp, _ = _ok_response()
    with mock.patch.object(autostart.urllib.request, "urlopen", return_value=resp), \
         mock.patch.object(autostart.MonitorBridge, "start", return_value=True):
        result = autostart.ensure_monitor_bridge_started(
            license_key="DENG-1A2B-3C4D-5E6F-7890",
            install_id_hash="a" * 64,
            announce=False,
        )
    assert result is True
    # Token is cached.
    cached = json.loads(autostart.BRIDGE_CACHE_PATH.read_text("utf-8"))
    assert cached["bridge_token"] == "bridge-tok-abc"
    assert cached["device_id"] == "dev-1"
    assert "expires_at" in cached
    assert cached["bridge_url"]  # must be present
    # No license_key / install_id leak in the cache.
    assert "license_key" not in cached
    assert "install_id" not in cached
    assert "install_id_hash" not in cached
    assert "owner" not in cached
    assert "owner_discord_user_id" not in cached
    raw = autostart.BRIDGE_CACHE_PATH.read_text("utf-8")
    assert "DENG-1A2B-3C4D-5E6F-7890" not in raw, "license key leaked into cache file"


def test_cache_file_is_0600_on_posix():
    autostart = _fresh()
    resp, _ = _ok_response()
    with mock.patch.object(autostart.urllib.request, "urlopen", return_value=resp), \
         mock.patch.object(autostart.MonitorBridge, "start", return_value=True):
        autostart.ensure_monitor_bridge_started(
            license_key="DENG-1A2B-3C4D-5E6F-7890",
            install_id_hash="a" * 64,
            announce=False,
        )
    if os.name == "posix":
        mode = os.stat(autostart.BRIDGE_CACHE_PATH).st_mode & 0o777
        assert mode == 0o600, f"cache file should be 0600, got {oct(mode)}"


def test_cached_token_is_reused_without_network():
    autostart = _fresh()
    # Pre-seed a cache file with an unexpired token at the default URL.
    from agent.monitor_bridge import DEFAULT_BRIDGE_URL
    autostart.BRIDGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    autostart.BRIDGE_CACHE_PATH.write_text(json.dumps({
        "bridge_url": DEFAULT_BRIDGE_URL,
        "bridge_token": "cached-tok-xyz",
        "device_id": "dev-cached",
        "expires_at": "2099-12-31T23:59:59Z",
        "expires_at_epoch": time.time() + 6 * 3600,
    }), encoding="utf-8")

    issue = mock.MagicMock()
    with mock.patch.object(autostart, "_issue_token_from_license", issue), \
         mock.patch.object(autostart.MonitorBridge, "start", return_value=True):
        ok = autostart.ensure_monitor_bridge_started(
            license_key="DENG-1A2B-3C4D-5E6F-7890",
            install_id_hash="a" * 64,
            announce=False,
        )
    assert ok is True
    issue.assert_not_called()


def test_expired_cache_triggers_reissue():
    autostart = _fresh()
    from agent.monitor_bridge import DEFAULT_BRIDGE_URL
    autostart.BRIDGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    autostart.BRIDGE_CACHE_PATH.write_text(json.dumps({
        "bridge_url": DEFAULT_BRIDGE_URL,
        "bridge_token": "old-tok",
        "expires_at_epoch": time.time() - 60,  # already expired
    }), encoding="utf-8")
    resp, _ = _ok_response(token="brand-new-tok")
    with mock.patch.object(autostart.urllib.request, "urlopen", return_value=resp), \
         mock.patch.object(autostart.MonitorBridge, "start", return_value=True):
        ok = autostart.ensure_monitor_bridge_started(
            license_key="DENG-1A2B-3C4D-5E6F-7890",
            install_id_hash="a" * 64,
            announce=False,
        )
    assert ok is True
    cached = json.loads(autostart.BRIDGE_CACHE_PATH.read_text("utf-8"))
    assert cached["bridge_token"] == "brand-new-tok"


def test_wrong_url_cache_triggers_reissue():
    autostart = _fresh()
    autostart.BRIDGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    autostart.BRIDGE_CACHE_PATH.write_text(json.dumps({
        "bridge_url": "https://OLD-host.example",
        "bridge_token": "leftover-tok",
        "expires_at_epoch": time.time() + 6 * 3600,
    }), encoding="utf-8")
    resp, _ = _ok_response(token="correct-host-tok")
    with mock.patch.object(autostart.urllib.request, "urlopen", return_value=resp), \
         mock.patch.object(autostart.MonitorBridge, "start", return_value=True):
        ok = autostart.ensure_monitor_bridge_started(
            license_key="DENG-1A2B-3C4D-5E6F-7890",
            install_id_hash="a" * 64,
            announce=False,
        )
    assert ok is True
    cached = json.loads(autostart.BRIDGE_CACHE_PATH.read_text("utf-8"))
    assert cached["bridge_token"] == "correct-host-tok"


def test_idempotent_when_already_running():
    autostart = _fresh()
    resp, _ = _ok_response()
    started = {"count": 0}
    def _fake_start(self):
        started["count"] += 1
        return True
    # Once it starts, is_running() must return True for the second call to short-circuit.
    with mock.patch.object(autostart.urllib.request, "urlopen", return_value=resp), \
         mock.patch.object(autostart.MonitorBridge, "start", _fake_start), \
         mock.patch.object(autostart.MonitorBridge, "is_running", return_value=True):
        autostart.ensure_monitor_bridge_started(
            license_key="DENG-1A2B-3C4D-5E6F-7890",
            install_id_hash="a" * 64, announce=False,
        )
        autostart.ensure_monitor_bridge_started(
            license_key="DENG-1A2B-3C4D-5E6F-7890",
            install_id_hash="a" * 64, announce=False,
        )
    assert started["count"] == 1, "bridge.start() must run only once"


# ── Status provider builds safe payload from the live supervisor ────────────


class _FakeSupervisor:
    def __init__(self, snapshot):
        self._snap = snapshot
    def get_status_snapshot(self):
        return list(self._snap)


def test_status_provider_returns_empty_packages_when_no_supervisor():
    autostart = _fresh()
    autostart.set_active_supervisor(None)
    payload = autostart._build_status_payload(tool_version="1.0.0", channel="stable")
    assert payload["packages"] == []
    assert payload["tool_version"] == "1.0.0"
    assert payload["channel"] == "stable"


def test_status_provider_maps_supervisor_status_to_state():
    autostart = _fresh()
    autostart.set_active_supervisor(_FakeSupervisor([
        {"package": "com.foo.bar", "username": "alice", "status": "Online",
         "revive_count": 2},
        {"package": "com.baz.qux", "username": "bob", "status": "Dead"},
    ]))
    payload = autostart._build_status_payload(tool_version="1.0.0", channel="stable")
    assert len(payload["packages"]) == 2
    assert payload["packages"][0]["package"] == "com.foo.bar"
    # Supervisor's "status" field must surface as "state" for the bridge.
    assert payload["packages"][0]["state"] == "Online"
    assert payload["packages"][0]["restart_count"] == 2
    assert payload["packages"][1]["state"] == "Dead"


def test_status_provider_swallows_broken_supervisor():
    autostart = _fresh()
    class _Broken:
        def get_status_snapshot(self):
            raise RuntimeError("supervisor exploded")
    autostart.set_active_supervisor(_Broken())
    payload = autostart._build_status_payload(tool_version="1.0.0", channel="stable")
    # Empty packages, no exception bubbled.
    assert payload["packages"] == []


def test_status_provider_drops_oversized_snapshots():
    autostart = _fresh()
    huge = [{"package": f"com.x.p{i}", "status": "Online"} for i in range(200)]
    autostart.set_active_supervisor(_FakeSupervisor(huge))
    payload = autostart._build_status_payload(tool_version="1.0.0", channel="stable")
    assert len(payload["packages"]) <= 64, "must cap at 64 packages per push"


# ── clear_cached_token forces reissue ────────────────────────────────────────


def test_clear_cached_token_removes_file():
    autostart = _fresh()
    autostart.BRIDGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    autostart.BRIDGE_CACHE_PATH.write_text("{}", encoding="utf-8")
    assert autostart.BRIDGE_CACHE_PATH.exists()
    autostart.clear_cached_token()
    assert not autostart.BRIDGE_CACHE_PATH.exists()
    # Calling again on a missing file must not raise.
    autostart.clear_cached_token()


# ── Sanity: no sensitive payload reaches the issue URL ──────────────────────


def test_issue_request_only_sends_whitelisted_fields():
    autostart = _fresh()
    captured = {}

    def _spy(req, **kw):
        # Capture the JSON body for inspection.
        body = req.data
        captured["body"] = json.loads(body.decode("utf-8"))

        class _Resp:
            status = 200
            def read(self): return b'{"bridge_token":"t","device_id":"d","expires_at":"2099-01-01T00:00:00Z"}'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return _Resp()

    with mock.patch.object(autostart.urllib.request, "urlopen", side_effect=_spy), \
         mock.patch.object(autostart.MonitorBridge, "start", return_value=True):
        autostart.ensure_monitor_bridge_started(
            license_key="DENG-1A2B-3C4D-5E6F-7890",
            install_id_hash="a" * 64,
            tool_version="1.0.0", channel="stable",
            device_label="Termux on Android",
            announce=False,
        )

    body = captured["body"]
    # Exactly the documented contract — no extras.
    assert set(body.keys()) == {
        "license_key", "install_id_hash",
        "device_label", "tool_version", "channel",
    }
    # No cookies / passwords / private URLs / Roblox tokens anywhere.
    raw = json.dumps(body).lower()
    for banned in ("cookie", "roblosecurity", "password", "private_url", "secret"):
        assert banned not in raw, f"banned key '{banned}' present in issue payload"
