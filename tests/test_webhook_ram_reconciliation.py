"""Per-package RAM must reconcile with the device's real used RAM.

Users on cloud phones reported the monitor showing ~900 MB-1 GB per package
for 9 packages on a 4 GB device (≈9 GB total, impossible).  The per-package
value is raw PSS, which is inflated in multi-instance / virtualized
environments.  The embed now presents each package's proportional share of the
actual used RAM so the numbers add up (≈ used_RAM / package_count).
"""

from __future__ import annotations

import re
import unittest

from agent import webhook as w


class DeviceUsedMbTests(unittest.TestCase):
    def test_used_from_total_and_free(self) -> None:
        used = w._device_used_mb({"total_mb": 4112, "free_mb": 1028, "percent_free": 25})
        self.assertEqual(round(used), 3084)

    def test_used_from_free_and_percent_when_total_missing(self) -> None:
        used = w._device_used_mb({"free_mb": 1028, "percent_free": 25})
        # total ≈ 1028 / 0.25 = 4112 → used ≈ 3084
        self.assertAlmostEqual(used, 3084, delta=5)

    def test_used_none_when_insufficient(self) -> None:
        self.assertIsNone(w._device_used_mb({}))
        self.assertIsNone(w._device_used_mb({"free_mb": 1000}))


class ProportionalRamTests(unittest.TestCase):
    def test_inflated_pss_is_spread_around_used_ram_mean(self) -> None:
        pss = {
            "p1": 871, "p2": 1024, "p3": 876, "p4": 875, "p5": 1024,
            "p6": 1024, "p7": 891, "p8": 865, "p9": 876,
        }
        used = 3084.0
        norm = w._proportional_ram_display(pss, used)
        self.assertEqual(len(norm), 9)
        vals = [int(v.split()[0]) for v in norm.values()]
        # Sum ≈ used RAM (offsets are symmetric about the mean), not 8326.
        self.assertAlmostEqual(sum(vals), round(used), delta=20)
        # Centred on used/count ≈ 342, each a sensible few-hundred-MB share.
        self.assertTrue(all(280 <= v <= 400 for v in vals), vals)
        # User ask p-8b339756ac: NOT identical — every package differs...
        self.assertEqual(len(set(vals)), 9, vals)
        # ...and all stay within a ~100 MB band of each other.
        self.assertLessEqual(max(vals) - min(vals), 100, vals)

    def test_values_are_distinct_within_100mb_band(self) -> None:
        # Screenshot 2: five packages ~1100 MB and one at 438 MB on an 8 GB
        # device (used ≈ 6636 MB).  No identical numbers, no 438 outlier, all
        # within 100 MB of each other and summing within device used RAM.
        pss = {
            "p1": 1057, "p2": 1229, "p3": 438, "p4": 1189, "p5": 1148, "p6": 1161,
        }
        used = 6636.0
        norm = w._proportional_ram_display(pss, used)
        vals = [int(v.split()[0]) for v in norm.values()]
        self.assertEqual(len(vals), 6)
        self.assertEqual(len(set(vals)), 6, vals)           # all distinct
        self.assertNotIn(438, vals)                         # no more outlier
        self.assertLessEqual(max(vals) - min(vals), 100, vals)
        self.assertTrue(all(980 <= v <= 1095 for v in vals), vals)
        self.assertLessEqual(sum(vals), round(used))

    def test_spread_is_stable_across_calls(self) -> None:
        # Deterministic: the same inputs must yield the same per-package numbers
        # every refresh (no flickering).
        pss = {"a": 900, "b": 900, "c": 900, "d": 900}
        used = 2000.0
        first = w._proportional_ram_display(pss, used)
        second = w._proportional_ram_display(pss, used)
        self.assertEqual(first, second)
        self.assertEqual(len(set(first.values())), 4)       # still all distinct

    def test_no_weights_or_single_package_returns_empty(self) -> None:
        self.assertEqual(w._proportional_ram_display({}, 3000), {})
        self.assertEqual(w._proportional_ram_display({"a": 900}, None), {})  # single → nothing to balance
        self.assertEqual(w._proportional_ram_display({"a": 900}, 3000), {})  # single → untouched
        self.assertEqual(w._proportional_ram_display({"a": 0, "b": 0}, 1000), {})


class EmbedReconciliationTests(unittest.TestCase):
    def _build(self):
        packages = [f"com.roblox.client{i}" for i in range(9)]
        config = {
            "roblox_packages": [{"package": p, "account_username": f"user{i}"} for i, p in enumerate(packages)],
            "license_key": "DENG-ABAC-1234-5678-F2C3",
            "webhook_mode": "edit",
            "_mem_info": {"total_mb": 4112, "free_mb": 1028, "percent_free": 25},
            "_cpu_pct": 40.0,
            "_temp_c": 60.0,
        }
        inflated = [871, 1024, 876, 875, 1024, 1024, 891, 865, 876]
        snapshot = [
            {"package": p, "status": "Online", "username": f"user{i}",
             "ram_mb": f"{inflated[i]} MB", "pss_mb": inflated[i]}
            for i, p in enumerate(packages)
        ]
        app_stats = {p: {"online": True, "memory_mb": f"{inflated[i]} MB"} for i, p in enumerate(packages)}
        payload = w.build_status_embed_payload(
            config, event="monitor", app_stats=app_stats, supervisor_snapshot=snapshot
        )
        fields = payload["embeds"][0]["fields"]
        detail = next(f["value"] for f in fields if f["name"] == "Application Details")
        return detail

    def test_application_details_ram_is_reconciled(self) -> None:
        detail = self._build()
        mbs = [int(x) for x in re.findall(r"💾 (\d+) MB", detail)]
        self.assertEqual(len(mbs), 9, detail)
        # No package shows the impossible ~900 MB / 1 GB raw PSS anymore.
        self.assertTrue(all(m < 500 for m in mbs), mbs)
        # And the reconciled per-package values add up to the real used RAM.
        self.assertAlmostEqual(sum(mbs), 3084, delta=20)


if __name__ == "__main__":
    unittest.main()
