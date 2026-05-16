"""Safe HTTPS helper for DENG Tool: Rejoin.

On Termux/Android (TERMUX_VERSION set, or DENG_HTTP_BACKEND=curl):
    All HTTPS JSON calls run through ``curl`` as a subprocess.
    curl executes in a *separate OS process* — if OpenSSL inside curl
    segfaults (SIGSEGV), only the curl child dies.  The Python main
    process survives and returns a clean "network error" to the caller.

On other platforms (CI, Windows, macOS, or DENG_HTTP_BACKEND=python):
    Uses Python's built-in ``urllib.request`` (no third-party dependency).

Backend selection (in order):
    1. DENG_HTTP_BACKEND=curl   → always curl
    2. DENG_HTTP_BACKEND=python → always urllib
    3. TERMUX_VERSION set       → curl
    4. Otherwise                → urllib

Public API:
    post_json(url, data, *, headers=None, timeout=30) -> dict
    get_json(url, *, headers=None, timeout=30)        -> dict

Both raise SafeHttpError subclasses on failure.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any

from .constants import VERSION

_log = logging.getLogger("deng.rejoin.safe_http")

# ── Backend selection ─────────────────────────────────────────────────────────

_CURL_MISSING_MSG = (
    "curl is required for network access on this device.\n"
    "Install it with:  pkg install -y curl"
)

_SHARED_HEADERS: dict[str, str] = {
    "User-Agent": f"deng-rejoin-installer/{VERSION}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# Maximum response body accepted (256 KiB) — prevents OOM from runaway server.
_MAX_RESPONSE_BYTES = 256 * 1024


def _http_backend() -> str:
    """Return the active HTTP backend identifier: ``'curl'`` or ``'python'``."""
    override = os.environ.get("DENG_HTTP_BACKEND", "auto").lower().strip()
    if override == "curl":
        return "curl"
    if override == "python":
        return "python"
    # auto: use curl on Termux to avoid Python ssl/OpenSSL SIGSEGV.
    if os.environ.get("TERMUX_VERSION"):
        return "curl"
    return "python"


def _curl_available() -> bool:
    return shutil.which("curl") is not None


# ── Public exceptions ─────────────────────────────────────────────────────────


class SafeHttpError(Exception):
    """Base class for all safe_http errors."""


class SafeHttpNetworkError(SafeHttpError):
    """Connection failure, timeout, curl unavailable, or child crash."""


class SafeHttpJsonError(SafeHttpError):
    """Server returned non-JSON or malformed JSON."""


class SafeHttpStatusError(SafeHttpError):
    """Server returned an HTTP error status (4xx / 5xx).

    Attributes:
        status_code: Integer HTTP status code.
        body:        Raw response body (may be empty).
    """

    def __init__(self, status_code: int, body: str = "") -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.body = body


# ── curl backend ──────────────────────────────────────────────────────────────


def _build_curl_header_args(headers: dict[str, str]) -> list[str]:
    args: list[str] = []
    for k, v in headers.items():
        args.extend(["-H", f"{k}: {v}"])
    return args


def _run_curl(
    args: list[str],
    *,
    stdin_bytes: bytes | None = None,
    timeout: int = 30,
) -> tuple[int, bytes]:
    """Run curl and return (http_status_code, response_body_bytes).

    curl is invoked with:
      -s              silent (no progress bar)
      -w '\\n%{http_code}'   append HTTP status code on last line
      --max-time      hard timeout (server response + transfer)
      --max-filesize  cap download size to _MAX_RESPONSE_BYTES

    Raises SafeHttpNetworkError on:
      - curl not found
      - curl child process exited with signal (SIGSEGV etc.)
      - process timeout (parent-side Python timeout > curl timeout)
      - any OS error launching the subprocess
    """
    if not _curl_available():
        raise SafeHttpNetworkError(_CURL_MISSING_MSG)

    full_cmd = [
        "curl",
        "-s",                                       # silent
        "--max-time", str(max(5, timeout)),         # hard timeout
        "--max-filesize", str(_MAX_RESPONSE_BYTES), # cap body
        "-w", "\n%{http_code}",                     # append status code
    ] + args

    try:
        proc = subprocess.run(
            full_cmd,
            input=stdin_bytes,
            capture_output=True,
            timeout=timeout + 10,   # python-side timeout > curl --max-time
            shell=False,            # never shell=True
        )
    except subprocess.TimeoutExpired:
        raise SafeHttpNetworkError("Network request timed out (curl process timeout).") from None
    except (OSError, FileNotFoundError) as exc:
        raise SafeHttpNetworkError(f"Failed to launch curl: {exc}") from exc

    # Negative returncode means the child was killed by a signal (e.g. SIGSEGV).
    if proc.returncode < 0:
        sig = -proc.returncode
        _log.debug("curl killed by signal %d", sig)
        raise SafeHttpNetworkError(
            f"Network check crashed safely (signal {sig}). Please retry."
        )

    # Non-zero curl exit codes indicate network/protocol errors.
    if proc.returncode != 0:
        stderr_hint = (proc.stderr or b"").decode("utf-8", errors="replace")[:200]
        curl_errors = {
            6: "Could not resolve host",
            7: "Failed to connect",
            22: "HTTP error returned",
            28: "Connection timed out",
            35: "SSL handshake failed",
            52: "Empty reply from server",
            56: "Network data receiving error",
            60: "SSL certificate verification failed",
        }
        reason = curl_errors.get(proc.returncode, f"curl error {proc.returncode}")
        _log.debug("curl exit %d: %s | %s", proc.returncode, reason, stderr_hint)
        raise SafeHttpNetworkError(f"Network error: {reason}.")

    raw = proc.stdout or b""
    # Split on LAST newline to separate body from the appended status code.
    last_nl = raw.rfind(b"\n")
    if last_nl == -1:
        body_bytes = raw
        status_bytes = b"0"
    else:
        body_bytes = raw[:last_nl]
        status_bytes = raw[last_nl + 1:]

    try:
        http_status = int(status_bytes.strip())
    except ValueError:
        http_status = 0

    return http_status, body_bytes


def _curl_post_json(
    url: str,
    data: dict[str, Any],
    *,
    extra_headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    headers = {**_SHARED_HEADERS, **(extra_headers or {})}
    header_args = _build_curl_header_args(headers)
    post_args = ["-X", "POST", "--data-binary", "@-"] + header_args + [url]

    json_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")
    http_status, body_bytes = _run_curl(post_args, stdin_bytes=json_bytes, timeout=timeout)

    body_text = body_bytes.decode("utf-8", errors="replace")[:_MAX_RESPONSE_BYTES]

    if http_status >= 400:
        # Try to parse error JSON for useful result/message fields.
        try:
            parsed: dict[str, Any] = json.loads(body_text)
            if isinstance(parsed, dict) and parsed.get("result"):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        raise SafeHttpStatusError(http_status, body_text)

    # 2xx with empty body (e.g. HTTP 204 No Content from Discord webhooks).
    if not body_text.strip():
        return {}

    try:
        return json.loads(body_text)  # type: ignore[return-value]
    except (json.JSONDecodeError, ValueError) as exc:
        raise SafeHttpJsonError(f"Invalid JSON response: {exc}") from exc


def _curl_get_json(
    url: str,
    *,
    extra_headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    headers = {k: v for k, v in _SHARED_HEADERS.items() if k != "Content-Type"}
    if extra_headers:
        headers.update(extra_headers)
    header_args = _build_curl_header_args(headers)
    get_args = header_args + [url]

    http_status, body_bytes = _run_curl(get_args, timeout=timeout)
    body_text = body_bytes.decode("utf-8", errors="replace")[:_MAX_RESPONSE_BYTES]

    if http_status >= 400:
        try:
            parsed: dict[str, Any] = json.loads(body_text)
            if isinstance(parsed, dict) and parsed.get("result"):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        raise SafeHttpStatusError(http_status, body_text)

    if not body_text.strip():
        return {}

    try:
        return json.loads(body_text)  # type: ignore[return-value]
    except (json.JSONDecodeError, ValueError) as exc:
        raise SafeHttpJsonError(f"Invalid JSON response: {exc}") from exc


# ── Python urllib backend ─────────────────────────────────────────────────────


def _python_post_json(
    url: str,
    data: dict[str, Any],
    *,
    extra_headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    headers = {**_SHARED_HEADERS, **(extra_headers or {})}
    body = json.dumps(data, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read(_MAX_RESPONSE_BYTES)
            if not raw.strip():
                return {}
            return json.loads(raw)  # type: ignore[return-value]
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(4096).decode("utf-8", errors="replace")
            parsed: dict[str, Any] = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("result"):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        raise SafeHttpStatusError(exc.code, "") from exc
    except urllib.error.URLError as exc:
        raise SafeHttpNetworkError(f"Network error: {exc.reason}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise SafeHttpJsonError(f"Invalid JSON response: {exc}") from exc
    except OSError as exc:
        raise SafeHttpNetworkError(f"Network I/O error: {exc}") from exc


def _python_get_json(
    url: str,
    *,
    extra_headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    headers = {k: v for k, v in _SHARED_HEADERS.items() if k != "Content-Type"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read(_MAX_RESPONSE_BYTES)
            return json.loads(raw)  # type: ignore[return-value]
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(4096).decode("utf-8", errors="replace")
            parsed: dict[str, Any] = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("result"):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        raise SafeHttpStatusError(exc.code, "") from exc
    except urllib.error.URLError as exc:
        raise SafeHttpNetworkError(f"Network error: {exc.reason}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise SafeHttpJsonError(f"Invalid JSON response: {exc}") from exc
    except OSError as exc:
        raise SafeHttpNetworkError(f"Network I/O error: {exc}") from exc


# ── Public API ────────────────────────────────────────────────────────────────


def post_json(
    url: str,
    data: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """POST ``data`` as JSON to ``url``; return parsed JSON response dict.

    Selects the backend automatically (curl on Termux, urllib elsewhere).
    On Termux, a curl SIGSEGV cannot kill the Python main process.

    Raises:
        SafeHttpNetworkError: connection/timeout/curl-crash.
        SafeHttpStatusError:  4xx/5xx HTTP status.
        SafeHttpJsonError:    malformed JSON from server.
    """
    backend = _http_backend()
    _log.debug("safe_http POST %s (backend=%s)", url, backend)
    if backend == "curl":
        return _curl_post_json(url, data, extra_headers=headers, timeout=timeout)
    return _python_post_json(url, data, extra_headers=headers, timeout=timeout)


def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """GET ``url``; parse and return JSON response dict.

    Selects backend automatically.

    Raises:
        SafeHttpNetworkError: connection/timeout/curl-crash.
        SafeHttpStatusError:  4xx/5xx HTTP status.
        SafeHttpJsonError:    malformed JSON from server.
    """
    backend = _http_backend()
    _log.debug("safe_http GET %s (backend=%s)", url, backend)
    if backend == "curl":
        return _curl_get_json(url, extra_headers=headers, timeout=timeout)
    return _python_get_json(url, extra_headers=headers, timeout=timeout)
