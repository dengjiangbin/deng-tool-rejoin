"""Regression: every external spawn uses TTY isolation (DEVNULL + close_fds)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from agent import root_access, subprocess_isolated


class SubprocessIsolationTests(unittest.TestCase):
    def test_run_isolated_text_uses_devnull_and_close_fds_on_unix(self) -> None:
        captured: dict = {}

        class _Proc:
            returncode = 0

            def communicate(self, timeout=None):
                return b"ok", b""

        def _fake_popen(args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _Proc()

        with mock.patch("agent.subprocess_isolated.subprocess.Popen", side_effect=_fake_popen):
            rc, out, err, timed_out = subprocess_isolated.run_isolated_text(
                ["echo", "hi"],
                timeout=1.0,
            )

        self.assertEqual(rc, 0)
        self.assertEqual(out, "ok")
        self.assertFalse(timed_out)
        self.assertIs(captured["kwargs"]["stdin"], subprocess_isolated.subprocess.DEVNULL)
        if os.name != "nt":
            self.assertTrue(captured["kwargs"].get("close_fds"))

    def test_root_run_raw_delegates_to_isolated_runner(self) -> None:
        with mock.patch(
            "agent.root_access._iso.run_isolated_text",
            return_value=(0, "uid=0", "", False),
        ) as isolated:
            rc, out, err, timed_out = root_access._run_raw(["su", "-c", "id"], timeout=3)

        self.assertEqual(rc, 0)
        self.assertIn("uid=0", out)
        isolated.assert_called_once()


if __name__ == "__main__":
    unittest.main()
