"""Tests for the upload-budget trim logic added after user feedback:

  "I CANT UPLOAD PROBE AFTER TESTING SO MANY THINGS BACK TO BACK FOR YOU
  THIS VERSION, WHY?"

After we added the dumpsys/logcat captures the probe payload regularly
exceeded the server's 4 MB compressed cap and uploads failed with an
opaque 413.  ``trim_probe_for_upload`` drops the highest-cost,
lowest-signal fields first and falls back to truncating every string
when the budget still can't be met.
"""

from __future__ import annotations

import gzip
import json
import unittest

from agent.probe import (
    _PROBE_DROP_ORDER,
    _UPLOAD_HARD_CAP_BYTES,
    _UPLOAD_SOFT_BUDGET_BYTES,
    _drop_field,
    trim_probe_for_upload,
)


class DropFieldTest(unittest.TestCase):

    def test_drops_top_level_key(self) -> None:
        d = {"logcat": "x" * 500, "device": {"model": "S"}}
        self.assertTrue(_drop_field(d, "logcat"))
        self.assertEqual(d["logcat"], "<dropped: payload size budget>")
        # untouched siblings stay
        self.assertEqual(d["device"], {"model": "S"})

    def test_drops_nested_key(self) -> None:
        d = {"dumpsys_global": {"window_windows_full": "AAA",
                                 "activity_recents_full": "BBB"}}
        self.assertTrue(_drop_field(d, "dumpsys_global.window_windows_full"))
        self.assertEqual(d["dumpsys_global"]["window_windows_full"],
                         "<dropped: payload size budget>")
        self.assertEqual(d["dumpsys_global"]["activity_recents_full"], "BBB")

    def test_returns_false_when_path_missing(self) -> None:
        d = {"a": {"b": "c"}}
        self.assertFalse(_drop_field(d, "a.b.c"))   # too deep
        self.assertFalse(_drop_field(d, "z"))       # absent root


class TrimProbeForUploadTest(unittest.TestCase):

    def _build_oversized_probe(self) -> dict:
        # gzip is very effective on repetitive text — to push a probe
        # past the soft budget we need INCOMPRESSIBLE bytes.  Use
        # ``os.urandom`` and base64-encode so the result is still valid
        # JSON.  ~6 MB of base64 random data compresses to ~6 MB.
        import base64
        import os as _os

        def _filler(n_bytes: int) -> str:
            return base64.b64encode(_os.urandom(n_bytes)).decode("ascii")

        return {
            "probe_version": 1,
            "logcat": _filler(800_000),
            "logs": {"agent": _filler(400_000)},
            "dumpsys_global": {
                "window_windows_full":      _filler(800_000),
                "activity_activities_full": _filler(800_000),
                "activity_recents_full":    _filler(500_000),
                "surfaceflinger_full":      _filler(500_000),
                "activity_top":             _filler(300_000),
                "activity_starter":         _filler(200_000),
            },
            "third_party_evidence": {
                "pm_dump":      {f"com.app{i}": _filler(50_000) for i in range(8)},
                "shared_prefs": {f"com.app{i}": _filler(50_000) for i in range(8)},
            },
            "installed_packages": {
                "pm_list_third_party_raw": _filler(200_000),
            },
            "small_keep_me":  "this is short and should survive",
        }

    def test_small_probe_passes_through_untouched(self) -> None:
        probe = {"probe_version": 1, "logcat": "ok", "device": {"x": 1}}
        trimmed, report = trim_probe_for_upload(probe)
        self.assertEqual(report["dropped"], [])
        # Round-tripped — every non-meta key still present.
        self.assertEqual(trimmed["logcat"], "ok")
        self.assertEqual(trimmed["device"], {"x": 1})

    def test_oversized_probe_is_trimmed_under_hard_cap(self) -> None:
        probe = self._build_oversized_probe()
        trimmed, report = trim_probe_for_upload(probe)

        # Final payload must fit under the hard cap with margin.
        gz = gzip.compress(json.dumps(trimmed, separators=(",", ":")).encode())
        self.assertLessEqual(len(gz), _UPLOAD_HARD_CAP_BYTES,
                             f"trimmed gzip size {len(gz)} > hard cap")

        # Drops were applied in priority order — at least logcat is dropped.
        self.assertGreater(len(report["dropped"]), 0)
        self.assertEqual(report["dropped"][0], "logcat")

        # Surviving section we asked to keep stays intact.
        self.assertEqual(trimmed["small_keep_me"],
                         "this is short and should survive")

    def test_drop_order_matches_constant(self) -> None:
        # Each entry in the constant must reference an existing field
        # under one of the known top-level capture keys.  This guards
        # against typos that would make trim_probe_for_upload silently
        # no-op a key it can never drop.
        valid_top_level = {
            "logcat", "logs", "dumpsys_global", "third_party_evidence",
            "installed_packages",
        }
        for dotted in _PROBE_DROP_ORDER:
            head = dotted.split(".", 1)[0]
            self.assertIn(head, valid_top_level,
                          f"drop-order entry {dotted!r} references "
                          f"unknown top-level key {head!r}")

    def test_marker_field_recorded_on_trimmed_probe(self) -> None:
        probe = self._build_oversized_probe()
        trimmed, _ = trim_probe_for_upload(probe)
        self.assertIn("_upload_trim", trimmed)
        self.assertGreater(trimmed["_upload_trim"]["final_gzipped_bytes"], 0)
        self.assertEqual(trimmed["_upload_trim"]["hard_cap"],
                         _UPLOAD_HARD_CAP_BYTES)
        self.assertEqual(trimmed["_upload_trim"]["soft_budget"],
                         _UPLOAD_SOFT_BUDGET_BYTES)


class CollectProbeRespectsDiagFlagTest(unittest.TestCase):

    def test_diag_startup_is_skipped_by_default(self) -> None:
        # Avoid hitting Android — patch every collector to a no-op.
        import os
        from unittest import mock
        from agent import probe as _p

        # Force the "off" default explicitly so any test runner with
        # the env var set doesn't surprise us.
        with mock.patch.dict(os.environ, {"DENG_PROBE_DIAG_STARTUP": ""},
                              clear=False), \
             mock.patch.object(_p, "_capture_diag_startup",
                               side_effect=AssertionError(
                                   "diag-startup must NOT run by default",
                               )), \
             mock.patch.object(_p, "_capture_build_info",     return_value={}), \
             mock.patch.object(_p, "_capture_device",         return_value={}), \
             mock.patch.object(_p, "_capture_screen",         return_value={}), \
             mock.patch.object(_p, "_capture_settings",       return_value={}), \
             mock.patch.object(_p, "_capture_command_help",   return_value={}), \
             mock.patch.object(_p, "_capture_config",         return_value={}), \
             mock.patch.object(_p, "_capture_log_tail",       return_value=""), \
             mock.patch.object(_p, "_capture_all_logs",       return_value={}), \
             mock.patch.object(_p, "_capture_installed_build", return_value={}), \
             mock.patch.object(_p, "_capture_wrapper_script", return_value={}), \
             mock.patch.object(_p, "_capture_last_diagnostics", return_value={}), \
             mock.patch.object(_p, "_capture_installed_packages",
                               return_value={"third_party_tools": [],
                                              "clone_packages": []}), \
             mock.patch.object(_p, "_capture_kaeru_evidence",
                               return_value={"targets": []}), \
             mock.patch.object(_p, "_capture_appops",         return_value={}), \
             mock.patch.object(_p, "_capture_termux_prefs",   return_value={}), \
             mock.patch.object(_p, "_capture_getprop",        return_value={}), \
             mock.patch.object(_p, "_capture_dumpsys_global", return_value={}), \
             mock.patch.object(_p, "_capture_logcat",         return_value=""):
            data = _p.collect_probe()

        self.assertIn("diag_startup", data)
        self.assertTrue(data["diag_startup"].get("skipped"),
                        f"diag_startup should be marked skipped, got: "
                        f"{data['diag_startup']!r}")


if __name__ == "__main__":
    unittest.main()
