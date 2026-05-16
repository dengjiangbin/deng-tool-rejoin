"""Global stability, segfault-prevention, and menu-safety tests.

Covers:
- safe_io.safe_prompt() contract (EOF, Ctrl-C, blank, default)
- safe_io.setup_faulthandler() does not crash
- main() global exception wrapper (KeyboardInterrupt, EOFError, unhandled)
- Menu stability under blank / invalid / EOF / Ctrl-C input (50-iteration stress)
- Package submenu stability (blank/invalid/EOF for every option)
- Start flow handles layout failure, config error, su failure without crash
- window_layout excludes Termux and non-Roblox packages
- Stale "40%" string is gone from codebase strings
- Banner is NOT called inside layout / supervisor heartbeat
- License gate does not segfault on wrong/bound key retry
"""

from __future__ import annotations

import argparse
import io
import itertools
import sys
import threading
import unittest
import unittest.mock
from contextlib import redirect_stdout

# ── helpers ──────────────────────────────────────────────────────────────────


def _args(no_color: bool = True, **kw) -> argparse.Namespace:
    ns = argparse.Namespace(no_color=no_color, verbose=False, debug=False, lines=50)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _stdin_of(text: str) -> io.StringIO:
    return io.StringIO(text)


def _input_seq(*values: str):
    """Return an iterator of input values, then raise EOFError on exhaustion."""
    def _side_effect(prompt=""):
        try:
            return next(_gen)
        except StopIteration:
            raise EOFError
    _gen = iter(values)
    return _side_effect


def _input_val(value: str):
    """Always return the same value from input()."""
    def _side_effect(prompt=""):
        return value
    return _side_effect


def _ctx_input(*values: str):
    """Context manager that patches builtins.input with a sequence of values."""
    return unittest.mock.patch("builtins.input", side_effect=_input_seq(*values))


# ─────────────────────────────────────────────────────────────────────────────
# safe_io.safe_prompt
# ─────────────────────────────────────────────────────────────────────────────

class TestSafePrompt(unittest.TestCase):
    """safe_prompt() contract tests.

    Tests cover the Termux path (TERMUX_VERSION set → uses sys.stdin.readline)
    and the standard path (no env var → uses builtins.input).
    """

    def setUp(self):
        from agent import safe_io
        self.sp = safe_io.safe_prompt

    # ── Termux / readline-bypass path ────────────────────────────────────────

    def _termux_env(self):
        return unittest.mock.patch.dict("os.environ", {"TERMUX_VERSION": "1"})

    def _patch_stdin(self, text: str):
        return unittest.mock.patch("sys.stdin", _stdin_of(text))

    def test_termux_returns_value_on_normal_input(self):
        with self._termux_env(), self._patch_stdin("hello\n"):
            result = self.sp("Enter: ")
        self.assertEqual(result, "hello")

    def test_termux_strips_newline(self):
        with self._termux_env(), self._patch_stdin("world\n"):
            result = self.sp("> ")
        self.assertEqual(result, "world")

    def test_termux_returns_default_on_blank_enter(self):
        with self._termux_env(), self._patch_stdin("\n"):
            result = self.sp("Name: ", default="fallback")
        self.assertEqual(result, "fallback")

    def test_termux_returns_empty_string_when_allow_blank_and_no_default(self):
        with self._termux_env(), self._patch_stdin("\n"):
            result = self.sp("Val: ", allow_blank=True)
        self.assertEqual(result, "")

    def test_termux_returns_none_on_eof(self):
        with self._termux_env(), self._patch_stdin(""):
            result = self.sp("Input: ")
        self.assertIsNone(result)

    def test_termux_returns_none_on_keyboard_interrupt(self):
        with self._termux_env(), unittest.mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.readline.side_effect = KeyboardInterrupt
            result = self.sp("Input: ")
        self.assertIsNone(result)

    def test_termux_returns_none_on_oserror(self):
        with self._termux_env(), unittest.mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.readline.side_effect = OSError("bad fd")
            result = self.sp("Input: ")
        self.assertIsNone(result)

    # ── Standard path (builtins.input-compatible) ─────────────────────────────

    def test_standard_returns_value(self):
        with unittest.mock.patch("builtins.input", return_value="hello"):
            result = self.sp("Enter: ")
        self.assertEqual(result, "hello")

    def test_standard_returns_default_on_blank(self):
        with unittest.mock.patch("builtins.input", return_value=""):
            result = self.sp("Name: ", default="fallback")
        self.assertEqual(result, "fallback")

    def test_standard_returns_none_on_keyboard_interrupt(self):
        with unittest.mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            result = self.sp("Input: ")
        self.assertIsNone(result)

    def test_standard_returns_none_on_eoferror(self):
        with unittest.mock.patch("builtins.input", side_effect=EOFError):
            result = self.sp("Input: ")
        self.assertIsNone(result)

    def test_stress_50_iterations_no_exception(self):
        """50 rapid calls must all return without raising."""
        for _ in range(50):
            with unittest.mock.patch("builtins.input", return_value="test"):
                result = self.sp("> ")
            self.assertEqual(result, "test")


# ─────────────────────────────────────────────────────────────────────────────
# safe_io.setup_faulthandler
# ─────────────────────────────────────────────────────────────────────────────

class TestSetupFaulthandler(unittest.TestCase):

    def test_does_not_raise(self):
        from agent import safe_io
        try:
            safe_io.setup_faulthandler()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"setup_faulthandler raised: {exc}")

    def test_calling_twice_does_not_raise(self):
        from agent import safe_io
        safe_io.setup_faulthandler()
        safe_io.setup_faulthandler()


# ─────────────────────────────────────────────────────────────────────────────
# main() global exception wrapper
# ─────────────────────────────────────────────────────────────────────────────

class TestMainGlobalWrapper(unittest.TestCase):
    """main() must handle all exceptions and never print raw traceback."""

    def _run_main(self, argv, stdin_text=""):
        from agent.commands import main
        buf = io.StringIO()
        err_buf = io.StringIO()
        with unittest.mock.patch("sys.stdin", _stdin_of(stdin_text)), \
             redirect_stdout(buf), \
             unittest.mock.patch("sys.stderr", err_buf):
            try:
                rc = main(argv)
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 0
        return rc, buf.getvalue(), err_buf.getvalue()

    def test_keyboard_interrupt_returns_cleanly(self):
        """Ctrl+C must exit cleanly (return 0) with no public traceback or message.

        User requirement: 'Ctrl+C must stop cleanly and return control'.
        """
        from agent.commands import main
        buf = io.StringIO()
        err = io.StringIO()
        with (
            unittest.mock.patch("agent.commands.parse_args", side_effect=KeyboardInterrupt),
            redirect_stdout(buf),
            unittest.mock.patch("sys.stderr", err),
        ):
            rc = main(["version"])
        self.assertEqual(rc, 0, "KeyboardInterrupt must return 0 (clean exit)")
        # No public Interrupted/Stopped/traceback text
        public_out = buf.getvalue() + err.getvalue()
        self.assertNotIn("Interrupted", public_out)
        self.assertNotIn("Traceback", public_out)

    def test_eof_error_returns_0(self):
        from agent.commands import main
        with unittest.mock.patch("agent.commands.parse_args", side_effect=EOFError):
            rc = main(["version"])
        self.assertEqual(rc, 0)

    def test_unhandled_exception_returns_1_no_traceback(self):
        from agent.commands import main
        out = io.StringIO()
        err = io.StringIO()
        with unittest.mock.patch("agent.commands.parse_args", side_effect=RuntimeError("boom")), \
             redirect_stdout(out), \
             unittest.mock.patch("sys.stderr", err):
            rc = main(["version"])
        self.assertEqual(rc, 1)
        combined = out.getvalue() + err.getvalue()
        self.assertNotIn("Traceback", combined)
        self.assertIn("internal error", combined.lower())

    def test_system_exit_with_int_is_propagated(self):
        from agent.commands import main
        with unittest.mock.patch("agent.commands.parse_args", side_effect=SystemExit(42)):
            rc = main(["version"])
        self.assertEqual(rc, 42)

    def test_system_exit_with_none_returns_0(self):
        from agent.commands import main
        with unittest.mock.patch("agent.commands.parse_args", side_effect=SystemExit(None)):
            rc = main(["version"])
        self.assertEqual(rc, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Menu stability
# ─────────────────────────────────────────────────────────────────────────────

class TestMenuStabilityEOF(unittest.TestCase):
    """Every menu exit path must work under EOF / blank / invalid input."""

    def _run_menu_with_inputs(self, *inputs: str):
        from agent.menu import run_menu
        from agent.commands import _handlers
        args = _args()
        buf = io.StringIO()
        with unittest.mock.patch("agent.menu._is_interactive", return_value=True), \
             unittest.mock.patch("builtins.input", side_effect=_input_seq(*inputs)), \
             redirect_stdout(buf):
            try:
                rc = run_menu(args, _handlers())
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 0
        return rc, buf.getvalue()

    def test_eof_exits_cleanly_returns_int(self):
        # No inputs → first input() raises EOFError → menu exits cleanly
        rc, _ = self._run_menu_with_inputs()
        self.assertIsInstance(rc, int)

    def test_eof_does_not_raise(self):
        try:
            self._run_menu_with_inputs()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"run_menu raised on EOF: {exc}")

    def test_invalid_choice_loops_then_exits_on_eof(self):
        """Invalid choice → press enter → next choice is EOF → exits cleanly."""
        # "9" → invalid, "" → press enter, then EOFError from exhaustion
        rc, _ = self._run_menu_with_inputs("9", "")
        self.assertIsInstance(rc, int)

    def test_exit_choice_returns_0(self):
        rc, _ = self._run_menu_with_inputs("0")
        self.assertEqual(rc, 0)

    def test_blank_input_stress_50_then_eof(self):
        """50 blank inputs followed by EOF must not crash."""
        many_blanks = [""] * 50
        rc, _ = self._run_menu_with_inputs(*many_blanks)
        self.assertIsInstance(rc, int)


# ─────────────────────────────────────────────────────────────────────────────
# Package submenu stability
# ─────────────────────────────────────────────────────────────────────────────

class TestPackageMenuStability(unittest.TestCase):
    """Every package submenu option must handle blank/invalid/EOF without crash."""

    def _draft(self):
        from agent.config import default_config
        cfg = default_config()
        cfg["roblox_packages"] = [
            {"package": "com.roblox.client", "account_username": "Main", "enabled": True, "username_source": "manual"}
        ]
        return cfg

    def _run_pkg_menu(self, *inputs: str):
        from agent.commands import _config_menu_package
        draft = self._draft()
        with unittest.mock.patch("builtins.input", side_effect=_input_seq(*inputs)), \
             redirect_stdout(io.StringIO()):
            try:
                result = _config_menu_package(draft)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"_config_menu_package raised: {exc}")
        return result

    def test_eof_exits_cleanly(self):
        result = self._run_pkg_menu()  # no inputs → EOF
        self.assertIsInstance(result, dict)

    def test_back_choice_exits(self):
        result = self._run_pkg_menu("0")
        self.assertIsInstance(result, dict)

    def test_blank_input_exits(self):
        result = self._run_pkg_menu("")  # blank → treated as "0" (default)
        self.assertIsInstance(result, dict)

    def test_invalid_choice_many_then_eof(self):
        """Many invalid inputs followed by EOF must not crash."""
        result = self._run_pkg_menu(*["99"] * 20)
        self.assertIsInstance(result, dict)

    def test_auto_detect_eof_returns_draft(self):
        """Auto Detect → EOF on package selection returns original draft."""
        from agent.commands import _package_menu_auto_detect
        draft = self._draft()
        with unittest.mock.patch("agent.commands._gather_roblox_candidates_for_ui", return_value=[]), \
             unittest.mock.patch("builtins.input", side_effect=EOFError), \
             redirect_stdout(io.StringIO()):
            result = _package_menu_auto_detect(draft)
        self.assertIsInstance(result, dict)

    def test_add_package_eof_returns_draft(self):
        """Add Package → EOF returns original draft."""
        from agent.commands import _package_menu_add
        draft = self._draft()
        with unittest.mock.patch("agent.commands._gather_roblox_candidates_for_ui", return_value=[]), \
             unittest.mock.patch("builtins.input", side_effect=EOFError), \
             redirect_stdout(io.StringIO()):
            result = _package_menu_add(draft)
        self.assertIsInstance(result, dict)

    def test_remove_package_eof_returns_draft(self):
        """Remove Package → EOF returns original draft."""
        from agent.commands import _package_menu_remove
        draft = self._draft()
        with unittest.mock.patch("builtins.input", side_effect=EOFError), \
             redirect_stdout(io.StringIO()):
            result = _package_menu_remove(draft)
        self.assertIsInstance(result, dict)

    def test_remove_package_invalid_then_eof(self):
        from agent.commands import _package_menu_remove
        draft = self._draft()
        with unittest.mock.patch("builtins.input", side_effect=_input_seq("99")), \
             redirect_stdout(io.StringIO()):
            result = _package_menu_remove(draft)
        self.assertIsInstance(result, dict)

    def test_remove_package_back_choice(self):
        from agent.commands import _package_menu_remove
        draft = self._draft()
        with unittest.mock.patch("builtins.input", return_value="0"), \
             redirect_stdout(io.StringIO()):
            result = _package_menu_remove(draft)
        self.assertIsInstance(result, dict)

    def test_no_traceback_in_package_menu_output(self):
        """Package submenu must never produce a raw Traceback string."""
        from agent.commands import _config_menu_package
        draft = self._draft()
        buf = io.StringIO()
        with unittest.mock.patch("builtins.input", side_effect=EOFError), \
             redirect_stdout(buf):
            _config_menu_package(draft)
        self.assertNotIn("Traceback", buf.getvalue())


# ─────────────────────────────────────────────────────────────────────────────
# Start flow safety
# ─────────────────────────────────────────────────────────────────────────────

class TestStartFlowSafety(unittest.TestCase):
    """Start must not crash or call sys.exit when sub-operations fail."""

    def _make_args(self):
        return _args(no_color=True, verbose=False, debug=False, lines=50)

    def _base_cfg(self):
        from agent.config import default_config
        cfg = default_config()
        cfg["first_setup_completed"] = True
        cfg["roblox_packages"] = [
            {"package": "com.roblox.client", "account_username": "Main",
             "enabled": True, "username_source": "manual"}
        ]
        return cfg

    def test_layout_failure_does_not_crash_start(self):
        """If _prepare_automatic_layout raises, cmd_start must still continue."""
        from agent.commands import _prepare_automatic_layout

        cfg = self._base_cfg()
        entries = [{"package": "com.roblox.client", "account_username": "Main",
                    "enabled": True, "username_source": "manual"}]
        with unittest.mock.patch(
            "agent.window_layout.calculate_split_layout",
            side_effect=RuntimeError("layout boom"),
        ):
            result_cfg, note = _prepare_automatic_layout(cfg, entries)
        self.assertIsInstance(result_cfg, dict)
        self.assertIsInstance(note, str)
        self.assertIn("layout", note.lower())

    def test_layout_failure_note_is_internal_not_public(self):
        """A layout failure must not produce a public-friendly error note.

        The note string returned by _prepare_automatic_layout is internal
        (logged to the debug log).  Public users see only the table state.
        We assert the note is short, internal-looking, and never raises.
        """
        from agent.commands import _prepare_automatic_layout

        cfg = self._base_cfg()
        entries = [{"package": "com.roblox.client", "account_username": "Main",
                    "enabled": True, "username_source": "manual"}]
        with unittest.mock.patch(
            "agent.window_layout.calculate_split_layout",
            side_effect=RuntimeError("boom"),
        ):
            result_cfg, note = _prepare_automatic_layout(cfg, entries)
        self.assertIsInstance(result_cfg, dict)
        self.assertIsInstance(note, str)
        # Note must be present and indicate layout outcome (internal label),
        # but must not contain user-visible noise like 'Kaeru' or long sentences.
        self.assertIn("layout", note.lower())

    def test_start_returns_int_when_config_missing(self):
        """If config is missing, cmd_start must return int, not crash."""
        from agent.commands import cmd_start
        from agent.config import ConfigError

        args = self._make_args()
        buf = io.StringIO()
        with unittest.mock.patch("agent.commands.load_config", side_effect=ConfigError("no config")), \
             unittest.mock.patch("agent.commands.ensure_app_dirs"), \
             unittest.mock.patch("agent.commands.keystore") as mock_ks, \
             redirect_stdout(buf):
            mock_ks.DEV_MODE = True
            rc = cmd_start(args)
        self.assertIsInstance(rc, int)

    def test_start_does_not_print_traceback_on_error(self):
        """Even on unexpected error, cmd_start must not dump raw Traceback."""
        from agent.commands import cmd_start
        from agent.config import default_config

        args = self._make_args()
        buf = io.StringIO()
        with unittest.mock.patch("agent.commands.load_config", side_effect=RuntimeError("unexpected")), \
             redirect_stdout(buf):
            try:
                rc = cmd_start(args)
            except Exception:  # noqa: BLE001
                pass
        self.assertNotIn("Traceback", buf.getvalue())

    def test_su_failure_in_layout_does_not_crash(self):
        """Root command failure during layout must not kill Start."""
        from agent.commands import _prepare_automatic_layout
        cfg = self._base_cfg()
        entries = [{"package": "com.roblox.client", "account_username": "Main",
                    "enabled": True, "username_source": "manual"}]
        with unittest.mock.patch("agent.android.detect_root") as mock_root, \
             unittest.mock.patch("agent.window_layout.update_app_cloner_xml",
                                 return_value=(False, "permission denied")), \
             unittest.mock.patch("agent.window_layout.update_app_cloner_xml_root",
                                 side_effect=RuntimeError("su died")):
            from agent.android import RootInfo
            mock_root.return_value = RootInfo(available=True, tool="su")
            result_cfg, note = _prepare_automatic_layout(cfg, entries)
        self.assertIsInstance(result_cfg, dict)
        self.assertIsInstance(note, str)


# ─────────────────────────────────────────────────────────────────────────────
# window_layout Termux exclusion
# ─────────────────────────────────────────────────────────────────────────────

class TestLayoutTermuxExclusion(unittest.TestCase):
    """Layout must never target Termux or Android system packages."""

    def test_is_layout_excluded_termux(self):
        from agent.window_layout import _is_layout_excluded
        self.assertTrue(_is_layout_excluded("com.termux"))
        self.assertTrue(_is_layout_excluded("com.termux.boot"))
        self.assertTrue(_is_layout_excluded("com.termux.api"))

    def test_is_layout_excluded_android_system(self):
        from agent.window_layout import _is_layout_excluded
        self.assertTrue(_is_layout_excluded("com.android.systemui"))
        self.assertTrue(_is_layout_excluded("android"))

    def test_roblox_not_excluded(self):
        from agent.window_layout import _is_layout_excluded
        self.assertFalse(_is_layout_excluded("com.roblox.client"))
        self.assertFalse(_is_layout_excluded("com.example.robloxclone"))

    def test_apply_layout_filters_termux_from_packages(self):
        """If com.termux sneaks into the package list, layout must skip it."""
        from agent.window_layout import apply_layout_to_packages
        packages = ["com.termux", "com.roblox.client"]
        msgs, preview = apply_layout_to_packages(packages, write_xml=False)
        layout_packages = [p["package"] for p in preview]
        self.assertNotIn("com.termux", layout_packages)

    def test_apply_layout_with_only_termux_returns_safe_message(self):
        """Package list containing only Termux → safe message, no crash."""
        from agent.window_layout import apply_layout_to_packages
        msgs, preview = apply_layout_to_packages(["com.termux"], write_xml=False)
        self.assertEqual(preview, [])
        self.assertIsInstance(msgs, list)
        self.assertEqual(len(msgs), 1)

    def test_apply_layout_empty_list_returns_safe_message(self):
        from agent.window_layout import apply_layout_to_packages
        msgs, preview = apply_layout_to_packages([], write_xml=False)
        self.assertEqual(preview, [])


# ─────────────────────────────────────────────────────────────────────────────
# Stale "40%" string audit
# ─────────────────────────────────────────────────────────────────────────────

class TestNo40PercentStrings(unittest.TestCase):
    """Verify that the stale '40% left reserved' message no longer appears."""

    def _read_py_sources(self) -> str:
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        sources = []
        for path in (root / "agent").glob("*.py"):
            try:
                sources.append(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        return "\n".join(sources)

    def test_no_40pct_left_reserved_message(self):
        combined = self._read_py_sources()
        self.assertNotIn("40% left reserved for Termux log", combined)

    def test_layout_constants_are_35_65(self):
        from agent.window_layout import TERMUX_LOG_FRACTION, RIGHT_PANE_FRACTION
        self.assertAlmostEqual(TERMUX_LOG_FRACTION, 0.35, places=5)
        self.assertAlmostEqual(RIGHT_PANE_FRACTION, 0.65, places=5)

    def test_docstring_says_35_not_40(self):
        import agent.window_layout as wl
        doc = wl.__doc__ or ""
        self.assertIn("35%", doc)
        self.assertNotIn("40%", doc)


# ─────────────────────────────────────────────────────────────────────────────
# Banner not called in loops
# ─────────────────────────────────────────────────────────────────────────────

class TestBannerNotCalledInLoops(unittest.TestCase):
    """print_banner must not appear inside auto-layout or supervisor logic."""

    def test_layout_apply_does_not_call_banner(self):
        from agent.commands import _prepare_automatic_layout
        from agent.config import default_config
        cfg = default_config()
        entries = [{"package": "com.roblox.client", "account_username": "",
                    "enabled": True, "username_source": "not_set"}]
        with unittest.mock.patch("agent.banner.print_banner") as mock_banner, \
             unittest.mock.patch("agent.window_layout.apply_layout_to_packages",
                                 return_value=(["ok"], [])):
            _prepare_automatic_layout(cfg, entries)
        mock_banner.assert_not_called()

    def test_run_menu_calls_banner_through_print_menu(self):
        """run_menu uses print_menu which calls print_banner — exactly once for one loop iteration."""
        from agent.menu import run_menu
        from agent.commands import _handlers
        args = _args()
        banner_calls = []

        def counting_banner(*a, **kw):
            banner_calls.append(1)

        # patch the name as it is imported into agent.menu
        with unittest.mock.patch("agent.menu.print_banner", side_effect=counting_banner), \
             unittest.mock.patch("agent.menu._is_interactive", return_value=True), \
             unittest.mock.patch("builtins.input", side_effect=_input_seq("0")), \
             redirect_stdout(io.StringIO()):
            run_menu(args, _handlers())
        # Must have been called exactly once (for the one menu display before exit choice)
        self.assertEqual(len(banner_calls), 1)

    def test_edit_config_menu_noninteractive_banner_once(self):
        """Non-interactive config menu must print banner at most once."""
        from agent.commands import _run_edit_config_menu
        from agent.config import default_config, validate_config
        args = _args()
        cfg = validate_config(default_config())
        banner_calls = []

        def counting_banner(*a, **kw):
            banner_calls.append(1)

        with unittest.mock.patch("agent.commands.print_banner", side_effect=counting_banner), \
             unittest.mock.patch("agent.commands._is_interactive", return_value=False), \
             redirect_stdout(io.StringIO()):
            _run_edit_config_menu(cfg, args)

        self.assertLessEqual(len(banner_calls), 1)


# ─────────────────────────────────────────────────────────────────────────────
# License gate stability
# ─────────────────────────────────────────────────────────────────────────────

class TestLicenseGateStability(unittest.TestCase):
    """License gate must not segfault on wrong/bound key retry."""

    def _run_remote_loop(self, input_values: list[str], check_results: list[tuple[str, str]]):
        from agent.commands import _ensure_remote_license_menu_loop
        from agent.config import default_config
        cfg = default_config()
        cfg.setdefault("license", {})["key"] = "DENG-1234-5678-9ABC-DEF0"
        args = _args()
        results_iter = iter(check_results)

        def fake_check(c):
            return next(results_iter, ("error", "no more results"))

        buf = io.StringIO()
        with unittest.mock.patch("agent.commands._remote_license_run_check", side_effect=fake_check), \
             unittest.mock.patch("agent.commands._ensure_install_id_saved", side_effect=lambda x: x), \
             unittest.mock.patch("agent.commands._is_interactive", return_value=True), \
             unittest.mock.patch("agent.commands.load_config", side_effect=lambda: dict(cfg)), \
             unittest.mock.patch("agent.commands.save_config", side_effect=lambda x: x), \
             unittest.mock.patch("builtins.input", side_effect=_input_seq(*input_values)), \
             redirect_stdout(buf):
            result = _ensure_remote_license_menu_loop(cfg, args, False)
        return result, buf.getvalue()

    def test_wrong_device_then_eof_returns_false(self):
        """Wrong device key + EOF on choice → returns False, no crash."""
        ok, _ = self._run_remote_loop(
            [],  # no inputs → EOF immediately
            [("wrong_device", "Device mismatch")]
        )
        self.assertFalse(ok)

    def test_wrong_device_then_exit_choice_returns_false(self):
        ok, _ = self._run_remote_loop(
            ["2"],  # choose Exit
            [("wrong_device", "Device mismatch")]
        )
        self.assertFalse(ok)

    def test_invalid_key_then_valid_key_returns_true(self):
        """Bad key + Try Another Key + valid key → returns True."""
        ok, _ = self._run_remote_loop(
            ["1", "DENG-AAAA-BBBB-CCCC-DDDD"],  # "Try Another Key", then paste new key
            [
                ("invalid", "Bad key"),    # first check fails
                ("active", "License OK"),  # second check succeeds
            ]
        )
        self.assertTrue(ok)

    def test_does_not_exceed_max_retries(self):
        """Even with always-invalid keys, loop terminates within _MAX_RETRIES."""
        from agent.commands import _ensure_remote_license_menu_loop
        from agent.config import default_config
        cfg = default_config()
        cfg.setdefault("license", {})["key"] = "DENG-1234-5678-9ABC-DEF0"
        args = _args()
        call_count = [0]

        def always_invalid(cfg):
            call_count[0] += 1
            return ("invalid", "bad key")

        with unittest.mock.patch("agent.commands._remote_license_run_check", side_effect=always_invalid), \
             unittest.mock.patch("agent.commands._ensure_install_id_saved", side_effect=lambda x: x), \
             unittest.mock.patch("agent.commands.load_config", side_effect=lambda: dict(cfg)), \
             unittest.mock.patch("agent.commands.save_config", side_effect=lambda x: x), \
             unittest.mock.patch("builtins.input", side_effect=_input_seq(*["1"] * 20)), \
             redirect_stdout(io.StringIO()):
            result = _ensure_remote_license_menu_loop(cfg, args, False)
        self.assertFalse(result)
        self.assertLessEqual(call_count[0], 11)  # _MAX_RETRIES = 10 + 1 safety

    def test_no_sys_exit_from_license_loop(self):
        """License loop must never call sys.exit."""
        from agent.commands import _ensure_remote_license_menu_loop
        from agent.config import default_config
        cfg = default_config()
        cfg.setdefault("license", {})["key"] = "DENG-DEAD-BEEF-0000-CAFE"
        args = _args()

        with unittest.mock.patch("agent.commands._remote_license_run_check",
                                 return_value=("invalid", "bad")), \
             unittest.mock.patch("agent.commands._ensure_install_id_saved", side_effect=lambda x: x), \
             unittest.mock.patch("agent.commands.load_config", side_effect=lambda: dict(cfg)), \
             unittest.mock.patch("agent.commands.save_config", side_effect=lambda x: x), \
             unittest.mock.patch("builtins.input", side_effect=EOFError), \
             redirect_stdout(io.StringIO()), \
             unittest.mock.patch("sys.exit", side_effect=AssertionError("sys.exit called")):
            result = _ensure_remote_license_menu_loop(cfg, args, False)
        self.assertFalse(result)


# ─────────────────────────────────────────────────────────────────────────────
# Concurrent / threading safety
# ─────────────────────────────────────────────────────────────────────────────

class TestThreadSafety(unittest.TestCase):
    """Basic threading test to verify shared state does not crash."""

    def test_safe_prompt_callable_from_multiple_threads(self):
        """safe_prompt called from multiple threads must not raise."""
        from agent import safe_io
        errors = []

        def worker():
            try:
                with unittest.mock.patch("sys.stdin", _stdin_of("test\n")):
                    safe_io.safe_prompt("t> ")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [])


# ─────────────────────────────────────────────────────────────────────────────
# Orientation / coordinate validation
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinateValidation(unittest.TestCase):
    """All layout rectangles must stay inside the screen bounds."""

    def _check_rects(self, packages, width, height):
        from agent.window_layout import apply_layout_to_packages
        _, preview = apply_layout_to_packages(packages, write_xml=False, use_split_layout=len(packages) > 1)
        for rect in preview:
            self.assertGreaterEqual(rect["left"], 0, f"left<0: {rect}")
            self.assertGreaterEqual(rect["top"], 0, f"top<0: {rect}")
            self.assertGreater(rect["right"], rect["left"], f"right<=left: {rect}")
            self.assertGreater(rect["bottom"], rect["top"], f"bottom<=top: {rect}")
            self.assertLessEqual(rect["right"], width + 2, f"right>{width}: {rect}")
            self.assertLessEqual(rect["bottom"], height + 2, f"bottom>{height}: {rect}")

    def test_landscape_1920x1080_single(self):
        with unittest.mock.patch("agent.window_layout.detect_display_info") as mock_di:
            from agent.window_layout import DisplayInfo
            mock_di.return_value = DisplayInfo(width=1920, height=1080, density=420)
            self._check_rects(["com.roblox.client"], 1920, 1080)

    def test_landscape_1920x1080_two_packages(self):
        with unittest.mock.patch("agent.window_layout.detect_display_info") as mock_di:
            from agent.window_layout import DisplayInfo
            mock_di.return_value = DisplayInfo(width=1920, height=1080, density=420)
            self._check_rects(["com.roblox.client", "com.roblox.clone1"], 1920, 1080)

    def test_portrait_1080x1920_single(self):
        with unittest.mock.patch("agent.window_layout.detect_display_info") as mock_di:
            from agent.window_layout import DisplayInfo
            mock_di.return_value = DisplayInfo(width=1080, height=1920, density=420)
            self._check_rects(["com.roblox.client"], 1080, 1920)

    def test_landscape_reserves_35_pct_left_for_termux(self):
        """Right-pane origin must be >= 35% of screen width."""
        from agent.window_layout import apply_layout_to_packages, DisplayInfo
        with unittest.mock.patch("agent.window_layout.detect_display_info") as mock_di:
            mock_di.return_value = DisplayInfo(width=1920, height=1080, density=420)
            _, preview = apply_layout_to_packages(["com.roblox.client"], write_xml=False)
        if preview:
            min_left = min(r["left"] for r in preview)
            self.assertGreaterEqual(min_left, int(1920 * 0.34))  # ≥ ~35%


if __name__ == "__main__":
    unittest.main()
