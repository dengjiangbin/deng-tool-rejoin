import unittest
from unittest.mock import MagicMock, patch


class AutoExecuteHelperTests(unittest.TestCase):
    def test_build_execute_command_is_disabled(self):
        from agent.auto_execute import AUTO_EXECUTE_DISABLED_MESSAGE, build_execute_command

        with self.assertRaisesRegex(RuntimeError, AUTO_EXECUTE_DISABLED_MESSAGE):
            build_execute_command("print(1)")

    def test_normalize_scripts_ignores_old_saved_scripts(self):
        from agent.auto_execute import normalize_scripts

        self.assertEqual(normalize_scripts(["", " print(1) ", "print(2)"]), [])

    def test_send_execute_command_does_not_touch_android_input(self):
        from agent import auto_execute

        with patch("agent.android.run_android_command") as run_android:
            result = auto_execute.send_execute_command("com.roblox.client", "print(1)")

        self.assertFalse(result["success"], result)
        self.assertEqual(result["method"], "disabled")
        self.assertIn("disabled", result["error"].lower())
        run_android.assert_not_called()

    def test_run_auto_execute_returns_empty_without_sending(self):
        from agent import auto_execute

        ran: set[tuple[str, str]] = set()
        with patch("agent.auto_execute.send_execute_command") as send:
            result = auto_execute.run_auto_execute_for_package(
                {"auto_execute_scripts": ["print(1)"]},
                "com.roblox.client",
                ran,
                logger=MagicMock(),
            )
        self.assertEqual(result, [])
        self.assertEqual(ran, set())
        send.assert_not_called()


class SupervisorAutoExecuteTests(unittest.TestCase):
    def test_handle_online_does_not_trigger_auto_execute_in_start_supervisor(self):
        from agent.supervisor import STATUS_ONLINE
        from tests.test_ram_optimization import _ENTRY, _PKG, _make_supervisor

        sup = _make_supervisor({"auto_execute_scripts": ["print(1)"]})
        sup._check_ram_optimization = MagicMock()

        with patch("agent.supervisor.effective_private_server_url", return_value=""), \
             patch("agent.supervisor.log_event") as log:
            sup._handle_state(_PKG, _ENTRY, STATUS_ONLINE, STATUS_ONLINE, 123.0)

        event_names = [call.args[2] for call in log.call_args_list]
        self.assertIn("[DENG_REJOIN_ONLINE_STABLE]", event_names)
        sup._check_ram_optimization.assert_called_once()


if __name__ == "__main__":
    unittest.main()
