import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch


class AutoExecuteHelperTests(unittest.TestCase):
    def test_build_execute_command_wraps_script(self):
        from agent.auto_execute import build_execute_command

        script = 'loadstring(game:HttpGet("https://example.com/Deng.lua"))()'
        self.assertEqual(build_execute_command(script), f"/execute {script}")

    def test_normalize_scripts_removes_blanks_and_duplicates(self):
        from agent.auto_execute import normalize_scripts

        self.assertEqual(
            normalize_scripts(["", " print(1) ", "print(1)", "print(2)"]),
            ["print(1)", "print(2)"],
        )

    def test_send_execute_uses_clipboard_paste_and_enter(self):
        from agent import auto_execute
        from agent.android import CommandResult

        calls: list[tuple[str, ...]] = []

        def fake_run(args, **_kwargs):
            calls.append(tuple(args))
            return CommandResult(tuple(args), 0, "", "")

        with patch("agent.auto_execute._focus_package", return_value=CommandResult((), 0, "", "")), \
             patch("agent.auto_execute.android.run_android_command", side_effect=fake_run), \
             patch("agent.auto_execute.time.sleep"):
            result = auto_execute.send_execute_command("com.roblox.client", "print(1)")

        self.assertTrue(result["success"], result)
        self.assertIn(("input", "keyevent", "KEYCODE_SLASH"), calls)
        self.assertIn(("input", "keyevent", "279"), calls)
        self.assertIn(("input", "keyevent", "KEYCODE_ENTER"), calls)
        clipboard_call = next(c for c in calls if c[:3] == ("sh", "-c", "cmd clipboard set '/execute print(1)'"))
        self.assertEqual(clipboard_call[2], "cmd clipboard set '/execute print(1)'")

    def test_run_auto_execute_once_per_package_script(self):
        from agent import auto_execute

        ran: set[tuple[str, str]] = set()
        with patch("agent.auto_execute.send_execute_command", return_value={"success": True, "method": "test"} ) as send:
            auto_execute.run_auto_execute_for_package(
                {"auto_execute_scripts": ["print(1)"]},
                "com.roblox.client",
                ran,
                logger=MagicMock(),
            )
            auto_execute.run_auto_execute_for_package(
                {"auto_execute_scripts": ["print(1)"]},
                "com.roblox.client",
                ran,
                logger=MagicMock(),
            )

        send.assert_called_once_with("com.roblox.client", "print(1)")


class AutoExecuteMenuTests(unittest.TestCase):
    def test_auto_execute_menu_add_script_saves_to_config(self):
        from agent import commands

        cfg = {"auto_execute_scripts": []}
        prompts = iter(["1", "y", 'loadstring(game:HttpGet("https://example.com/Deng.lua"))()', "", "n", "0"])
        prompt_texts: list[str] = []
        out = io.StringIO()
        def fake_prompt(prompt="", **_kwargs):
            prompt_texts.append(prompt)
            return next(prompts)

        with patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands.safe_io.safe_prompt", side_effect=fake_prompt), \
             patch("agent.commands.safe_io.press_enter"), \
             patch("agent.commands.save_config", side_effect=lambda c: c), \
             redirect_stdout(out):
            result = commands._config_menu_auto_execute(cfg)

        self.assertEqual(
            result["auto_execute_scripts"],
            ['loadstring(game:HttpGet("https://example.com/Deng.lua"))()'],
        )
        self.assertTrue(any("Add script #1" in prompt for prompt in prompt_texts))
        self.assertIn("Saved 1 Auto Execute script(s).", out.getvalue())

    def test_auto_execute_menu_adds_multiple_numbered_scripts(self):
        from agent import commands

        cfg = {"auto_execute_scripts": []}
        prompts = iter(["1", "y", "print(1)", "", "y", "print(2)", "", "n", "0"])
        prompt_texts: list[str] = []
        out = io.StringIO()
        def fake_prompt(prompt="", **_kwargs):
            prompt_texts.append(prompt)
            return next(prompts)

        with patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands.safe_io.safe_prompt", side_effect=fake_prompt), \
             patch("agent.commands.safe_io.press_enter"), \
             patch("agent.commands.save_config", side_effect=lambda c: c), \
             redirect_stdout(out):
            result = commands._config_menu_auto_execute(cfg)

        self.assertEqual(result["auto_execute_scripts"], ["print(1)", "print(2)"])
        text = out.getvalue()
        self.assertTrue(any("Add script #1" in prompt for prompt in prompt_texts))
        self.assertTrue(any("Add script #2" in prompt for prompt in prompt_texts))
        self.assertIn("Saved 2 Auto Execute script(s).", text)


class SupervisorAutoExecuteTests(unittest.TestCase):
    def test_handle_online_runs_auto_execute_before_ram_check(self):
        from agent.supervisor import STATUS_ONLINE
        from tests.test_ram_optimization import _ENTRY, _PKG, _make_supervisor

        sup = _make_supervisor({"auto_execute_scripts": ["print(1)"]})
        sup._auto_execute_ran = set()
        sup._check_ram_optimization = MagicMock()

        with patch("agent.supervisor.effective_private_server_url", return_value=""), \
             patch("agent.supervisor.auto_execute.run_auto_execute_for_package") as run:
            sup._handle_state(_PKG, _ENTRY, STATUS_ONLINE, STATUS_ONLINE, 123.0)

        run.assert_called_once_with(
            sup.cfg,
            _PKG,
            sup._auto_execute_ran,
            logger=sup._logger,
        )
        sup._check_ram_optimization.assert_called_once()


if __name__ == "__main__":
    unittest.main()
