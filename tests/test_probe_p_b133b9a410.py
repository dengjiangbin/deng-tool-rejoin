"""Regression context for dev-probe p-b133b9a410 (snapshot + package dashboard).

The probe was uploaded before ``snapshot_proof`` existed (probe_version 1).
These tests lock the *corroborating* evidence we can read from the stored
probe file so we do not regress package-count semantics or mis-label the
failure mode.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROBE_PATH = Path(__file__).resolve().parent.parent / "data" / "dev_probes" / "p-b133b9a410.json"


@pytest.mark.skipif(not PROBE_PATH.is_file(), reason="probe file not present on this host")
def test_probe_p_b133b9a410_has_eight_configured_packages_all_not_running() -> None:
    probe = json.loads(PROBE_PATH.read_text(encoding="utf-8"))
    assert probe.get("probe_id") == "p-b133b9a410"
    pkgs = (probe.get("config") or {}).get("roblox_packages") or []
    assert len(pkgs) == 8
    names = {e.get("package") for e in pkgs if isinstance(e, dict)}
    assert "com.moons.litesc" in names
    # pidof/pgrep failures for every configured package → none running.
    steps = {e.get("step", "") for e in (probe.get("errors") or [])}
    for pkg in names:
        assert any(pkg in s and "pidof" in s for s in steps)


@pytest.mark.skipif(not PROBE_PATH.is_file(), reason="probe file not present on this host")
def test_probe_p_b133b9a410_predates_snapshot_proof_section() -> None:
    probe = json.loads(PROBE_PATH.read_text(encoding="utf-8"))
    assert probe.get("probe_version") == 1
    assert "snapshot_proof" not in probe
    cfg = probe.get("config") or {}
    assert cfg.get("root_available") is True
    # Old agent builds kept root screencap behind root_mode_enabled.
    assert cfg.get("root_mode_enabled") is False
