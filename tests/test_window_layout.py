import unittest

from agent.window_layout import calculate_grid_layout


class WindowLayoutTests(unittest.TestCase):
    def test_two_packages_side_by_side(self):
        rects = calculate_grid_layout(["pkg.one", "pkg.two"], 1080, 960, gap=0)
        self.assertEqual(len(rects), 2)
        self.assertEqual(rects[0].left, 0)
        self.assertEqual(rects[0].right, 540)
        self.assertEqual(rects[1].left, 540)
        self.assertEqual(rects[1].right, 1080)

    def test_four_packages_grid(self):
        rects = calculate_grid_layout(["a.one", "a.two", "a.three", "a.four"], 1000, 1000, gap=10)
        self.assertEqual(len(rects), 4)
        self.assertGreaterEqual(rects[0].left, 0)
        self.assertLessEqual(rects[-1].right, 1000)
        self.assertLessEqual(rects[-1].bottom, 1000)


if __name__ == "__main__":
    unittest.main()
