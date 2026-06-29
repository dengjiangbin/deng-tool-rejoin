"""Regression tests for the in-game Lua detection push channel.

Covers the two systems requested in probe p-1bc476d931 follow-up:
  1. A dedicated loopback detection worker port / push channel.
  2. Auto-injected, obscured ``deng.txt`` bootstrap that loadstrings the
     remote ``detector.lua`` and pins per-package config.

Plus the supervisor/lifecycle wiring that consumes heartbeats for fast,
reliable Online / Wrong-Server detection.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path


def _post(port: int, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/h",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        return exc.code, {}


class DetectionWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["DENG_REJOIN_DETECTION_PORT"] = "52793"
        from agent import detection_worker as dw

        cls.dw = dw
        cls.port = dw.start_detection_worker()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.dw.stop_detection_worker()
        os.environ.pop("DENG_REJOIN_DETECTION_PORT", None)

    def test_port_env_override_is_respected(self) -> None:
        self.assertEqual(self.port, 52793)
        self.assertEqual(self.dw.detection_worker_port(), 52793)

    def test_heartbeat_round_trip_is_stored_fresh(self) -> None:
        token = self.dw.current_token()
        status, body = _post(
            self.port,
            {
                "k": token,
                "pkg": "com.roblox.clientHB",
                "alive": True,
                "placeId": 123,
                "universeId": 999,
                "jobId": "guid-aaa",
                "user": "Tester",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        hb = self.dw.get_heartbeat("com.roblox.clientHB")
        self.assertIsNotNone(hb)
        self.assertEqual(hb["placeId"], 123)
        self.assertEqual(hb["universeId"], 999)
        self.assertEqual(hb["jobId"], "guid-aaa")
        self.assertTrue(hb["alive"])
        self.assertLess(hb["age_seconds"], 5.0)

    def test_wrong_token_is_forbidden(self) -> None:
        status, _ = _post(self.port, {"k": "totally-wrong", "pkg": "com.x"})
        self.assertEqual(status, 403)

    def test_missing_package_is_rejected(self) -> None:
        status, _ = _post(self.port, {"k": self.dw.current_token()})
        self.assertEqual(status, 400)

    def test_unknown_package_returns_none(self) -> None:
        self.assertIsNone(self.dw.get_heartbeat("com.roblox.never.seen"))

    def test_ping_endpoint_ok(self) -> None:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/ping", timeout=5)
        self.assertEqual(resp.status, 200)


class DetectionLuaBootstrapTests(unittest.TestCase):
    def test_deng_txt_is_obscured_and_decodes_to_bootstrap(self) -> None:
        from agent import detection_lua as dl

        txt = dl.build_deng_txt("com.roblox.clientab", port=52793, token="TOK123", interval=5)
        # Obscured: the raw URL must NOT appear in plaintext.
        self.assertNotIn("raw.githubusercontent.com", txt)
        self.assertIn("string.char", txt)
        # Decode the byte array back and confirm the real bootstrap.
        nums = re.search(r"local b=\{([0-9,]+)\}", txt).group(1)
        decoded = bytes(int(x) for x in nums.split(",")).decode("utf-8")
        self.assertIn(
            "loadstring(game:HttpGet(" if False else dl.DETECTOR_URL,
            decoded,
        )
        self.assertIn("getgenv", decoded)
        self.assertIn("G.DENG=D", decoded)
        self.assertIn("D.port=52793", decoded)
        self.assertIn('D.token="TOK123"', decoded)
        self.assertIn("com.roblox.clientab", decoded)

    def test_detector_url_is_canonical(self) -> None:
        from agent import detection_lua as dl

        self.assertEqual(
            dl.DETECTOR_URL,
            "https://raw.githubusercontent.com/dengjiangbin/global/main/detector.lua",
        )
        self.assertEqual(dl.DETECTION_FILENAME, "deng.txt")

    def test_lua_quote_escapes_quotes(self) -> None:
        from agent import detection_lua as dl

        self.assertEqual(dl._lua_quote('a"b'), '"a\\"b"')

    def test_detector_lua_resource_exists_and_reports_heartbeats(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        detector = repo_root / "assets" / "lua" / "detector.lua"
        self.assertTrue(detector.is_file(), "assets/lua/detector.lua must exist for upload")
        src = detector.read_text(encoding="utf-8")
        self.assertIn("getgenv", src)
        self.assertIn("DENG", src)
        self.assertIn("127.0.0.1", src)
        self.assertIn("JobId", src)
        self.assertIn("PlaceId", src)


class WriteDetectionScriptTests(unittest.TestCase):
    def test_write_then_overwrite_then_remove(self) -> None:
        from agent import auto_execute as ae

        root = Path(tempfile.mkdtemp())
        res = ae.write_detection_script(["com.roblox.clientab"], storage_root=root)
        self.assertTrue(res[0]["success"])
        self.assertEqual(res[0]["filename"], "deng.txt")
        target = (
            root / "Android" / "data" / "com.roblox.clientab"
            / "files" / "gloop" / "external" / "Autoexecute" / "deng.txt"
        )
        self.assertTrue(target.is_file())
        # Overwrite must succeed (unlike user-managed scripts).
        res2 = ae.write_detection_script(["com.roblox.clientab"], storage_root=root)
        self.assertTrue(res2[0]["success"])
        # Removal deletes it.
        rem = ae.remove_detection_script(["com.roblox.clientab"], storage_root=root)
        self.assertTrue(rem[0]["success"])
        self.assertTrue(rem[0]["deleted"])
        self.assertFalse(target.exists())

    def test_detection_filename_is_not_user_managed(self) -> None:
        # deng.txt must stay separate from the deng_autoexec_NNN.lua numbering.
        from agent import auto_execute as ae

        self.assertFalse(ae.is_managed_filename("deng.txt"))


class LifecyclePushHeartbeatTests(unittest.TestCase):
    def _monitor(self):
        from agent.rjn_lifecycle_monitor import RjnLifecycleMonitor

        m = RjnLifecycleMonitor(["com.roblox.clientab"])
        m.note_launch_watchdog("com.roblox.clientab")
        return m

    def test_alive_heartbeat_confirms_online(self) -> None:
        m = self._monitor()
        verdict = m.ingest_push_heartbeat(
            "com.roblox.clientab", alive=True, place_id=111, universe_id=222, job_id="srvA"
        )
        self.assertEqual(verdict, "online")

    def test_jobid_change_flags_wrong_server(self) -> None:
        m = self._monitor()
        m.ingest_push_heartbeat(
            "com.roblox.clientab", alive=True, place_id=111, universe_id=222, job_id="srvA"
        )
        verdict = m.ingest_push_heartbeat(
            "com.roblox.clientab", alive=True, place_id=111, universe_id=222, job_id="srvB-different"
        )
        self.assertEqual(verdict, "wrong_server")

    def test_empty_package_is_noop(self) -> None:
        m = self._monitor()
        self.assertEqual(m.ingest_push_heartbeat("", alive=True), "")


class SupervisorWiringTests(unittest.TestCase):
    def test_freshness_constant_exists(self) -> None:
        from agent import supervisor

        self.assertGreater(supervisor.PUSH_HEARTBEAT_FRESH_SECONDS, 0)

    def test_detect_state_calls_push_ingest(self) -> None:
        # _detect_android_package_state must consult the push channel.
        import inspect

        from agent import supervisor

        src = inspect.getsource(supervisor.WatchdogSupervisor._detect_android_package_state)
        self.assertIn("_ingest_push_heartbeat", src)

    def test_run_starts_detection_worker(self) -> None:
        import inspect

        from agent import supervisor

        src = inspect.getsource(supervisor.WatchdogSupervisor._run_watchdog_loop)
        self.assertIn("start_detection_worker", src)


if __name__ == "__main__":
    unittest.main()
