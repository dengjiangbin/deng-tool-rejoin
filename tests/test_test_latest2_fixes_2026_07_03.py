"""test/latest2 fixes: banner, detection CLI dispatch, overlay deps."""

from __future__ import annotations

import sys
import time
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.banner import banner_text, visible_width  # noqa: E402
from agent.lime_cli_dispatch import try_dispatch_lime_argv  # noqa: E402


class BannerTests(unittest.TestCase):
    def test_banner_includes_mons_and_version(self) -> None:
        text = banner_text(use_color=False, version="test-latest2")
        self.assertIn("MONS", text)
        self.assertIn("Tool: Rejoin test-latest2", text)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 7)
        self.assertGreater(max(visible_width(line) for line in lines), 20)

    def test_banner_has_no_double_colon_typo(self) -> None:
        text = banner_text(use_color=False, version="test-latest2")
        self.assertNotIn("::", text)


class LimeCliDispatchTests(unittest.TestCase):
    def test_non_detection_argv_not_handled(self) -> None:
        self.assertIsNone(try_dispatch_lime_argv(["menu"]))

    def test_detection_speed_test_runs_without_menu(self) -> None:
        with patch("agent.detection_speed_test.run_speed_test_cli", return_value=0) as run:
            rc = try_dispatch_lime_argv(
                [
                    "detection",
                    "speed-test",
                    "--upload-probe",
                    "--scenario",
                    "force-close",
                    "--package",
                    "com.moons.litesc",
                ]
            )
        self.assertEqual(rc, 0)
        run.assert_called_once_with(
            package="com.moons.litesc",
            scenario="force-close",
            upload_probe=True,
        )

    def test_unknown_detection_subcommand_returns_error(self) -> None:
        buf = StringIO()
        with patch("sys.stdout", buf):
            rc = try_dispatch_lime_argv(["detection", "unknown-sub"])
        self.assertEqual(rc, 2)
        self.assertIn("Unknown detection subcommand", buf.getvalue())


class OverlayDepCheckerTests(unittest.TestCase):
    def test_package_online_evidence_in_overlay_list(self) -> None:
        import subprocess

        proc = subprocess.run(
            [sys.executable, str(PROJECT / "scripts" / "_check_lime_overlay_deps.py")],
            cwd=str(PROJECT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


class ProtectedEntrypointTests(unittest.TestCase):
    def test_artifact_entrypoint_includes_lime_dispatch(self) -> None:
        from agent.internal_test_artifact import _RAW_RUNTIME_FILES

        src = _RAW_RUNTIME_FILES["agent/deng_tool_rejoin.py"]
        self.assertIn("lime_cli_dispatch", src)
        self.assertIn("try_dispatch_lime_argv", src)
        self.assertIn("_protected_runtime", src)


class RuntimePatchTests(unittest.TestCase):
    def test_recovery_gate_patch_only_on_test_latest2(self) -> None:
        from agent import supervisor as sup
        from agent import test_latest2_runtime_patch as patch_mod

        patch_mod._PATCHED = False
        with patch("agent.lime_channel.lime_detection_enabled", return_value=True):
            patch_mod.apply_test_latest2_runtime_patches()
            self.assertTrue(
                getattr(sup.WatchdogSupervisor, "_test_latest2_recovery_gate_patched", False)
            )
            self.assertTrue(
                getattr(sup.WatchdogSupervisor, "_test_latest2_monitoring_relay_patched", False)
            )

    def test_stagger_safety_wraps_supervisor_methods(self) -> None:
        from agent import supervisor as sup
        from agent.test_latest2_runtime_patch import apply_test_latest2_runtime_patches

        with patch("agent.lime_channel.lime_detection_enabled", return_value=True):
            apply_test_latest2_runtime_patches()
            sup_cls = sup.WatchdogSupervisor
            self.assertTrue(getattr(sup_cls, "_test_latest2_stagger_safety_patched", False))
            self.assertTrue(getattr(sup_cls, "_test_latest2_stagger_interval_patched", False))
            self.assertEqual(sup_cls.LAUNCH_STAGGER_SECONDS, 15)
            self.assertIsNot(sup_cls._handle_state, sup_cls._test_latest2_orig_handle_state)
            self.assertIsNot(sup_cls._detect_package_state, sup_cls._test_latest2_orig_detect)
            self.assertIsNot(sup_cls.mark_package_launched, sup_cls._test_latest2_orig_mark_launched)


class ProcessMissingEligibleTests(unittest.TestCase):
    def test_first_launch_process_flicker_not_dead_eligible(self) -> None:
        from agent.rjn_lifecycle_monitor import (
            PackageRjnState,
            _process_missing_dead_eligible,
        )

        row = PackageRjnState(package="com.moons.litesc")
        row.watchdog_active = True
        row.process_seen_since_launch = True
        row.launch_started_at = time.time() - 2.0
        self.assertFalse(_process_missing_dead_eligible("com.moons.litesc", row))


class ProbeLandscapeReadonlyTests(unittest.TestCase):
    def test_landscape_probe_skips_rotation_without_apply_correction(self) -> None:
        import inspect

        from agent import probe as probe_mod

        errors: list[dict[str, str]] = []

        def _enforce(*, phase="before_start", screen_mode_config="landscape"):
            raise AssertionError("probe must not call enforce_landscape on v1.3.0 android")

        v130_sig = inspect.signature(
            lambda *, phase="before_start", screen_mode_config="landscape": None
        )

        with patch("agent.android.enforce_landscape_home_state", _enforce):
            with patch("agent.android.get_display_orientation_state", return_value={"orientation": "landscape"}):
                with patch("agent.android.get_wm_size", return_value={"width": 2400, "height": 1080}):
                    with patch("agent.android.get_wm_density", return_value=420):
                        with patch("agent.android.get_rotation_settings", return_value={"user_rotation": 1}):
                            with patch("inspect.signature", return_value=v130_sig):
                                out = probe_mod._capture_landscape_debug_state(errors)
        state = out.get("[DENG_REJOIN_LANDSCAPE_STATE]", {})
        self.assertTrue(state.get("skipped_rotation"))
        self.assertEqual(errors, [])


class LauncherBootstrapTests(unittest.TestCase):
    def test_launcher_import_applies_runtime_patches_on_test_latest2(self) -> None:
        import importlib

        import agent.launcher as launcher_mod
        import agent.test_latest2_runtime_patch as rtp

        rtp._PATCHED = False
        setattr(launcher_mod, "_test_latest2_lime_bypass_patched", False)
        with patch("agent.lime_channel.lime_detection_enabled", return_value=True):
            importlib.reload(launcher_mod)
            from agent import supervisor as sup

            self.assertTrue(rtp._PATCHED)
            self.assertEqual(sup.WatchdogSupervisor.LAUNCH_STAGGER_SECONDS, 15)


if __name__ == "__main__":
    unittest.main()
