"""Tests for Lime-style detection speed tracking."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.detection_speed_test import (  # noqa: E402
    build_probe_proof_section,
    format_speed_test_report,
)
from agent.lime_detection_speed import (  # noqa: E402
    LimeDetectionSpeedTracker,
    probe_lime_detection_speed_snapshot,
    read_lime_state_file,
)
from agent.ocr_screen_detector import OcrScreenDetector  # noqa: E402

PKG = "com.moons.litesc"


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class OcrMatchTests(unittest.TestCase):
    def test_match_kick_phrases(self) -> None:
        match = OcrScreenDetector.match_text("You were kicked from this experience")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.phrase, "kicked")

    def test_match_bot_challenge(self) -> None:
        match = OcrScreenDetector.match_text("Complete the challenge to continue")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertIn("challenge", match.phrase.lower())


class LimeTimestampTests(unittest.TestCase):
    def test_detection_latency_from_baseline(self) -> None:
        clock = FakeClock(1000.0)
        monitor = MagicMock()
        monitor.packages = [PKG]
        tracker = LimeDetectionSpeedTracker([PKG], monitor=monitor, clock=clock.now)
        tracker.set_evidence_baseline(PKG, at=clock.now())
        clock.advance(0.4)
        tracker.note_process_dead(PKG, at=clock.now())
        snap = tracker.probe_snapshot()
        row = snap["packages"][PKG]
        self.assertEqual(row["process_dead_detected_at"], 1000.4)
        self.assertEqual(row["detection_latency_ms"], 400.0)

    def test_online_and_checking_timestamps(self) -> None:
        clock = FakeClock(2000.0)
        monitor = MagicMock()
        monitor.packages = [PKG]
        tracker = LimeDetectionSpeedTracker([PKG], monitor=monitor, clock=clock.now)
        tracker.note_online_evidence(PKG, at=clock.now(), source="gamejoinloadtime")
        clock.advance(0.2)
        tracker.note_checking_committed(PKG, at=clock.now(), state="Online")
        clock.advance(0.1)
        tracker.note_recovery_requested(PKG, at=clock.now())
        snap = tracker.probe_snapshot()
        row = snap["packages"][PKG]
        self.assertEqual(row["online_evidence_at"], 2000.0)
        self.assertEqual(row["checking_committed_state_at"], 2000.2)
        self.assertEqual(row["recovery_requested_at"], 2000.3)


class LimeStateFileTests(unittest.TestCase):
    def test_state_file_roundtrip(self) -> None:
        clock = FakeClock(3000.0)
        monitor = MagicMock()
        monitor.packages = [PKG]
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "lime-state.json"
            with patch("agent.lime_detection_speed.LIME_STATE_PATH", state_path):
                tracker = LimeDetectionSpeedTracker([PKG], monitor=monitor, clock=clock.now)
                tracker.set_evidence_baseline(PKG)
                clock.advance(0.8)
                tracker.note_logcat_dead(PKG, evidence="idle_disconnect_278")
                tracker._write_state_file(force=True)
                disk = read_lime_state_file(max_age_s=60.0)
                self.assertIsNotNone(disk)
                assert disk is not None
                row = disk["packages"][PKG]
                self.assertEqual(row["logcat_dead_detected_at"], 3000.8)


class LimeChannelGateTests(unittest.TestCase):
    def test_lime_only_on_test_latest2_channel(self) -> None:
        from agent.lime_channel import is_lime_detection_channel

        self.assertTrue(is_lime_detection_channel("test-latest2"))
        self.assertFalse(is_lime_detection_channel("main-dev"))


class ProbeProofTests(unittest.TestCase):
    def test_build_probe_proof_section(self) -> None:
        snap = {
            "enabled": True,
            "cookie_auto_extract": False,
            "packages": {
                PKG: {
                    "process_dead_detected_at": 1.0,
                    "logcat_dead_detected_at": 1.1,
                    "ocr_dead_detected_at": None,
                    "online_evidence_at": 2.0,
                    "checking_committed_state_at": 2.1,
                    "recovery_requested_at": 2.2,
                    "detection_latency_ms": 100.0,
                }
            },
        }
        proof = build_probe_proof_section(snap)
        self.assertEqual(proof["detection_latency_ms"], 100.0)
        self.assertFalse(proof["cookie_auto_extract"])
        self.assertIn("source_version", proof)
        self.assertIn("keyless_start_ok", proof)
        lines = format_speed_test_report(snap)
        self.assertTrue(any("process_dead_detected_at" in ln for ln in lines))


class CookieAutoScanDisabledTests(unittest.TestCase):
    def test_constants_default_disabled(self) -> None:
        from agent.constants import COOKIE_AUTO_SCAN_DISABLED

        self.assertTrue(COOKIE_AUTO_SCAN_DISABLED)

    def test_ensure_presence_auth_is_noop(self) -> None:
        from agent.commands import _ensure_presence_auth_for_entries

        entries = [{"package": PKG}]
        out = _ensure_presence_auth_for_entries(entries, {})
        self.assertEqual(out[0]["package"], PKG)
        self.assertNotIn("roblox_cookie", out[0])


class ProbeCollectTests(unittest.TestCase):
    def test_probe_includes_lime_section(self) -> None:
        from agent import probe as probe_mod

        with patch(
            "agent.lime_detection_speed.probe_lime_detection_speed_snapshot",
            return_value={"enabled": False, "packages": {}, "cookie_auto_extract": False},
        ):
            doc = probe_mod.collect_probe()
        self.assertIn("lime_detection_speed", doc)


if __name__ == "__main__":
    unittest.main()
