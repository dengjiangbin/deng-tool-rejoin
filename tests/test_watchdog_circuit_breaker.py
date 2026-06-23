"""Recovery circuit breaker and Roblox 429 safe-state regression tests."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.roblox_presence import RobloxRateLimitedError
from agent.supervisor import (
    STATUS_LAUNCHING,
    STATUS_NO_HEARTBEAT,
    STATUS_ONLINE,
    STATUS_SUSPENDED,
    WatchdogSupervisor,
)

_PKG = "com.roblox.client"
_PKG2 = "com.roblox.client2"


def _entry(pkg: str = _PKG) -> dict:
    return {
        "package": pkg,
        "account_username": "TestUser",
        "roblox_user_id": 12345,
        "enabled": True,
    }


def _cfg() -> dict:
    return {"supervisor": {}, "log_level": "INFO"}


def _alive_evidence() -> dict:
    return {
        "alive": True,
        "running": True,
        "root_running": False,
        "task": True,
        "window": True,
        "surface": False,
        "foreground": True,
        "process_missing": False,
    }


class TestRecoveryCircuitBreaker(unittest.TestCase):
    def test_recovery_gate_max_attempts_is_three(self) -> None:
        self.assertEqual(WatchdogSupervisor.RECOVERY_GATE_MAX_ATTEMPTS, 3)

    def test_gate_suspends_after_three_failed_recovery_cycles(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_ONLINE})
        eval_calls = {"n": 0}

        def _isolated(_pkg, _entry):
            eval_calls["n"] += 1
            return STATUS_LAUNCHING

        with patch.object(sup, "_evaluate_package_presence_isolated", side_effect=_isolated), \
             patch.object(sup, "_deploy_gate_recovery_cycle"), \
             patch.object(sup, "_interruptible_sleep"), \
             patch("agent.supervisor.log_event"):
            sup._run_blocking_recovery_gate(
                _PKG,
                _entry(),
                package_index=1,
                package_total=1,
            )

        self.assertEqual(sup.status_map.get(_PKG), STATUS_SUSPENDED)
        self.assertEqual(eval_calls["n"], 3)

    def test_suspended_package_skipped_in_watchdog_round(self) -> None:
        src = __import__("inspect").getsource(WatchdogSupervisor._run_watchdog_loop)
        self.assertIn("WATCHDOG_SKIP_SUSPENDED", src)
        self.assertIn("STATUS_SUSPENDED", src)


class TestPresenceRateLimitShield(unittest.TestCase):
    def test_rate_limit_backoff_constant_is_15_seconds(self) -> None:
        self.assertEqual(WatchdogSupervisor.PRESENCE_RATE_LIMIT_BACKOFF_SECONDS, 15.0)

    def test_http_429_preserves_online_state(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_ONLINE})
        sup._prev_state[_PKG] = STATUS_ONLINE
        sup._last_online_ts[_PKG] = time.time()

        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(
                 sup,
                 "_fetch_presence",
                 side_effect=RobloxRateLimitedError("presence"),
             ):
            state, detail = sup._detect_package_state(_PKG, _entry())

        self.assertEqual(state, STATUS_ONLINE)
        self.assertIn("preserve_state", detail["reason"])
        self.assertTrue(sup._presence_rate_limit_active())

    def test_rate_limited_round_triggers_backoff_sleep(self) -> None:
        sup = WatchdogSupervisor(
            [_entry(_PKG), _entry(_PKG2)],
            _cfg(),
            initial_status={_PKG: STATUS_ONLINE, _PKG2: STATUS_ONLINE},
        )
        sup.mark_all_launches_completed()
        sup._prev_state[_PKG] = STATUS_ONLINE
        sup._last_online_ts[_PKG] = time.time()
        sleep_calls: list[float] = []

        def _fetch_side_effect(pkg, **kwargs):
            if pkg == _PKG:
                raise RobloxRateLimitedError("presence")
            game = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
            game.is_in_game = True
            game.is_offline = False
            game.is_lobby = False
            game.is_unknown = False
            return game

        def _record_sleep(seconds: float) -> None:
            sleep_calls.append(float(seconds))

        with patch.object(sup, "_fetch_presence", side_effect=_fetch_side_effect), \
             patch.object(sup, "_interruptible_sleep", side_effect=_record_sleep), \
             patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch("agent.supervisor.db.insert_event"), \
             patch("agent.supervisor.db.insert_heartbeat"), \
             patch("agent.supervisor.log_event"):
            sup.start_daemon(display_interval=0.05)

            def _stop_soon() -> None:
                time.sleep(0.3)
                sup.stop("test")

            import threading
            threading.Thread(target=_stop_soon, daemon=True).start()
            if sup._watchdog_thread is not None:
                sup._watchdog_thread.join(timeout=5.0)

        self.assertIn(
            WatchdogSupervisor.PRESENCE_RATE_LIMIT_BACKOFF_SECONDS,
            sleep_calls,
            f"expected 15s rate-limit backoff, got {sleep_calls}",
        )
        self.assertEqual(sup.status_map.get(_PKG), STATUS_ONLINE)


if __name__ == "__main__":
    unittest.main()
