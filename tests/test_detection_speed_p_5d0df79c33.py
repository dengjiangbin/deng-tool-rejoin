"""Regression tests for the detection-speed rework (probe p-5d0df79c33).

User report: detection took ~5 minutes per transition despite the dedicated
push port + in-game Lua detector; "other" error codes and wrong-server moves
were not caught.  Root cause: the fresh in-game heartbeat (the fast truth) was
ignored — push "online" was suppressed for 20s after launch, required a resolved
device UID, the slow dumpsys/uiautomator/logcat scrape + presence round-trip ran
every round anyway, and a package sitting in the wrong server kept getting
re-confirmed Online by its next (unchanged) heartbeat.

These tests pin the fast path: a fresh heartbeat is authoritative and
instantaneous, and a moved package stays flagged.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import detection_lua, supervisor
from agent.rjn_lifecycle_monitor import (
    LAUNCH_ONLINE_FALLBACK_MIN_AGE_SECONDS,
    RjnLifecycleMonitor,
)

PKG = "com.roblox.clientab"


def _monitor() -> RjnLifecycleMonitor:
    m = RjnLifecycleMonitor([PKG])
    m.start_session()
    m.note_launch_watchdog(PKG, relaunch=True)
    # Make evaluate_package cheap + deterministic: process alive, no scrapes.
    m._process_check = lambda pkg: (True, ["123"])  # type: ignore[assignment]
    m._ensure_logcat_stream = lambda: None  # type: ignore[assignment]
    m._poll_recent_logcat = lambda: None  # type: ignore[assignment]
    return m


class PushOnlineIsInstantTests(unittest.TestCase):
    def test_push_confirms_online_inside_launch_window(self) -> None:
        # A heartbeat that arrives 0s after a (re)launch must confirm Online
        # immediately — NOT wait out the 20s launch-window debounce.
        m = _monitor()
        self.assertGreater(LAUNCH_ONLINE_FALLBACK_MIN_AGE_SECONDS, 0)  # window exists
        verdict = m.ingest_push_heartbeat(PKG, alive=True, place_id=111, universe_id=222, job_id="srvA")
        self.assertEqual(verdict, "online")
        ev = m.evaluate_package(PKG, fast_push=True)
        self.assertTrue(ev.is_online_confirmed)  # confirmed despite fresh launch

    def test_push_online_does_not_require_resolved_uid(self) -> None:
        # No UID resolved for this clone, yet a heartbeat still flips it Online.
        m = _monitor()
        m.ingest_push_heartbeat(PKG, alive=True, place_id=111, universe_id=222, job_id="srvA")
        ev = m.evaluate_package(PKG, fast_push=True)
        # is_online_confirmed must be True even though no UID resolved (the
        # missing UID may still be listed as a soft diagnostic check, but it must
        # NOT veto Online for the push_heartbeat source).
        self.assertTrue(ev.is_online_confirmed)


class FastPushSkipsScrapeTests(unittest.TestCase):
    def test_fast_push_skips_logcat_dump_and_ui_scan(self) -> None:
        m = _monitor()
        m.ingest_push_heartbeat(PKG, alive=True, place_id=111, universe_id=222, job_id="srvA")
        with patch.object(m, "_scan_logcat_dump") as dump, \
             patch.object(m, "_detect_live_disconnect", return_value=("", "")) as disc:
            m.evaluate_package(PKG, fast_push=True)
        dump.assert_not_called()
        disc.assert_not_called()

    def test_slow_path_still_scrapes(self) -> None:
        m = _monitor()
        m.ingest_push_heartbeat(PKG, alive=True, place_id=111, universe_id=222, job_id="srvA")
        with patch.object(m, "_scan_logcat_dump") as dump:
            m.evaluate_package(PKG, fast_push=False)
        dump.assert_called_once()


class WrongServerStaysLatchedTests(unittest.TestCase):
    def test_unchanged_heartbeat_in_wrong_server_does_not_reconfirm_online(self) -> None:
        m = _monitor()
        # First server is the legit anchor.
        self.assertEqual(
            m.ingest_push_heartbeat(PKG, alive=True, place_id=111, universe_id=222, job_id="srvA"),
            "online",
        )
        # Moved to a different server of the same game.
        self.assertEqual(
            m.ingest_push_heartbeat(PKG, alive=True, place_id=111, universe_id=222, job_id="srvB"),
            "wrong_server",
        )
        # The SAME (unchanged) wrong-server heartbeat must keep it flagged, not
        # flip it back to Online — the old "elif changed" gate did exactly that.
        self.assertEqual(
            m.ingest_push_heartbeat(PKG, alive=True, place_id=111, universe_id=222, job_id="srvB"),
            "wrong_server",
        )
        ev = m.evaluate_package(PKG, fast_push=True)
        self.assertFalse(ev.is_online_confirmed)
        self.assertEqual(ev.internal_state, "DISCONNECTED")

    def test_different_game_placeid_flags_wrong_server(self) -> None:
        m = _monitor()
        m.ingest_push_heartbeat(PKG, alive=True, place_id=111, universe_id=222, job_id="srvA")
        verdict = m.ingest_push_heartbeat(PKG, alive=True, place_id=999, universe_id=888, job_id="srvA")
        self.assertEqual(verdict, "wrong_server")


class PushFreshHelperTests(unittest.TestCase):
    def _sup(self) -> supervisor.WatchdogSupervisor:
        entry = {"package": PKG, "account_username": "U"}
        return supervisor.WatchdogSupervisor([entry], {"roblox_packages": [entry]})

    def test_fresh_when_recent(self) -> None:
        sup = self._sup()
        sup._push_ever_seen.add(PKG)
        sup._push_last_seen[PKG] = time.time()
        self.assertTrue(sup._push_fresh(PKG))

    def test_stale_when_old(self) -> None:
        sup = self._sup()
        sup._push_ever_seen.add(PKG)
        sup._push_last_seen[PKG] = time.time() - (supervisor.PUSH_HEARTBEAT_FRESH_SECONDS + 5)
        self.assertFalse(sup._push_fresh(PKG))

    def test_never_seen_is_not_fresh(self) -> None:
        sup = self._sup()
        self.assertFalse(sup._push_fresh(PKG))


class SpeedConstantsTests(unittest.TestCase):
    def test_thresholds_target_sub_15s(self) -> None:
        # Detector posts every 3s; fresh/loss windows keep detection well under
        # the user's 15s ceiling.
        self.assertLessEqual(detection_lua.DEFAULT_HEARTBEAT_INTERVAL, 3)
        self.assertLessEqual(supervisor.PUSH_HEARTBEAT_FRESH_SECONDS, 12.0)
        self.assertLessEqual(supervisor.PUSH_HEARTBEAT_LOSS_SECONDS, 15.0)
        # Fresh must be <= loss so a heartbeat is never both "fresh" and "lost".
        self.assertLessEqual(
            supervisor.PUSH_HEARTBEAT_FRESH_SECONDS, supervisor.PUSH_HEARTBEAT_LOSS_SECONDS
        )


if __name__ == "__main__":
    unittest.main()
