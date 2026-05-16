"""Tests that the public Start screen never leaks debug/backend text.

Verifies (per user requirement):
  - No `landscape_blocks` text
  - No `root_state` text
  - No `doctor` text
  - No `Supervisor` text
  - No `Segmentation fault` text
  - No `dumpsys`, `pidof`, raw XML paths
  - No `Start Summary` / `Packages selected` / `Detected packages` / `Launch mode`
  - No `Session active` text
  - No traceback
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


_FORBIDDEN_PUBLIC_TEXT = [
    "landscape_blocks",
    "landscape_bl",
    "root_state",
    "doctor",
    "Supervisor stopping",
    "Supervisor",
    "Segmentation fault",
    "Traceback",
    "dumpsys",
    "pidof",
    "shared_prefs",
    "Start Summary",
    "Packages selected:",
    "Detected packages:",
    "Launch mode:",
    "Session active",
    "Ctrl+C received",
    "Interrupted",
    "Kaeru layout",
    "preview_line",
    "/data/data/",
    "pkg_preferences.xml",
]


def _capture_start_output(entries) -> str:
    """Run cmd_start with deep mocking, capturing both stdout and stderr."""
    import agent.commands as _cmd
    import agent.keystore as _ks
    from agent.logger import silence_public_loggers

    silence_public_loggers()

    cfg = {
        "roblox_package": "com.roblox.client",
        "first_setup_completed": True,
        "packages": [
            {"package": "com.roblox.client", "enabled": True,
             "username": "TestUser", "low_graphics_enabled": True},
        ],
        "supervisor": {"enabled": False, "launch_grace_seconds": 0},
        "optimization": {"low_graphics_enabled": False},
    }

    args = MagicMock()
    args.no_color = True
    args.verbose = False
    args.debug = False

    out = io.StringIO()
    err = io.StringIO()

    mock_sup_inst = MagicMock()
    mock_sup_inst.status_map = {"com.roblox.client": "Lobby"}
    mock_sup_inst.run_forever = lambda render_callback=None: None

    with (
        patch.object(_ks, "DEV_MODE", True),
        patch("agent.commands.load_config", return_value=cfg),
        patch("agent.commands.save_config"),
        patch("agent.commands._ensure_install_id_saved", return_value=cfg),
        patch("agent.commands.enabled_package_entries", return_value=entries),
        patch("agent.commands.android.discover_roblox_package_candidates", return_value=entries),
        patch("agent.commands.android.force_stop_packages_except", return_value=[]),
        patch("agent.commands.android.clear_safe_package_cache", return_value="Skipped"),
        patch("agent.commands.android.apply_low_graphics_optimization", return_value="Skipped"),
        patch("agent.commands._prepare_automatic_layout", return_value=(cfg, "layout_prepared n=1")),
        patch("agent.commands._verify_layout_post_launch", return_value={"com.roblox.client": True}),
        patch("agent.commands.perform_rejoin", return_value=MagicMock(success=True, error=None)),
        patch("agent.commands.android.is_process_running", return_value=True),
        patch("agent.commands.effective_private_server_url", return_value=None),
        patch("agent.commands.MultiPackageSupervisor", return_value=mock_sup_inst),
        patch("agent.commands._package_detection_options", return_value=(None, True, True)),
        patch("agent.commands._clear_terminal"),
        redirect_stdout(out),
        redirect_stderr(err),
    ):
        try:
            _cmd.cmd_start(args)
        except SystemExit:
            pass
        except Exception:
            pass

    return out.getvalue() + err.getvalue()


class TestPublicStartIsClean(unittest.TestCase):
    """Public Start output must contain ONLY logo + table — no debug terms."""

    ENTRIES = [
        {"package": "com.roblox.client", "enabled": True,
         "username": "User1", "low_graphics_enabled": True},
    ]

    @classmethod
    def setUpClass(cls):
        cls.output = _capture_start_output(cls.ENTRIES)

    def test_no_forbidden_text(self):
        for term in _FORBIDDEN_PUBLIC_TEXT:
            self.assertNotIn(term, self.output,
                f"FORBIDDEN public text '{term}' leaked into Start output:\n"
                f"{self.output[:800]}")

    def test_output_has_table_or_state(self):
        """Output must contain at least the table header or a state."""
        self.assertTrue(
            "Package" in self.output or "State" in self.output
            or "Joining" in self.output or "Lobby" in self.output
            or "Launching" in self.output,
            f"Start output missing table:\n{self.output[:800]}",
        )


class TestInternalLoggersAreSilencedOnImport(unittest.TestCase):
    """After silence_public_loggers(), internal namespace must not leak."""

    def test_silence_public_loggers_drops_warnings(self):
        from agent.logger import silence_public_loggers
        import logging

        silence_public_loggers()

        err = io.StringIO()
        with patch("sys.stderr", err):
            for ns in ("deng.rejoin", "deng.rejoin.window_layout",
                       "deng.rejoin.layout", "deng.rejoin.start",
                       "deng.rejoin.supervisor", "deng.rejoin.window_apply"):
                lg = logging.getLogger(ns)
                lg.warning("test warning that should not leak")
                lg.error("test error that should not leak")

        self.assertEqual(err.getvalue(), "",
            f"internal logger leaked to stderr: {err.getvalue()!r}")


if __name__ == "__main__":
    unittest.main()
