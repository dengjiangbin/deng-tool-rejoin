from __future__ import annotations

import time
import unittest
from unittest import mock


class TestLobbyMapsToDeadRecovery(unittest.TestCase):
    def test_presence_lobby_resolves_to_dead(self) -> None:
        from agent import roblox_presence as rp

        presence = rp.PresenceResult(user_id=1, presence_type=rp.PresenceType.ONLINE)
        resolved = rp.resolve_presence_state(presence, process_alive=True)

        self.assertEqual(resolved.state, "Dead")
        self.assertEqual(resolved.server_verification, "not_playing")

    def test_dead_relaunches_only_affected_package(self) -> None:
        from agent.supervisor import STATUS_DEAD, STATUS_ONLINE, WatchdogSupervisor

        entries = [
            {"package": "com.roblox.dead", "enabled": True, "auto_reopen_enabled": True},
            {"package": "com.roblox.online", "enabled": True, "auto_reopen_enabled": True},
        ]
        sup = WatchdogSupervisor(entries, {"supervisor": {}})
        sup.status_map["com.roblox.dead"] = STATUS_DEAD
        sup.status_map["com.roblox.online"] = STATUS_ONLINE

        with mock.patch("agent.supervisor.effective_private_server_url", return_value=""), \
             mock.patch.object(sup, "_do_launch", return_value=True) as launch, \
             mock.patch("agent.supervisor.log_event"):
            sup._handle_state(entries[0]["package"], entries[0], STATUS_DEAD, STATUS_ONLINE, time.time())

        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0], "com.roblox.dead")
        self.assertEqual(sup.status_map["com.roblox.online"], STATUS_ONLINE)

