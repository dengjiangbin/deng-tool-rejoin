"""Safe I/O helpers for DENG Tool: Rejoin.

Two core exports
────────────────
safe_prompt()       – Replacement for input() that handles KeyboardInterrupt,
                      EOFError, and (on Termux/Android) bypasses the readline C
                      extension to prevent segfaults when the terminal state is
                      corrupted (e.g. after screen rotation, ANSI sequences, or
                      a prior ctrl-C inside a readline call).

setup_faulthandler() – Enable Python faulthandler so that true SIGSEGV crashes
                       (which cannot be caught by try/except) write a C-level
                       traceback to a local log file before the process dies.
                       NEVER writes to stderr — the crash stack must NOT appear
                       on the user's public terminal.

Platform strategy
─────────────────
Android / Termux (TERMUX_VERSION set):
    Uses sys.stdin.readline() to bypass the readline C library entirely.
    This prevents SIGSEGV when readline's internal state is corrupted.

All other platforms (Windows, CI, macOS):
    Uses builtins.input() which is compatible with unittest.mock.patch
    ("builtins.input", ...) patterns used throughout the test suite.

Return value contract
─────────────────────
  safe_prompt() returns:
    • The stripped user input (str, possibly empty when allow_blank=True).
    • ``default`` when the user pressed Enter with no input, if provided.
    • ``None`` on EOF, KeyboardInterrupt, or any unrecoverable I/O error.
      Callers MUST treat None as "cancel / go back to previous menu."
"""

from __future__ import annotations

import os
import sys
import time

# ── Platform detection ────────────────────────────────────────────────────────

def _on_termux() -> bool:
    """Return True when running inside Termux on Android."""
    return bool(os.environ.get("TERMUX_VERSION"))


# ── Public helpers ────────────────────────────────────────────────────────────


def safe_prompt(
    prompt: str = "",
    *,
    default: str | None = None,
    allow_blank: bool = False,
) -> str | None:
    """Read one line from stdin, handling EOF and KeyboardInterrupt safely.

    On Termux/Android (TERMUX_VERSION env var set), uses sys.stdin.readline()
    to bypass the readline C extension and prevent SIGSEGV crashes.

    On other platforms, uses builtins.input() so that test-suite mocks of
    ``builtins.input`` continue to work without modification.

    Args:
        prompt:      Text to display before the cursor (no trailing newline).
        default:     Returned when the user presses Enter with no input.
                     Ignored when allow_blank=True.
        allow_blank: When True, an empty response is returned as "" rather
                     than substituting ``default``.

    Returns:
        Stripped string, ``default``, or None (EOF / Ctrl-C / cancel signal).
    """
    if _on_termux():
        return _safe_prompt_readline(prompt, default=default, allow_blank=allow_blank)
    return _safe_prompt_input(prompt, default=default, allow_blank=allow_blank)


def _safe_prompt_input(
    prompt: str,
    *,
    default: str | None,
    allow_blank: bool,
) -> str | None:
    """input()-based path: compatible with builtins.input test mocks."""
    try:
        value = input(prompt)
    except KeyboardInterrupt:
        try:
            print()
        except Exception:  # noqa: BLE001
            pass
        return None
    except EOFError:
        return None
    except Exception:  # noqa: BLE001
        return None

    result = value.strip() if isinstance(value, str) else str(value).strip()
    if not result and not allow_blank and default is not None:
        return default
    return result


def _safe_prompt_readline(
    prompt: str,
    *,
    default: str | None,
    allow_blank: bool,
) -> str | None:
    """sys.stdin.readline()-based path: bypasses readline C extension on Termux."""
    try:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass

    try:
        line = sys.stdin.readline()
    except KeyboardInterrupt:
        try:
            sys.stdout.write("\n")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        return None
    except Exception:  # noqa: BLE001 – covers EOFError, OSError, etc.
        return None

    if not line:  # readline() returns "" on end-of-file
        return None

    result = line.rstrip("\n\r")
    if not result and not allow_blank and default is not None:
        return default
    return result


# ── Press-Enter convenience wrapper ──────────────────────────────────────────


def press_enter(msg: str = "\nPress Enter to continue...") -> None:
    """Show ``msg`` and wait; silently returns on EOF/Ctrl-C."""
    safe_prompt(msg)


# ── Crash-log setup ───────────────────────────────────────────────────────────


def setup_faulthandler() -> None:
    """Enable Python faulthandler to log crash tracebacks to a local log file.

    True segfaults (SIGSEGV) cannot be caught by try/except.  faulthandler
    writes a C-level traceback to the crash log *before* the process dies,
    which allows post-mortem diagnosis without polluting the user's terminal.

    Safety rules:
        • The crash log only contains Python frame names and line numbers.
        • No secrets, keys, or user data are written.
        • The crash stack is NEVER written to stderr or the terminal.
          If no writable log path can be found, faulthandler is simply not
          enabled (silent failure — better than spamming the user's screen).
    """
    try:
        import faulthandler  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        from .constants import CRASH_LOG_PATH  # noqa: PLC0415

        # Try the preferred path first, then a /tmp fallback.
        candidate_paths = [
            str(CRASH_LOG_PATH),
            "/tmp/deng-rejoin-crash.log",
            str(Path.home() / ".deng-rejoin-crash.log"),
        ]

        for crash_path_str in candidate_paths:
            try:
                Path(crash_path_str).parent.mkdir(parents=True, exist_ok=True)
                # Open in append mode — previous crashes are preserved.
                # Keep the file open for the lifetime of the process so
                # faulthandler can write to it at any moment, including
                # during a crash when the GC is no longer running.
                _crash_file = open(  # noqa: SIM115
                    crash_path_str, "a", encoding="utf-8", errors="replace"
                )
                # Store reference — prevents premature GC close.
                _setup_faulthandler._crash_file = _crash_file  # type: ignore[attr-defined]
                faulthandler.enable(file=_crash_file)
                return  # Successfully enabled; done.
            except Exception:  # noqa: BLE001
                continue

        # All paths failed — do NOT fall back to stderr.
        # Losing crash diagnostics is preferable to printing a giant stack
        # trace on the user's public terminal.

    except Exception:  # noqa: BLE001
        pass  # faulthandler module unavailable — ignore silently.


def check_and_report_crash_log(max_age_seconds: int = 3600) -> str | None:
    """Return a user-friendly warning string if a recent crash log exists.

    Called at startup to inform the user that a previous crash occurred.
    Returns a one-line message or ``None`` if no recent crash is detected.

    Args:
        max_age_seconds: Crash log is "recent" if modified within this window.
    """
    try:
        from .constants import CRASH_LOG_PATH  # noqa: PLC0415

        if not CRASH_LOG_PATH.exists():
            return None
        age = time.time() - CRASH_LOG_PATH.stat().st_mtime
        if age > max_age_seconds:
            return None
        return (
            f"Previous crash detected. "
            f"Crash log saved at: {CRASH_LOG_PATH}\n"
            f"If this keeps happening, share the log with support."
        )
    except Exception:  # noqa: BLE001
        return None
