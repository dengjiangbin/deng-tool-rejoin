"""Tests for agent.dumpsys_cache — the per-argv 2.5s shared cache."""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from agent import dumpsys_cache as dc


def _runner_factory(return_value):
    """Build a runner that records its calls."""
    calls = []
    def _run(args):
        calls.append(tuple(args))
        return dc.CachedResult(ok=True, stdout=return_value)
    return _run, calls


class TestCachedRun(unittest.TestCase):
    def setUp(self) -> None:
        dc.invalidate()

    def test_first_call_runs_and_caches(self) -> None:
        run, calls = _runner_factory("hello")
        result = dc.cached_run(["dumpsys", "window"], run)
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, "hello")
        self.assertEqual(len(calls), 1)

    def test_second_call_within_ttl_returns_cached(self) -> None:
        run, calls = _runner_factory("hello")
        dc.cached_run(["dumpsys", "window"], run)
        dc.cached_run(["dumpsys", "window"], run)
        dc.cached_run(["dumpsys", "window"], run)
        self.assertEqual(len(calls), 1)

    def test_ttl_zero_forces_call(self) -> None:
        run, calls = _runner_factory("hello")
        dc.cached_run(["dumpsys", "window"], run)
        dc.cached_run(["dumpsys", "window"], run, ttl=0)
        self.assertEqual(len(calls), 2)

    def test_different_args_use_different_cache_slots(self) -> None:
        run, calls = _runner_factory("hello")
        dc.cached_run(["dumpsys", "window"], run)
        dc.cached_run(["dumpsys", "activity"], run)
        self.assertEqual(len(calls), 2)

    def test_concurrent_calls_coalesce_to_one_runner(self) -> None:
        """N concurrent threads must trigger only ONE runner invocation."""
        call_count = [0]
        lock = threading.Lock()
        gate = threading.Event()

        def slow_runner(args):
            with lock:
                call_count[0] += 1
            # Block until released so multiple threads pile up.
            gate.wait(timeout=2)
            return dc.CachedResult(ok=True, stdout="x")

        threads = [
            threading.Thread(
                target=lambda: dc.cached_run(["dumpsys", "X"], slow_runner),
            ) for _ in range(8)
        ]
        for t in threads:
            t.start()
        time.sleep(0.05)
        gate.set()
        for t in threads:
            t.join(timeout=3)
        # The per-key lock means only one runner actually ran; the rest
        # waited and then served the cached result.
        self.assertEqual(call_count[0], 1)

    def test_runner_exception_falls_back_to_empty(self) -> None:
        def bad(args):
            raise RuntimeError("boom")
        result = dc.cached_run(["dumpsys", "X"], bad)
        self.assertFalse(result.ok)
        self.assertEqual(result.stdout, "")

    def test_invalidate_with_prefix(self) -> None:
        run, _ = _runner_factory("hi")
        dc.cached_run(["dumpsys", "window"], run)
        dc.cached_run(["dumpsys", "activity"], run)
        dc.invalidate(["dumpsys", "window"])
        # A fresh runner — proves we ran again post-invalidation.
        run2, calls2 = _runner_factory("hi2")
        dc.cached_run(["dumpsys", "window"], run2)
        self.assertEqual(len(calls2), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
