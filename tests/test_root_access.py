"""Tests for agent/root_access.py — root capability helper.

Covers: tsu/su detection, command execution, read/list helpers,
root_status_summary, error paths, and caching.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from agent import root_access
from agent.root_access import (
    COMMAND_TIMEOUT,
    DETECT_TIMEOUT,
    RootCapability,
    RootResult,
    RootStatus,
    clear_cache,
    detect,
    has_root,
    list_root_glob,
    read_root_file,
    root_status_summary,
    run_root_command,
)


def _make_completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    p = MagicMock(spec=subprocess.CompletedProcess)
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


class TestRootDetect(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

    def tearDown(self) -> None:
        clear_cache()

    @patch("agent.root_access._run_raw")
    def test_tsu_available(self, mock_run: MagicMock) -> None:
        """If tsu returns uid=0, has_root() is True and tool is 'tsu'."""
        mock_run.return_value = (0, "uid=0(root) gid=0(root)", "", False)
        cap = detect(force=True)
        self.assertTrue(cap.available)
        self.assertEqual(cap.tool, "tsu")
        self.assertEqual(cap.status, RootStatus.AVAILABLE)

    @patch("agent.root_access._run_raw")
    def test_su_available_when_tsu_not_found(self, mock_run: MagicMock) -> None:
        """If tsu is not found but su returns uid=0, tool is 'su'."""
        def side(args, timeout):
            tool = args[0]
            if tool == "tsu":
                return (127, "", "not found", False)
            return (0, "uid=0(root)", "", False)
        mock_run.side_effect = side
        cap = detect(force=True)
        self.assertTrue(cap.available)
        self.assertEqual(cap.tool, "su")

    @patch("agent.root_access._run_raw")
    def test_both_unavailable(self, mock_run: MagicMock) -> None:
        """If all candidates return 127, has_root() is False."""
        mock_run.return_value = (127, "", "not found", False)
        cap = detect(force=True)
        self.assertFalse(cap.available)
        self.assertIsNone(cap.tool)
        self.assertIn(cap.status, (RootStatus.NOT_FOUND, RootStatus.DENIED, RootStatus.ERROR))

    @patch("agent.root_access._run_raw")
    def test_root_denied(self, mock_run: MagicMock) -> None:
        """If su/tsu runs but does not return uid=0, status is DENIED."""
        mock_run.return_value = (1, "uid=2000(shell)", "", False)
        cap = detect(force=True)
        self.assertFalse(cap.available)
        self.assertEqual(cap.status, RootStatus.DENIED)

    @patch("agent.root_access._run_raw")
    def test_root_timeout(self, mock_run: MagicMock) -> None:
        """If the first candidate times out, detect returns TIMED_OUT immediately."""
        def side(args, timeout):
            if args[0] in ("tsu", "su"):
                return (-1, "", "timed out", True)
            return (127, "", "not found", False)
        mock_run.side_effect = side
        cap = detect(force=True)
        self.assertFalse(cap.available)
        self.assertEqual(cap.status, RootStatus.TIMED_OUT)

    @patch("agent.root_access._run_raw")
    def test_detect_caching(self, mock_run: MagicMock) -> None:
        """detect() returns cached result without re-running subprocess."""
        mock_run.return_value = (0, "uid=0(root)", "", False)
        first = detect(force=True)
        second = detect(force=False)
        # Both should be same object (cached)
        self.assertIs(first, second)
        # _run_raw called only for initial detection pass
        self.assertTrue(mock_run.call_count >= 1)
        call_count_after_first = mock_run.call_count
        detect(force=False)
        self.assertEqual(mock_run.call_count, call_count_after_first)

    @patch("agent.root_access._run_raw")
    def test_has_root_true(self, mock_run: MagicMock) -> None:
        mock_run.return_value = (0, "uid=0(root)", "", False)
        clear_cache()
        self.assertTrue(has_root(force=True))

    @patch("agent.root_access._run_raw")
    def test_has_root_false(self, mock_run: MagicMock) -> None:
        mock_run.return_value = (127, "", "not found", False)
        clear_cache()
        self.assertFalse(has_root(force=True))

    @patch("agent.root_access._run_raw")
    def test_detect_never_raises(self, mock_run: MagicMock) -> None:
        """detect() must not raise even if _run_raw raises."""
        mock_run.side_effect = RuntimeError("unexpected")
        cap = detect(force=True)
        self.assertIsInstance(cap, RootCapability)
        self.assertFalse(cap.available)


class TestRunRootCommand(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

    def tearDown(self) -> None:
        clear_cache()

    @patch("agent.root_access._run_raw")
    def test_command_success(self, mock_run: MagicMock) -> None:
        """run_root_command succeeds when root is available."""
        def side(args, timeout):
            if args[-1] == "id":
                return (0, "uid=0(root)", "", False)
            return (0, "hello world", "", False)
        mock_run.side_effect = side
        clear_cache()
        result = run_root_command(["echo", "hello world"])
        self.assertIsInstance(result, RootResult)
        self.assertTrue(result.ok)

    @patch("agent.root_access._run_raw")
    def test_command_no_root(self, mock_run: MagicMock) -> None:
        """run_root_command returns error result when root unavailable."""
        mock_run.return_value = (127, "", "not found", False)
        clear_cache()
        result = run_root_command(["echo", "hello"])
        self.assertIsInstance(result, RootResult)
        self.assertFalse(result.ok)
        self.assertIn("unavailable", result.error.lower())

    @patch("agent.root_access._run_raw")
    def test_command_timeout(self, mock_run: MagicMock) -> None:
        """run_root_command handles timeout gracefully."""
        def side(args, timeout):
            if "id" in args:
                return (0, "uid=0(root)", "", False)
            return (-1, "", "timed out", True)
        mock_run.side_effect = side
        clear_cache()
        result = run_root_command(["sleep", "9999"])
        self.assertIsInstance(result, RootResult)
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)

    @patch("agent.root_access._run_raw")
    def test_command_error_returncode(self, mock_run: MagicMock) -> None:
        """run_root_command returns ok=False for non-zero returncode."""
        def side(args, timeout):
            if "id" in args:
                return (0, "uid=0(root)", "", False)
            return (1, "", "error", False)
        mock_run.side_effect = side
        clear_cache()
        result = run_root_command(["false"])
        self.assertFalse(result.ok)

    @patch("agent.root_access._run_raw")
    def test_run_root_command_never_raises(self, mock_run: MagicMock) -> None:
        """run_root_command must never raise even on unexpected errors."""
        mock_run.side_effect = Exception("boom")
        clear_cache()
        result = run_root_command(["any", "command"])
        self.assertIsInstance(result, RootResult)
        self.assertFalse(result.ok)


class TestReadRootFile(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

    def tearDown(self) -> None:
        clear_cache()

    @patch("agent.root_access.run_root_command")
    def test_read_success(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = RootResult(0, "file contents here", "", False)
        result = read_root_file("/data/data/com.roblox.client/shared_prefs/test.xml")
        self.assertEqual(result, "file contents here")

    @patch("agent.root_access.run_root_command")
    def test_read_empty_file(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = RootResult(0, "", "", False)
        result = read_root_file("/data/data/com.roblox.client/shared_prefs/test.xml")
        self.assertIsNone(result)

    @patch("agent.root_access.run_root_command")
    def test_read_command_failure(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = RootResult(1, "", "permission denied", False)
        result = read_root_file("/data/data/some.app/prefs.xml")
        self.assertIsNone(result)

    def test_read_invalid_path(self) -> None:
        """read_root_file returns None for invalid paths without running subprocess."""
        result = read_root_file("")
        self.assertIsNone(result)

    def test_read_no_slash_path(self) -> None:
        result = read_root_file("noslash")
        self.assertIsNone(result)

    @patch("agent.root_access.run_root_command")
    def test_read_never_raises(self, mock_cmd: MagicMock) -> None:
        mock_cmd.side_effect = Exception("boom")
        result = read_root_file("/some/path")
        self.assertIsNone(result)


class TestListRootGlob(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

    def tearDown(self) -> None:
        clear_cache()

    @patch("agent.root_access.run_root_command")
    def test_list_success(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = RootResult(
            0,
            "/data/data/com.roblox.client/shared_prefs/a.xml\n"
            "/data/data/com.roblox.client/shared_prefs/b.xml",
            "",
            False,
        )
        result = list_root_glob("/data/data/com.roblox.client/shared_prefs/*.xml")
        self.assertEqual(len(result), 2)
        self.assertTrue(all(f.endswith(".xml") for f in result))

    @patch("agent.root_access.run_root_command")
    def test_list_empty(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = RootResult(0, "", "", False)
        result = list_root_glob("/data/data/com.roblox.client/shared_prefs/*.xml")
        self.assertEqual(result, [])

    @patch("agent.root_access.run_root_command")
    def test_list_failure(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = RootResult(1, "", "no such file", False)
        result = list_root_glob("/data/data/nope/*.xml")
        self.assertEqual(result, [])

    def test_list_empty_pattern(self) -> None:
        result = list_root_glob("")
        self.assertEqual(result, [])

    @patch("agent.root_access.run_root_command")
    def test_list_never_raises(self, mock_cmd: MagicMock) -> None:
        mock_cmd.side_effect = Exception("boom")
        result = list_root_glob("/some/*.xml")
        self.assertEqual(result, [])


class TestRootStatusSummary(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

    def tearDown(self) -> None:
        clear_cache()

    @patch("agent.root_access._run_raw")
    def test_available(self, mock_run: MagicMock) -> None:
        mock_run.return_value = (0, "uid=0(root)", "", False)
        summary = root_status_summary()
        self.assertIn("Root available", summary)

    @patch("agent.root_access._run_raw")
    def test_not_found(self, mock_run: MagicMock) -> None:
        mock_run.return_value = (127, "", "not found", False)
        summary = root_status_summary()
        self.assertIn("not found", summary.lower())

    @patch("agent.root_access._run_raw")
    def test_denied(self, mock_run: MagicMock) -> None:
        mock_run.return_value = (1, "uid=2000(shell)", "", False)
        summary = root_status_summary()
        self.assertNotIn("available", summary.lower())

    @patch("agent.root_access._run_raw")
    def test_timed_out(self, mock_run: MagicMock) -> None:
        mock_run.return_value = (-1, "", "timed out", True)
        summary = root_status_summary()
        self.assertIn("timed out", summary.lower())

    @patch("agent.root_access._run_raw")
    def test_summary_never_raises(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = Exception("boom")
        summary = root_status_summary()
        self.assertIsInstance(summary, str)
        self.assertGreater(len(summary), 0)

    def test_no_traceback_in_summary(self) -> None:
        """Status summary must never include a raw traceback."""
        clear_cache()
        with patch("agent.root_access._run_raw", side_effect=RuntimeError("oops")):
            summary = root_status_summary()
        self.assertNotIn("Traceback", summary)
        self.assertNotIn("RuntimeError", summary)

    @patch("agent.root_access._run_raw")
    def test_fallback_does_not_crash(self, mock_run: MagicMock) -> None:
        """Even if detection returns unexpected data, no crash."""
        mock_run.return_value = (0, "some weird output", "", False)
        summary = root_status_summary()
        self.assertIsInstance(summary, str)


class TestClearCache(unittest.TestCase):
    def test_clear_invalidates(self) -> None:
        """clear_cache() forces re-detection on next detect() call."""
        with patch("agent.root_access._run_raw") as mock_run:
            mock_run.return_value = (0, "uid=0(root)", "", False)
            first = detect(force=True)
            clear_cache()
            mock_run.return_value = (127, "", "not found", False)
            second = detect(force=False)
        self.assertIsNot(first, second)
        self.assertTrue(first.available)
        self.assertFalse(second.available)


if __name__ == "__main__":
    unittest.main()
