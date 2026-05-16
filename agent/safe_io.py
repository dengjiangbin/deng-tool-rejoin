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
    """Enable Python faulthandler to log crash tracebacks to a local file.

    True segfaults (SIGSEGV) cannot be caught by try/except.  faulthandler
    writes a C-level traceback to the crash log *before* the process dies,
    which allows post-mortem diagnosis.

    Safety:
        • The crash log only contains Python frame names and line numbers.
        • No secrets, keys, or user data are written.
        • If the log file cannot be opened, faulthandler falls back to stderr.
    """
    try:
        import faulthandler  # noqa: PLC0415

        from .constants import CRASH_LOG_PATH  # noqa: PLC0415

        CRASH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Open in append mode — previous crashes are preserved for support.
        # We intentionally keep this file open for the lifetime of the process
        # so faulthandler can write to it at any point, including during a crash.
        _crash_file = open(  # noqa: SIM115
            str(CRASH_LOG_PATH), "a", encoding="utf-8", errors="replace"
        )
        # Store a reference so the GC does not close the file prematurely.
        _setup_faulthandler._crash_file = _crash_file  # type: ignore[attr-defined]
        faulthandler.enable(file=_crash_file)
    except Exception:  # noqa: BLE001
        try:
            import faulthandler  # noqa: PLC0415

            faulthandler.enable()  # stderr fallback
        except Exception:  # noqa: BLE001
            pass
