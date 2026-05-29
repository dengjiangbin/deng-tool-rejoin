"""Tests for agent/snapshot.py — v1.0.6 fullscreen capture ladder.

These tests isolate the capture logic by stubbing the single ``_run`` seam
(every provider rung shells out through it) plus su/binary availability, so
no real ``screencap``/``su`` is ever executed. They prove:

  * normal screencap success
  * empty output falls through to the next provider
  * root screencap stdout success
  * root screencap file success (write then read-back)
  * invalid PNG is rejected (never uploaded as an image)
  * root denial is classified as failed_root_denied
  * timeouts are recorded
  * provider-attempt list is populated for the probe
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import agent.snapshot as snap

VALID_PNG = snap.PNG_SIGNATURE + b"\x00" * 20_000  # > 10 KB, valid header


def _install_run(monkeypatch, handler):
    """Replace snapshot._run with a handler(cmd) -> (rc, stdout, stderr, kind)."""
    monkeypatch.setattr(snap, "_run", lambda cmd, *, timeout=snap.ATTEMPT_TIMEOUT: handler(cmd))


def _disable_system_binary(monkeypatch):
    # No /system/bin/screencap rung unless a test wants it.
    monkeypatch.setattr(snap.os.path, "isfile", lambda p: False)


def test_png_signature_constant():
    assert snap.PNG_SIGNATURE == b"\x89PNG\r\n\x1a\n"


def test_normal_screencap_success(monkeypatch, tmp_path):
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(snap, "_su_available", lambda: False)
    _disable_system_binary(monkeypatch)

    def handler(cmd):
        if cmd[:2] == ["screencap", "-p"]:
            return 0, VALID_PNG, b"", None
        return None, b"", b"", "not_found"

    _install_run(monkeypatch, handler)
    cap = snap.capture_snapshot_detailed()
    assert cap.ok
    assert cap.result == snap.RESULT_SUCCESS
    assert cap.provider == "normal_screencap"
    assert cap.png_valid is True
    assert cap.byte_length == len(VALID_PNG)


def test_empty_output_falls_through_to_root_stdout(monkeypatch, tmp_path):
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(snap, "_su_available", lambda: True)
    _disable_system_binary(monkeypatch)

    def handler(cmd):
        if cmd[:2] == ["screencap", "-p"]:
            return 0, b"", b"", None  # empty → fall through
        if cmd[:3] == ["su", "-c", "screencap -p"]:
            return 0, VALID_PNG, b"", None
        return None, b"", b"", "not_found"

    _install_run(monkeypatch, handler)
    cap = snap.capture_snapshot_detailed()
    assert cap.ok
    assert cap.provider == "root_screencap_stdout"
    assert cap.root_granted is True


def test_root_screencap_file_success(monkeypatch, tmp_path):
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(snap, "_su_available", lambda: True)
    _disable_system_binary(monkeypatch)

    def handler(cmd):
        # normal + root-stdout produce nothing.
        if cmd[:2] == ["screencap", "-p"]:
            return 0, b"", b"", None
        if cmd[:3] == ["su", "-c", "screencap -p"]:
            return 0, b"", b"", None
        # write to file
        if cmd[:3] == ["su", "-c", f"screencap -p {snap.ROOT_TMP_PATH}"]:
            return 0, b"", b"", None
        # read back via cat (direct read fails because file doesn't exist)
        if cmd[:3] == ["su", "-c", f"cat {snap.ROOT_TMP_PATH}"]:
            return 0, VALID_PNG, b"", None
        if cmd[:2] == ["su", "-c"] and cmd[2].startswith("rm -f"):
            return 0, b"", b"", None
        return None, b"", b"", "not_found"

    _install_run(monkeypatch, handler)
    cap = snap.capture_snapshot_detailed()
    assert cap.ok
    assert cap.provider == "root_screencap_file"
    assert cap.png_valid is True


def test_invalid_png_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(snap, "_su_available", lambda: False)
    _disable_system_binary(monkeypatch)

    def handler(cmd):
        if cmd[:2] == ["screencap", "-p"]:
            return 0, b"/system/bin/sh: screencap: inaccessible", b"", None
        return None, b"", b"", "not_found"

    _install_run(monkeypatch, handler)
    cap = snap.capture_snapshot_detailed()
    assert not cap.ok
    assert cap.result == snap.RESULT_INVALID_PNG
    assert cap.data is None


def test_root_denied_is_classified(monkeypatch, tmp_path):
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(snap, "_su_available", lambda: True)
    _disable_system_binary(monkeypatch)

    def handler(cmd):
        if cmd[:2] == ["screencap", "-p"]:
            return 0, b"", b"", None  # empty
        if cmd[0] == "su":
            return 1, b"", b"su: permission denied", None
        return None, b"", b"", "not_found"

    _install_run(monkeypatch, handler)
    cap = snap.capture_snapshot_detailed()
    assert not cap.ok
    assert cap.result == snap.RESULT_ROOT_DENIED
    assert cap.root_granted is False


def test_timeout_is_recorded(monkeypatch, tmp_path):
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(snap, "_su_available", lambda: False)
    _disable_system_binary(monkeypatch)

    def handler(cmd):
        if cmd[:2] == ["screencap", "-p"]:
            return None, b"", b"", "timeout"
        return None, b"", b"", "not_found"

    _install_run(monkeypatch, handler)
    cap = snap.capture_snapshot_detailed()
    assert not cap.ok
    assert cap.result == snap.RESULT_TIMEOUT
    assert any(a.timeout for a in cap.attempts)


def test_provider_attempt_list_populated(monkeypatch, tmp_path):
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(snap, "_su_available", lambda: True)
    _disable_system_binary(monkeypatch)

    def handler(cmd):
        return 0, b"", b"", None  # everything empty

    _install_run(monkeypatch, handler)
    cap = snap.capture_snapshot_detailed()
    names = [a.provider for a in cap.attempts]
    # normal + 3 root rungs (system binary disabled in this test)
    assert "normal_screencap" in names
    assert "root_screencap_stdout" in names
    assert "root_screencap_file" in names
    assert "root_system_screencap" in names
    # to_safe_dict serializes cleanly for the probe
    d = cap.to_safe_dict()
    assert "attempts" in d and isinstance(d["attempts"], list)


def test_no_screencap_and_no_su(monkeypatch, tmp_path):
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(snap, "_su_available", lambda: False)
    _disable_system_binary(monkeypatch)

    def handler(cmd):
        return None, b"", b"", "not_found"

    _install_run(monkeypatch, handler)
    cap = snap.capture_snapshot_detailed()
    assert not cap.ok
    assert cap.result == snap.RESULT_NO_SCREENCAP


def test_backward_compatible_wrapper(monkeypatch, tmp_path):
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(snap, "_su_available", lambda: False)
    _disable_system_binary(monkeypatch)
    _install_run(monkeypatch, lambda cmd: (0, VALID_PNG, b"", None)
                 if cmd[:2] == ["screencap", "-p"] else (None, b"", b"", "not_found"))
    path, msg = snap.capture_snapshot()
    assert path is not None
    assert msg == "snapshot captured"


def test_snapshot_test_report_runs_all_providers(monkeypatch, tmp_path):
    """``deng-rejoin monitor snapshot-test`` must exercise every rung."""
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(snap, "_su_available", lambda: True)
    _disable_system_binary(monkeypatch)

    def handler(cmd):
        if cmd[:2] == ["screencap", "-p"]:
            return 0, VALID_PNG, b"", None
        return None, b"", b"", "not_found"

    _install_run(monkeypatch, handler)
    report = snap.snapshot_test_report()
    assert report["final_result"] == snap.RESULT_SUCCESS
    assert report["selected_provider"] == "normal_screencap"
    providers = [r["provider"] for r in report["providers"]]
    assert "normal_screencap" in providers
    assert "root_screencap_stdout" in providers
    assert "root_screencap_file" in providers
    assert all("command" in r and "png_valid" in r for r in report["providers"])


def test_root_disabled_env_skips_root(monkeypatch, tmp_path):
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setenv("DENG_REJOIN_SNAPSHOT_USE_SU", "0")
    monkeypatch.setattr(snap, "_su_available", lambda: True)
    _disable_system_binary(monkeypatch)
    _install_run(monkeypatch, lambda cmd: (0, b"", b"", None))
    cap = snap.capture_snapshot_detailed()
    names = [a.provider for a in cap.attempts]
    assert "root_screencap_stdout" not in names  # root disabled by env
