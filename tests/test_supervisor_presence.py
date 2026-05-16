"""Tests verifying the supervisor uses Roblox presence as ground truth.

When Roblox's presence API says the configured account is InGame, the table
MUST show ``Online`` regardless of what local dumpsys / pidof reports.  This
is the screenshot bug the user reported: clones visibly playing while local
heuristics said "Preparing" / "Offline".
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from agent.monitor import HealthResult
from agent.roblox_presence import PresenceResult, PresenceType
from agent.supervisor import (
    STATUS_LOBBY,
    STATUS_ONLINE,
    _PackageWorker,
)


def _make_cfg(pkg: str) -> dict:
    return {
        "roblox_package": pkg,
        "health_check_interval_seconds": 30,
        "foreground_grace_seconds": 30,
        "supervisor": {
            "enabled": True,
            "health_check_interval_seconds": 1,
            "launch_grace_seconds": 1,
        },
        "auto_rejoin_enabled": True,
        "package_entries": [],
        "log_level": "INFO",
    }


def _make_entry(pkg: str, username: str = "TestAcc", user_id: int | None = None) -> dict:
    e = {"package": pkg, "account_username": username, "auto_reopen_enabled": True}
    if user_id:
        e["roblox_user_id"] = user_id
    return e


def _run_one_iteration(worker: _PackageWorker, *,
                       presence: PresenceResult | None,
                       health_state: str = "healthy",
                       meta: dict | None = None) -> str:
    """Drive one iteration of the worker and return the resulting status."""
    pkg = worker.package
    stop_event = worker.stop_event

    def health_side_effect(_cfg, _package):
        stop_event.set()
        return HealthResult(health_state, "ok", meta or {})

    def presence_side_effect(_user_id, **kw):
        return presence

    with mock.patch("agent.supervisor.check_package_health",
                    side_effect=health_side_effect), \
         mock.patch("agent.supervisor.db"), \
         mock.patch("agent.supervisor.log_event"), \
         mock.patch("agent.config.effective_private_server_url", return_value=""), \
         mock.patch("agent.roblox_presence.fetch_presence_one",
                    side_effect=presence_side_effect), \
         mock.patch("agent.roblox_presence.lookup_user_id", return_value=12345):
        worker.run()
    return worker.status_map[pkg]


class TestPresenceDrivesState(unittest.TestCase):
    """Roblox presence is authoritative — local heuristics never override it."""

    def test_in_game_presence_makes_status_online(self) -> None:
        pkg = "com.example.clone1"
        worker = _PackageWorker(
            entry=_make_entry(pkg, user_id=12345),
            cfg=_make_cfg(pkg),
            status_map={pkg: "Preparing"},
            stop_event=threading.Event(),
        )
        presence = PresenceResult(
            user_id=12345, presence_type=PresenceType.IN_GAME,
            place_id=999, last_location="Test Place",
        )
        # Even though our mocked health says "roblox_not_running" (i.e.
        # local dumpsys/pidof failed), the InGame presence MUST win.
        status = _run_one_iteration(
            worker, presence=presence,
            health_state="roblox_not_running",
            meta={"running": False, "task": False, "window": False, "surface": False,
                  "fg_evidence": False, "root_running": False},
        )
        self.assertEqual(status, STATUS_ONLINE)

    def test_online_presence_in_lobby_shows_lobby(self) -> None:
        pkg = "com.example.clone2"
        worker = _PackageWorker(
            entry=_make_entry(pkg, user_id=22),
            cfg=_make_cfg(pkg),
            status_map={pkg: ""},
            stop_event=threading.Event(),
        )
        presence = PresenceResult(user_id=22, presence_type=PresenceType.ONLINE)
        status = _run_one_iteration(
            worker, presence=presence,
            health_state="roblox_not_running",
            meta={},
        )
        self.assertEqual(status, STATUS_LOBBY)

    def test_offline_presence_falls_through_to_local_logic(self) -> None:
        """When presence==Offline, the worker doesn't short-circuit — it lets
        the existing local logic decide between Background/Reconnecting/Offline.
        """
        pkg = "com.example.clone3"
        worker = _PackageWorker(
            entry=_make_entry(pkg, user_id=33),
            cfg=_make_cfg(pkg),
            status_map={pkg: "Online"},
            stop_event=threading.Event(),
        )
        presence = PresenceResult(user_id=33, presence_type=PresenceType.OFFLINE)
        status = _run_one_iteration(
            worker, presence=presence,
            health_state="roblox_not_running",
            meta={"running": False, "task": False, "window": False,
                  "surface": False, "fg_evidence": False, "root_running": False},
        )
        # Not "Online" — falls through to the offline-handling path.
        self.assertNotEqual(status, STATUS_ONLINE)

    def test_unknown_presence_keeps_prior_state_logic(self) -> None:
        """When the API is unreachable (Unknown), the worker uses local heuristics."""
        pkg = "com.example.clone4"
        worker = _PackageWorker(
            entry=_make_entry(pkg, user_id=44),
            cfg=_make_cfg(pkg),
            status_map={pkg: "Online"},
            stop_event=threading.Event(),
        )
        status = _run_one_iteration(
            worker, presence=None,    # network failure
            health_state="healthy",
            meta={"running": True, "window": True},
        )
        # Healthy local state stays healthy.
        self.assertEqual(status, STATUS_ONLINE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
