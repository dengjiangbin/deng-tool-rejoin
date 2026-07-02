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

  read_interactive_line() returns:
    • A stripped string (possibly empty when allow_blank=True).
    • ``default`` only when the user pressed Enter on an empty line.
    • ``None`` on KeyboardInterrupt only.
    • Raises :exc:`InteractiveInputUnavailable` when neither stdin nor
      ``/dev/tty`` can supply input (never silently substitutes a default).
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

LICENSE_GATE_INPUT_UNAVAILABLE_MSG = (
    "Cannot read input from terminal. Re-run deng-rejoin in an interactive Termux session."
)


class InteractiveInputUnavailable(Exception):
    """Neither stdin nor /dev/tty could supply interactive input."""


# ── Platform detection ────────────────────────────────────────────────────────

def _on_termux() -> bool:
    """Return True when running inside Termux on Android."""
    return bool(os.environ.get("TERMUX_VERSION"))


def terminal_columns(*, fallback: int = 80) -> int:
    """Return current TTY width; never below 40."""
    try:
        return max(40, int(shutil.get_terminal_size(fallback=(fallback, 24)).columns))
    except Exception:  # noqa: BLE001
        return max(40, fallback)


_UI_IO_ERRORS = (OSError, BrokenPipeError, EOFError)


def _record_ui_io_error(exc: BaseException) -> None:
    try:
        from . import start_lifecycle as _start_lifecycle

        _start_lifecycle.record_ui_render_error(exc)
    except Exception:  # noqa: BLE001
        pass


def write_stdout(text: str = "", *, end: str = "\n", flush: bool = True) -> None:
    """Write one frame to STDOUT without blocking locks (Termux-safe)."""
    payload = text if end == "" else f"{text}{end}"
    try:
        sys.stdout.write(payload)
        if flush:
            sys.stdout.flush()
    except UnicodeEncodeError:
        try:
            if hasattr(sys.stdout, "buffer"):
                sys.stdout.buffer.write(payload.encode("utf-8", errors="replace"))
                if flush:
                    sys.stdout.buffer.flush()
            else:
                sys.stdout.write(payload.encode("ascii", errors="replace").decode("ascii"))
                if flush:
                    sys.stdout.flush()
        except _UI_IO_ERRORS as exc:
            _record_ui_io_error(exc)
    except _UI_IO_ERRORS as exc:
        _record_ui_io_error(exc)


def write_stdout_block(text: str, *, flush: bool = True) -> None:
    """Write a multi-line block as one STDOUT frame (no mutex)."""
    try:
        sys.stdout.write(text)
        if flush:
            sys.stdout.flush()
    except UnicodeEncodeError:
        try:
            if hasattr(sys.stdout, "buffer"):
                sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
                if flush:
                    sys.stdout.buffer.flush()
        except _UI_IO_ERRORS as exc:
            _record_ui_io_error(exc)
    except _UI_IO_ERRORS as exc:
        _record_ui_io_error(exc)


@contextmanager
def interactive_output_guard() -> Iterator[None]:
    """Mark an interactive menu/session — nested-safe (no locks)."""
    yield


def restore_terminal() -> None:
    """Force TTY back to cooked/sane mode (call before crash notices or menus)."""
    _run_stty_sane()
    if os.name == "nt":
        return
    if not getattr(sys.stdin, "isatty", lambda: False)():
        return
    try:
        import termios  # noqa: PLC0415

        attrs = termios.tcgetattr(sys.stdin.fileno())
        lflag = attrs[3]
        lflag |= termios.ICANON | termios.ECHO
        lflag &= ~(termios.ECHOCTL | termios.ECHOK | termios.ECHOKE)
        attrs[3] = lflag
        attrs[6][termios.VMIN] = 1
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, attrs)
    except Exception:  # noqa: BLE001
        pass


# ── Public helpers ────────────────────────────────────────────────────────────


def read_interactive_line(
    prompt: str = "",
    *,
    default: str | None = None,
    allow_blank: bool = False,
) -> str | None:
    """Block on a real TTY for license-gate and other must-not-exit prompts.

    On Termux, ``sys.stdin.readline()`` can return EOF immediately while the
    user still sees the prompt (stdin detached from the controlling TTY).
    This helper falls back to ``/dev/tty`` before giving up.

    Raises :exc:`InteractiveInputUnavailable` when no input source works.
    Returns ``None`` only for KeyboardInterrupt (Ctrl-C).
    """
    if not _on_termux():
        return safe_prompt(prompt, default=default, allow_blank=allow_blank)

    _write_prompt(prompt)
    line = _read_line_from_stream(sys.stdin)
    if line is not None:
        return _finalize_prompt_line(line, default=default, allow_blank=allow_blank)

    tty_line = _read_line_from_dev_tty()
    if tty_line is not None:
        return _finalize_prompt_line(tty_line, default=default, allow_blank=allow_blank)

    raise InteractiveInputUnavailable(LICENSE_GATE_INPUT_UNAVAILABLE_MSG)


def _write_prompt(prompt: str) -> None:
    """Write a prompt exactly ONCE to the user's terminal.

    Previously this wrote to BOTH ``sys.stdout`` and ``/dev/tty``. On Termux the
    two are normally the same terminal, so every prompt was echoed twice — e.g.
    ``Choose [1/0]: Choose [1/0]:`` / ``Enter license key: Enter license key:``.
    We now write to stdout, and only fall back to ``/dev/tty`` when stdout is NOT
    a TTY (i.e. redirected), so the prompt still reaches the real terminal in
    that edge case without doubling in the common case.
    """
    wrote_stdout = False
    try:
        stdout_is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    except Exception:  # noqa: BLE001
        stdout_is_tty = False
    try:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        wrote_stdout = True
    except Exception:  # noqa: BLE001
        wrote_stdout = False
    # Only mirror to /dev/tty when stdout did not already reach the terminal
    # (stdout failed, or stdout is redirected to a file/pipe, not the TTY).
    if os.name != "nt" and (not wrote_stdout or not stdout_is_tty):
        try:
            with open("/dev/tty", "w", encoding="utf-8", errors="replace") as tty_out:
                tty_out.write(prompt)
                tty_out.flush()
        except OSError:
            pass


def _read_line_from_stream(stream: Any) -> str | None:
    """Return a raw line, or ``None`` when the stream hits EOF."""
    try:
        line = stream.readline()
    except KeyboardInterrupt:
        try:
            sys.stdout.write("\n")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        raise
    except Exception:  # noqa: BLE001
        return None
    if not line:
        return None
    return line


def _read_line_from_dev_tty() -> str | None:
    if os.name == "nt":
        return None
    try:
        with open("/dev/tty", "r", encoding="utf-8", errors="replace") as tty_in:
            line = tty_in.readline()
    except OSError:
        return None
    if not line:
        return None
    return line


def _finalize_prompt_line(
    line: str,
    *,
    default: str | None,
    allow_blank: bool,
) -> str:
    result = line.rstrip("\n\r")
    if not result and not allow_blank and default is not None:
        return default
    return result


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


@contextmanager
def tty_session(*, restore_on_exit: bool = True) -> Iterator[None]:
    """Guard interactive menu I/O — always restore TTY on exit."""
    saved = _save_tty_attrs() if restore_on_exit else None
    with interactive_output_guard():
        try:
            yield
        finally:
            if restore_on_exit:
                _restore_tty_attrs(saved)
                _run_stty_sane()


def _save_tty_attrs() -> list[Any] | None:
    if os.name == "nt":
        return None
    if not getattr(sys.stdin, "isatty", lambda: False)():
        return None
    try:
        import termios  # noqa: PLC0415

        return termios.tcgetattr(sys.stdin.fileno())
    except Exception:  # noqa: BLE001
        return None


def _restore_tty_attrs(saved: list[Any] | None) -> None:
    if not saved or os.name == "nt":
        return
    try:
        import termios  # noqa: PLC0415

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
    except Exception:  # noqa: BLE001
        pass


def _run_stty_sane() -> None:
    if os.name == "nt":
        return
    try:
        from . import subprocess_isolated as _iso  # noqa: PLC0415

        _iso.run_isolated_text(["stty", "sane"], timeout=2.0)
    except Exception:  # noqa: BLE001
        pass


def _terminal_input_usable() -> bool:
    """Return True when stdin looks like a normal interactive TTY."""
    try:
        return bool(getattr(sys.stdin, "isatty", lambda: False)())
    except Exception:  # noqa: BLE001
        return False


def safe_clear_screen(*, clear_scrollback: bool = False) -> None:
    """Clear the visible terminal using ANSI only (Termux-safe, fork-free).

    Does not call HOME, force-stop Termux, close apps, or run wm/orientation
    commands.  Failures are swallowed so callers can continue safely.
    """
    try:
        if os.name == "nt":
            os.system("cls")  # Windows only — no Termux risk here
        else:
            if clear_scrollback:
                sys.stdout.write("\033[2J\033[3J\033[H")
            else:
                sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass


# ── Crash-log setup ───────────────────────────────────────────────────────────

_crash_context: dict[str, str] = {}


def _sanitize_crash_context_value(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("\n", " ").replace("\r", " ")[:240]


def set_crash_context(**fields: Any) -> None:
    """Record current Start phase context for native crash diagnostics.

    The context is appended to ``data/logs/crash_faulthandler.log`` immediately,
    so even native crashes that bypass Python exception handling leave the last
    known phase, screen mode, package count, and build identifiers behind.
    """
    try:
        for key, value in fields.items():
            _crash_context[str(key)] = _sanitize_crash_context_value(value)
        _write_crash_context_line()
    except Exception:  # noqa: BLE001
        pass


def _write_crash_context_line() -> None:
    try:
        from .constants import FAULT_HANDLER_LOG_PATH  # noqa: PLC0415

        FAULT_HANDLER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        parts = [
            f"{key}={_crash_context.get(key, '')!r}"
            for key in sorted(_crash_context)
        ]
        with FAULT_HANDLER_LOG_PATH.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"[DENG_REJOIN_CRASH_CONTEXT] {' '.join(parts)}\n")
    except Exception:  # noqa: BLE001
        pass


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

        from .constants import CRASH_LOG_PATH, FAULT_HANDLER_LOG_PATH  # noqa: PLC0415

        # Try the preferred path first, then a /tmp fallback.
        candidate_paths = [
            str(FAULT_HANDLER_LOG_PATH),
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
                try:
                    os.set_inheritable(_crash_file.fileno(), False)
                except Exception:  # noqa: BLE001
                    pass
                # Store reference — prevents premature GC close.
                setup_faulthandler._crash_file = _crash_file  # type: ignore[attr-defined]
                faulthandler.enable(file=_crash_file, all_threads=True)
                set_crash_context(phase="entrypoint")
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

    Never blocks on interactive crash reporting.  If the TTY cannot be
    verified, returns the notice text only — callers print it and continue.
    """
    try:
        restore_terminal()
    except Exception:  # noqa: BLE001
        pass
    try:
        from .constants import CRASH_LOG_PATH  # noqa: PLC0415

        if not CRASH_LOG_PATH.exists():
            return None
        age = time.time() - CRASH_LOG_PATH.stat().st_mtime
        if age > max_age_seconds:
            return None
        notice = (
            f"Previous crash detected. "
            f"Crash log saved at: {CRASH_LOG_PATH}\n"
            f"If this keeps happening, share the log with support."
        )
        if not _terminal_input_usable():
            return notice
        return notice
    except Exception:  # noqa: BLE001
        return None


def termux_exit_clean() -> None:
    """Bypass Python finalization on Termux to avoid libc-shutdown segfaults.

    Real-device evidence (probe ``p-47fa33562a``, ``p-bdc29e9af9``): on Termux
    + Python 3.13, clean menu/supervisor shutdown sometimes segfaults during
    interpreter teardown.  ``os._exit(0)`` skips the buggy native finalizers.

    Non-Termux contexts return without exiting so unittest can inspect results.
    """
    if not os.environ.get("TERMUX_VERSION"):
        return
    if os.environ.get("DENG_DISABLE_TERMUX_HARD_EXIT") == "1":
        return
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
    try:
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    os._exit(0)
