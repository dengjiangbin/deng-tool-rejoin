import unittest
from unittest.mock import patch

from agent.android import find_roblox_packages


class AndroidPackageDetectionTests(unittest.TestCase):
    def test_detects_clone_prefix_hints(self):
        packages = [
            "com.other.app",
            "com.moons.alpha",
            "com.roblox.client",
            "net.random.launcher",
        ]
        with patch("agent.android.list_packages", return_value=packages):
            detected = find_roblox_packages(["roblox", "moons"])

        self.assertEqual(detected[0], "com.roblox.client")
        self.assertIn("com.moons.alpha", detected)
        self.assertNotIn("com.other.app", detected)

    def test_ignores_unsafe_detection_hints(self):
        packages = ["com.moons.alpha", "com.roblox.client"]
        with patch("agent.android.list_packages", return_value=packages):
            detected = find_roblox_packages(["bad;rm", "moons"])

        self.assertIn("com.moons.alpha", detected)
        self.assertIn("com.roblox.client", detected)


if __name__ == "__main__":
    unittest.main()
