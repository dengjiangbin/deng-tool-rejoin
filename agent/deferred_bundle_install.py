"""First-run: complete protected-bundle install after unattended shell bootstrap.

The shell installer only extracts a small launcher and writes ``.install_requested``.
This module prompts for a license, calls ``POST /api/install/authorize``, downloads the
tokenized tarball, extracts into ``DENG_REJOIN_HOME``, then re-execs the real entrypoint.

Stdlib only — safe inside the minimal launcher tarball.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.error
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
    """Run deferred install; on success replace stub and re-exec ``deng_tool_rejoin``."""
    app_home = _app_home()
    marker = _requested_path(app_home)
    if not marker.is_file():
        print(
            "This install is already complete, or .install_requested is missing.\n"
            "Run: deng-rejoin",
            file=sys.stderr,
        )
        return 1

    requested = marker.read_text(encoding="utf-8", errors="replace").strip().splitlines()[0].strip()
    if not requested:
        print("Invalid install state (.install_requested is empty).", file=sys.stderr)
        return 1

    base = resolve_install_api(app_home)

    bs_path = app_home / ".bootstrap_session"
    bootstrap_session = ""
    if bs_path.is_file():
        bootstrap_session = bs_path.read_text(encoding="utf-8", errors="replace").strip()

    while True:
        try:
            raw = input("Paste your license key: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            return 130
        if not raw:
            print("License key is required. Try again or press Ctrl+C to exit.")
            continue

        install_hash = (os.environ.get("DENG_INSTALL_ID_HASH") or "").strip()
        payload: dict = {
            "license_key": raw,
            "requested_version": requested,
            "install_id_hash": install_hash,
        }
        if bootstrap_session:
            payload["bootstrap_session"] = bootstrap_session
        auth_url = f"{base}/api/install/authorize"
        try:
            status, body, resp_raw = _http_json_post(auth_url, payload)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"Network error: {exc}", file=sys.stderr)
            return 1

        if not (status == 200 and body.get("result") == "active"):
            msg = describe_install_authorize_failure(status, body, resp_raw)
            print(msg, file=sys.stderr)
            if _is_cloudflare_block(status, body, resp_raw):
                return 1
            continue

        dl_url = (body.get("download_url") or "").strip()
        if not dl_url:
            print("Server returned no download URL.", file=sys.stderr)
            return 1
        want_sha = str(body.get("sha256") or "").strip()

        tmp = Path(tempfile.mkdtemp(prefix="deng-full-"))
        arc = tmp / "bundle.tar.gz"
        try:
            _http_download(dl_url, arc)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"Download failed: {exc}", file=sys.stderr)
            shutil.rmtree(tmp, ignore_errors=True)
            return 1

        if want_sha and _sha256_file(arc) != want_sha.lower():
            print("SHA256 mismatch — download corrupted or incomplete.", file=sys.stderr)
            shutil.rmtree(tmp, ignore_errors=True)
            return 1

        app_home.mkdir(parents=True, exist_ok=True)
        try:
            _extract_tar_gz(arc, app_home)
        except (OSError, tarfile.TarError) as exc:
            print(f"Extract failed: {exc}", file=sys.stderr)
            shutil.rmtree(tmp, ignore_errors=True)
            return 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        try:
            marker.unlink()
        except OSError:
            pass
        try:
            if bs_path.is_file():
                bs_path.unlink()
        except OSError:
            pass

        exe = sys.executable
        main_py = app_home / "agent" / "deng_tool_rejoin.py"
        if not main_py.is_file():
            print("Extracted bundle is missing agent/deng_tool_rejoin.py.", file=sys.stderr)
            return 1

        os.execv(exe, [exe, str(main_py), *sys.argv[1:]])
        raise RuntimeError("execv returned unexpectedly")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(run())
