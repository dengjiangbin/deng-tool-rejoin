"""Fullscreen Android screenshot capture for the DENG monitor bridge.

v1.0.6 rewrite — "snapshot must finally work".

The monitor only needs ONE thing: a fullscreen PNG of the cloud phone's
display. The previous versions failed on most cloud phones because:

  * Termux's plain ``screencap -p`` is frequently blocked (no framebuffer
    access for the shell user) and produced empty / permission-denied
    output.
  * The only root fallback was ``su -c "screencap -p"`` to **stdout**, and
    it was gated behind ``DENG_REJOIN_SNAPSHOT_USE_SU=1`` — an env var the
    monitor autostart path never set. So root was never even tried.
  * The most reliable rooted path — ``su -c 'screencap -p /sdcard/x.png'``
    then read the file back — did not exist at all.

This module now tries a ladder of providers, automatically escalating to
root when the unprivileged path fails, and records rich diagnostics for
every attempt so the APK / probe can show EXACTLY why a capture failed.

Capture ladder (priority order)::

    1. normal_screencap        screencap -p                         (stdout)
    2. system_screencap        /system/bin/screencap -p             (stdout)
    3. root_screencap_stdout   su -c 'screencap -p'                 (stdout)
    4. root_screencap_file     su -c 'screencap -p <tmp>' + read    (file)
    5. root_system_screencap   su -c '/system/bin/screencap -p <tmp>' (file)

Safety:
    * Never uses OCR / accessibility / UIAutomator / MediaProjection.
    * Never saves tokens or secrets in the file or metadata.
    * Never deletes user data; only its own temp PNG under /sdcard.
    * Every rung carries a strict per-attempt timeout.
    * Never raises — callers get a structured result.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import SNAPSHOT_DIR

# PNG magic — every valid PNG starts with these 8 bytes.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Per-attempt hard timeout (seconds).
ATTEMPT_TIMEOUT = 12

# Prefer images larger than this; smaller valid PNGs are accepted but flagged.
SUSPICIOUS_MIN_BYTES = 10 * 1024  # 10 KB

# Root-side temp files. On some cloud phones /sdcard itself refuses writes,
# while Download remains shared and readable by Termux.
ROOT_TMP_PATH = "/sdcard/deng-monitor-snapshot.png"
ROOT_TMP_PATHS = (
    ROOT_TMP_PATH,
    "/sdcard/Download/deng-monitor-snapshot.png",
    "/storage/emulated/0/Download/deng-monitor-snapshot.png",
)

# Result enum values (kept as module constants so callers/tests can reference).
RESULT_SUCCESS = "success"
RESULT_NO_SCREENCAP = "failed_no_screencap"
RESULT_ROOT_DENIED = "failed_root_denied"
RESULT_EMPTY_OUTPUT = "failed_empty_output"
RESULT_INVALID_PNG = "failed_invalid_png"
RESULT_TIMEOUT = "failed_timeout"
RESULT_UNKNOWN = "failed_unknown"


@dataclass
class ProviderAttempt:
    """Diagnostics for a single capture attempt (one rung of the ladder)."""

    provider: str
    exit_code: int | None = None
    byte_length: int = 0
    png_valid: bool = False
    timeout: bool = False
    found: bool = True            # binary/command existed
    stderr: str | None = None     # first safe line of stderr
    note: str | None = None       # short, safe human note

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "exit_code": self.exit_code,
            "byte_length": int(self.byte_length or 0),
            "png_valid": bool(self.png_valid),
            "timeout": bool(self.timeout),
            "found": bool(self.found),
            "stderr": self.stderr,
            "note": self.note,
        }


@dataclass
class SnapshotCapture:
    """Structured capture result handed back to the bridge.

    ``data`` is the validated PNG bytes on success, else ``None``.
    Everything else is safe-to-surface diagnostics — no secrets.
    """

    data: bytes | None = None
    mime: str = "image/png"
    path: Path | None = None
    result: str = RESULT_UNKNOWN
    provider: str | None = None       # provider that produced the bytes
    byte_length: int = 0
    png_valid: bool = False
    suspicious_small: bool = False
    screencap_found: bool = False
    su_available: bool = False
    root_granted: bool | None = None
    error: str | None = None
    attempts: list[ProviderAttempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.data is not None and self.png_valid

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "provider": self.provider,
            "byte_length": int(self.byte_length or 0),
            "png_valid": bool(self.png_valid),
            "suspicious_small": bool(self.suspicious_small),
            "screencap_found": bool(self.screencap_found),
            "su_available": bool(self.su_available),
            "root_granted": self.root_granted,
            "error": self.error,
            "attempts": [a.to_safe_dict() for a in self.attempts],
        }


# ── helpers ──────────────────────────────────────────────────────────────────


def _su_available() -> bool:
    if shutil.which("su"):
        return True
    for cand in ("/system/bin/su", "/system/xbin/su", "/sbin/su", "/su/bin/su"):
        if os.path.isfile(cand):
            return True
    return False


def _root_disabled() -> bool:
    """Root escalation is ON by default in v1.0.6. Operators can disable it
    by setting DENG_REJOIN_SNAPSHOT_USE_SU to an explicit falsey value."""
    v = os.environ.get("DENG_REJOIN_SNAPSHOT_USE_SU")
    if v is None:
        return False
    return v.strip().lower() in {"0", "false", "no", "off"}


def _first_safe_stderr_line(stderr: bytes | None) -> str | None:
    if not stderr:
        return None
    try:
        text = stderr.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    for line in text.splitlines():
        line = line.strip()
        if line:
            # Cap and strip anything that could carry a path/secret-ish token.
            return line[:160]
    return None


def _looks_like_root_denied(exit_code: int | None, stderr: str | None) -> bool:
    if exit_code is not None and exit_code in (1, 13, 126, 127, 255):
        # Could be denial; corroborate with stderr where possible.
        pass
    blob = (stderr or "").lower()
    return any(
        frag in blob
        for frag in ("permission denied", "not allowed", "access denied",
                     "su: ", "not permitted", "denied", "no su")
    )


def _extract_png(raw: bytes) -> bytes | None:
    """Return PNG bytes from ``raw``, trimming any leading shell/su banner.

    Some ``su`` implementations print a banner on stdout before the real
    payload. We locate the PNG signature and slice from there. Returns
    ``None`` when there's no signature at all.
    """
    if not raw:
        return None
    if raw.startswith(PNG_SIGNATURE):
        return raw
    idx = raw.find(PNG_SIGNATURE)
    if idx > 0:
        return raw[idx:]
    return None


def _run(cmd: list[str], *, timeout: int = ATTEMPT_TIMEOUT) -> tuple[int | None, bytes, bytes, str | None]:
    """Run ``cmd`` capturing stdout+stderr. Returns (rc, stdout, stderr, kind).

    ``kind`` is None when the process ran, else one of "not_found",
    "timeout", "oserror". Never raises.
    """
    try:
        completed = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
        )
        return completed.returncode, completed.stdout or b"", completed.stderr or b"", None
    except FileNotFoundError:
        return None, b"", b"", "not_found"
    except subprocess.TimeoutExpired:
        return None, b"", b"", "timeout"
    except OSError:
        return None, b"", b"", "oserror"


def _capture_via_stdout(provider: str, cmd: list[str]) -> tuple[bytes | None, ProviderAttempt]:
    rc, out, err, kind = _run(cmd)
    attempt = ProviderAttempt(provider=provider, exit_code=rc)
    if kind == "not_found":
        attempt.found = False
        attempt.note = "binary_not_found"
        return None, attempt
    if kind == "timeout":
        attempt.timeout = True
        attempt.note = "timeout"
        return None, attempt
    if kind == "oserror":
        attempt.note = "oserror"
        return None, attempt
    attempt.stderr = _first_safe_stderr_line(err)
    png = _extract_png(out)
    attempt.byte_length = len(png) if png else len(out)
    if png is None:
        attempt.note = "no_png_signature" if out else "empty_output"
        return None, attempt
    attempt.png_valid = True
    attempt.byte_length = len(png)
    return png, attempt


def _capture_via_root_file(provider: str, remote_cmd_template: str) -> tuple[bytes | None, ProviderAttempt]:
    """Run a root command that writes a PNG to ROOT_TMP_PATH, then read it.

    The write goes through ``su -c '<screencap> -p <tmp>'`` and the read
    prefers a direct filesystem read (fast, no extra root call), falling
    back to ``su -c 'cat <tmp>'`` if the file isn't readable as the shell
    user. Always cleans up the temp file.
    """
    last_attempt: ProviderAttempt | None = None
    for tmp_path in ROOT_TMP_PATHS:
        remote_cmd = remote_cmd_template.format(path=tmp_path)
        write_rc, _w_out, w_err, w_kind = _run(["su", "-c", remote_cmd])
        attempt = ProviderAttempt(provider=provider, exit_code=write_rc)
        last_attempt = attempt
        attempt.note = f"path={tmp_path}"
        if w_kind == "not_found":
            attempt.found = False
            attempt.note = "su_not_found"
            return None, attempt
        if w_kind == "timeout":
            attempt.timeout = True
            attempt.note = f"timeout path={tmp_path}"
            return None, attempt
        if w_kind == "oserror":
            attempt.note = f"oserror path={tmp_path}"
            continue
        attempt.stderr = _first_safe_stderr_line(w_err)

        raw: bytes = b""
        try:
            raw = Path(tmp_path).read_bytes()
        except Exception:  # noqa: BLE001
            raw = b""
        if not raw:
            rc2, out2, _err2, kind2 = _run(["su", "-c", f"cat {tmp_path}"])
            if kind2 is None and out2:
                raw = out2

        _run(["su", "-c", f"rm -f {tmp_path}"], timeout=6)

        png = _extract_png(raw)
        attempt.byte_length = len(png) if png else len(raw)
        if png is None:
            attempt.note = f"{'no_png_signature' if raw else 'empty_output'} path={tmp_path}"
            continue
        attempt.png_valid = True
        attempt.byte_length = len(png)
        return png, attempt

    return None, last_attempt or ProviderAttempt(provider=provider, note="no_paths_tried")


def _build_providers() -> list[tuple[str, str, list[str] | None, str | None]]:
    """Return ladder rungs as (kind, provider, stdout_cmd, root_file_cmd).

    ``kind`` is "stdout" or "root_file". Exactly one of the command fields
    is populated per rung.
    """
    rungs: list[tuple[str, str, list[str] | None, str | None]] = []
    # 1 + 2 unprivileged stdout
    rungs.append(("stdout", "normal_screencap", ["screencap", "-p"], None))
    if os.path.isfile("/system/bin/screencap"):
        rungs.append(("stdout", "system_screencap", ["/system/bin/screencap", "-p"], None))
    # 3 + 4 + 5 root (auto unless explicitly disabled)
    if not _root_disabled() and _su_available():
        rungs.append(("stdout", "root_screencap_stdout", ["su", "-c", "screencap -p"], None))
        rungs.append(("root_file", "root_screencap_file", None, "screencap -p {path}"))
        rungs.append(("root_file", "root_system_screencap", None,
                      "/system/bin/screencap -p {path}"))
    return rungs


def capture_snapshot_detailed() -> SnapshotCapture:
    """Capture a fullscreen PNG via the provider ladder. Never raises."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    su_avail = _su_available()
    cap = SnapshotCapture(su_available=su_avail)

    any_screencap_found = False
    any_root_attempted = False
    any_root_denied = False
    any_timeout = False
    any_bytes = False

    for kind, provider, stdout_cmd, root_cmd in _build_providers():
        if kind == "stdout":
            png, attempt = _capture_via_stdout(provider, stdout_cmd or [])
        else:
            any_root_attempted = True
            png, attempt = _capture_via_root_file(provider, root_cmd or "")

        cap.attempts.append(attempt)
        if attempt.found:
            any_screencap_found = True
        if attempt.timeout:
            any_timeout = True
        if attempt.byte_length:
            any_bytes = True
        if provider.startswith("root") and (
            attempt.exit_code not in (0, None) or _looks_like_root_denied(attempt.exit_code, attempt.stderr)
        ) and not attempt.png_valid:
            any_root_denied = True

        if png is not None:
            # Success — persist to disk so callers that want a path get one.
            path = SNAPSHOT_DIR / f"snapshot-{int(time.time())}.png"
            try:
                path.write_bytes(png)
            except OSError:
                path = None
            cap.data = png
            cap.path = path
            cap.provider = provider
            cap.byte_length = len(png)
            cap.png_valid = True
            cap.screencap_found = True
            cap.suspicious_small = len(png) < SUSPICIOUS_MIN_BYTES
            cap.result = RESULT_SUCCESS
            if provider.startswith("root"):
                cap.root_granted = True
            return cap

    # No provider produced a valid PNG — classify the failure.
    cap.screencap_found = any_screencap_found
    if any_root_attempted:
        cap.root_granted = False if any_root_denied else cap.root_granted
    if not any_screencap_found and not su_avail:
        cap.result = RESULT_NO_SCREENCAP
        cap.error = "screencap binary not found and su unavailable"
    elif any_root_attempted and any_root_denied and not any_bytes:
        cap.result = RESULT_ROOT_DENIED
        cap.error = "root screencap denied"
    elif any_timeout and not any_bytes:
        cap.result = RESULT_TIMEOUT
        cap.error = "screencap timed out"
    elif any_bytes:
        # We got bytes from at least one provider but none were valid PNG.
        cap.result = RESULT_INVALID_PNG
        cap.error = "screencap did not return valid PNG bytes"
    elif not any_bytes and any_screencap_found:
        cap.result = RESULT_EMPTY_OUTPUT
        cap.error = "screencap returned empty output"
    else:
        cap.result = RESULT_UNKNOWN
        cap.error = "snapshot capture failed"
    return cap


def _provider_command_str(kind: str, stdout_cmd: list[str] | None, root_cmd: str | None) -> str:
    if kind == "stdout":
        return " ".join(stdout_cmd or [])
    return f"su -c '{root_cmd}'  (then read back first writable shared path)"


def snapshot_test_report() -> dict[str, Any]:
    """Run EVERY provider rung (not just until first success) and return a
    full per-provider diagnostic report.

    Used by ``deng-rejoin monitor snapshot-test``. Never raises.
    """
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    su_avail = _su_available()
    rows: list[dict[str, Any]] = []
    chosen: str | None = None
    chosen_bytes = 0
    for kind, provider, stdout_cmd, root_cmd in _build_providers():
        cmd_str = _provider_command_str(kind, stdout_cmd, root_cmd)
        if kind == "stdout":
            png, attempt = _capture_via_stdout(provider, stdout_cmd or [])
        else:
            png, attempt = _capture_via_root_file(provider, root_cmd or "")
        suspicious = bool(png is not None and len(png) < SUSPICIOUS_MIN_BYTES)
        rows.append({
            "provider": provider,
            "command": cmd_str,
            "exit_code": attempt.exit_code,
            "timeout_seconds": ATTEMPT_TIMEOUT,
            "timed_out": bool(attempt.timeout),
            "found": bool(attempt.found),
            "stderr": attempt.stderr,
            "byte_length": int(attempt.byte_length or 0),
            "png_valid": bool(attempt.png_valid),
            "suspicious_small": suspicious,
            "note": attempt.note,
        })
        if png is not None and chosen is None:
            chosen = provider
            chosen_bytes = len(png)
    return {
        "su_available": bool(su_avail),
        "root_disabled": bool(_root_disabled()),
        "providers": rows,
        "selected_provider": chosen,
        "selected_bytes": int(chosen_bytes),
        "final_result": RESULT_SUCCESS if chosen else RESULT_UNKNOWN,
    }


def capture_snapshot() -> tuple[Path | None, str]:
    """Backward-compatible wrapper used by the webhook path and tests.

    Returns ``(path, "snapshot captured")`` on success or
    ``(None, "<safe error message>")`` on failure. Never raises.
    """
    cap = capture_snapshot_detailed()
    if cap.ok and cap.path is not None:
        return cap.path, "snapshot captured"
    return None, (cap.error or cap.result or "screenshot unavailable")


def cleanup_old_snapshots(max_age_seconds: int = 300) -> None:
    cutoff = time.time() - max(30, int(max_age_seconds))
    if not SNAPSHOT_DIR.exists():
        return
    for item in SNAPSHOT_DIR.glob("snapshot-*.png"):
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
        except OSError:
            continue


__all__ = [
    "PNG_SIGNATURE",
    "ROOT_TMP_PATHS",
    "ProviderAttempt",
    "SnapshotCapture",
    "RESULT_SUCCESS",
    "RESULT_NO_SCREENCAP",
    "RESULT_ROOT_DENIED",
    "RESULT_EMPTY_OUTPUT",
    "RESULT_INVALID_PNG",
    "RESULT_TIMEOUT",
    "RESULT_UNKNOWN",
    "capture_snapshot",
    "capture_snapshot_detailed",
    "snapshot_test_report",
    "cleanup_old_snapshots",
]
