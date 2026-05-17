"""Resize the Termux window to free up screen space for Roblox clones.

When ``deng-rejoin Start`` runs on a small phone screen, the Termux task
itself is normally full-screen.  That covers the area where the layout
algorithm wants to place clones, so the user can't actually see whether
the windows landed in their pane.  This module dock-resizes Termux into
the left "log/status" pane so the right pane is free for clones.

Pipeline
────────
1. Detect display size (re-uses :func:`window_layout.detect_display_info`).
2. Compute the dock rect ``(0, 0, int(W * fraction), H)``.  Fraction is
   the user's chosen size — default :data:`window_layout.TERMUX_LOG_FRACTION`
   (35 %).  The user-tunable knob is ``config.termux_dock_fraction``.
3. Find the Termux task via ``dumpsys activity activities`` /
   ``dumpsys window windows`` (matches package ``com.termux``).
4. Flip the task into freeform windowing mode (``cmd activity
   set-task-windowing-mode <tid> 5``).  Some Android forks no-op resize
   on fullscreen tasks until this flip succeeds.
5. Try a cascade of resize commands (``cmd activity resize-task``,
   ``am task resize``, ``am stack resize``, ``wm task resize``).  First
   one that runs cleanly wins.
6. Optionally read back the bounds via ``dumpsys window windows`` for
   verification.

Returns a small :class:`MinimizeResult` so callers (the Start path,
``last_start_diagnostics.json``, tests) can introspect what happened.
Never raises — every command is guarded.

Public API
──────────
* :func:`minimize_termux_to_dock(display, *, fraction)` — perform the dock.
* :class:`MinimizeResult` — outcome dataclass.

Importing this module is free of side effects.  It writes to the
``deng.rejoin.termux_minimize`` logger only.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import android
from .window_layout import (
    DisplayInfo,
    TERMUX_LOG_FRACTION,
    detect_display_info,
)

_log = logging.getLogger("deng.rejoin.termux_minimize")

# Termux's own package — fixed.  The minimizer ONLY ever touches this
# package, not user-selected ones.
TERMUX_PACKAGE = "com.termux"

# Minimum width/height in pixels.  Anything smaller than this gets clipped
# to keep the terminal usable — a 60-px-wide column with 1-char of text
# would just frustrate the user.
_MIN_DOCK_WIDTH = 240
_MIN_DOCK_HEIGHT = 320

# Maximum fraction allowed.  We refuse to dock Termux to >90% of the
# screen because that defeats the purpose (no room for clones).
_MAX_FRACTION = 0.9
_MIN_FRACTION = 0.15


@dataclass
class MinimizeResult:
    """Outcome of a Termux dock-resize attempt."""
    ok: bool = False
    skipped: bool = False
    reason: str = ""
    task_id: int | None = None
    fraction: float = TERMUX_LOG_FRACTION
    desired: tuple[int, int, int, int] | None = None
    actual: tuple[int, int, int, int] | None = None
    method: str = ""
    attempts: list[str] = field(default_factory=list)
    display: tuple[int, int] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok":       self.ok,
            "skipped":  self.skipped,
            "reason":   self.reason,
            "task_id":  self.task_id,
            "fraction": self.fraction,
            "desired":  list(self.desired) if self.desired else None,
            "actual":   list(self.actual) if self.actual else None,
            "method":   self.method,
            "attempts": list(self.attempts),
            "display":  list(self.display) if self.display else None,
        }


# ── Task ID lookup ───────────────────────────────────────────────────────────


_TASK_ID_PATTERNS = (
    re.compile(r"taskId=(\d+)"),
    re.compile(r"Task\s*id\s*#?(\d+)", re.IGNORECASE),
    re.compile(r"\* Task\{[^}]*#(\d+)\b"),
)


def _find_termux_task_id() -> tuple[int | None, str]:
    """Return ``(task_id, source)`` for the running Termux task, or (None, reason).

    Uses :func:`android.run_android_command` so it transparently retries via
    root when the unprivileged ``dumpsys`` call returns a permission denial.
    """
    sources = (
        (["dumpsys", "activity", "activities"], "dumpsys activity activities"),
        (["dumpsys", "activity", "recents"],    "dumpsys activity recents"),
        (["dumpsys", "window", "windows"],      "dumpsys window windows"),
    )
    last_err = ""
    for args, label in sources:
        try:
            res = android.run_android_command(args, timeout=6)
        except Exception as exc:  # noqa: BLE001
            last_err = f"{label}: {exc}"
            continue
        if not res.ok or not res.stdout:
            last_err = f"{label}: rc={res.returncode}"
            continue
        tid = _scan_for_termux_task(res.stdout)
        if tid is not None:
            return tid, label
        last_err = f"{label}: no com.termux task found"
    return None, last_err or "no dumpsys output"


def _scan_for_termux_task(text: str) -> int | None:
    """Scan *text* (a dumpsys dump) for the first task entry naming Termux.

    Looks for a ``taskId=<n>`` (or equivalent) within ±15 lines of a
    ``com.termux`` reference.  The default app activity is
    ``com.termux/.app.TermuxActivity`` but Termux:Boot, Termux:API, and
    Termux:Float each register additional activities — we accept any
    ``com.termux*`` package so we don't miss a forked Termux.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "com.termux" not in line:
            continue
        # Search forward + backward for taskId on the same activity block.
        window = lines[max(0, i - 15): i + 16]
        for cand in window:
            for pat in _TASK_ID_PATTERNS:
                m = pat.search(cand)
                if m:
                    try:
                        return int(m.group(1))
                    except (TypeError, ValueError):
                        continue
    return None


# ── Resize cascade ───────────────────────────────────────────────────────────


def _clamp_fraction(fraction: float) -> float:
    try:
        f = float(fraction)
    except (TypeError, ValueError):
        return TERMUX_LOG_FRACTION
    if f < _MIN_FRACTION:
        return _MIN_FRACTION
    if f > _MAX_FRACTION:
        return _MAX_FRACTION
    return f


def _dock_rect(display: DisplayInfo, fraction: float) -> tuple[int, int, int, int]:
    f = _clamp_fraction(fraction)
    # ``round`` not ``int`` so 720 × 0.35 → 252 instead of 251 (floating-
    # point round-down).  One-pixel difference is invisible but the
    # rounder number reads better in diagnostics.
    w = max(_MIN_DOCK_WIDTH, round(display.width * f))
    h = max(_MIN_DOCK_HEIGHT, int(display.height))
    # Clip to the screen so a tiny screen doesn't produce out-of-bounds
    # numbers that the window manager would reject.
    w = min(w, display.width)
    h = min(h, display.height)
    return (0, 0, w, h)


def _try_resize(task_id: int, rect: tuple[int, int, int, int],
                root_tool: str | None) -> tuple[bool, str, list[str]]:
    """Walk a cascade of resize commands, return (ok, method, attempts).

    Each attempt is recorded so the caller can surface the full trail in
    diagnostics.  When ``root_tool`` is None the cascade is restricted to
    unprivileged ``cmd``/``am`` invocations — most Android builds reject
    resize-task without root, so the result is usually a clean ``False``.
    """
    l, t, r, b = rect
    attempts: list[str] = []

    # Step 1 — set freeform windowing mode (root-only).  No-op if already
    # freeform.  Resize-task is otherwise silently ignored on fullscreen.
    if root_tool:
        try:
            wm_res = android.run_root_command(
                ["cmd", "activity", "set-task-windowing-mode", str(task_id), "5"],
                root_tool=root_tool, timeout=4,
            )
            attempts.append(
                f"set-windowing-mode rc={wm_res.returncode} err={(wm_res.stderr or '')[:60]}"
            )
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"set-windowing-mode exc={exc}")

    # Step 2 — resize cascade.
    cmds: list[tuple[list[str], str]] = [
        (["cmd", "activity", "resize-task", str(task_id),
          str(l), str(t), str(r), str(b)],          "cmd resize-task"),
        (["cmd", "activity", "resize-task", str(task_id),
          str(l), str(t), str(r), str(b), "1"],     "cmd resize-task (mode=1)"),
        (["am", "task", "resize", str(task_id),
          str(l), str(t), str(r), str(b)],          "am task resize"),
        (["am", "stack", "resize", str(task_id),
          str(l), str(t), str(r), str(b)],          "am stack resize"),
        (["wm", "task", "resize", str(task_id),
          str(l), str(t), str(r), str(b)],          "wm task resize"),
    ]
    for cmd_args, label in cmds:
        try:
            if root_tool:
                res = android.run_root_command(
                    cmd_args, root_tool=root_tool, timeout=4,
                )
            else:
                res = android.run_command(cmd_args, timeout=4)
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"{label} exc={exc}")
            continue
        # ``ok`` is rc==0 — many Android resize commands print to stderr
        # even on success; check rc only.
        if res.ok:
            attempts.append(f"{label} OK")
            return True, label, attempts
        attempts.append(
            f"{label} rc={res.returncode} err={(res.stderr or '')[:80]}"
        )
    return False, "", attempts


def _read_back_termux_bounds() -> tuple[int, int, int, int] | None:
    """Best-effort read of Termux's current window bounds. None on failure."""
    try:
        res = android.run_android_command(
            ["dumpsys", "window", "windows"], timeout=6,
        )
    except Exception:  # noqa: BLE001
        return None
    if not res.ok or not res.stdout:
        return None
    # Match the first ``Window{... com.termux/...}`` block and pull its
    # frame.  Format: ``mFrame=[0,0][720,1280]`` or ``Frame: [...]``.
    text = res.stdout
    m = re.search(
        r"com\.termux[\s\S]{0,400}?[mM]Frame[=:]?\s*\[?(\d+),(\d+)\]?\[?(\d+),(\d+)\]?",
        text,
    )
    if not m:
        m = re.search(
            r"com\.termux[\s\S]{0,400}?Frame:\s*\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
            text,
        )
    if not m:
        return None
    try:
        return (int(m.group(1)), int(m.group(2)),
                int(m.group(3)), int(m.group(4)))
    except (TypeError, ValueError):
        return None


# ── Public entrypoint ────────────────────────────────────────────────────────


def minimize_termux_to_dock(
    display: DisplayInfo | None = None,
    *,
    fraction: float = TERMUX_LOG_FRACTION,
    verify: bool = True,
) -> MinimizeResult:
    """Resize Termux to the left dock pane.

    Args:
        display:  Pre-fetched display info.  Pass ``None`` to auto-detect.
        fraction: Width of the dock as a fraction of the screen, clamped
                  to ``[0.15, 0.9]``.  Default: :data:`TERMUX_LOG_FRACTION`.
        verify:   If True, re-read Termux bounds after the resize.

    Returns:
        :class:`MinimizeResult`.
    """
    result = MinimizeResult(fraction=_clamp_fraction(fraction))
    try:
        disp = display or detect_display_info()
    except Exception as exc:  # noqa: BLE001
        result.skipped = True
        result.reason = f"detect_display_info failed: {exc}"
        return result
    result.display = (disp.width, disp.height)
    result.desired = _dock_rect(disp, result.fraction)

    # Find the task.
    try:
        tid, source = _find_termux_task_id()
    except Exception as exc:  # noqa: BLE001
        result.skipped = True
        result.reason = f"task lookup failed: {exc}"
        return result
    if tid is None:
        result.skipped = True
        result.reason = f"no Termux task found ({source})"
        return result
    result.task_id = tid
    result.attempts.append(f"task lookup: tid={tid} via {source}")

    # Resize cascade.
    try:
        root_info = android.detect_root()
    except Exception:  # noqa: BLE001
        root_info = None
    root_tool = getattr(root_info, "tool", None) if (
        root_info and getattr(root_info, "available", False)
    ) else None
    if not root_tool:
        result.attempts.append("root unavailable — trying unprivileged cascade")
    ok, method, attempts = _try_resize(tid, result.desired, root_tool)
    result.attempts.extend(attempts)
    if not ok:
        result.reason = "all resize variants failed"
        return result
    result.method = method

    if verify:
        # Tiny wait so dumpsys reflects the new bounds.
        time.sleep(0.3)
        actual = _read_back_termux_bounds()
        result.actual = actual
        if actual is None:
            # Resize ran without error but readback failed — call it OK
            # since the command succeeded; the dashboard will show the
            # missing actual.
            result.ok = True
            result.reason = "resize ran; readback unavailable"
            return result
        # Treat ±64 px as a match (status bars / IMEs eat some pixels).
        wl, wt, wr, wb = result.desired
        al, at, ar, ab = actual
        within = (abs(al - wl) <= 64 and abs(at - wt) <= 64
                  and abs(ar - wr) <= 64 and abs(ab - wb) <= 64)
        result.ok = bool(within)
        if not within:
            result.reason = f"resize verified out-of-tolerance: actual={actual}"
        return result

    result.ok = True
    return result


def minimize_termux_silent(fraction: float = TERMUX_LOG_FRACTION) -> dict[str, Any]:
    """Thin wrapper that returns ``MinimizeResult.as_dict()``; never raises."""
    try:
        return minimize_termux_to_dock(fraction=fraction).as_dict()
    except Exception as exc:  # noqa: BLE001
        _log.debug("minimize_termux_silent error: %s", exc)
        return {
            "ok": False, "skipped": True, "reason": f"exception: {exc}",
            "task_id": None, "fraction": fraction, "desired": None,
            "actual": None, "method": "", "attempts": [], "display": None,
        }
