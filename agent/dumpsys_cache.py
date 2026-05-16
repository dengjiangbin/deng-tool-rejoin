"""Tiny shared cache for ``dumpsys`` / ``cmd`` outputs.

Why this exists
───────────────
Each supervisor worker thread independently calls ``dumpsys window windows``,
``dumpsys activity activities``, ``dumpsys SurfaceFlinger``, and
``current_foreground_package``.  Every dumpsys call is a binder transaction
that can take 1-5 seconds on a cloud phone, and binder serializes calls
through ``system_server`` — so with 3 packages × 5 dumpsys calls × 2 s each
= ~30 seconds before the first row's state can transition out of
"Preparing".  The user sees a tool stuck in Preparing.

This module exposes a 2-3 second TTL cache keyed by the command argv.
Multiple threads can ask for the same dumpsys output and only one process
actually runs; the rest read the cached text.

The cache is intentionally TINY and short-lived — Android state genuinely
changes every few seconds, so we don't want to serve stale data for more
than a heartbeat or two.  Tests and tools that need a fresh probe can
call :func:`invalidate` or pass ``ttl=0`` to :func:`cached_run`.

Never raises.  Safe across threads.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Sequence

DEFAULT_TTL: float = 2.5

# Per-key locks so that simultaneous calls for the same dumpsys output
# coalesce into a single child process instead of N concurrent ones.
_KEY_LOCKS: dict[tuple[str, ...], threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()

_CACHE: dict[tuple[str, ...], tuple[float, bool, str]] = {}
_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class CachedResult:
    ok: bool
    stdout: str


def _key_lock(key: tuple[str, ...]) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock


def cached_run(
    args: Sequence[str],
    runner: Callable[[Sequence[str]], CachedResult],
    *,
    ttl: float = DEFAULT_TTL,
) -> CachedResult:
    """Return cached result for *args*; otherwise call ``runner(args)``.

    ``runner`` must take the argv and return a :class:`CachedResult`.  It
    is invoked under a per-key lock, so concurrent requests for the same
    argv coalesce into one child process.

    Setting ``ttl`` to 0 forces a fresh call and updates the cache.
    """
    key = tuple(args)
    now = time.monotonic()

    if ttl > 0:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit and (now - hit[0]) < ttl:
                return CachedResult(ok=hit[1], stdout=hit[2])

    lock = _key_lock(key)
    with lock:
        # Re-check inside the lock — someone else may have populated it.
        if ttl > 0:
            with _CACHE_LOCK:
                hit = _CACHE.get(key)
                if hit and (time.monotonic() - hit[0]) < ttl:
                    return CachedResult(ok=hit[1], stdout=hit[2])

        try:
            result = runner(args)
        except Exception:  # noqa: BLE001
            result = CachedResult(ok=False, stdout="")

        with _CACHE_LOCK:
            _CACHE[key] = (time.monotonic(), bool(result.ok), str(result.stdout or ""))
        return result


def invalidate(prefix: Sequence[str] | None = None) -> None:
    """Drop cached entries; if ``prefix`` is given, only matching commands.

    Used by tests and by the worker after a force-stop / am start (so the
    next read sees the new state, not the pre-launch snapshot).
    """
    if prefix is None:
        with _CACHE_LOCK:
            _CACHE.clear()
        return
    prefix_tup = tuple(prefix)
    plen = len(prefix_tup)
    with _CACHE_LOCK:
        for key in list(_CACHE.keys()):
            if key[:plen] == prefix_tup:
                del _CACHE[key]
