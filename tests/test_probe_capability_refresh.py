from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


class TestProbeCapabilityRefresh(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_home = os.environ.get("DENG_REJOIN_HOME")
        os.environ["DENG_REJOIN_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("DENG_REJOIN_HOME", None)
        else:
            os.environ["DENG_REJOIN_HOME"] = self.old_home

    def test_valid_cached_capability_reused(self) -> None:
        from agent.license_session import ensure_session_for_feature, save_session

        save_session({"session_id": "sess-ok", "expires_in": 60, "capabilities": {"probe_upload": True}})

        ok, value = ensure_session_for_feature("probe_upload")
        self.assertTrue(ok)
        self.assertEqual(value, "sess-ok")

    def test_force_refresh_ignores_cached_capability(self) -> None:
        from agent.license_session import ensure_session_for_feature, save_session

        save_session({"session_id": "server-dead", "expires_in": 60, "capabilities": {"probe_upload": True}})
        cfg = {
            "license_key": "DENG-AAAA-BBBB-CCCC-DDDD",
            "license": {
                "key": "DENG-AAAA-BBBB-CCCC-DDDD",
                "install_id": "a" * 32,
                "server_url": "https://rejoin.deng.my.id",
                "device_label": "",
            },
        }

        def fake_check(*args, **kwargs):
            from agent.license_session import save_session
            save_session({"session_id": "fresh-after-401", "expires_in": 60, "capabilities": {"probe_upload": True}})
            return "active", "License active."

        with mock.patch("agent.config.load_config", return_value=cfg), \
             mock.patch("agent.license.check_remote_license_status", side_effect=fake_check), \
             mock.patch("agent.license.get_public_device_model", return_value="Pixel"):
            ok, value = ensure_session_for_feature("probe_upload", force_refresh=True)

        self.assertTrue(ok)
        self.assertEqual(value, "fresh-after-401")

    def test_expired_capability_refreshes_with_validate_only(self) -> None:
        from agent.license_session import ensure_session_for_feature

        path = Path(self.tmp.name) / ".license-session.json"
        path.write_text(json.dumps({
            "session_id": "old",
            "saved_at": time.time() - 999,
            "expires_in": 1,
            "capabilities": {"probe_upload": True},
        }), encoding="utf-8")
        cfg = {
            "license_key": "DENG-AAAA-BBBB-CCCC-DDDD",
            "license": {
                "key": "DENG-AAAA-BBBB-CCCC-DDDD",
                "install_id": "a" * 32,
                "server_url": "https://rejoin.deng.my.id",
                "device_label": "",
            },
        }

        def fake_check(*args, **kwargs):
            from agent.license_session import save_session
            save_session({"session_id": "fresh", "expires_in": 60, "capabilities": {"probe_upload": True}})
            return "active", "License active."

        with mock.patch("agent.config.load_config", return_value=cfg), \
             mock.patch("agent.license.check_remote_license_status", side_effect=fake_check), \
             mock.patch("agent.license.get_public_device_model", return_value="Pixel"):
            ok, value = ensure_session_for_feature("probe_upload")

        self.assertTrue(ok)
        self.assertEqual(value, "fresh")

    def test_unbound_key_does_not_bind_and_fails_cleanly(self) -> None:
        from agent.license_session import ensure_session_for_feature

        cfg = {
            "license_key": "DENG-AAAA-BBBB-CCCC-DDDD",
            "license": {
                "key": "DENG-AAAA-BBBB-CCCC-DDDD",
                "install_id": "a" * 32,
                "server_url": "https://rejoin.deng.my.id",
                "device_label": "",
            },
        }
        with mock.patch("agent.config.load_config", return_value=cfg), \
             mock.patch("agent.license.check_remote_license_status", return_value=("requires_manual_rebind", "manual")), \
             mock.patch("agent.license.bind_remote_license_key") as bind:
            ok, value = ensure_session_for_feature("probe_upload")

        self.assertFalse(ok)
        self.assertIn("manually", value)
        bind.assert_not_called()

    def test_wrong_hwid_fails_cleanly(self) -> None:
        from agent.license_session import ensure_session_for_feature

        cfg = {
            "license_key": "DENG-AAAA-BBBB-CCCC-DDDD",
            "license": {
                "key": "DENG-AAAA-BBBB-CCCC-DDDD",
                "install_id": "a" * 32,
                "server_url": "https://rejoin.deng.my.id",
                "device_label": "",
            },
        }
        with mock.patch("agent.config.load_config", return_value=cfg), \
             mock.patch("agent.license.check_remote_license_status", return_value=("wrong_device", "Wrong device")):
            ok, value = ensure_session_for_feature("probe_upload")

        self.assertFalse(ok)
        self.assertEqual(value, "Wrong device")

    def test_without_key_gives_clean_instruction(self) -> None:
        from agent.license_session import ensure_session_for_feature

        with mock.patch("agent.config.load_config", return_value={"license": {"key": ""}, "license_key": ""}):
            ok, value = ensure_session_for_feature("probe_upload")

        self.assertFalse(ok)
        self.assertIn("Probe upload requires a valid license session", value)

    def test_no_static_probe_token_constant_exists(self) -> None:
        import inspect
        import agent.probe as probe

        src = inspect.getsource(probe.upload_probe)
        self.assertNotIn("ensure_session_for_feature", src)
        self.assertNotIn("X-DENG-Session", src)
        self.assertNotIn("X-Dev-Probe-Token", src)
        self.assertNotIn("DENG_DEV_PROBE_TOKEN", src)


class TestProbeEmptyShellGuard(unittest.TestCase):
    def test_empty_command_is_skipped_without_shell_error(self) -> None:
        from agent import probe

        errors: list[dict[str, str]] = []
        res = probe._run("empty", [], errors)

        self.assertEqual(res.returncode, 127)
        self.assertEqual(errors[0]["error"], "empty command skipped")

    def test_empty_shell_path_is_skipped_without_invoking_sh(self) -> None:
        from agent import probe

        errors: list[dict[str, str]] = []
        with mock.patch("agent.probe.run_command") as run:
            res = probe._run("bad shell", ["sh", ""], errors)

        self.assertEqual(res.returncode, 127)
        self.assertEqual(errors[0]["error"], "empty shell script path skipped")
        run.assert_not_called()

    def test_empty_shell_command_is_skipped_without_invoking_sh(self) -> None:
        from agent import probe

        errors: list[dict[str, str]] = []
        with mock.patch("agent.probe.run_command") as run:
            res = probe._run("bad shell command", ["sh", "-c", ""], errors)

        self.assertEqual(res.returncode, 127)
        self.assertEqual(errors[0]["error"], "empty shell command skipped")
        run.assert_not_called()

