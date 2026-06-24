"""Current Android dumpsys evidence parser regressions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android

PKG = "com.moons.litesc"


def _result(text: str):
    return android.CommandResult(("dumpsys",), 0, text, "")


class CurrentAndroidEvidenceTests(unittest.TestCase):
    def _evidence(self, processes: str = "", activities: str = "", windows: str = ""):
        def run(args, **_kwargs):
            if args[-1] == "processes":
                return _result(processes)
            if args[-1] == "activities":
                return _result(activities)
            return _result(windows)
        with patch("agent.android.run_android_command", side_effect=run):
            return android.get_current_android_package_evidence(PKG)

    def test_process_only_is_not_alive(self) -> None:
        ev = self._evidence(processes=f"ProcessRecord{{abc 4242:{PKG}/u0a104}}\n  pid=4242")
        self.assertTrue(ev["process"])
        self.assertFalse(ev["strict_alive"])

    def test_live_activity_attached_to_process_is_alive(self) -> None:
        text = (
            f"ActivityRecord{{abc u0 {PKG}/.Main t42}}\n"
            f"  packageName={PKG} state=PAUSED app=ProcessRecord{{abc 4242:{PKG}/u0a104}}\n"
        )
        process = f"ProcessRecord{{abc 4242:{PKG}/u0a104}}\n  pid=4242\n"
        ev = self._evidence(processes=process, activities=text)
        self.assertTrue(ev["activity"])
        self.assertTrue(ev["strict_alive"])

    def test_live_window_requires_surface_alive_and_ready(self) -> None:
        text = (
            f"Window{{abc u0 {PKG}/.Main}}\n"
            "  mHasSurface=true mAppDied=false isReadyForDisplay()=true\n"
        )
        process = f"ProcessRecord{{abc 4242:{PKG}/u0a104}}\n  pid=4242\n"
        ev = self._evidence(processes=process, windows=text)
        self.assertTrue(ev["window"])
        self.assertTrue(ev["strict_alive"])

    def test_stale_task_surface_and_appops_are_not_alive(self) -> None:
        stale_activity = f"TaskRecord{{abc A={PKG}}}\n  Activities=[]\n"
        stale_surface = f"Surface(name={PKG})/@0x123\n"
        ev = self._evidence(activities=stale_activity, windows=stale_surface)
        self.assertFalse(ev["strict_alive"])

    def test_bad_process_and_partial_package_do_not_match(self) -> None:
        bad = f"Bad Processes:\nProcessRecord{{abc 4242:{PKG}/u0a104}}\n"
        partial = "ProcessRecord{abc 4242:com.moons.litescore/u0a104}\n"
        self.assertFalse(self._evidence(processes=bad)["strict_alive"])
        self.assertFalse(self._evidence(processes=partial)["strict_alive"])


if __name__ == "__main__":
    unittest.main()
