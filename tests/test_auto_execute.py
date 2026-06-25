from __future__ import annotations

import inspect
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent import auto_execute, commands, termux_ui
from agent.config import default_config


class AutoExecutePathAndFileTests(unittest.TestCase):
    def _cfg(self) -> dict:
        cfg = default_config()
        cfg["roblox_packages"] = [
            {"package": "com.moons.litesc", "enabled": True},
            {"package": "com.roblox.client", "enabled": True},
        ]
        return cfg

    def test_delta_path_template(self) -> None:
        path = auto_execute.delta_autoexecute_dir("com.moons.litesc")
        self.assertEqual(
            str(path).replace("\\", "/"),
            "/storage/emulated/0/Android/data/com.moons.litesc/files/gloop/external/Autoexecute",
        )

    def test_add_script_writes_to_all_configured_packages_and_redacts_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packages = ["com.one", "com.two"]
            results = auto_execute.write_script_to_packages(
                packages,
                'loadstring(game:HttpGet("https://private.example/token"))()',
                storage_root=Path(tmp),
            )
            self.assertTrue(all(row["success"] for row in results), results)
            for package in packages:
                target = Path(tmp) / "Android" / "data" / package / "files/gloop/external/Autoexecute/deng_autoexec_001.lua"
                self.assertTrue(target.is_file())
                self.assertTrue(target.read_text(encoding="utf-8").endswith("\n"))
            self.assertNotIn("private.example", repr(results))
            self.assertNotIn("loadstring", repr(results))

    def test_add_script_numbering_continues_and_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "Android/data/com.one/files/gloop/external/Autoexecute/deng_autoexec_001.lua"
            existing.parent.mkdir(parents=True)
            existing.write_text("old\n", encoding="utf-8")
            filename = auto_execute.next_managed_filename(["com.one", "com.two"], storage_root=root)
            self.assertEqual(filename, "deng_autoexec_002.lua")
            auto_execute.write_script_to_packages(["com.one", "com.two"], "new", storage_root=root, filename=filename)
            self.assertEqual(existing.read_text(encoding="utf-8"), "old\n")
            self.assertTrue((root / "Android/data/com.one/files/gloop/external/Autoexecute/deng_autoexec_002.lua").is_file())

    def test_remove_script_deletes_selected_deng_managed_file_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for package in ("com.one", "com.two"):
                directory = root / "Android" / "data" / package / "files/gloop/external/Autoexecute"
                directory.mkdir(parents=True)
                (directory / "deng_autoexec_001.lua").write_text("x\n", encoding="utf-8")
                (directory / "user_script.lua").write_text("keep\n", encoding="utf-8")
            results = auto_execute.remove_script_from_packages(["com.one", "com.two"], "deng_autoexec_001.lua", storage_root=root)
            self.assertTrue(all(row["success"] for row in results), results)
            for package in ("com.one", "com.two"):
                directory = root / "Android" / "data" / package / "files/gloop/external/Autoexecute"
                self.assertFalse((directory / "deng_autoexec_001.lua").exists())
                self.assertTrue((directory / "user_script.lua").exists())

    def test_remove_all_deletes_only_deng_managed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "Android/data/com.one/files/gloop/external/Autoexecute"
            directory.mkdir(parents=True)
            (directory / "deng_autoexec_001.lua").write_text("x\n", encoding="utf-8")
            (directory / "deng_autoexec_999.lua").write_text("x\n", encoding="utf-8")
            (directory / "not_deng_autoexec.lua").write_text("keep\n", encoding="utf-8")
            results = auto_execute.remove_all_scripts_from_packages(["com.one"], storage_root=root)
            self.assertEqual(results[0]["deleted_count"], 2)
            self.assertFalse((directory / "deng_autoexec_001.lua").exists())
            self.assertFalse((directory / "deng_autoexec_999.lua").exists())
            self.assertTrue((directory / "not_deng_autoexec.lua").exists())

    def test_inventory_reports_filenames_by_package_and_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            one = root / "Android/data/com.one/files/gloop/external/Autoexecute"
            two = root / "Android/data/com.two/files/gloop/external/Autoexecute"
            one.mkdir(parents=True)
            two.mkdir(parents=True)
            (one / "deng_autoexec_001.lua").write_text("x\n", encoding="utf-8")
            (two / "deng_autoexec_001.lua").write_text("x\n", encoding="utf-8")
            inventory = auto_execute.managed_filenames_by_package(["com.one", "com.two"], storage_root=root)
            self.assertEqual(inventory, {"com.one": ["deng_autoexec_001.lua"], "com.two": ["deng_autoexec_001.lua"]})
            self.assertFalse(auto_execute.filenames_mismatch(inventory))
            (two / "deng_autoexec_002.lua").write_text("x\n", encoding="utf-8")
            inventory = auto_execute.managed_filenames_by_package(["com.one", "com.two"], storage_root=root)
            self.assertTrue(auto_execute.filenames_mismatch(inventory))

    def test_write_failure_reports_failure_and_continues(self) -> None:
        with patch("pathlib.Path.mkdir", side_effect=[PermissionError("denied"), None]), \
             patch("pathlib.Path.exists", return_value=False), \
             patch("pathlib.Path.write_bytes", return_value=None):
            results = auto_execute.write_script_to_packages(["com.bad", "com.good"], "x")
        self.assertFalse(results[0]["success"])
        self.assertIn("permission denied", results[0]["error"])
        self.assertTrue(results[1]["success"])


class AutoExecuteMenuTests(unittest.TestCase):
    def _cfg(self) -> dict:
        cfg = default_config()
        cfg["roblox_packages"] = [
            {"package": "com.moons.litesc", "enabled": True},
            {"package": "com.roblox.client", "enabled": True},
        ]
        return cfg

    def test_first_time_setup_mentions_auto_execute(self) -> None:
        source = inspect.getsource(commands._run_first_time_setup_wizard)
        self.assertIn("4. Auto Execute", source)
        self.assertIn("_config_menu_auto_execute(draft)", source)

    def test_edit_setting_menu_has_auto_execute_option(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            termux_ui.print_config_menu()
        text = out.getvalue()
        self.assertIn("Auto Execute", text)
        source = inspect.getsource(commands._run_edit_config_menu)
        self.assertIn("_config_menu_auto_execute(draft)", source)

    def test_auto_execute_menu_options(self) -> None:
        source = inspect.getsource(commands._config_menu_auto_execute)
        self.assertIn('print("1. Add Script")', source)
        self.assertIn('print("2. Remove Script")', source)
        self.assertIn('print("3. Remove All Scripts")', source)

    def test_menu_inventory_empty(self) -> None:
        out = io.StringIO()
        with patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands.auto_execute.managed_filenames_by_package", return_value={"com.moons.litesc": [], "com.roblox.client": []}), \
             patch("agent.commands.safe_io.safe_prompt", side_effect=["0"]), \
             redirect_stdout(out):
            commands._config_menu_auto_execute(self._cfg())
        text = out.getvalue()
        self.assertIn("Scripts set: 0", text)
        self.assertNotIn("/storage/emulated/0", text)

    def test_menu_inventory_lists_filenames_only_and_warns_on_mismatch(self) -> None:
        out = io.StringIO()
        inventory = {
            "com.moons.litesc": ["deng_autoexec_001.lua"],
            "com.roblox.client": ["deng_autoexec_001.lua", "deng_autoexec_002.lua"],
        }
        with patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands.auto_execute.managed_filenames_by_package", return_value=inventory), \
             patch("agent.commands.safe_io.safe_prompt", side_effect=["0"]), \
             redirect_stdout(out):
            commands._config_menu_auto_execute(self._cfg())
        text = out.getvalue()
        self.assertIn("Scripts set: 2", text)
        self.assertIn("- deng_autoexec_001.lua", text)
        self.assertIn("- deng_autoexec_002.lua", text)
        self.assertIn("Warning: scripts differ across packages. Use Remove All Scripts to reset.", text)
        self.assertNotIn("/storage/emulated/0", text)
        self.assertNotIn("loadstring", text)

    def test_no_configured_packages_message(self) -> None:
        out = io.StringIO()
        cfg = default_config()
        cfg["roblox_packages"] = []
        cfg["roblox_package"] = ""
        with patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands.safe_io.safe_prompt", side_effect=["1", "0"]), \
             redirect_stdout(out):
            commands._config_menu_auto_execute(cfg)
        self.assertIn("No Roblox packages configured. Configure packages first.", out.getvalue())

    def test_add_script_loop_until_n(self) -> None:
        cfg = self._cfg()
        calls: list[str] = []

        def fake_write(packages, script, **kwargs):
            calls.append(script)
            return [{"package": p, "path": "/x", "filename": kwargs["filename"], "byte_count": len(script), "success": True} for p in packages]

        with patch("agent.commands.auto_execute.write_script_to_packages", side_effect=fake_write), \
             patch("agent.commands.auto_execute.next_managed_filename", side_effect=["deng_autoexec_001.lua", "deng_autoexec_002.lua"]), \
             patch("agent.commands.safe_io.safe_prompt", side_effect=["1", "Y", "script-one", "Y", "script-two", "N"]), \
             redirect_stdout(io.StringIO()) as out:
            commands._config_auto_execute_add(cfg)
        self.assertEqual(calls, ["script-one", "script-two"])
        text = out.getvalue()
        self.assertIn("Added deng_autoexec_001.lua to 2 packages.", text)
        self.assertIn("Added deng_autoexec_002.lua to 2 packages.", text)
        self.assertNotIn("/storage/emulated/0", text)

    def test_add_script_failure_output_is_concise(self) -> None:
        out = io.StringIO()
        cfg = self._cfg()

        def fake_write(packages, script, **kwargs):
            return [
                {"package": packages[0], "path": "/storage/emulated/0/secret/path", "filename": kwargs["filename"], "byte_count": 100, "success": True},
                {"package": packages[1], "path": "/storage/emulated/0/secret/path", "filename": kwargs["filename"], "byte_count": 100, "success": False, "error": "permission denied: /storage/emulated/0/secret/path"},
            ]

        with patch("agent.commands.auto_execute.write_script_to_packages", side_effect=fake_write), \
             patch("agent.commands.auto_execute.next_managed_filename", return_value="deng_autoexec_001.lua"), \
             patch("agent.commands.safe_io.safe_prompt", side_effect=["1", "Y", 'loadstring(game:HttpGet("https://secret"))()', "N"]), \
             redirect_stdout(out):
            commands._config_auto_execute_add(cfg)
        text = out.getvalue()
        self.assertIn("Added deng_autoexec_001.lua to 1/2 packages.", text)
        self.assertIn("Failed:", text)
        self.assertIn("- com.roblox.client: permission denied", text)
        self.assertNotIn("/storage/emulated/0", text)
        self.assertNotIn("loadstring", text)


class StartTablePolishTests(unittest.TestCase):
    def test_runtime_and_usage_columns_hidden_but_values_accepted(self) -> None:
        table = commands.build_start_table([(1, "com.roblox.client", "Main", "Online", "45s", "100 MB")])
        self.assertNotIn("Runtime", table)
        self.assertNotIn("Usage", table)
        self.assertNotIn("45s", table)
        self.assertNotIn("100 MB", table)
        self.assertIn("Online", table)


if __name__ == "__main__":
    unittest.main()
