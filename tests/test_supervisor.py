"""God-mode liveness ordering regression tests."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.supervisor import STATUS_NO_HEARTBEAT, STATUS_ONLINE, WatchdogSupervisor


_PKG = "com.moons.litesc"


def _entry() -> dict:
    return {"package": _PKG, "enabled": True, "roblox_user_id": 12345}


class TestGodModeLiveness(unittest.TestCase):
    def _supervisor(self) -> WatchdogSupervisor:
        supervisor = WatchdogSupervisor([_entry()], {"supervisor": {}})
        supervisor._last_launched_at[_PKG] = time.monotonic() - (
            supervisor.LOADING_GRACE_SECONDS + 1
        )
        return supervisor

    @staticmethod
    def _lua_server(*, fresh: bool) -> MagicMock:
        server = MagicMock()
        server.is_fresh.return_value = fresh
        server.get_record.return_value = {"count": 0}
        server.age_seconds.return_value = None
        return server

    def test_fresh_lua_heartbeat_wins_without_presence_call(self) -> None:
        supervisor = self._supervisor()
        supervisor._lua_heartbeat_server = self._lua_server(fresh=True)
        with \
             patch.object(supervisor, "_fetch_presence") as fetch:
            state, detail = supervisor._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail["reason"], "local_lua_heartbeat_fresh")
        fetch.assert_not_called()

    def test_stale_lua_uses_cookie_presence_before_no_heartbeat(self) -> None:
        supervisor = self._supervisor()
        presence = MagicMock()
        presence.is_in_game = True
        presence.is_lobby = False
        presence.is_offline = False
        presence.is_unknown = False
        supervisor._lua_heartbeat_server = self._lua_server(fresh=False)
        with \
             patch.object(supervisor, "_fetch_presence", return_value=presence) as fetch:
            state, detail = supervisor._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail["reason"], "roblox_presence_in_game")
        fetch.assert_called_once_with(_PKG, force_cookie_rescan=False)

    def test_stale_lua_and_offline_presence_is_no_heartbeat(self) -> None:
        supervisor = self._supervisor()
        presence = MagicMock()
        presence.is_in_game = False
        presence.is_lobby = False
        presence.is_offline = True
        presence.is_unknown = False
        supervisor._lua_heartbeat_server = self._lua_server(fresh=False)
        with \
             patch.object(supervisor, "_fetch_presence", return_value=presence):
            state, detail = supervisor._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_NO_HEARTBEAT)
        self.assertEqual(detail["reason"], "presence_offline")


if __name__ == "__main__":
    unittest.main()
