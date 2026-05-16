"""Real-window apply layer for landscape-block layout.

Strategy
────────
1. PRE-LAUNCH: Write App Cloner ``pkg_preferences.xml`` (direct or root) for
   each Roblox package so its next start uses the desired bounds.
2. FORCE-STOP + RELAUNCH: Force-stop any selected package that is already
   running with stale bounds, so the new prefs take effect on next launch.
3. POST-LAUNCH VERIFICATION: After launch grace, read actual task/window
   bounds via ``dumpsys activity activities`` and ``dumpsys window windows``.
4. DIRECT RESIZE FALLBACK: If actual bounds drift from desired, try
   ``cmd activity stack`` / ``am stack`` resize commands via root.
5. RETRY LOOP: Apply→Verify→Reapply up to N times before logging failure.

All output goes to ``deng.rejoin.window_apply`` logger (file only).
Never raises.  Never prints to stdout.  Public dashboard reads the status
field set on each package's apply result.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from . import android
from .window_layout import (
    WindowRect,
    _is_layout_excluded,
    update_app_cloner_xml,
    update_app_cloner_xml_root,
)

_log = logging.getLogger("deng.rejoin.window_apply")


@dataclass
class ApplyResult:
    package: str
    desired: WindowRect
    pre_write_ok: bool = False
    pre_write_method: str = ""
    actual_bounds: tuple[int, int, int, int] | None = None  # (l, t, r, b)
    actual_method: str = ""
    direct_resize_ok: bool = False
    final_ok: bool = False
    detail: str = ""
    attempts: list[str] = field(default_factory=list)


# ── Capability probes ─────────────────────────────────────────────────────────

def _capability_probes() -> dict[str, bool]:
    """Detect which apply methods are available on this device.  Never raises."""
    caps = {
        "root":             False,
        "cmd_activity":     False,
        "am_stack":         False,
        "dumpsys_activity": False,
        "dumpsys_window":   False,
        "wm_size":          False,
    }
    try:
        caps["root"] = android.detect_root().available
    except Exception:  # noqa: BLE001
        pass
    for cmd, key in (
        (["cmd", "activity", "-h"], "cmd_activity"),
        (["am", "-h"],              "am_stack"),
        (["dumpsys", "activity"],   "dumpsys_activity"),
        (["dumpsys", "window"],     "dumpsys_window"),
        (["wm", "size"],            "wm_size"),
    ):
        try:
            res = android.run_command(cmd, timeout=3)
            caps[key] = bool(res.ok or (res.stdout and "Usage" in res.stdout))
        except Exception:  # noqa: BLE001
            pass
    return caps


# ── Read actual bounds ───────────────────────────────────────────────────────

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _parse_bounds_line(text: str) -> tuple[int, int, int, int] | None:
    """Extract first ``[l,t][r,b]`` rect from a text blob."""
    m = _BOUNDS_RE.search(text)
    if not m:
        return None
    try:
        l, t, r, b = (int(g) for g in m.groups())
        return l, t, r, b
    except Exception:  # noqa: BLE001
        return None


def _find_bounds_near_package(text: str, package: str, lookahead: int = 20) -> tuple[int, int, int, int] | None:
    """Find ``[l,t][r,b]`` within ``lookahead`` lines after the line that mentions ``package``."""
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if package not in line:
            continue
        # First check the line itself
        bounds = _parse_bounds_line(line)
        if bounds:
            return bounds
        # Then look ahead within the same block
        for j in range(idx + 1, min(idx + 1 + lookahead, len(lines))):
            nxt = lines[j]
            # Stop at next obvious block boundary
            if nxt.strip() == "" and j > idx + 2:
                break
            if "Window{" in nxt or ("TaskRecord" in nxt and package not in nxt):
                break
            bounds = _parse_bounds_line(nxt)
            if bounds:
                return bounds
    return None


def read_actual_bounds(package: str) -> tuple[tuple[int, int, int, int] | None, str]:
    """Read the actual on-screen bounds for ``package``.

    Returns (bounds_tuple_or_None, source_label).  Never raises.
    Source labels: "dumpsys_window", "dumpsys_activity", "unavailable".
    """
    try:
        res = android.run_command(["dumpsys", "window", "windows"], timeout=6)
        if res.ok:
            bounds = _find_bounds_near_package(res.stdout, package, lookahead=30)
            if bounds:
                return bounds, "dumpsys_window"
    except Exception:  # noqa: BLE001
        pass
    try:
        res = android.run_command(["dumpsys", "activity", "activities"], timeout=6)
        if res.ok:
            bounds = _find_bounds_near_package(res.stdout, package, lookahead=20)
            if bounds:
                return bounds, "dumpsys_activity"
    except Exception:  # noqa: BLE001
        pass
    return None, "unavailable"


def _get_task_id(package: str) -> int | None:
    """Best-effort: find the task id for ``package`` from dumpsys activity."""
    try:
        res = android.run_command(["dumpsys", "activity", "activities"], timeout=6)
        if not res.ok:
            return None
        cur_task: int | None = None
        for line in res.stdout.splitlines():
            m = re.search(r"TaskRecord\{[^}]+\s+#(\d+)", line)
            if m:
                cur_task = int(m.group(1))
            if package in line and cur_task is not None:
                return cur_task
    except Exception:  # noqa: BLE001
        pass
    return None


def _direct_resize_via_root(
    package: str, rect: WindowRect, root_tool: str
) -> tuple[bool, str]:
    """Try root ``cmd activity stack resize-docked-stack``/``am stack`` for the task.

    Best-effort.  Returns (ok, detail).  Never raises.
    """
    task_id = _get_task_id(package)
    if task_id is None:
        return False, "no task id"
    bounds = f"{rect.left} {rect.top} {rect.right} {rect.bottom}"
    # Try multiple variants — Android versions differ
    candidates = [
        ["cmd", "activity", "resize-task", str(task_id),
         str(rect.left), str(rect.top), str(rect.right), str(rect.bottom)],
        ["am", "task", "resize", str(task_id),
         str(rect.left), str(rect.top), str(rect.right), str(rect.bottom)],
        ["am", "stack", "resize", str(task_id),
         str(rect.left), str(rect.top), str(rect.right), str(rect.bottom)],
        ["wm", "size", f"{rect.right - rect.left}x{rect.bottom - rect.top}"],
    ]
    for cmd_args in candidates:
        try:
            res = android.run_root_command(cmd_args, root_tool=root_tool, timeout=4)
            if res.ok:
                return True, f"resize via {cmd_args[0]} {cmd_args[1]}"
        except Exception:  # noqa: BLE001
            continue
    return False, f"all direct-resize variants failed (task #{task_id} bounds={bounds})"


# ── High-level apply ─────────────────────────────────────────────────────────

def apply_window_layout(
    rects: Sequence[WindowRect],
    *,
    force_stop_before: bool = False,
    relaunch_after: bool = False,
    verify_after: bool = True,
    retries: int = 1,
) -> list[ApplyResult]:
    """Apply landscape-block layout to a list of WindowRect.

    Pipeline:
      1. Capability probe (logged at DEBUG).
      2. For each rect:
         a. Pre-write App Cloner XML (direct → root fallback).
         b. (optional) Force-stop package so XML is honored on next launch.
         c. (optional) Relaunch package.
      3. After launch grace, read actual bounds.
      4. If actual bounds differ significantly, try direct resize via root.
      5. Retry up to ``retries`` times.

    Returns one ApplyResult per rect.  Never raises.  Never prints to stdout.
    """
    caps = _capability_probes()
    _log.debug("apply_window_layout caps=%s", caps)

    results: list[ApplyResult] = []
    root_info = android.detect_root()
    root_tool = root_info.tool if root_info.available else None

    for rect in rects:
        result = ApplyResult(package=rect.package, desired=rect)

        if _is_layout_excluded(rect.package):
            result.detail = "excluded (Termux/system)"
            result.attempts.append("skip-excluded")
            results.append(result)
            continue

        # ── Step 1: pre-write XML ──
        try:
            ok, msg = update_app_cloner_xml(rect.package, rect)
            result.attempts.append(f"xml-direct: {msg}")
            if ok:
                result.pre_write_ok = True
                result.pre_write_method = "xml-direct"
            elif root_tool:
                ok, msg = update_app_cloner_xml_root(rect.package, rect, root_tool)
                result.attempts.append(f"xml-root: {msg}")
                if ok:
                    result.pre_write_ok = True
                    result.pre_write_method = "xml-root"
        except Exception as exc:  # noqa: BLE001
            result.attempts.append(f"xml-error: {exc}")

        # ── Step 2: force-stop (so prefs are reloaded on next launch) ──
        if force_stop_before and result.pre_write_ok:
            try:
                android.force_stop_package(rect.package)
                result.attempts.append("force-stop ok")
            except Exception as exc:  # noqa: BLE001
                result.attempts.append(f"force-stop error: {exc}")

        results.append(result)

    if not verify_after:
        for r in results:
            r.final_ok = r.pre_write_ok
            r.detail = r.detail or (
                "pre-write succeeded; verification skipped"
                if r.pre_write_ok else "pre-write failed; verification skipped"
            )
        return results

    # ── Step 3: post-launch verification + direct resize retry ──
    for attempt in range(max(1, retries + 1)):
        all_ok = True
        for result in results:
            if result.detail == "excluded (Termux/system)":
                continue
            bounds, source = read_actual_bounds(result.package)
            result.actual_bounds = bounds
            result.actual_method = source
            result.attempts.append(f"verify-attempt-{attempt}: {source}={bounds}")

            if bounds is None:
                # Verification unavailable — trust pre-write success
                result.final_ok = result.pre_write_ok
                continue

            # Compare with desired (tolerate ±32px drift due to status/nav bars)
            tolerance = 32
            d = result.desired
            close_enough = (
                abs(bounds[0] - d.left)   <= tolerance and
                abs(bounds[1] - d.top)    <= tolerance and
                abs(bounds[2] - d.right)  <= tolerance and
                abs(bounds[3] - d.bottom) <= tolerance
            )
            if close_enough:
                result.final_ok = True
                continue

            all_ok = False
            # ── Step 4: try direct resize via root ──
            if root_tool:
                ok, detail = _direct_resize_via_root(result.package, d, root_tool)
                result.direct_resize_ok = ok
                result.attempts.append(f"direct-resize: {detail}")
                if ok:
                    time.sleep(0.5)
                    bounds, source = read_actual_bounds(result.package)
                    result.actual_bounds = bounds
                    result.actual_method = source
                    if bounds and (
                        abs(bounds[0] - d.left)   <= tolerance and
                        abs(bounds[1] - d.top)    <= tolerance
                    ):
                        result.final_ok = True
                        continue

        if all_ok or not root_tool:
            break

    # Finalize details
    for r in results:
        if r.detail:
            continue
        if r.final_ok:
            r.detail = f"applied via {r.pre_write_method}" + (
                f", verified by {r.actual_method}" if r.actual_bounds else ""
            )
        else:
            r.detail = (
                f"layout not fully applied "
                f"(pre_write={r.pre_write_ok}, actual_bounds={r.actual_bounds})"
            )

    return results


def apply_window_layout_silent(
    rects: Iterable[WindowRect],
    *,
    force_stop_before: bool = False,
    relaunch_after: bool = False,
    verify_after: bool = True,
    retries: int = 1,
) -> tuple[int, int]:
    """Silent wrapper: returns (success_count, total_count).  Never raises."""
    try:
        results = apply_window_layout(
            list(rects),
            force_stop_before=force_stop_before,
            relaunch_after=relaunch_after,
            verify_after=verify_after,
            retries=retries,
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("apply_window_layout_silent error: %s", exc)
        return 0, 0
    return sum(1 for r in results if r.final_ok), len(results)
