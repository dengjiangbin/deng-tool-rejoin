"""Regression tests for probe p-630c95f7cc.

User feedback after the logcat-heartbeat rollout:

1. While LAUNCHING/RELAUNCHING (2nd package to last), the detector falsely
   decided packages were dead "while they are only loading to the game" — the
   heartbeat-loss path fired during the launch storm. That false mass-dead is
   what stormed recovery into force-stopping every clone + Termux (critical
   kill-all bug). Fix: heartbeat-loss now stands down while this package is still
   launching, while ANY package launched recently (launch-quiet window — the
   storm CPU-throttles every online clone's heartbeat), and right after a
   join/teleport loading screen.
2. Leaving the map / lingering in the lobby (disconnect code 285) must report
   the plain reason "Account stays too long in the lobby" (covered in
   test_disconnect_codes_and_deeplink_resolve_2026_06_28).
3. Joining a different game must report "Account is not in configured server"
   (wrong_server friendly text), not the generic heartbeat-lost phrase.
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

from agent.constants import DATA_DIR
from agent import rjn_lifecycle_monitor as rlm
from agent.rjn_lifecycle_monitor import (
    RjnLifecycleMonitor,
    STATE_DISCONNECTED,
    STATE_ONLINE_CONFIRMED,
    INGAME_HB_LOSS_SECONDS,
    INGAME_HB_LOSS_LAUNCH_QUIET_SECONDS,
)


def _monitor(pkg: str, uid: str = "10104") -> RjnLifecycleMonitor:
    mon = RjnLifecycleMonitor([pkg])
    mon._uid_map = {pkg: uid}
    mon._monitor_started_at = time.time() - 120
    row = mon._states[pkg]
    row.uid = uid
    row.launch_started_at = time.time() - 90
    return mon


def _online(mon: RjnLifecycleMonitor, pkg: str) -> None:
    mon.ingest_push_heartbeat(
        pkg, alive=True, place_id=121864768012064, universe_id=6701277882, at=time.time()
    )
    assert mon._states[pkg].internal_state == STATE_ONLINE_CONFIRMED


def _silence(mon: RjnLifecycleMonitor, pkg: str) -> None:
    mon._states[pkg].last_ingame_hb_at = time.time() - (INGAME_HB_LOSS_SECONDS + 5)


def _evaluate(mon: RjnLifecycleMonitor, pkg: str):
    with patch.object(mon, "_process_check", return_value=(True, ["3721"])), \
         patch.object(mon, "_dump_pkg_logcat", return_value=[]):
        return mon.evaluate_package(pkg)


class HeartbeatLossLaunchGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def tearDown(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def test_launch_storm_suppresses_false_heartbeat_loss(self) -> None:
        # An online clone whose heartbeat is starved while ANOTHER clone launches
        # must NOT be demoted (this is the "2nd package to last killed while
        # loading" false positive that storms recovery → kill-all).
        pkg = "com.pkg.guard"
        mon = _monitor(pkg)
        _online(mon, pkg)
        mon.note_launch_watchdog("com.pkg.other", relaunch=True)  # launch storm now
        _silence(mon, pkg)
        ev = _evaluate(mon, pkg)
        self.assertEqual(ev.internal_state, STATE_ONLINE_CONFIRMED)
        self.assertTrue(ev.is_online_confirmed)

    def test_after_launch_quiet_real_heartbeat_loss_still_fires(self) -> None:
        # Once the launch storm has fully settled, a genuine heartbeat silence
        # (real kick / GL error / captcha) still demotes — detection preserved.
        pkg = "com.pkg.guard2"
        mon = _monitor(pkg)
        _online(mon, pkg)
        mon.note_launch_watchdog("com.pkg.other", relaunch=True)
        mon._last_any_launch_at = time.time() - (INGAME_HB_LOSS_LAUNCH_QUIET_SECONDS + 5)
        _silence(mon, pkg)
        ev = _evaluate(mon, pkg)
        self.assertEqual(ev.internal_state, STATE_DISCONNECTED)
        self.assertEqual(mon._states[pkg].last_transition_reason, "heartbeat_lost")

    def test_recent_join_loading_suppresses_false_heartbeat_loss(self) -> None:
        # Right after a join/teleport the heartbeat legitimately pauses during the
        # loading screen; do not demote a client that is simply loading.
        pkg = "com.pkg.loading"
        mon = _monitor(pkg)
        _online(mon, pkg)
        mon._states[pkg].last_gamejoinloadtime_at = time.time()
        _silence(mon, pkg)
        ev = _evaluate(mon, pkg)
        self.assertEqual(ev.internal_state, STATE_ONLINE_CONFIRMED)

    def test_packages_loading_in_watchdog_not_false_killed(self) -> None:
        # A package still inside its launch watchdog window must never be demoted
        # by heartbeat-loss even if a stale beat exists.
        pkg = "com.pkg.wd"
        mon = _monitor(pkg)
        _online(mon, pkg)
        mon._states[pkg].watchdog_active = True
        _silence(mon, pkg)
        ev = _evaluate(mon, pkg)
        self.assertNotEqual(ev.internal_state, STATE_DISCONNECTED)


class ReasonTextTests(unittest.TestCase):
    def test_wrong_server_reason_text(self) -> None:
        from agent.lifecycle_reasons import format_user_friendly_dead_reason

        self.assertEqual(
            format_user_friendly_dead_reason("wrong_server"),
            "Account is not in configured server",
        )

    def test_lobby_285_reason_text(self) -> None:
        from agent.roblox_disconnect_reasons import format_lifecycle_dead_reason

        self.assertEqual(
            format_lifecycle_dead_reason(
                "disconnect_code_285", "Sending disconnect with reason: 285"
            ),
            "Account stays too long in the lobby/wrong server",
        )


if __name__ == "__main__":
    unittest.main()
