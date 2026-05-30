"""Tests for agent/monitor_autostart.py — Termux-side monitor bridge wiring.

These tests verify the public contract that the Rejoin APK fix depends on:

* ``ensure_monitor_bridge_started`` is idempotent and never raises.
* Cached bridge tokens are reused (no extra HTTP call).
* Expired / wrong-URL caches force re-issue.
* Backend offline → returns False, no crash, no token written.
* Active supervisor is mirrored into the status_provider payload.
* When no supervisor is active but a saved config IS registered, the
  bridge still reports each enabled package with state=Dead so the APK
  has rows to render on the main menu (v1.0.2 fix).
* Cache file is locked down (mode 0600 on POSIX).
* No license keys / install IDs / owner identifiers leak into the cache file.
* The issue-from-license request is routed via :mod:`agent.safe_http` so
  the OpenSSL crash in ``EVP_PKEY_generate`` observed on probe
  ``p-d1cb86fd89`` cannot kill the Termux Python process.
"""

from __future__ import annotations

import json
import os
import sys
import time
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


def _ok_payload(token: str = "bridge-tok-abc",
                device_id: str = "dev-1",
                ttl_sec: int = 12 * 3600) -> dict:
    expires_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + ttl_sec)
    )
    return {
        "bridge_token": token,
        "device_id": device_id,
        "expires_at": expires_at,
    }


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


# ── Backend offline is non-fatal (segfault-fix invariant) ───────────────────


def test_backend_offline_returns_false_and_never_raises():
    """Probe p-d1cb86fd89: the *segfault* fix sends all bridge HTTPS via
    safe_http (curl subprocess). A network error from safe_http must not
    bubble up — autostart must catch it and return False."""
    autostart = _fresh()
    import agent.safe_http as sh

    def _net_fail(*_a, **_kw):
        raise sh.SafeHttpNetworkError("simulated curl SIGSEGV")

    with mock.patch("agent.safe_http.post_json", side_effect=_net_fail):
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
    import agent.safe_http as sh

    def _http_500(*_a, **_kw):
        raise sh.SafeHttpStatusError(500, "")

    with mock.patch("agent.safe_http.post_json", side_effect=_http_500):
        result = autostart.ensure_monitor_bridge_started(
            license_key="DENG-1A2B-3C4D-5E6F-7890",
            install_id_hash="a" * 64,
            announce=False,
        )
    assert result is False
    assert not autostart.BRIDGE_CACHE_PATH.exists()


# ── Successful issue path ───────────────────────────────────────────────────


def test_success_issues_token_and_starts_bridge():
    autostart = _fresh()
    with mock.patch("agent.safe_http.post_json", return_value=_ok_payload()), \
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
    with mock.patch("agent.safe_http.post_json", return_value=_ok_payload()), \
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
    with mock.patch("agent.safe_http.post_json",
                    return_value=_ok_payload(token="brand-new-tok")), \
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
    with mock.patch("agent.safe_http.post_json",
                    return_value=_ok_payload(token="correct-host-tok")), \
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
    started = {"count": 0}

    def _fake_start(self):
        started["count"] += 1
        return True

    with mock.patch("agent.safe_http.post_json", return_value=_ok_payload()), \
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


def test_status_provider_returns_empty_packages_when_no_supervisor_and_no_config():
    autostart = _fresh()
    autostart.set_active_supervisor(None)
    autostart.set_config(None)
    payload = autostart._build_status_payload(tool_version="1.0.0", channel="stable")
    assert payload["packages"] == []
    assert payload["tool_version"] == "1.0.0"
    assert payload["channel"] == "stable"


# ── v1.0.6 device RAM (for the redesigned dashboard) ────────────────────────


def test_parse_meminfo_computes_available_total_percent():
    import agent.monitor_autostart as ma
    text = "MemTotal:        4096000 kB\nMemFree:         512000 kB\nMemAvailable:    1024000 kB\n"
    ram = ma._parse_meminfo(text)
    assert ram is not None
    assert ram["total_mb"] == 4096000 // 1024
    assert ram["percent"] == 25
    assert ram["available_mb"] == 1024000 // 1024


def test_parse_meminfo_falls_back_to_memfree_without_available():
    import agent.monitor_autostart as ma
    text = "MemTotal: 1000000 kB\nMemFree: 400000 kB\n"
    ram = ma._parse_meminfo(text)
    assert ram is not None
    assert ram["percent"] == 40


def test_parse_meminfo_returns_none_on_garbage():
    import agent.monitor_autostart as ma
    assert ma._parse_meminfo("not meminfo at all") is None


def test_status_payload_includes_device_ram_when_available(monkeypatch):
    import agent.monitor_autostart as ma
    autostart = _fresh()
    autostart.set_active_supervisor(None)
    autostart.set_config(None)
    monkeypatch.setattr(ma, "read_device_ram",
                        lambda: {"available_mb": 2048, "total_mb": 4096, "percent": 50})
    payload = autostart._build_status_payload(tool_version="1.0.0", channel="stable")
    assert payload["device_ram"] == {"available_mb": 2048, "total_mb": 4096, "percent": 50}


def test_status_payload_omits_device_ram_on_non_linux(monkeypatch):
    import agent.monitor_autostart as ma
    autostart = _fresh()
    autostart.set_active_supervisor(None)
    autostart.set_config(None)
    monkeypatch.setattr(ma, "read_device_ram", lambda: None)
    payload = autostart._build_status_payload(tool_version="1.0.0", channel="stable")
    assert "device_ram" not in payload


def test_default_snapshot_provider_returns_capture_object(monkeypatch):
    """The autostart snapshot provider must hand the bridge a rich
    SnapshotCapture (so per-provider diagnostics reach the APK)."""
    import agent.monitor_autostart as ma
    import agent.snapshot as snap
    cap = snap.SnapshotCapture(result=snap.RESULT_NO_SCREENCAP, su_available=False)
    monkeypatch.setattr(snap, "capture_snapshot_detailed", lambda: cap)
    out = ma._default_snapshot_provider()
    assert out is cap
    assert hasattr(out, "data") and hasattr(out, "result")


def test_status_provider_maps_supervisor_status_to_public_state():
    """v1.0.4 — APK-visible vocabulary is exactly five states.

    Online / Dead / Launching / Joining / No Heartbeat. Anything outside
    that set collapses to Dead so the watchdog (not the APK) owns
    recovery. In particular "In-Lobby"/"Lobby" must NOT leak out anymore.
    """
    autostart = _fresh()
    autostart.set_active_supervisor(_FakeSupervisor([
        {"package": "com.foo.bar", "username": "alice", "status": "Online",
         "revive_count": 2, "online_since": time.time() - 120},
        {"package": "com.baz.qux", "username": "bob", "status": "Dead"},
        # Launching now passes through as Launching (was: collapsed to Dead).
        {"package": "com.x.y", "username": "carol", "status": "Launching"},
        # Reconnecting collapses to No Heartbeat.
        {"package": "com.x.z", "username": "dan", "status": "Reconnecting"},
        # Joining is its own state in v1.0.4 (was: deprecated, never emitted).
        {"package": "com.x.w", "username": "eve", "status": "Joining"},
        # Lobby/In-Lobby must collapse to Dead — never leak to APK.
        {"package": "com.x.v", "username": "frank", "status": "In-Lobby"},
        {"package": "com.x.u", "username": "grace", "status": "Lobby"},
    ]))
    payload = autostart._build_status_payload(tool_version="1.0.0", channel="stable")
    pkgs = payload["packages"]
    assert len(pkgs) == 7
    assert pkgs[0]["state"] == "Online"
    assert pkgs[0]["restart_count"] == 2
    assert pkgs[0]["runtime_seconds"] >= 100
    assert pkgs[1]["state"] == "Dead"
    assert pkgs[2]["state"] == "Launching", "Launching now visible to APK"
    assert pkgs[3]["state"] == "No Heartbeat"
    assert pkgs[4]["state"] == "Joining", "Joining now visible to APK"
    assert pkgs[5]["state"] == "Dead", "In-Lobby must collapse to Dead"
    assert pkgs[6]["state"] == "Dead", "Lobby must collapse to Dead"


def test_apk_visible_states_is_exactly_five_with_no_in_lobby():
    """The APK_VISIBLE_STATES allow-list must be the canonical 5 set."""
    from agent import monitor_autostart
    assert monitor_autostart.APK_VISIBLE_STATES == frozenset(
        {"Dead", "Launching", "Joining", "Online", "No Heartbeat"}
    )
    assert "In-Lobby" not in monitor_autostart.APK_VISIBLE_STATES
    assert "Lobby" not in monitor_autostart.APK_VISIBLE_STATES


def test_status_provider_swallows_broken_supervisor():
    autostart = _fresh()

    class _Broken:
        def get_status_snapshot(self):
            raise RuntimeError("supervisor exploded")

    autostart.set_active_supervisor(_Broken())
    autostart.set_config({"roblox_packages": []})
    payload = autostart._build_status_payload(tool_version="1.0.0", channel="stable")
    # Broken supervisor → falls back to config (here empty).
    assert payload["packages"] == []


def test_status_provider_drops_oversized_snapshots():
    autostart = _fresh()
    huge = [{"package": f"com.x.p{i}", "status": "Online"} for i in range(200)]
    autostart.set_active_supervisor(_FakeSupervisor(huge))
    payload = autostart._build_status_payload(tool_version="1.0.0", channel="stable")
    assert len(payload["packages"]) <= 64, "must cap at 64 packages per push"


# ── v1.0.2: config-only packages (pre-Start / main menu) ────────────────────


def test_config_only_path_reports_enabled_packages_as_dead():
    """User in main menu, never pressed Start: APK still gets rows."""
    autostart = _fresh()
    autostart.set_active_supervisor(None)
    cfg = {
        "roblox_packages": [
            {"package": "com.litec.client", "app_name": "LiteC",
             "account_username": "deng1629", "enabled": True,
             "private_server_url": ""},
            {"package": "com.moons.litesd", "app_name": "LiteD",
             "account_username": "", "enabled": True,
             "private_server_url": "https://example.com/share/abc"},
            {"package": "com.disabled.x", "enabled": False,
             "account_username": "ghost"},
        ],
    }
    autostart.set_config(cfg)
    payload = autostart._build_status_payload(tool_version="1.0.0", channel="stable")
    pkgs = payload["packages"]
    # Only the two enabled packages.
    assert {p["package"] for p in pkgs} == {"com.litec.client", "com.moons.litesd"}
    for p in pkgs:
        assert p["state"] == "Dead"
        assert p["runtime_seconds"] == 0
        assert p["ram_mb"] == 0
    by_pkg = {p["package"]: p for p in pkgs}
    # Saved account_username surfaces as username.
    assert by_pkg["com.litec.client"]["username"] == "deng1629"
    # private_server_url is collapsed to a single bool — never leaked raw.
    assert by_pkg["com.moons.litesd"]["private_url_configured"] is True
    assert by_pkg["com.litec.client"]["private_url_configured"] is False
    # Username falls back to "" (UI will show "Unknown") when not detected.
    assert by_pkg["com.moons.litesd"]["username"] == ""


def test_config_falls_through_to_username_cache():
    autostart = _fresh()
    autostart.set_active_supervisor(None)
    cfg = {
        "roblox_packages": [
            {"package": "com.litec.client", "enabled": True,
             "account_username": ""},
        ],
        "package_username_cache": {"com.litec.client": "deng_from_cache"},
    }
    autostart.set_config(cfg)
    payload = autostart._build_status_payload(tool_version="1.0.0", channel="stable")
    assert payload["packages"][0]["username"] == "deng_from_cache"


def test_supervisor_active_overrides_config():
    autostart = _fresh()
    autostart.set_config({
        "roblox_packages": [
            {"package": "com.from.config", "enabled": True,
             "account_username": "config_user"},
        ],
    })
    autostart.set_active_supervisor(_FakeSupervisor([
        {"package": "com.from.supervisor", "username": "sup_user",
         "status": "Online"},
    ]))
    payload = autostart._build_status_payload(tool_version="1.0.0", channel="stable")
    pkgs = payload["packages"]
    assert len(pkgs) == 1
    assert pkgs[0]["package"] == "com.from.supervisor"
    assert pkgs[0]["state"] == "Online"


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

    def _spy(url, payload, **kw):
        captured["url"] = url
        captured["payload"] = dict(payload)
        return _ok_payload()

    with mock.patch("agent.safe_http.post_json", side_effect=_spy), \
         mock.patch.object(autostart.MonitorBridge, "start", return_value=True):
        autostart.ensure_monitor_bridge_started(
            license_key="DENG-1A2B-3C4D-5E6F-7890",
            install_id_hash="a" * 64,
            tool_version="1.0.0", channel="stable",
            device_label="Termux on Android",
            announce=False,
        )

    # Endpoint is the documented one.
    assert captured["url"].endswith("/api/monitor/bridge/issue-from-license")
    body = captured["payload"]
    assert set(body.keys()) == {
        "license_key", "install_id_hash",
        "device_label", "tool_version", "channel",
    }
    raw = json.dumps(body).lower()
    for banned in ("cookie", "roblosecurity", "password", "private_url", "secret"):
        assert banned not in raw, f"banned key '{banned}' present in issue payload"


# ── v1.0.2: monitor status summary is redacted ──────────────────────────────


def test_monitor_status_summary_never_includes_secrets():
    autostart = _fresh()
    autostart.set_config({
        "roblox_packages": [
            {"package": "com.litec.client", "enabled": True,
             "account_username": "deng1629",
             "roblox_cookie": "_|WARNING:-DO-NOT-SHARE-THIS|_super_secret"},
        ],
        "license": {"key": "DENG-1A2B-3C4D-5E6F-7890"},
    })
    # Pre-seed a token cache so the summary has something to report.
    from agent.monitor_bridge import DEFAULT_BRIDGE_URL
    autostart.BRIDGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    autostart.BRIDGE_CACHE_PATH.write_text(json.dumps({
        "bridge_url": DEFAULT_BRIDGE_URL,
        "bridge_token": "super-secret-token-xyz",
        "device_id": "dev-redacted",
        "expires_at": "2099-12-31T23:59:59Z",
        "expires_at_epoch": time.time() + 6 * 3600,
    }), encoding="utf-8")

    summary = autostart.get_monitor_status_summary()
    raw = json.dumps(summary).lower()

    assert summary["configured_packages"] == 1
    assert summary["autostart_enabled"] is True
    # No raw secrets must appear.
    for banned in (
        "super-secret-token-xyz",
        "deng-1a2b-3c4d-5e6f-7890",
        "_|warning:-do-not-share-this|_",
        "roblox_cookie",
        "license_key",
        "install_id",
    ):
        assert banned.lower() not in raw, f"banned token '{banned}' leaked into summary"
    # Cache presence flag is fine; raw token must not be.
    assert summary["token_cache"]["present"] is True
    assert "bridge_token" not in summary["token_cache"]
