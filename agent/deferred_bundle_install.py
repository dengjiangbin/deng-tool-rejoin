"""Deferred-install helpers — kept for backward compatibility and utility functions.

The active installer (``/install/test/latest``) now downloads the full package
directly during ``bash install.sh`` via :func:`agent.bootstrap_installer.render_direct_install_bootstrap`.
No license key is required at install time; license verification happens inside the
real tool on first run (inside the menu flow of :mod:`agent.commands`).

This module is retained for:
- ``resolve_install_api`` and HTTP helper utilities used by other code.
- Graceful handling of old launcher bundles that still have ``.install_requested``.

Stdlib only — safe inside the minimal launcher tarball.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# Official production API host (no localhost; used when env and file are absent).
DEFAULT_PUBLIC_INSTALL_API = "https://rejoin.deng.my.id"

# Installer User-Agent — identifies the DENG Rejoin installer to the server.
# Cloudflare Browser Integrity Check blocks the default Python-urllib UA with
# error 1010.  Using a descriptive but non-browser UA passes the check.
_INSTALLER_UA = "deng-rejoin-installer/1.0"


def _app_home() -> Path:
    raw = (os.environ.get("DENG_REJOIN_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".deng-tool" / "rejoin"


def _install_api_file(app_home: Path) -> Path:
    return app_home / ".install_api"


def resolve_install_api(app_home: Path | None = None) -> str:
    """Public base URL for install/authorize (no trailing slash).

    Priority:
    1. ``DENG_REJOIN_INSTALL_API`` environment variable
    2. ``$DENG_REJOIN_HOME/.install_api`` (first line), written by the shell installer
    3. :data:`DEFAULT_PUBLIC_INSTALL_API`
    """
    env_raw = (os.environ.get("DENG_REJOIN_INSTALL_API") or "").strip()
    if env_raw:
        return env_raw.rstrip("/")

    home = app_home if app_home is not None else _app_home()
    p = _install_api_file(home)
    if p.is_file():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        line = text.strip().splitlines()[0].strip() if text.strip() else ""
        if line:
            return line.rstrip("/")

    return DEFAULT_PUBLIC_INSTALL_API.rstrip("/")


def _requested_path(app_home: Path) -> Path:
    return app_home / ".install_requested"


def _http_json_post(url: str, payload: dict) -> tuple[int, dict, str]:
    """POST JSON; return status, parsed JSON object (may be empty), and raw response text.

    Sends a descriptive User-Agent so Cloudflare Browser Integrity Check does not
    block the request with error 1010 (which it does for the default Python-urllib UA).
    """
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _INSTALLER_UA,
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                body = {}
            return int(getattr(resp, "status", 200) or 200), body, raw
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = {}
        return int(exc.code), body, raw


def _safe_response_snippet(raw: str, max_len: int = 500) -> str:
    if not raw:
        return ""
    cleaned = " ".join(raw.split())
    if len(cleaned) > max_len:
        return cleaned[:max_len] + "…"
    return cleaned


def _is_cloudflare_block(status: int, body: dict, raw: str) -> bool:
    """Detect a Cloudflare edge-block response (BIC / WAF / Bot Fight / 1010).

    Cloudflare returns HTML when it blocks a request — the body is never valid JSON
    from our backend.  We detect this by looking for characteristic CF markers in
    the raw response text combined with the HTTP 403 status.
    """
    if status != 403:
        return False
    raw_lower = (raw or "").lower()
    if body:
        return False
    cf_markers = ("cloudflare", "error code: 1010", "error code:1010", "cf-ray", "attention required")
    return any(m in raw_lower for m in cf_markers) or "<html" in raw_lower


def _is_server_side_error(status: int, body: dict, raw: str) -> bool:  # noqa: ARG001
    """Return True when retrying the same key cannot resolve the error.

    Server-side errors (build not configured, artifact missing, server unavailable)
    are permanent until the server is fixed — re-prompting the user for the same
    key would loop forever and confuse them.
    """
    if status >= 500:
        return True
    result = str(body.get("result") or "").strip().lower()
    if result in {"server_unavailable", "forbidden", "no_release", "missing_version"}:
        return True
    if result == "not_found":
        msg_lower = str(body.get("message") or "").strip().lower()
        key_phrases = ("key not found", "check the key", "not found. check")
        return not any(p in msg_lower for p in key_phrases)
    return False


def describe_install_authorize_failure(
    status: int,
    body: dict | None,
    raw: str | None,
) -> str:
    """One-line message for stderr when POST /api/install/authorize did not return active.

    Uses ``message``, ``error``, ``detail``, ``result``, ``code`` from JSON when present.
    If the response is a Cloudflare edge-block (error code 1010 / BIC / WAF), returns a
    clear human-readable explanation instead of dumping HTML.  Never echoes secrets that
    are not in the server response (license key is only client-side and not passed here).
    """
    body = body or {}
    raw = raw or ""

    if _is_cloudflare_block(status, body, raw):
        return (
            "Install request was blocked by server protection (Cloudflare) before license "
            "verification could run.\n"
            "This is NOT a license key issue — the network/security layer rejected the "
            f"request before it reached the server [HTTP {status} — error code 1010].\n"
            "Contact support at https://rejoin.deng.my.id if this persists."
        )

    msg = str(body.get("message") or "").strip()
    err = str(body.get("error") or "").strip()
    detail = str(body.get("detail") or "").strip()
    res = str(body.get("result") or "").strip()
    code = body.get("code")

    parts: list[str] = []

    if msg and msg.lower() not in {"install denied.", "install denied"}:
        parts.append(msg)
    if err and err not in parts and err.lower() not in " ".join(parts).lower():
        parts.append(err)
    if detail and detail not in " ".join(parts):
        parts.append(detail)

    if res and res != "active":
        blob = "; ".join(parts)
        if res not in blob and (not msg or res not in msg):
            parts.append(f"result={res}")

    if code is not None and str(code).strip() != "":
        c_str = str(code).strip()
        if c_str not in " ".join(parts):
            parts.append(f"code={c_str}")

    if not parts:
        snippet = _safe_response_snippet(raw)
        if snippet:
            parts.append(f"response: {snippet}")
        elif status:
            parts.append(f"empty body (HTTP {status})")

    headline = "; ".join(parts) if parts else "unknown error"
    out = f"Install denied: {headline}"
    if status >= 400 and f"HTTP {status}" not in out and f"{status}" not in headline:
        out += f" [HTTP {status}]"
    return out


def _http_download(url: str, dest: Path) -> None:
    req = Request(url, headers={"User-Agent": _INSTALLER_UA})
    with urlopen(req, timeout=300) as resp:  # noqa: S310
        dest.write_bytes(resp.read())


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_tar_gz(archive: Path, dest: Path) -> None:
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(dest)


def run() -> int:
    """Handle the old launcher stub entrypoint.

    The current installer downloads the full package directly during ``bash install.sh``
    and does not use the deferred-install mechanism.  If this function is reached, the
    user has an **old launcher bundle** (installed before the direct-install update).

    Direct them to re-run the installer to get the full package.
    """
    app_home = _app_home()
    marker = _requested_path(app_home)

    reinstall_cmd = "curl -fsSL https://rejoin.deng.my.id/install/test/latest -o install.sh && bash install.sh"

    if marker.is_file():
        print(
            "Your launcher is outdated and cannot complete the install.\n"
            "Please re-run the installer to download the full DENG Tool: Rejoin package:\n"
            f"\n  {reinstall_cmd}\n",
            file=sys.stderr,
        )
    else:
        # No marker — tool may already be installed but pointing to this stub.
        main_py = app_home / "agent" / "deng_tool_rejoin.py"
        if main_py.is_file():
            # Real entrypoint is present; exec it directly.
            exe = sys.executable
            os.execv(exe, [exe, str(main_py), *sys.argv[1:]])
            raise RuntimeError("execv returned unexpectedly")  # pragma: no cover
        print(
            "DENG Tool: Rejoin is not fully installed.\n"
            "Run the installer to set up the full package:\n"
            f"\n  {reinstall_cmd}\n",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
