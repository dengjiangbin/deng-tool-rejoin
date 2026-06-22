"""Regression: API URL isolation, STDOUT serialization, terminal width clamps."""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from agent import api_config, safe_io, termux_ui
from agent.commands import build_start_table


class ApiConfigIsolationTests(unittest.TestCase):
    def test_deng_api_url_overrides_install_file_and_default(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "DENG_API_URL": "http://10.255.255.1:9999",
                "DENG_REJOIN_INSTALL_API": "https://ignored.example",
            },
            clear=True,
        ):
            base = api_config.resolve_api_base_url(allow_install_file=True, allow_default=True)
            self.assertEqual(base, "http://10.255.255.1:9999")
            self.assertEqual(
                api_config.dev_probe_fetch_url("p-test"),
                "http://10.255.255.1:9999/api/dev-probe/p-test",
            )

    def test_no_env_uses_install_file_before_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch(
                 "agent.api_config._install_api_file",
                 return_value=mock.Mock(is_file=lambda: True, read_text=lambda *a, **k: "http://file.api\n"),
             ):
            base = api_config.resolve_api_base_url(allow_install_file=True, allow_default=False)
        self.assertEqual(base, "http://file.api")


class StdoutWriteTests(unittest.TestCase):
    def test_write_stdout_appends_newline_and_flushes(self) -> None:
        seen: list[str] = []

        def _fake_write(text: str) -> int:
            seen.append(text)
            return len(text)

        with mock.patch("agent.safe_io.sys.stdout.write", side_effect=_fake_write), \
             mock.patch("agent.safe_io.sys.stdout.flush") as flush:
            safe_io.write_stdout("a")
            safe_io.write_stdout("b")
        self.assertEqual(seen, ["a\n", "b\n"])
        self.assertEqual(flush.call_count, 2)


class TerminalWidthTests(unittest.TestCase):
    def test_fit_line_truncates_without_wrap(self) -> None:
        long_line = "x" * 120
        out = termux_ui.fit_line(long_line, width=40)
        self.assertLessEqual(termux_ui.visible_len(out), 40)
        self.assertTrue(out.endswith("..."))

    def test_build_start_table_respects_terminal_width(self) -> None:
        rows = [
            (1, "com.moons.litesc", "JBDENG8", "Online", "01:02:03", "120 MB"),
            (2, "com.moons.litesd", "thankyou1821", "Clear Cache", "00:05:01", "99 MB"),
        ]
        with mock.patch("agent.safe_io.terminal_columns", return_value=52):
            table = build_start_table(rows, use_color=False)
        for line in table.splitlines():
            self.assertLessEqual(
                termux_ui.visible_len(line),
                52,
                msg=f"line exceeded width: {line!r}",
            )


class CrashTtyRestoreTests(unittest.TestCase):
    def test_check_and_report_crash_log_restores_terminal(self) -> None:
        with mock.patch("agent.safe_io.restore_terminal") as restore, \
             mock.patch("pathlib.Path.exists", return_value=False):
            safe_io.check_and_report_crash_log()
        restore.assert_called_once()


if __name__ == "__main__":
    unittest.main()
