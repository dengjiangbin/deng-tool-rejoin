"""Per-package CPU must be real, and device CPU must be a sane 0-100%.

Before: the monitor parsed a single all-cores ``top`` value (e.g. 327% on an
8-core phone) and copied it into every package row, so all packages showed the
same nonsensical CPU.  Now CPU is sampled from /proc deltas as a share of total
capacity (0-100%), per package, with Σ per-package ≈ device CPU.
"""

from __future__ import annotations

import re
import unittest
from unittest import mock

from agent import android_cpu as ac
from agent import webhook as w


class CpuSamplerMathTests(unittest.TestCase):
    def test_device_and_per_package_share_of_capacity(self) -> None:
        # stat: t0 total/idle, t1 total/idle  → dtotal=1600, didle=1000 → 37.5%
        stat_side = [(9500, 8000), (11100, 9000)]
        # per-package jiffies: t0=150, t1=450 → dproc=300 → 300/1600 = 18.8%
        proc_side = [150, 450]
        fake_root = type("R", (), {"available": True, "tool": "su"})()
        with mock.patch.object(ac, "_read_proc_stat_total", side_effect=stat_side), \
             mock.patch.object(ac, "_sum_pid_jiffies", side_effect=proc_side), \
             mock.patch("agent.android_memory.get_package_pids", return_value=["100"]), \
             mock.patch("agent.android.detect_root", return_value=fake_root), \
             mock.patch.object(ac.time, "sleep", lambda *_: None):
            out = ac.collect_cpu_usage(["com.roblox.client"], sample_seconds=0)
        self.assertEqual(out["device_pct"], 37.5)
        self.assertEqual(out["per_package_pct"]["com.roblox.client"], 18.8)
        # Per-package share never exceeds device busy share.
        self.assertLessEqual(out["per_package_pct"]["com.roblox.client"], out["device_pct"])

    def test_device_pct_capped_0_100(self) -> None:
        with mock.patch.object(ac, "_read_proc_stat_total", side_effect=[(1000, 100), (2000, 100)]), \
             mock.patch("agent.android_memory.get_package_pids", return_value=[]), \
             mock.patch("agent.android.detect_root", return_value=None), \
             mock.patch.object(ac.time, "sleep", lambda *_: None):
            out = ac.collect_cpu_usage([], sample_seconds=0)
        self.assertIsNotNone(out["device_pct"])
        self.assertGreaterEqual(out["device_pct"], 0.0)
        self.assertLessEqual(out["device_pct"], 100.0)

    def test_unavailable_proc_stat_returns_none(self) -> None:
        with mock.patch.object(ac, "_read_proc_stat_total", return_value=None), \
             mock.patch("agent.android_memory.get_package_pids", return_value=["1"]), \
             mock.patch("agent.android.detect_root", return_value=None), \
             mock.patch.object(ac.time, "sleep", lambda *_: None):
            out = ac.collect_cpu_usage(["com.x"], sample_seconds=0)
        self.assertIsNone(out["device_pct"])
        self.assertEqual(out["per_package_pct"], {})


class PidStatParseTests(unittest.TestCase):
    def test_comm_with_spaces_and_parens_is_handled(self) -> None:
        # utime=field14, stime=field15. Build a stat line with a tricky comm.
        rest = ["S"] + ["0"] * 10 + ["100", "50"] + ["0"] * 8  # rest[11]=100, rest[12]=50
        line = "1234 (weird )name) " + " ".join(rest)
        with mock.patch.object(ac.Path, "read_text", lambda self, *a, **k: line):
            val = ac._read_pid_cpu_jiffies("1234")
        self.assertEqual(val, 150)


class EmbedCpuTests(unittest.TestCase):
    def _build(self, per_cpu, device_cpu):
        packages = ["com.roblox.a", "com.roblox.b", "com.roblox.c"]
        config = {
            "roblox_packages": [{"package": p} for p in packages],
            "license_key": "DENG-ABAC-1234-5678-F2C3",
            "webhook_mode": "edit",
            "_mem_info": {"total_mb": 4096, "free_mb": 2048, "percent_free": 50},
            "_cpu_pct": device_cpu,
            "_temp_c": 60.0,
        }
        snapshot = [{"package": p, "status": "Online", "ram_mb": "300 MB", "pss_mb": 300} for p in packages]
        app_stats = {p: {"online": True, "memory_mb": "300 MB", "cpu_pct": per_cpu.get(p)} for p in packages}
        payload = w.build_status_embed_payload(
            config, event="monitor", app_stats=app_stats, supervisor_snapshot=snapshot
        )
        fields = payload["embeds"][0]["fields"]
        detail = next(f["value"] for f in fields if f["name"] == "Application Details")
        sysv = next(f["value"] for f in fields if f["name"] == "🖥️ System Stats")
        return detail, sysv

    def test_per_package_cpu_is_distinct_not_identical(self) -> None:
        detail, sysv = self._build(
            {"com.roblox.a": 12.5, "com.roblox.b": 8.0, "com.roblox.c": 20.1},
            device_cpu=41.0,
        )
        cpus = [float(x) for x in re.findall(r"⚡ ([\d.]+)%", detail)]
        self.assertEqual(sorted(cpus), [8.0, 12.5, 20.1])
        # Device CPU shown as a sane 0-100% value, not 327%.
        self.assertIn("CPU: 41%", sysv)

    def test_missing_per_package_cpu_omits_line(self) -> None:
        detail, _ = self._build(
            {"com.roblox.a": 10.0, "com.roblox.b": None, "com.roblox.c": None},
            device_cpu=10.0,
        )
        cpus = re.findall(r"⚡ ([\d.]+)%", detail)
        self.assertEqual(cpus, ["10.0"])  # only the one with a real value


if __name__ == "__main__":
    unittest.main()
