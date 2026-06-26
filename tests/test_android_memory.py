"""Tests for PSS-based Android memory reporting (agent.android_memory)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.android_memory import (
    _INCREMENTAL_PATH,
    build_ram_report_text,
    collect_package_memory,
    parse_dumpsys_meminfo,
    parse_proc_meminfo,
    parse_smaps_rollup,
    record_incremental_sample,
    record_launch_baseline,
)


class ParseDumpsysMeminfoTests(unittest.TestCase):
    def test_total_pss_line(self) -> None:
        text = "           TOTAL PSS:   358400\n"
        parsed = parse_dumpsys_meminfo(text)
        self.assertEqual(parsed["pss_kb"], 358400)
        self.assertEqual(parsed["source"], "dumpsys_total_pss_line")

    def test_total_row_with_private_and_swap(self) -> None:
        text = (
            "                 Pss  Private  Private  SwapPss\n"
            "               TOTAL   350000   250000    50000    10000\n"
        )
        parsed = parse_dumpsys_meminfo(text)
        self.assertEqual(parsed["pss_kb"], 350000)
        self.assertEqual(parsed["private_dirty_kb"], 250000)
        self.assertEqual(parsed["swap_pss_kb"], 10000)

    def test_rss_1000_mb_pss_350_mb_scenario(self) -> None:
        """RSS inflation must not be mistaken for PSS."""
        text = (
            "               TOTAL  1024000   300000    50000    5000\n"
            "           TOTAL PSS:   358400\n"
            "           TOTAL RSS:  1024000\n"
        )
        parsed = parse_dumpsys_meminfo(text)
        self.assertEqual(parsed["pss_kb"], 358400)
        self.assertEqual(parsed.get("rss_kb"), 1024000)


class ParseSmapsRollupTests(unittest.TestCase):
    def test_reads_pss_and_private(self) -> None:
        text = (
            "Rss:               1024000 kB\n"
            "Pss:                358400 kB\n"
            "Private_Clean:      100000 kB\n"
            "Private_Dirty:      250000 kB\n"
            "SwapPss:             50000 kB\n"
        )
        parsed = parse_smaps_rollup(text)
        self.assertEqual(parsed["rss_kb"], 1024000)
        self.assertEqual(parsed["pss_kb"], 358400)
        self.assertEqual(parsed["private_dirty_kb"], 250000)
        self.assertEqual(parsed["uss_kb"], 350000)
        self.assertEqual(parsed["swap_pss_kb"], 50000)


class ParseProcMeminfoTests(unittest.TestCase):
    def test_four_gb_device(self) -> None:
        text = (
            "MemTotal:        4194304 kB\n"
            "MemAvailable:    1048576 kB\n"
            "SwapTotal:       2097152 kB\n"
            "SwapFree:        1048576 kB\n"
            "Zswap:           524288 kB\n"
        )
        parsed = parse_proc_meminfo(text)
        self.assertTrue(parsed["parse_ok"])
        self.assertEqual(parsed["total_mb"], 4096)
        self.assertEqual(parsed["mem_available_mb"], 1024)
        self.assertEqual(parsed["swap_total_mb"], 2048)
        self.assertEqual(parsed["swap_used_mb"], 1024)
        self.assertEqual(parsed["zswap_mb"], 512)


class CollectPackageMemoryTests(unittest.TestCase):
    def test_rss_inflated_note_when_pss_lower(self) -> None:
        smaps = {
            "pss_kb": 358400,
            "rss_kb": 1024000,
            "private_dirty_kb": 250000,
            "uss_kb": 350000,
            "swap_pss_kb": 5000,
            "source": "smaps_rollup",
        }
        root = type("RI", (), {"available": True, "tool": "su"})()

        with patch("agent.android_memory.get_package_pids", return_value=["4242"]), \
             patch("agent.android_memory._read_smaps_rollup", return_value=smaps), \
             patch("agent.android_memory._detect_process_status", return_value="background"):
            result = collect_package_memory("com.test.pkg", root)

        self.assertTrue(result["success"])
        self.assertEqual(result["pss_kb"], 358400)
        self.assertEqual(result["rss_kb"], 1024000)
        self.assertTrue(any("inflated/shared RSS" in n for n in result["notes"]))

    def test_multiple_pids_summed(self) -> None:
        root = type("RI", (), {"available": True, "tool": "su"})()
        pid_metrics = [
            {"pss_kb": 200000, "rss_kb": 400000, "method": "proc_smaps_rollup"},
            {"pss_kb": 150000, "rss_kb": 300000, "method": "proc_smaps_rollup"},
        ]

        def fake_collect(pid: str, root_info=None):
            idx = int(pid) - 1
            return dict(pid_metrics[idx])

        with patch("agent.android_memory.get_package_pids", return_value=["1", "2"]), \
             patch("agent.android_memory.collect_pid_memory", side_effect=fake_collect), \
             patch("agent.android_memory._detect_process_status", return_value="foreground"):
            result = collect_package_memory("com.test.multi", root)

        self.assertEqual(result["pss_kb"], 350000)
        self.assertEqual(result["pids"], ["1", "2"])

    def test_smaps_unavailable_uses_dumpsys_fallback(self) -> None:
        root = type("RI", (), {"available": True, "tool": "su"})()
        dumpsys = {
            "pss_kb": 256000,
            "source": "dumpsys_total_pss_line",
            "method": "dumpsys_meminfo_pid",
        }

        with patch("agent.android_memory.get_package_pids", return_value=["99"]), \
             patch("agent.android_memory._read_smaps_rollup", return_value={"source": "unavailable"}), \
             patch("agent.android_memory._read_dumpsys_meminfo", return_value=dumpsys), \
             patch("agent.android_memory._detect_process_status", return_value="background"):
            result = collect_package_memory("com.test.fallback", root)

        self.assertTrue(result["success"])
        self.assertEqual(result["pss_kb"], 256000)
        self.assertIn("dumpsys", result["method"])


class BuildRamReportTests(unittest.TestCase):
    def test_nine_packages_on_four_gb_not_impossible(self) -> None:
        packages = [f"com.test.pkg{i}" for i in range(9)]
        meminfo = (
            "MemTotal:        4194304 kB\n"
            "MemAvailable:     524288 kB\n"
            "SwapTotal:       2097152 kB\n"
            "SwapFree:         524288 kB\n"
        )

        def fake_collect(pkg: str, root_info=None):
            return {
                "package": pkg,
                "pids": ["100"],
                "status": "background",
                "pss_kb": 358400,
                "rss_kb": 1024000,
                "private_dirty_kb": 250000,
                "uss_kb": 300000,
                "swap_pss_kb": 10000,
                "usage_mb": "350 MB",
                "method": "proc_smaps_rollup",
                "success": True,
                "error": "",
                "notes": ["1024 MB is inflated/shared RSS, not real private RAM."],
            }

        with patch("agent.android_memory.collect_device_memory") as mock_dev, \
             patch("agent.android_memory.collect_package_memory", side_effect=fake_collect), \
             patch("agent.android_memory.get_incremental_samples", return_value=[280000]):
            mock_dev.return_value = parse_proc_meminfo(meminfo)
            text = build_ram_report_text(packages)

        self.assertIn("4096 MB", text)
        self.assertIn("9 active package", text)
        self.assertIn("PSS", text)
        self.assertIn("do NOT prove this device is impossible", text)
        self.assertNotIn("minimum 1000 MB", text.lower())

    def test_zram_active_mentioned(self) -> None:
        device = parse_proc_meminfo(
            "MemTotal: 4194304 kB\nMemAvailable: 1048576 kB\n"
            "SwapTotal: 2097152 kB\nSwapFree: 524288 kB\n"
        )
        device["zram_used_mb"] = 512
        device["zram_total_mb"] = 2048

        with patch("agent.android_memory.collect_device_memory", return_value=device), \
             patch("agent.android_memory.collect_package_memory") as mock_pkg:
            mock_pkg.return_value = {
                "package": "com.test.one",
                "pids": ["1"],
                "status": "foreground",
                "pss_kb": 300000,
                "rss_kb": 900000,
                "success": True,
                "usage_mb": "293 MB",
                "notes": [],
                "error": "",
            }
            text = build_ram_report_text(["com.test.one"])

        self.assertIn("zRAM", text)


class IncrementalSampleTests(unittest.TestCase):
    def test_record_and_load_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ram_incremental_samples.json"
            with patch("agent.android_memory._INCREMENTAL_PATH", path):
                record_launch_baseline("com.test.pkg", 2000000)
                record_incremental_sample("com.test.pkg", 2000000, 1700000)
                data = json.loads(path.read_text(encoding="utf-8"))
                samples = data["packages"]["com.test.pkg"]["incremental_kb"]
                self.assertEqual(samples, [300000])


if __name__ == "__main__":
    unittest.main()
