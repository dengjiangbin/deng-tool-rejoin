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


if __name__ == "__main__":
    unittest.main()
