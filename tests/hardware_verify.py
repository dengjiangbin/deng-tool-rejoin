#!/usr/bin/env python3
"""Live Termux hardware verification for TTY isolation and layout safety.

Run inside Termux on a cloud phone (or dev box with deng-rejoin on PATH):

    python tests/hardware_verify.py

Each vector uses monotonic timers and programmatic assertions — no manual
stopwatch or visual layout inspection required.
"""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from unittest import mock

if os.name != "nt":
    import fcntl

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.constants import CRASH_LOG_PATH  # noqa: E402
from agent import subprocess_isolated, termux_ui  # noqa: E402


@dataclass
class VectorResult:
    name: str
    passed: bool
    metrics: str


def _resolve_deng_rejoin_argv() -> list[str]:
    exe = shutil.which("deng-rejoin")
    if exe:
        return [exe]
    entry = _REPO_ROOT / "agent" / "deng_tool_rejoin.py"
    if entry.is_file():
        return [sys.executable, str(entry)]
    raise RuntimeError("deng-rejoin not found on PATH and agent/deng_tool_rejoin.py missing")


def _isolated_popen(argv: list[str], *, env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "env": env or os.environ.copy(),
    }
    if os.name != "nt":
        kwargs["close_fds"] = True
    return subprocess.Popen(argv, **kwargs)


def _stdin_descriptor_healthy() -> tuple[bool, str]:
    """Return (ok, detail) for STDIN line-discipline / hijack checks."""
    if os.name == "nt":
        return True, "non-POSIX host (ioctl skipped)"

    fd = sys.stdin.fileno()
    try:
        import termios  # noqa: PLC0415

        termios.tcgetattr(fd)
    except Exception as exc:  # noqa: BLE001
        return False, f"tcgetattr failed: {exc}"

    try:
        fcntl.fcntl(fd, fcntl.F_GETFL)
    except OSError as exc:
        return False, f"fcntl F_GETFL failed: {exc}"

    started = time.monotonic()
    try:
        _r, _w, exceptional = select.select([sys.stdin], [], [sys.stdin], 0.0)
    except Exception as exc:  # noqa: BLE001
        return False, f"select failed: {exc}"
    elapsed = time.monotonic() - started
    if elapsed > 0.05:
        return False, f"select blocked {elapsed:.3f}s"
    if exceptional:
        return False, "stdin exceptional fd set"
    return True, "STDIN descriptor un-hijacked"


def test_network_isolation_timeout() -> VectorResult:
    name = "Network Isolation Timeout"
    env = os.environ.copy()
    env["DENG_API_URL"] = "http://10.255.255.1:9999"
    env.setdefault("DENG_DISABLE_TERMUX_HARD_EXIT", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")

    argv = _resolve_deng_rejoin_argv() + ["probe", "--upload"]
    started = time.monotonic()
    output = ""
    rc = -1
    try:
        proc = _isolated_popen(argv, env=env)
        try:
            output, _ = proc.communicate(timeout=16.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=2)
            elapsed = time.monotonic() - started
            return VectorResult(
                name,
                False,
                f"deadlocked past 16.00s limit ({elapsed:.2f}s)",
            )
        rc = proc.returncode if proc.returncode is not None else -1
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - started
        return VectorResult(name, False, f"spawn failed after {elapsed:.2f}s: {exc}")

    elapsed = time.monotonic() - started
    combined = output or ""
    if elapsed >= 16.0:
        return VectorResult(name, False, f"exceeded 16.00s cap ({elapsed:.2f}s)")
    if "rejoin.deng.my.id" in combined.lower():
        return VectorResult(
            name,
            False,
            f"production host leaked in output ({elapsed:.2f}s, rc={rc})",
        )
    return VectorResult(name, True, f"Executed in {elapsed:.2f}s (rc={rc})")


def test_tty_input_protection() -> VectorResult:
    name = "TTY Input Protection"
    if os.name == "nt":
        return VectorResult(name, True, "skipped on Windows (POSIX TTY N/A)")

    from agent import root_access  # noqa: PLC0415

    worker_error: list[str] = []
    finished = threading.Event()

    def _root_sleep_worker() -> None:
        try:
            cap = root_access.detect(timeout=3)
            if cap.available and cap.tool:
                argv, parent_timeout = root_access._wrap_root_invocation(cap.tool, "sleep 5", 5)
            else:
                argv = ["sh", "-c", "sleep 5"]
                parent_timeout = 7
            lock = None
            try:
                from agent import android as _android  # noqa: PLC0415

                lock = _android.subprocess_lock()
            except Exception:  # noqa: BLE001
                pass
            subprocess_isolated.run_isolated_text(
                argv,
                timeout=float(parent_timeout),
                lock=lock,
            )
        except Exception as exc:  # noqa: BLE001
            worker_error.append(str(exc))
        finally:
            finished.set()

    thread = threading.Thread(target=_root_sleep_worker, name="hw-root-sleep", daemon=True)
    thread.start()

    failures: list[str] = []
    deadline = time.monotonic() + 5.5
    polls = 0
    while time.monotonic() < deadline and not finished.is_set():
        polls += 1
        ok, detail = _stdin_descriptor_healthy()
        if not ok:
            failures.append(detail)
            break
        time.sleep(0.1)

    thread.join(timeout=8.0)
    if worker_error:
        return VectorResult(name, False, f"background root worker failed: {worker_error[0][:120]}")
    if failures:
        return VectorResult(name, False, failures[0])
    if polls < 3:
        return VectorResult(name, False, f"insufficient polls ({polls}) during root sleep")
    return VectorResult(name, True, "STDIN descriptor un-hijacked")


def test_layout_truncation_safety() -> VectorResult:
    name = "Layout Truncation Safety"
    width = 50
    long_pkg = "com.moons.litesc" + ("X" * 64)
    if len(long_pkg) < 80:
        long_pkg = (long_pkg + "XXXXXXXX")[:80]

    with mock.patch("shutil.get_terminal_size", return_value=os.terminal_size((width, 24))), \
         mock.patch("agent.safe_io.terminal_columns", return_value=width):
        fitted = termux_ui.fit_line(f"Package: {long_pkg}", width=width)
        if termux_ui.visible_len(fitted) > width:
            return VectorResult(
                name,
                False,
                f"fit_line width {termux_ui.visible_len(fitted)} > {width}",
            )
        if not fitted.endswith("..."):
            return VectorResult(name, False, "fit_line missing trailing ellipsis")
        if "\n" in fitted or "\r" in fitted:
            return VectorResult(name, False, "wrap/cascade newline detected in fit_line")

        from agent.commands import build_start_table  # noqa: PLC0415

        table = build_start_table(
            [(1, long_pkg, "verylongusername123456", "Online", "01:02:03", "120 MB")],
            use_color=False,
        )
        for line in table.splitlines():
            if termux_ui.visible_len(line) > width:
                return VectorResult(
                    name,
                    False,
                    f"table line width {termux_ui.visible_len(line)} > {width}",
                )
            if line.count("\n") > 0:
                return VectorResult(name, False, "embedded newline in table row")

    return VectorResult(name, True, "Hard-clamped to terminal width")


def test_boot_crash_rescue() -> VectorResult:
    name = "Boot Crash Rescue"
    CRASH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup: str | None = None
    if CRASH_LOG_PATH.exists():
        backup = CRASH_LOG_PATH.read_text(encoding="utf-8", errors="replace")
    dummy = (
        "Traceback (most recent call last):\n"
        '  File "agent/commands.py", line 1, in <module>\n'
        "RuntimeError: hardware_verify dummy crash\n"
    )
    CRASH_LOG_PATH.write_text(dummy, encoding="utf-8")
    os.utime(CRASH_LOG_PATH, None)

    env = os.environ.copy()
    env.setdefault("DENG_DISABLE_TERMUX_HARD_EXIT", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")

    argv = _resolve_deng_rejoin_argv() + ["--diag-startup"]
    started = time.monotonic()
    output = ""
    try:
        proc = _isolated_popen(argv, env=env)
        try:
            output, _ = proc.communicate(timeout=3.0)
        except subprocess.TimeoutExpired:
            if proc.stdout is not None:
                output = proc.stdout.read() or ""
            proc.kill()
            proc.communicate(timeout=2)
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - started
        return VectorResult(name, False, f"boot spawn failed after {elapsed:.2f}s: {exc}")
    finally:
        try:
            if backup is None:
                CRASH_LOG_PATH.unlink(missing_ok=True)
            else:
                CRASH_LOG_PATH.write_text(backup, encoding="utf-8")
        except OSError:
            pass

    elapsed = time.monotonic() - started
    text = output or ""
    crash_markers = (
        "Previous crash detected",
        "STEP:check_crash_log",
        "check_crash_log",
    )
    if elapsed > 3.0:
        return VectorResult(name, False, f"exceeded 3.00s macro timeout ({elapsed:.2f}s)")
    if not any(marker in text for marker in crash_markers):
        return VectorResult(
            name,
            False,
            f"crash notice missing within {elapsed:.2f}s",
        )
    if "Press Enter" in text or "safe_prompt" in text:
        return VectorResult(name, False, "interactive prompt detected during boot")
    return VectorResult(name, True, "Bypassed interactive lock")


def _print_summary(results: list[VectorResult]) -> None:
    print()
    print("| Test Vector | Status | Metrics / Failure Reason |")
    print("| :--- | :--- | :--- |")
    for row in results:
        status = "PASS" if row.passed else "FAIL"
        metrics = row.metrics.replace("|", "\\|")
        print(f"| {row.name} | {status} | {metrics} |")
    print()
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"Summary: {passed}/{total} vectors passed")
    if passed == total:
        print("HARDWARE VERIFY: PASS")
    else:
        print("HARDWARE VERIFY: FAIL")


def main() -> int:
    tests: list[tuple[str, Callable[[], VectorResult]]] = [
        ("network", test_network_isolation_timeout),
        ("tty", test_tty_input_protection),
        ("layout", test_layout_truncation_safety),
        ("boot", test_boot_crash_rescue),
    ]

    selected = set(sys.argv[1:]) if len(sys.argv) > 1 else set()
    results: list[VectorResult] = []
    for key, fn in tests:
        if selected and key not in selected:
            continue
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001
            results.append(
                VectorResult(
                    fn.__name__,
                    False,
                    f"unhandled: {exc.__class__.__name__}: {exc}",
                )
            )
            traceback.print_exc()

    if not results:
        print("No tests selected.", file=sys.stderr)
        return 2

    _print_summary(results)
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
