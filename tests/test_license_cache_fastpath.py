"""Tests for the license cache fast-path and isolated-subprocess fallback.

Real-device probe ``p-39924732cd`` showed the parent ``deng-rejoin``
process dies of ``SIGSEGV`` at ``STEP:license_remote_check`` — the
network code path in :mod:`agent.license` segfaults Python intermittently
on Termux 13 + Python 3.13.13.  These tests verify the two safeguards:

1. ``_license_cache_is_fresh_active``  — skip the network call entirely
   when the most recent cached check was ``active`` within 24 h.
2. ``_license_should_offline_grace``   — treat transient remote failures
   (server_unavailable, error, "") as ``active`` when the cached check
   was ``active`` within 30 days.
3. ``_remote_license_check_isolated``  — runs the segfault-prone code in
   a child Python; a SIGSEGV becomes a clean ``server_unavailable``
   instead of killing the menu.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock


def _iso(delta_seconds: float) -> str:
    """ISO-8601 timestamp ``delta_seconds`` ago in UTC."""
    return (datetime.now(timezone.utc) - timedelta(seconds=delta_seconds)).isoformat()


class LicenseCacheFreshActiveTest(unittest.TestCase):
    """``_license_cache_is_fresh_active`` only opens menu on recent ``active``."""

    def setUp(self) -> None:
        from agent import commands  # noqa: PLC0415
        self.fn = commands._license_cache_is_fresh_active

    def test_recent_active_is_fresh(self) -> None:
        lic = {"last_status": "active", "last_check_at": _iso(60)}
        self.assertTrue(self.fn(lic))

    def test_just_under_24h_is_fresh(self) -> None:
        lic = {"last_status": "active", "last_check_at": _iso(23 * 3600)}
        self.assertTrue(self.fn(lic))

    def test_over_24h_is_stale(self) -> None:
        lic = {"last_status": "active", "last_check_at": _iso(25 * 3600)}
        self.assertFalse(self.fn(lic))

    def test_non_active_status_never_fresh(self) -> None:
        for status in ("inactive", "wrong_device", "server_unavailable",
                       "not_configured", "error", ""):
            lic = {"last_status": status, "last_check_at": _iso(60)}
            self.assertFalse(self.fn(lic), f"status={status!r}")

    def test_missing_or_garbage_timestamp_is_stale(self) -> None:
        for raw in (None, "", "not-a-date", 12345, [], {}):
            lic = {"last_status": "active", "last_check_at": raw}
            self.assertFalse(self.fn(lic), f"raw={raw!r}")

    def test_naive_timestamp_assumed_utc(self) -> None:
        naive = (datetime.utcnow() - timedelta(seconds=120)).isoformat()
        lic = {"last_status": "active", "last_check_at": naive}
        self.assertTrue(self.fn(lic))

    def test_zulu_suffix_supported(self) -> None:
        z = (datetime.now(timezone.utc) - timedelta(seconds=300)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        lic = {"last_status": "active", "last_check_at": z}
        self.assertTrue(self.fn(lic))

    def test_future_timestamp_is_stale(self) -> None:
        # Clock skew → future last_check_at; treat as stale (negative age).
        lic = {"last_status": "active", "last_check_at": _iso(-3600)}
        self.assertFalse(self.fn(lic))

    def test_non_dict_input_is_stale(self) -> None:
        self.assertFalse(self.fn(None))         # type: ignore[arg-type]
        self.assertFalse(self.fn("not-a-dict"))  # type: ignore[arg-type]


class LicenseOfflineGraceTest(unittest.TestCase):
    """``_license_should_offline_grace`` lets recent-active users survive transient failures."""

    def setUp(self) -> None:
        from agent import commands  # noqa: PLC0415
        self.fn = commands._license_should_offline_grace

    def test_active_within_24h_qualifies(self) -> None:
        lic = {"last_status": "active", "last_check_at": _iso(3600)}
        self.assertTrue(self.fn(lic))

    def test_active_within_30d_qualifies(self) -> None:
        lic = {"last_status": "active", "last_check_at": _iso(29 * 24 * 3600)}
        self.assertTrue(self.fn(lic))

    def test_active_over_30d_does_not_qualify(self) -> None:
        lic = {"last_status": "active", "last_check_at": _iso(31 * 24 * 3600)}
        self.assertFalse(self.fn(lic))

    def test_non_active_never_qualifies(self) -> None:
        lic = {"last_status": "wrong_device", "last_check_at": _iso(60)}
        self.assertFalse(self.fn(lic))


class IsolatedRemoteCheckTest(unittest.TestCase):
    """``_remote_license_check_isolated`` translates subprocess outcomes."""

    def _patched(self, proc_mock: mock.Mock) -> tuple[str, str]:
        from agent import commands  # noqa: PLC0415
        with mock.patch("subprocess.run", return_value=proc_mock):
            return commands._remote_license_check_isolated({"license": {"key": "X"}})

    def test_normal_active(self) -> None:
        proc = mock.Mock()
        proc.returncode = 0
        proc.stdout = json.dumps({"result": "active", "message": "ok"}).encode()
        proc.stderr = b""
        result, msg = self._patched(proc)
        self.assertEqual(result, "active")
        self.assertEqual(msg, "ok")

    def test_sigsegv_child_returns_server_unavailable(self) -> None:
        """Child SIGSEGV (rc = -11) MUST NOT raise — must become a soft fail."""
        proc = mock.Mock()
        proc.returncode = -11
        proc.stdout = b""
        proc.stderr = b""
        result, msg = self._patched(proc)
        self.assertEqual(result, "server_unavailable")
        self.assertIn("crashed safely", msg)
        self.assertIn("signal 11", msg)

    def test_nonzero_exit_returns_server_unavailable(self) -> None:
        proc = mock.Mock()
        proc.returncode = 2
        proc.stdout = b""
        proc.stderr = b""
        result, msg = self._patched(proc)
        self.assertEqual(result, "server_unavailable")
        self.assertIn("rc=2", msg)

    def test_invalid_json_returns_server_unavailable(self) -> None:
        proc = mock.Mock()
        proc.returncode = 0
        proc.stdout = b"this is not json"
        proc.stderr = b""
        result, msg = self._patched(proc)
        self.assertEqual(result, "server_unavailable")
        self.assertIn("invalid JSON", msg)

    def test_timeout_returns_server_unavailable(self) -> None:
        import subprocess
        from agent import commands  # noqa: PLC0415
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
            result, msg = commands._remote_license_check_isolated({"license": {"key": "X"}})
        self.assertEqual(result, "server_unavailable")
        self.assertIn("timed out", msg.lower())


class LicenseMenuLoopFastPathTest(unittest.TestCase):
    """End-to-end: cmd_menu loop short-circuits on a fresh cached active license."""

    def test_recent_active_skips_remote_check(self) -> None:
        from agent import commands  # noqa: PLC0415
        cfg = {
            "license": {
                "key": "ABCD-EFGH",
                "last_status": "active",
                "last_check_at": _iso(60),
                "mode": "remote",
            },
        }
        with mock.patch.object(commands, "load_config", return_value=cfg), \
             mock.patch.object(commands, "_ensure_install_id_saved", side_effect=lambda c: c), \
             mock.patch.object(commands, "_remote_license_run_check") as dispatcher, \
             mock.patch.object(commands, "_remote_license_check_isolated") as iso, \
             mock.patch.object(commands, "_remote_license_check_direct") as direct, \
             mock.patch.object(commands, "_print_license_ok"):
            import argparse  # noqa: PLC0415
            ok = commands._ensure_remote_license_menu_loop(
                cfg, argparse.Namespace(), use_color=False,
            )
        self.assertTrue(ok)
        # Crucially, no network code path was entered.
        dispatcher.assert_not_called()
        iso.assert_not_called()
        direct.assert_not_called()

    def test_stale_cache_falls_back_to_dispatcher(self) -> None:
        from agent import commands  # noqa: PLC0415
        cfg = {
            "license": {
                "key": "ABCD-EFGH",
                "last_status": "active",
                "last_check_at": _iso(48 * 3600),  # 48 h ago → stale
                "mode": "remote",
            },
        }
        with mock.patch.object(commands, "load_config", return_value=cfg), \
             mock.patch.object(commands, "_ensure_install_id_saved", side_effect=lambda c: c), \
             mock.patch.object(commands, "_remote_license_run_check",
                               return_value=("active", "ok")) as dispatcher, \
             mock.patch.object(commands, "_persist_license_status",
                               side_effect=lambda c, s: c), \
             mock.patch.object(commands, "_print_license_ok"):
            import argparse  # noqa: PLC0415
            ok = commands._ensure_remote_license_menu_loop(
                cfg, argparse.Namespace(), use_color=False,
            )
        self.assertTrue(ok)
        dispatcher.assert_called_once()

    def test_offline_grace_when_dispatcher_returns_unavailable(self) -> None:
        """Cached active + transient remote failure → menu still opens."""
        from agent import commands  # noqa: PLC0415
        cfg = {
            "license": {
                "key": "ABCD-EFGH",
                "last_status": "active",
                "last_check_at": _iso(48 * 3600),  # stale → triggers remote
                "mode": "remote",
            },
        }
        with mock.patch.object(commands, "load_config", return_value=cfg), \
             mock.patch.object(commands, "_ensure_install_id_saved", side_effect=lambda c: c), \
             mock.patch.object(commands, "_remote_license_run_check",
                               return_value=("server_unavailable", "crashed safely (signal 11)")), \
             mock.patch.object(commands, "_print_license_ok") as ok_print:
            import argparse  # noqa: PLC0415
            ok = commands._ensure_remote_license_menu_loop(
                cfg, argparse.Namespace(), use_color=False,
            )
        self.assertTrue(ok, "offline grace must allow menu through")
        ok_print.assert_called()


# ── New: cache-integrity regression for probe p-09484eaab4 ───────────────────


class LicenseTransientNotPersistedTest(unittest.TestCase):
    """A transient remote failure MUST NOT overwrite ``last_status``.

    Real-device evidence (probe ``p-09484eaab4``): the subprocess license
    check was returning ``rc=1`` (broken PYTHONPATH).  The menu loop
    persisted ``last_status = "server_unavailable"`` and the user got
    permanently locked out — cache fast-path & offline grace both require
    ``last_status == "active"``.  After the fix, the menu loop must
    leave ``last_status`` alone for transient results.
    """

    def _run_loop_with(self, transient_result: str) -> dict:
        """Run the menu loop once with ``transient_result`` from the dispatcher
        and a stale, non-cached license; return the final cfg dict."""
        from agent import commands  # noqa: PLC0415

        original_status = "active"
        original_check = _iso(48 * 3600)  # stale (>24h), so cache misses
        cfg = {
            "license": {
                "key": "ABCD-EFGH",
                "last_status": original_status,
                "last_check_at": original_check,
                "mode": "remote",
            },
        }
        persisted: list[tuple[str, str]] = []

        def fake_persist(c: dict, status: str) -> dict:
            persisted.append((status, "PERSISTED"))
            c.setdefault("license", {})["last_status"] = status
            return c

        with mock.patch.object(commands, "load_config", return_value=cfg), \
             mock.patch.object(commands, "_ensure_install_id_saved", side_effect=lambda c: c), \
             mock.patch.object(commands, "_remote_license_run_check",
                               return_value=(transient_result, "boom")), \
             mock.patch.object(commands, "_persist_license_status",
                               side_effect=fake_persist), \
             mock.patch.object(commands, "_print_license_ok"), \
             mock.patch.object(commands, "_print_license_err"), \
             mock.patch.object(commands, "print_beginner_license_gate_help"), \
             mock.patch.object(commands, "_is_interactive", return_value=False):
            import argparse  # noqa: PLC0415
            commands._ensure_remote_license_menu_loop(
                cfg, argparse.Namespace(), use_color=False,
            )
        return {"cfg": cfg, "persisted": persisted}

    def test_server_unavailable_does_not_overwrite_active(self) -> None:
        result = self._run_loop_with("server_unavailable")
        # Offline grace already triggered (cached active < 30d), so no
        # persistence happens at all on this path either.
        self.assertEqual(
            result["cfg"]["license"]["last_status"], "active",
            "transient server_unavailable must NOT corrupt last_status",
        )

    def test_error_result_does_not_persist(self) -> None:
        result = self._run_loop_with("error")
        self.assertEqual(
            result["cfg"]["license"]["last_status"], "active",
            "transient 'error' result must NOT corrupt last_status",
        )

    def test_definitive_wrong_device_still_persists(self) -> None:
        """Sanity: ``wrong_device`` is *not* transient and SHOULD persist."""
        from agent import commands  # noqa: PLC0415

        cfg = {
            "license": {
                "key": "ABCD-EFGH",
                "last_status": "active",
                "last_check_at": _iso(48 * 3600),
                "mode": "remote",
            },
        }
        persisted: list[str] = []

        def fake_persist(c: dict, status: str) -> dict:
            persisted.append(status)
            c.setdefault("license", {})["last_status"] = status
            return c

        with mock.patch.object(commands, "load_config", return_value=cfg), \
             mock.patch.object(commands, "_ensure_install_id_saved", side_effect=lambda c: c), \
             mock.patch.object(commands, "_remote_license_run_check",
                               return_value=("wrong_device", "hwid mismatch")), \
             mock.patch.object(commands, "_persist_license_status",
                               side_effect=fake_persist), \
             mock.patch.object(commands, "_print_license_err"), \
             mock.patch.object(commands, "print_beginner_license_gate_help"), \
             mock.patch.object(commands, "_is_interactive", return_value=False):
            import argparse  # noqa: PLC0415
            commands._ensure_remote_license_menu_loop(
                cfg, argparse.Namespace(), use_color=False,
            )
        self.assertIn("wrong_device", persisted,
                      "definitive results MUST be persisted")

    def test_noninteractive_verify_does_not_persist_transient(self) -> None:
        """``verify_remote_license_noninteractive`` must also keep cache safe."""
        from agent import commands  # noqa: PLC0415

        cfg = {
            "license": {
                "key": "ABCD-EFGH",
                "last_status": "active",
                "last_check_at": _iso(3600),
                "mode": "remote",
            },
        }
        persisted: list[str] = []

        def fake_persist(c: dict, status: str) -> dict:
            persisted.append(status)
            c.setdefault("license", {})["last_status"] = status
            return c

        with mock.patch.object(commands, "_remote_license_run_check",
                               return_value=("server_unavailable", "boom")), \
             mock.patch.object(commands, "_persist_license_status",
                               side_effect=fake_persist), \
             mock.patch.object(commands, "_print_license_err"), \
             mock.patch.object(commands, "print_beginner_license_gate_help"):
            ok = commands.verify_remote_license_noninteractive(cfg, use_color=False)
        self.assertFalse(ok, "transient must still surface as failure")
        self.assertEqual(
            persisted, [],
            "transient result MUST NOT be persisted in noninteractive verify",
        )


class IsolatedSubprocessImportsAgentTest(unittest.TestCase):
    """The isolated subprocess must be able to ``import agent.license``.

    Regression for probe ``p-09484eaab4``: the child Python was launched
    with ``python3 -c "..."`` but no PYTHONPATH, so ``from agent.license``
    failed with ``ModuleNotFoundError`` and the child exited rc=1.  Every
    license check returned ``server_unavailable``.
    """

    def test_pythonpath_passed_to_subprocess(self) -> None:
        """Subprocess env MUST carry PYTHONPATH pointing at the agent's parent."""
        from pathlib import Path  # noqa: PLC0415
        import os  # noqa: PLC0415

        from agent import commands  # noqa: PLC0415

        captured_env: dict[str, str] = {}

        def fake_run(args, **kwargs):  # noqa: ANN001, ANN002
            captured_env.update(kwargs.get("env") or {})
            m = mock.Mock()
            m.returncode = 0
            m.stdout = json.dumps({"result": "active", "message": "ok"}).encode()
            m.stderr = b""
            return m

        with mock.patch("subprocess.run", side_effect=fake_run):
            commands._remote_license_check_isolated({"license": {"key": "X"}})

        self.assertIn("PYTHONPATH", captured_env, "PYTHONPATH must be set")
        agent_parent = str(Path(commands.__file__).resolve().parent.parent)
        pp = captured_env["PYTHONPATH"]
        # First entry must be the agent's parent directory.
        first = pp.split(os.pathsep)[0]
        self.assertEqual(first, agent_parent,
                         f"PYTHONPATH[0] must point at {agent_parent}, got {pp!r}")

    def test_inline_code_inserts_sys_path(self) -> None:
        """The inline ``-c`` payload must ``sys.path.insert(0, ...)`` so the
        child can find ``agent`` even if PYTHONPATH is stripped (some
        sandboxes do this)."""
        from agent import commands  # noqa: PLC0415

        captured_args: list[list[str]] = []

        def fake_run(args, **kwargs):  # noqa: ANN001, ANN002
            captured_args.append(list(args))
            m = mock.Mock()
            m.returncode = 0
            m.stdout = json.dumps({"result": "active", "message": "ok"}).encode()
            m.stderr = b""
            return m

        with mock.patch("subprocess.run", side_effect=fake_run):
            commands._remote_license_check_isolated({"license": {"key": "X"}})

        self.assertEqual(len(captured_args), 1)
        code = captured_args[0][-1]
        self.assertIn("sys.path.insert", code,
                      "inline code MUST manipulate sys.path")
        self.assertIn("from agent.license import", code,
                      "inline code MUST import agent.license")
        self.assertIn("DENG_REJOIN_HOME", code,
                      "inline code SHOULD also honour DENG_REJOIN_HOME env")

    def test_subprocess_import_error_does_not_crash_parent(self) -> None:
        """If the child Python can't import ``agent``, the parent must still
        return cleanly as ``server_unavailable`` (no exception bubbles up)."""
        from agent import commands  # noqa: PLC0415

        proc = mock.Mock()
        proc.returncode = 1
        proc.stdout = b""
        proc.stderr = (
            b"Traceback (most recent call last):\n"
            b"  File \"<string>\", line 5, in <module>\n"
            b"ModuleNotFoundError: No module named 'agent'\n"
        )
        with mock.patch("subprocess.run", return_value=proc):
            result, msg = commands._remote_license_check_isolated(
                {"license": {"key": "X"}},
            )
        self.assertEqual(result, "server_unavailable")
        self.assertIn("rc=1", msg)
        # The stderr hint is included so future probes pinpoint the cause.
        self.assertIn("Traceback", msg)


if __name__ == "__main__":
    unittest.main()
