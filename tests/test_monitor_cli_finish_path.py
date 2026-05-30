from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


VALID_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 128


@pytest.fixture(autouse=True)
def _sandbox_app_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DENG_REJOIN_HOME", str(tmp_path / "rejoin"))
    for mod in ("agent.commands", "agent.monitor_autostart", "agent.constants"):
        sys.modules.pop(mod, None)
    agent_pkg = sys.modules.get("agent")
    if agent_pkg is not None:
        for attr in ("commands", "monitor_autostart", "constants"):
            try:
                delattr(agent_pkg, attr)
            except AttributeError:
                pass
    yield
    for mod in ("agent.commands", "agent.monitor_autostart", "agent.constants"):
        sys.modules.pop(mod, None)
    agent_pkg = sys.modules.get("agent")
    if agent_pkg is not None:
        for attr in ("commands", "monitor_autostart", "constants"):
            try:
                delattr(agent_pkg, attr)
            except AttributeError:
                pass


def _monitor_args() -> argparse.Namespace:
    return argparse.Namespace(monitor_subcommand="status", snapshot_upload_probe=False)


def test_doctor_versions_missing_metadata_and_latest_check_does_not_crash(monkeypatch, capsys):
    from agent import commands
    from agent import build_info, install_registry

    monkeypatch.setattr(build_info, "collect_version_info", lambda: {})
    monkeypatch.setattr(build_info, "find_wrapper_path", lambda: "")
    monkeypatch.setattr(build_info, "load_installed_build", lambda: {})
    monkeypatch.setattr(build_info, "load_build_info", lambda: {})

    def boom(_requested: str):
        raise RuntimeError("server unavailable")

    monkeypatch.setattr(install_registry, "resolve_requested_public_version", boom)
    rc = commands._cmd_doctor_versions()
    out = capsys.readouterr().out
    assert rc == 0
    assert "Agent VERSION:" in out
    assert "Monitor implementation:" in out
    assert "Snapshot-test available: yes" in out
    assert "Latest server version:  unavailable" in out


def test_monitor_status_summary_does_not_probe_android_when_no_live_bridge(monkeypatch):
    from agent import monitor_autostart

    monitor_autostart.reset_for_tests()
    monkeypatch.setattr(
        monitor_autostart,
        "_build_status_payload",
        mock.Mock(side_effect=AssertionError("must not call status shell path")),
    )
    monitor_autostart.set_config({"roblox_packages": [{"package": "com.moons.litesc", "enabled": True}]})
    summary = monitor_autostart.get_monitor_status_summary()
    assert summary["configured_packages"] == 1
    assert summary["device_ram"] is None
    monitor_autostart._build_status_payload.assert_not_called()


def test_monitor_status_before_status_json_is_clean_starting_state(monkeypatch, tmp_path, capsys):
    from agent import commands, monitor_autostart

    pid_path = tmp_path / "monitor-bridge.pid"
    status_path = tmp_path / "monitor-bridge.status.json"
    log_path = tmp_path / "monitor-bridge.log"
    pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    monkeypatch.setattr(commands, "MONITOR_PID_PATH", pid_path)
    monkeypatch.setattr(commands, "MONITOR_STATUS_PATH", status_path)
    monkeypatch.setattr(commands, "MONITOR_LOG_PATH", log_path)
    monkeypatch.setattr(commands, "load_config", lambda: {"roblox_packages": []})
    monkeypatch.setattr(
        monitor_autostart,
        "get_monitor_status_summary",
        lambda: {"bridge_url": "https://tool.deng.my.id", "autostart_enabled": True, "token_cache": {"present": False}},
    )
    rc = commands.cmd_monitor(_monitor_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "Bridge worker running:  starting" in out
    assert "Status file:            missing" in out
    assert "cannot open" not in out


def test_monitor_status_after_status_json_uses_disk_state(monkeypatch, tmp_path, capsys):
    from agent import commands, monitor_autostart

    pid_path = tmp_path / "monitor-bridge.pid"
    status_path = tmp_path / "monitor-bridge.status.json"
    log_path = tmp_path / "monitor-bridge.log"
    pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    status_path.write_text(json.dumps({
        "worker_pid": os.getpid(),
        "worker_running": True,
        "bridge_running": True,
        "connected": True,
        "last_push_result": "success",
        "configured_packages": 8,
        "reported_packages": 8,
        "device_ram": {"available_mb": 13863, "total_mb": 15120, "percent": 92},
        "device_label": "SM-A515F",
        "snapshot_interval_seconds": 15,
        "snapshot_last_result": "success",
    }), encoding="utf-8")
    monkeypatch.setattr(commands, "MONITOR_PID_PATH", pid_path)
    monkeypatch.setattr(commands, "MONITOR_STATUS_PATH", status_path)
    monkeypatch.setattr(commands, "MONITOR_LOG_PATH", log_path)
    monkeypatch.setattr(commands, "load_config", lambda: {"roblox_packages": []})
    monkeypatch.setattr(
        monitor_autostart,
        "get_monitor_status_summary",
        lambda: {"bridge_url": "https://tool.deng.my.id", "autostart_enabled": True, "token_cache": {"present": False}},
    )
    rc = commands.cmd_monitor(_monitor_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "Bridge worker running:  yes" in out
    assert "Device connected:       yes" in out
    assert "Configured packages:    8" in out
    assert "Reported packages:      8" in out
    assert "RAM:                    13,863 MB / 15,120 MB 92%" in out
    assert "Device name:            SM-A515F" in out
    assert "Last snapshot result:   success" in out


def test_snapshot_test_success_uploads_latest_and_probe(monkeypatch, capsys):
    from agent import commands
    from agent import probe, snapshot

    monkeypatch.setattr(snapshot, "snapshot_test_report", lambda: {
        "su_available": True,
        "root_disabled": False,
        "providers": [{"provider": "root_screencap_stdout", "command": "su -c screencap -p", "exit_code": 0, "timeout_seconds": 12, "timed_out": False, "found": True, "byte_length": len(VALID_PNG), "png_valid": True, "suspicious_small": False}],
        "selected_provider": "root_screencap_stdout",
        "selected_bytes": len(VALID_PNG),
        "final_result": "success",
    })
    monkeypatch.setattr(snapshot, "capture_snapshot_detailed", lambda: SimpleNamespace(ok=True, data=VALID_PNG, mime="image/png"))
    monkeypatch.setattr(commands, "_upload_snapshot_test_image", lambda *_a, **_k: (True, "http_200"))
    monkeypatch.setattr(commands, "_verify_latest_snapshot_fetch", lambda _cfg: (True, "http_200", "https://tool.deng.my.id/api/monitor/bridge/snapshot/latest"))
    monkeypatch.setattr(commands, "load_config", lambda: {})
    monkeypatch.setattr(probe, "collect_probe", lambda: {"probe_version": 1})
    captured = {}

    def upload_probe(doc):
        captured.update(doc)
        return True, "p-test123"

    monkeypatch.setattr(probe, "upload_probe", upload_probe)
    rc = commands._cmd_monitor_snapshot_test(upload_probe=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Snapshot latest upload: HTTP 200" in out
    assert "Backend latest visible: yes" in out
    assert "Probe upload:           OK" in out
    assert "Probe ID:               p-test123" in out
    assert "snapshot_live_test" in captured
    assert VALID_PNG not in json.dumps(captured, default=str).encode("utf-8")


def test_snapshot_test_upload_failure_still_uploads_probe_without_internal_error(monkeypatch, capsys):
    from agent import commands
    from agent import probe, snapshot

    monkeypatch.setattr(snapshot, "snapshot_test_report", lambda: {
        "su_available": True,
        "root_disabled": False,
        "providers": [],
        "selected_provider": "root_screencap_stdout",
        "selected_bytes": len(VALID_PNG),
        "final_result": "success",
    })
    monkeypatch.setattr(snapshot, "capture_snapshot_detailed", lambda: SimpleNamespace(ok=True, data=VALID_PNG, mime="image/png"))
    monkeypatch.setattr(commands, "_upload_snapshot_test_image", lambda *_a, **_k: (False, "http_500"))
    monkeypatch.setattr(commands, "load_config", lambda: {})
    monkeypatch.setattr(probe, "collect_probe", lambda: {"probe_version": 1})
    monkeypatch.setattr(probe, "upload_probe", lambda _doc: (True, "p-test456"))
    rc = commands._cmd_monitor_snapshot_test(upload_probe=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Snapshot latest upload: FAILED (http_500)" in out
    assert "Probe ID:               p-test456" in out
    assert "internal error" not in out.lower()


def test_snapshot_test_probe_upload_exception_is_reported_not_crashed(monkeypatch, capsys):
    from agent import commands
    from agent import probe, snapshot

    monkeypatch.setattr(snapshot, "snapshot_test_report", lambda: {
        "su_available": True,
        "root_disabled": False,
        "providers": [],
        "selected_provider": "root_screencap_stdout",
        "selected_bytes": len(VALID_PNG),
        "final_result": "success",
    })
    monkeypatch.setattr(snapshot, "capture_snapshot_detailed", lambda: SimpleNamespace(ok=True, data=VALID_PNG, mime="image/png"))
    monkeypatch.setattr(commands, "_upload_snapshot_test_image", lambda *_a, **_k: (True, "http_200"))
    monkeypatch.setattr(commands, "_verify_latest_snapshot_fetch", lambda _cfg: (True, "http_200", "url"))
    monkeypatch.setattr(commands, "load_config", lambda: {})
    monkeypatch.setattr(probe, "collect_probe", lambda: {"probe_version": 1})
    monkeypatch.setattr(probe, "upload_probe", mock.Mock(side_effect=RuntimeError("boom")))
    rc = commands._cmd_monitor_snapshot_test(upload_probe=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "Probe upload:           ERROR" in out
    assert "internal error" not in out.lower()
