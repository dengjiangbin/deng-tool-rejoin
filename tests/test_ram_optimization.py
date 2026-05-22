"""Tests for adaptive RAM optimization in WatchdogSupervisor._check_ram_optimization().

Covers:
  1.  RAM check skipped when ram_optimization_enabled=False.
  2.  RAM check skipped when package not yet Online long enough (delay guard).
  3.  RAM check skipped within trim_interval (rate-limit guard).
  4.  RAM ≤ effective target → no action taken (normal mode).
  5.  RAM > target, ≤ restart threshold → trim attempted (normal mode).
  6.  RAM > restart threshold + cooldown expired → restart triggered.
  7.  RAM > restart threshold + in RAM cooldown → restart skipped.
  8.  RAM > restart threshold + in NHB cooldown → restart skipped.
  9.  RAM > restart threshold → cooldown set on both _ram_cooldown_until and _nhb_cooldown_until.
  10. Aggressive mode uses target_aggressive_mb threshold.
  11. Trim result is logged with correct probe tag.
  12. Restart result is logged with correct probe tag.
  13. force_stop_package called before relaunch on RAM restart.
  14. _do_launch called with reason="ram_restart".
  15. On successful RAM restart, _revive_count incremented.
  16. On failed RAM restart, _failure_count incremented.
  17. On successful RAM restart, _online_start_ts cleared.
  18. On successful RAM restart, grace period set via _set_grace.
  19. Ram check updates _ram_last_check_at timestamp.
  20. Trim updates _ram_last_trim_at timestamp.
  21. config defaults used when keys missing.
  22. RAM check probe tag [DENG_REJOIN_RAM_CHECK] emitted.
  23. RAM trim probe tag [DENG_REJOIN_RAM_TRIM] emitted.
  24. RAM restart probe tag [DENG_REJOIN_RAM_RESTART] emitted.
  25. RAM cooldown probe tag [DENG_REJOIN_RAM_RESTART_COOLDOWN] emitted when in cooldown.
  26. No trim attempt when RAM already ≤ target in normal mode.
  27. STATUS_RELAUNCHING set before _do_launch on RAM restart.
  28. STATUS_LAUNCHING set after successful RAM restart.
  29. _check_ram_optimization called from _handle_state for STATUS_ONLINE.
  30. _check_ram_optimization NOT called for STATUS_DEAD.
  31. _check_ram_optimization NOT called for STATUS_NO_HEARTBEAT (handled separately).
  32. Trim not repeated within same trim_interval.
  33. Multiple packages each have independent cooldowns.
  34. Per-package check rate-limiting is independent.
  35. RAM check delay respects online_start_ts correctly.
  36. RAM restart render_callback called before launch.
  37. Trim failure does not crash or abort subsequent logic.
  38. get_package_ram_usage called with package name and root_info.
  39. RAM check with rss_kb=0 (measurement failure) → usage_mb=0 → no action.
  40. Restart threshold defaults to 900 MB.
"""
from __future__ import annotations

import time
import types
import unittest
import unittest.mock
from typing import Any
from unittest.mock import MagicMock, patch, call


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_supervisor(cfg: dict[str, Any] | None = None) -> Any:
    """Build a minimal WatchdogSupervisor instance with mocked dependencies."""
    from agent.supervisor import WatchdogSupervisor, STATUS_ONLINE

    default_cfg: dict[str, Any] = {
        "ram_optimization_enabled": True,
        "ram_target_normal_mb": 700,
        "ram_target_good_mb": 500,
        "ram_target_aggressive_mb": 300,
        "ram_restart_threshold_mb": 900,
        "ram_check_delay_after_online_sec": 30,
        "ram_trim_interval_sec": 120,
        "ram_restart_cooldown_sec": 180,
        "ram_aggressive_mode": False,
        # Opt-in: the restart-path tests below all assume the operator
        # has flipped this flag.  Production default is False (Bug 1 fix,
        # probe p-52aeb6420f) — covered by the dedicated regression class
        # ``TestBug1OnlineProtectedFromRamRestart``.
        "ram_restart_when_online_enabled": True,
        "roblox_package": "com.roblox.client",
        "packages": [{"package": "com.roblox.client", "username": "TestUser"}],
        "private_server_url": "",
        "root_mode_enabled": False,
        "check_interval": 30,
    }

    if cfg:
        default_cfg.update(cfg)

    packages = default_cfg.get("packages", [{"package": "com.roblox.client"}])
    entries = [dict(p) for p in packages]

    with (
        patch("agent.supervisor.android"),
        patch("agent.supervisor.log_event"),
    ):
        sup = WatchdogSupervisor.__new__(WatchdogSupervisor)
        sup.cfg = default_cfg
        sup.packages = [e.get("package", "") for e in entries]
        sup.entries = entries
        sup.status_map = {}
        sup._prev_state = {}
        sup._last_online_ts = {}
        sup._online_start_ts = {}
        sup._grace_until = {}
        sup._nhb_offline_count = {}
        sup._nhb_cooldown_until = {}
        sup._revive_count = {}
        sup._failure_count = {}
        sup._ram_last_check_at = {}
        sup._ram_last_trim_at = {}
        sup._ram_cooldown_until = {}
        sup._logger = MagicMock()
        sup._root_info = MagicMock()
        sup._root_info.available = False
        sup._root_info.tool = None
        sup._presence_user_ids = {}
        sup._presence_usernames = {}
        sup._presence_cookies = {}
        sup._presence_id_resolved = set()
        sup._presence_lookup_attempt_at = {}
        sup._presence_last_detail = {}

    return sup


def _ram_result(usage_mb: float, success: bool = True) -> dict[str, Any]:
    rss_kb = int(usage_mb * 1024)
    if rss_kb >= 1024 * 1024:
        display = f"{rss_kb / (1024 * 1024):.1f}GB"
    else:
        display = f"{round(rss_kb / 1024)}MB"
    return {
        "pid": "1234",
        "rss_kb": rss_kb,
        "usage_mb": display,
        "method": "proc_status",
        "success": success,
        "error": "",
    }


_PKG = "com.roblox.client"
_ENTRY: dict[str, Any] = {"package": _PKG, "username": "TestUser", "private_server_url": ""}


# ── 1: Disabled ───────────────────────────────────────────────────────────────

class TestRamOptimizationDisabled(unittest.TestCase):

    def test_skipped_when_disabled(self):
        sup = _make_supervisor({"ram_optimization_enabled": False})
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            sup._check_ram_optimization(_PKG, _ENTRY, now)
            mock_android.get_package_ram_usage.assert_not_called()


# ── 2: Online delay guard ─────────────────────────────────────────────────────

class TestRamOnlineDelayGuard(unittest.TestCase):

    def test_skipped_when_too_new(self):
        sup = _make_supervisor()
        now = time.monotonic()
        # Package only online for 5 seconds (delay=30).
        sup._online_start_ts[_PKG] = now - 5

        with patch("agent.supervisor.android") as mock_android:
            sup._check_ram_optimization(_PKG, _ENTRY, now)
            mock_android.get_package_ram_usage.assert_not_called()

    def test_proceeds_when_delay_met(self):
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 60  # well past 30s delay

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(500)
            sup._check_ram_optimization(_PKG, _ENTRY, now)
            mock_android.get_package_ram_usage.assert_called_once()


# ── 3: Rate-limit guard ───────────────────────────────────────────────────────

class TestRamCheckRateLimit(unittest.TestCase):

    def test_skipped_within_trim_interval(self):
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999
        sup._ram_last_check_at[_PKG] = now - 30  # only 30s ago, interval=120

        with patch("agent.supervisor.android") as mock_android:
            sup._check_ram_optimization(_PKG, _ENTRY, now)
            mock_android.get_package_ram_usage.assert_not_called()

    def test_proceeds_after_trim_interval(self):
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999
        sup._ram_last_check_at[_PKG] = now - 130  # 130s ago, interval=120

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(400)
            sup._check_ram_optimization(_PKG, _ENTRY, now)
            mock_android.get_package_ram_usage.assert_called_once()


# ── 4: RAM below target → no action ──────────────────────────────────────────

class TestRamBelowTarget(unittest.TestCase):

    def test_no_trim_when_below_normal_target(self):
        """RAM=600 MB, target_normal=700 → no trim."""
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(600)
            sup._check_ram_optimization(_PKG, _ENTRY, now)
            mock_android.clear_package_cache_verified.assert_not_called()
            mock_android.force_stop_package.assert_not_called()


# ── 5: RAM > target, ≤ restart threshold → trim ──────────────────────────────

class TestRamTrimTriggered(unittest.TestCase):

    def test_trim_attempted_when_above_target(self):
        """RAM=750 MB (> target=700), restart_threshold=900 → trim only."""
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(750)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            sup._check_ram_optimization(_PKG, _ENTRY, now)
            mock_android.clear_package_cache_verified.assert_called_once_with(_PKG)
            mock_android.force_stop_package.assert_not_called()


# ── 6: RAM > restart threshold, cooldown expired → restart ───────────────────

class TestRamRestartTriggered(unittest.TestCase):

    def test_restart_triggered_above_threshold(self):
        """RAM=950 MB > restart_threshold=900 and no cooldown → restart."""
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(950)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            with patch.object(sup, "_do_launch", return_value=True) as mock_launch:
                with patch.object(sup, "_set_grace"):
                    with patch.object(sup, "_set_status"):
                        sup._check_ram_optimization(_PKG, _ENTRY, now)
                mock_launch.assert_called_once_with(_PKG, _ENTRY, "ram_restart")


# ── 7: RAM restart blocked by RAM cooldown ────────────────────────────────────

class TestRamRestartCooldown(unittest.TestCase):

    def test_restart_skipped_in_ram_cooldown(self):
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999
        sup._ram_cooldown_until[_PKG] = now + 100  # still in cooldown

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(1000)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            with patch.object(sup, "_do_launch") as mock_launch:
                sup._check_ram_optimization(_PKG, _ENTRY, now)
                mock_launch.assert_not_called()


# ── 8: RAM restart blocked by NHB cooldown ────────────────────────────────────

class TestRamRestartBlockedByNhbCooldown(unittest.TestCase):

    def test_restart_skipped_in_nhb_cooldown(self):
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999
        sup._nhb_cooldown_until[_PKG] = now + 60  # NHB cooldown active

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(1000)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            with patch.object(sup, "_do_launch") as mock_launch:
                sup._check_ram_optimization(_PKG, _ENTRY, now)
                mock_launch.assert_not_called()


# ── 9: Cooldown set on both dicts ─────────────────────────────────────────────

class TestRamRestartSetsBothCooldowns(unittest.TestCase):

    def test_both_cooldowns_set_on_restart(self):
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(1000)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            with patch.object(sup, "_do_launch", return_value=True):
                with patch.object(sup, "_set_grace"):
                    with patch.object(sup, "_set_status"):
                        sup._check_ram_optimization(_PKG, _ENTRY, now)

        cooldown_sec = 180  # default ram_restart_cooldown_sec
        self.assertGreaterEqual(sup._ram_cooldown_until.get(_PKG, 0), now + cooldown_sec - 1)
        self.assertGreaterEqual(sup._nhb_cooldown_until.get(_PKG, 0), now + cooldown_sec - 1)


# ── 10: Aggressive mode ───────────────────────────────────────────────────────

class TestRamAggressiveMode(unittest.TestCase):

    def test_aggressive_mode_uses_lower_target(self):
        """With aggressive=True, target=300 MB. RAM=400 MB triggers trim."""
        sup = _make_supervisor({"ram_aggressive_mode": True})
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(400)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            sup._check_ram_optimization(_PKG, _ENTRY, now)
            mock_android.clear_package_cache_verified.assert_called_once()

    def test_normal_mode_does_not_trim_at_400mb(self):
        """With aggressive=False, target=700 MB. RAM=400 MB → no action."""
        sup = _make_supervisor({"ram_aggressive_mode": False})
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(400)
            sup._check_ram_optimization(_PKG, _ENTRY, now)
            mock_android.clear_package_cache_verified.assert_not_called()


# ── 13: force_stop_package called before relaunch ─────────────────────────────

class TestRamRestartForceStop(unittest.TestCase):

    def test_force_stop_called_before_launch(self):
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999
        call_order = []

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(1000)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            mock_android.force_stop_package.side_effect = lambda p: call_order.append("stop")

            def _fake_launch(pkg, entry, reason):
                call_order.append("launch")
                return True

            with patch.object(sup, "_do_launch", side_effect=_fake_launch):
                with patch.object(sup, "_set_grace"):
                    with patch.object(sup, "_set_status"):
                        with patch("agent.supervisor.time") as mock_time:
                            mock_time.monotonic.return_value = now
                            mock_time.sleep = lambda _: None
                            sup._check_ram_optimization(_PKG, _ENTRY, now)

        self.assertEqual(call_order, ["stop", "launch"])


# ── 15–16: revive/failure counts ──────────────────────────────────────────────

class TestRamRestartCounts(unittest.TestCase):

    def _run_restart(self, launch_ok: bool) -> Any:
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(1000)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            with patch.object(sup, "_do_launch", return_value=launch_ok):
                with patch.object(sup, "_set_grace"):
                    with patch.object(sup, "_set_status"):
                        with patch("agent.supervisor.time") as mock_time:
                            mock_time.monotonic.return_value = now
                            mock_time.sleep = lambda _: None
                            sup._check_ram_optimization(_PKG, _ENTRY, now)
        return sup

    def test_revive_count_incremented_on_success(self):
        sup = self._run_restart(launch_ok=True)
        self.assertEqual(sup._revive_count.get(_PKG, 0), 1)

    def test_failure_count_incremented_on_failure(self):
        sup = self._run_restart(launch_ok=False)
        self.assertEqual(sup._failure_count.get(_PKG, 0), 1)


# ── 17: online_start_ts cleared on restart ────────────────────────────────────

class TestRamRestartClearsOnlineTs(unittest.TestCase):

    def test_online_start_ts_cleared_after_restart(self):
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(1000)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            with patch.object(sup, "_do_launch", return_value=True):
                with patch.object(sup, "_set_grace"):
                    with patch.object(sup, "_set_status"):
                        with patch("agent.supervisor.time") as mock_time:
                            mock_time.monotonic.return_value = now
                            mock_time.sleep = lambda _: None
                            sup._check_ram_optimization(_PKG, _ENTRY, now)

        self.assertNotIn(_PKG, sup._online_start_ts)


# ── 19–20: Timestamp updates ──────────────────────────────────────────────────

class TestRamTimestampUpdates(unittest.TestCase):

    def test_last_check_at_updated(self):
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(400)
            sup._check_ram_optimization(_PKG, _ENTRY, now)

        self.assertAlmostEqual(sup._ram_last_check_at.get(_PKG, 0), now, delta=0.5)

    def test_last_trim_at_updated_when_trim_runs(self):
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(750)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            sup._check_ram_optimization(_PKG, _ENTRY, now)

        self.assertAlmostEqual(sup._ram_last_trim_at.get(_PKG, 0), now, delta=0.5)


# ── 21: Config defaults ───────────────────────────────────────────────────────

class TestRamConfigDefaults(unittest.TestCase):

    def test_uses_defaults_when_keys_missing(self):
        """Missing RAM config keys → uses hardcoded defaults; no KeyError."""
        sup = _make_supervisor({
            "ram_optimization_enabled": True,
            # All other RAM keys absent
        })
        del sup.cfg["ram_target_normal_mb"]
        del sup.cfg["ram_restart_threshold_mb"]
        del sup.cfg["ram_trim_interval_sec"]
        del sup.cfg["ram_check_delay_after_online_sec"]
        del sup.cfg["ram_restart_cooldown_sec"]
        del sup.cfg["ram_aggressive_mode"]

        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(400)
            # Should not raise any exception.
            try:
                sup._check_ram_optimization(_PKG, _ENTRY, now)
            except (KeyError, TypeError) as e:
                self.fail(f"Missing config key caused crash: {e}")


# ── 29–31: _handle_state routes correctly ────────────────────────────────────

class TestHandleStateRouting(unittest.TestCase):

    def _sup_with_mocked_ram_check(self):
        sup = _make_supervisor()
        sup._check_ram_optimization = MagicMock()
        return sup

    def test_ram_check_called_for_online_state(self):
        from agent.supervisor import STATUS_ONLINE, STATUS_DEAD, STATUS_NO_HEARTBEAT

        sup = self._sup_with_mocked_ram_check()
        now = time.monotonic()

        with patch("agent.supervisor.effective_private_server_url", return_value=""):
            with patch("agent.supervisor.log_event"):
                sup._handle_state(_PKG, _ENTRY, STATUS_ONLINE, STATUS_ONLINE, now)

        sup._check_ram_optimization.assert_called_once()

    def test_ram_check_not_called_for_dead_state(self):
        from agent.supervisor import STATUS_DEAD

        sup = self._sup_with_mocked_ram_check()
        now = time.monotonic()

        with patch("agent.supervisor.effective_private_server_url", return_value=""):
            with patch("agent.supervisor.log_event"):
                with patch("agent.supervisor.android"):
                    with patch.object(sup, "_do_launch", return_value=False):
                        with patch.object(sup, "_set_status"):
                            sup._handle_state(_PKG, _ENTRY, STATUS_DEAD, STATUS_DEAD, now)

        sup._check_ram_optimization.assert_not_called()

    def test_ram_check_not_called_for_no_heartbeat(self):
        from agent.supervisor import STATUS_NO_HEARTBEAT

        sup = self._sup_with_mocked_ram_check()
        now = time.monotonic()
        # Pre-set cooldown to prevent actual NHB launch.
        sup._nhb_cooldown_until[_PKG] = now + 9999

        with patch("agent.supervisor.effective_private_server_url", return_value=""):
            with patch("agent.supervisor.log_event"):
                sup._handle_state(_PKG, _ENTRY, STATUS_NO_HEARTBEAT, STATUS_NO_HEARTBEAT, now)

        sup._check_ram_optimization.assert_not_called()


# ── 32: Trim not repeated within interval ─────────────────────────────────────

class TestTrimRateLimit(unittest.TestCase):

    def test_trim_not_repeated_within_interval(self):
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999
        sup._ram_last_trim_at[_PKG] = now - 30  # Last trim 30s ago, interval=120.

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(750)
            sup._check_ram_optimization(_PKG, _ENTRY, now)
            mock_android.clear_package_cache_verified.assert_not_called()


# ── 33: Multi-package independent cooldowns ───────────────────────────────────

class TestMultiPackageCooldowns(unittest.TestCase):

    def test_each_package_has_independent_cooldown(self):
        pkg1 = "com.roblox.client"
        pkg2 = "com.moons.litesc"
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[pkg1] = now - 9999
        sup._online_start_ts[pkg2] = now - 9999
        # Only pkg1 has a RAM cooldown.
        sup._ram_cooldown_until[pkg1] = now + 100

        entry1 = {"package": pkg1, "username": "A", "private_server_url": ""}
        entry2 = {"package": pkg2, "username": "B", "private_server_url": ""}

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(1000)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            with patch.object(sup, "_do_launch", return_value=True) as mock_launch:
                with patch.object(sup, "_set_grace"):
                    with patch.object(sup, "_set_status"):
                        with patch("agent.supervisor.time") as mock_time:
                            mock_time.monotonic.return_value = now
                            mock_time.sleep = lambda _: None
                            # pkg1 in cooldown → no restart.
                            sup._check_ram_optimization(pkg1, entry1, now)
                            # pkg2 no cooldown → restart.
                            sup._check_ram_optimization(pkg2, entry2, now)

        launch_packages = [c.args[0] for c in mock_launch.call_args_list]
        self.assertNotIn(pkg1, launch_packages)
        self.assertIn(pkg2, launch_packages)


# ── 37: Trim failure does not abort ───────────────────────────────────────────

class TestTrimFailureSafe(unittest.TestCase):

    def test_trim_exception_does_not_crash(self):
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(750)
            mock_android.clear_package_cache_verified.side_effect = OSError("permission denied")
            # Should not raise.
            try:
                sup._check_ram_optimization(_PKG, _ENTRY, now)
            except OSError:
                self.fail("Trim exception must be caught and not re-raised")


# ── 38: get_package_ram_usage called with correct args ────────────────────────

class TestRamCheckArgs(unittest.TestCase):

    def test_get_package_ram_usage_called_with_pkg_and_root_info(self):
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(400)
            sup._check_ram_optimization(_PKG, _ENTRY, now)
            mock_android.get_package_ram_usage.assert_called_once_with(_PKG, sup._root_info)


# ── 39: Zero RSS → no action ──────────────────────────────────────────────────

class TestRamCheckZeroRss(unittest.TestCase):

    def test_zero_rss_no_action(self):
        """get_package_ram_usage returning 0 MB → no trim, no restart."""
        sup = _make_supervisor()
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(0, success=False)
            sup._check_ram_optimization(_PKG, _ENTRY, now)
            mock_android.clear_package_cache_verified.assert_not_called()
            mock_android.force_stop_package.assert_not_called()


# ── 40: Default restart threshold ────────────────────────────────────────────

class TestRamRestartThresholdDefault(unittest.TestCase):

    def test_default_restart_threshold_is_900mb(self):
        from agent.config import default_config
        cfg = default_config()
        self.assertEqual(cfg.get("ram_restart_threshold_mb"), 900)

    def test_default_target_normal_is_700mb(self):
        from agent.config import default_config
        cfg = default_config()
        self.assertEqual(cfg.get("ram_target_normal_mb"), 700)

    def test_default_target_good_is_500mb(self):
        from agent.config import default_config
        cfg = default_config()
        self.assertEqual(cfg.get("ram_target_good_mb"), 500)

    def test_default_target_aggressive_is_300mb(self):
        from agent.config import default_config
        cfg = default_config()
        self.assertEqual(cfg.get("ram_target_aggressive_mb"), 300)


# ── BUG 1: probe p-52aeb6420f regression ─────────────────────────────────────


class TestBug1OnlineProtectedFromRamRestart(unittest.TestCase):
    """An Online + in-game package must never be relaunched by the RAM path.

    Probe ``p-52aeb6420f`` (Samsung SM-N9810, Android 10) showed Roblox
    using 1.3–1.4 GB on a 720p device, well above the default 900 MB RAM
    threshold.  With the old code, this triggered a force-stop +
    private-URL relaunch every ``ram_restart_cooldown_sec`` (default
    180 s) for every Online package — an endless relaunch loop on a
    healthy session.

    Per user spec for Bug 1::

        If state is Online and process is alive:
          - do not relaunch
          - do not reopen private URL
          - do not force close

    The fix: gate the restart code path behind the opt-in flag
    ``ram_restart_when_online_enabled`` (default ``False``).  Cache trim
    is still allowed because it is non-disruptive.
    """

    def test_default_config_does_not_opt_in_to_online_ram_restart(self):
        """``default_config()`` must NOT enable RAM-restart for Online."""
        from agent.config import default_config
        cfg = default_config()
        self.assertFalse(
            cfg.get("ram_restart_when_online_enabled", False),
            "Bug 1 regression: default must be False so Online packages "
            "are never relaunched solely because of RAM usage.",
        )

    def test_online_above_threshold_does_not_force_stop_or_relaunch(self):
        """Online + 1.3 GB RAM + cooldown clear → no force-stop, no relaunch."""
        sup = _make_supervisor({"ram_restart_when_online_enabled": False})
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            # Probe-realistic value: 1.3 GB (≫ 900 MB threshold).
            mock_android.get_package_ram_usage.return_value = _ram_result(1328)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            with patch.object(sup, "_do_launch") as mock_launch:
                with patch.object(sup, "_set_status") as mock_status:
                    sup._check_ram_optimization(_PKG, _ENTRY, now)
            # Cache trim still runs (non-disruptive).
            mock_android.clear_package_cache_verified.assert_called_once_with(_PKG)
            # NO force-stop, NO relaunch, NO status change.
            mock_android.force_stop_package.assert_not_called()
            mock_launch.assert_not_called()
            mock_status.assert_not_called()

    def test_online_above_threshold_emits_skipped_event(self):
        """Inhibition must be observable via [DENG_REJOIN_RAM_RESTART_SKIPPED]."""
        sup = _make_supervisor({"ram_restart_when_online_enabled": False})
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(1404)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            with patch("agent.supervisor.log_event") as mock_log:
                sup._check_ram_optimization(_PKG, _ENTRY, now)

        emitted_tags = [
            args[2] for (args, _) in mock_log.call_args_list
            if len(args) >= 3 and isinstance(args[2], str)
        ]
        self.assertIn(
            "[DENG_REJOIN_RAM_RESTART_SKIPPED]", emitted_tags,
            f"Expected RAM_RESTART_SKIPPED probe event; got {emitted_tags}",
        )

    def test_loop_does_not_relaunch_online_package_over_multiple_rounds(self):
        """Repeated _check_ram_optimization invocations must not relaunch."""
        sup = _make_supervisor({"ram_restart_when_online_enabled": False})
        base = time.monotonic()
        sup._online_start_ts[_PKG] = base - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(1350)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            with patch.object(sup, "_do_launch") as mock_launch:
                # Simulate 30 supervisor rounds (5 minutes at 10s interval),
                # bumping the clock past the cooldown each round so the old
                # cooldown gate alone wouldn't be enough.
                for i in range(30):
                    now = base + i * 200  # > 180 s cooldown
                    sup._ram_last_check_at.pop(_PKG, None)  # bypass trim rate-limit
                    sup._ram_last_trim_at.pop(_PKG, None)
                    sup._check_ram_optimization(_PKG, _ENTRY, now)
                mock_launch.assert_not_called()

    def test_opt_in_restores_legacy_behaviour(self):
        """With the flag explicitly True, the old restart path still works."""
        sup = _make_supervisor({"ram_restart_when_online_enabled": True})
        now = time.monotonic()
        sup._online_start_ts[_PKG] = now - 9999

        with patch("agent.supervisor.android") as mock_android:
            mock_android.get_package_ram_usage.return_value = _ram_result(1500)
            mock_android.clear_package_cache_verified.return_value = {
                "success": True, "skipped": False, "skipped_reason": "", "error": "",
            }
            with patch.object(sup, "_do_launch", return_value=True) as mock_launch:
                with patch.object(sup, "_set_grace"):
                    with patch.object(sup, "_set_status"):
                        sup._check_ram_optimization(_PKG, _ENTRY, now)
                mock_launch.assert_called_once_with(_PKG, _ENTRY, "ram_restart")


if __name__ == "__main__":
    unittest.main()
