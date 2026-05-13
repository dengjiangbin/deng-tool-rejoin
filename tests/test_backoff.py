import unittest

from agent.backoff import calculate_backoff_seconds


class BackoffTests(unittest.TestCase):
    def test_backoff_is_exponential_and_capped(self):
        self.assertEqual(calculate_backoff_seconds(1, 10, 100), 10)
        self.assertEqual(calculate_backoff_seconds(2, 10, 100), 20)
        self.assertEqual(calculate_backoff_seconds(3, 10, 100), 40)
        self.assertEqual(calculate_backoff_seconds(10, 10, 100), 100)

    def test_backoff_respects_minimum(self):
        self.assertEqual(calculate_backoff_seconds(0, 1, 5), 10)


if __name__ == "__main__":
    unittest.main()
