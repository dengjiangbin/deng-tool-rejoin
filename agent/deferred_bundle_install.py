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


def _http_json_post(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
            body = json.loads(raw) if raw.strip() else {}
            return int(getattr(resp, "status", 200) or 200), body
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = {}
        return int(exc.code), body


def _http_download(url: str, dest: Path) -> None:
    with urlopen(url, timeout=300) as resp:  # noqa: S310
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
            status, body = _http_json_post(auth_url, payload)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"Network error: {exc}", file=sys.stderr)
            return 1

        if status >= 400 or body.get("result") != "active":
            print((body.get("message") or "Install denied.").strip(), file=sys.stderr)
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
