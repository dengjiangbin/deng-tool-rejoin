"""Client-side licensed package updater for DENG Tool: Rejoin.

Flow
────
1. Read license config (key, server_url, install_id, channel) from config.json.
2. POST /api/download/authorize  → download_token + package metadata.
3. Compare remote version with local VERSION.
4. If newer (or --force): GET /api/download/package/<token> → stream-download.
5. Verify SHA-256 of downloaded zip.
6. Backup current install (skip data/, logs/, config.json).
7. Extract zip into install dir (skip .env entries, skip path traversal).
8. Apply secure permissions.
9. If extraction fails: rollback to backup automatically.

Security
────────
• Never stores or transmits Supabase service role key.
• Never stores or transmits Discord bot token.
• Never stores or transmits raw install_id — only its SHA-256 hash is sent.
• Full license key is never logged; masked version only.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Exceptions ─────────────────────────────────────────────────────────────────


class UpdaterError(RuntimeError):
    """Raised when an update step fails unrecoverably."""


class HashMismatchError(UpdaterError):
    """Downloaded package SHA-256 does not match the manifest."""


class LicenseCheckError(UpdaterError):
    """License API rejected the request (wrong_device, revoked, expired, etc.)."""


# ── Internal helpers ───────────────────────────────────────────────────────────


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _mask_key(key: str) -> str:
    parts = (key or "").split("-")
    if len(parts) >= 5:
        return f"{parts[0]}-{parts[1]}...{parts[-1]}"
    return (key[:8] + "...") if len(key) > 8 else "***"


def _http_post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int = 30,
    bearer_token: str = "",
) -> dict[str, Any]:
    """POST *payload* as JSON to *url* and return the parsed response.

    Raises UpdaterError on HTTP errors or network failures.
    """
    data = json.dumps(payload).encode("utf-8")
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "DENG-Tool-Rejoin-Updater/1.0",
    }
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read(256 * 1024)  # cap at 256 KB
            return json.loads(body)  # type: ignore[return-value]
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        raise UpdaterError(f"HTTP {exc.code} from {url}: {body[:200]}") from exc
    except urllib.error.URLError as exc:
        raise UpdaterError(f"Network error reaching {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise UpdaterError(f"Invalid JSON from {url}: {exc}") from exc


def _http_download(
    url: str,
    dest: Path,
    *,
    bearer_token: str = "",
    timeout: int = 120,
) -> None:
    """Stream-download *url* into *dest* file.

    Raises UpdaterError on HTTP/network errors.
    """
    headers: dict[str, str] = {"User-Agent": "DENG-Tool-Rejoin-Updater/1.0"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:  # noqa: S310
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                out.write(chunk)
    except urllib.error.HTTPError as exc:
        raise UpdaterError(f"Download HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise UpdaterError(f"Download network error: {exc.reason}") from exc


# ── Version comparison ─────────────────────────────────────────────────────────


def _parse_version(ver: str) -> tuple[int, ...]:
    """Parse 'X.Y.Z' → (X, Y, Z). Non-numeric segments default to 0."""
    parts: list[int] = []
    for segment in (ver or "0").strip().split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer_version(remote: str, local: str) -> bool:
    """Return True if *remote* version is strictly newer than *local*."""
    return _parse_version(remote) > _parse_version(local)


# ── License config I/O ────────────────────────────────────────────────────────


def load_license_config(config_path: Path) -> dict[str, Any]:
    """Read the ``license`` section from agent config.json.

    Returns an empty dict if the file is missing or has no ``license`` key.
    Raises UpdaterError if the file cannot be parsed.
    """
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdaterError(f"Cannot read config at {config_path}: {exc}") from exc
    return data.get("license", {})


def save_license_status(config_path: Path, status: str) -> None:
    """Update ``license.last_status`` and ``last_check_at`` in config.json.

    Non-fatal: errors are silently ignored to not interrupt normal operation.
    """
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        lic = data.setdefault("license", {})
        lic["last_status"] = status
        lic["last_check_at"] = _utc_now()
        config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError):
        pass


def _save_install_id(config_path: Path, install_id: str) -> None:
    """Persist a newly-generated install_id into the license config block."""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        data.setdefault("license", {})["install_id"] = install_id
        config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError):
        pass


def _ensure_install_id(config_path: Path, lic: dict[str, Any]) -> str:
    """Return the configure install_id, generating and saving one if absent."""
    install_id = (lic.get("install_id") or "").strip()
    if not install_id:
        install_id = uuid.uuid4().hex
        _save_install_id(config_path, install_id)
    return install_id


# ── Core API calls ────────────────────────────────────────────────────────────


def request_download_token(
    server_url: str,
    key: str,
    install_id: str,
    device_model: str,
    app_version: str,
    channel: str = "stable",
    *,
    api_secret: str = "",
) -> dict[str, Any]:
    """Call ``POST /api/download/authorize`` and return the response dict.

    On success the dict includes: ``download_token``, ``expires_at``,
    ``version``, ``channel``, ``filename``, ``sha256``, ``size_bytes``,
    ``download_url``.

    Raises:
        LicenseCheckError: if the server rejects with a known result code.
        UpdaterError: on network failures or unexpected server errors.
    """
    # Hash install_id before sending — never transmit raw
    install_id_hash = hashlib.sha256(install_id.encode()).hexdigest()
    url = server_url.rstrip("/") + "/api/download/authorize"
    payload: dict[str, Any] = {
        "key": key,
        "install_id_hash": install_id_hash,
        "device_model": device_model[:120],
        "app_version": app_version[:40],
        "channel": channel,
    }
    resp = _http_post_json(url, payload, bearer_token=api_secret)

    result = resp.get("result", "")
    if result and result not in ("active", ""):
        raise LicenseCheckError(
            resp.get("message") or f"License check returned: {result}"
        )
    if "download_token" not in resp:
        raise UpdaterError(f"Server did not return a download_token: {list(resp.keys())}")
    return resp


# ── Package operations ─────────────────────────────────────────────────────────


def download_package(
    download_url: str,
    dest: Path,
    *,
    api_secret: str = "",
) -> None:
    """Download the package zip from *download_url* to *dest*."""
    _http_download(download_url, dest, bearer_token=api_secret)


def verify_package(path: Path, expected_sha256: str) -> None:
    """Verify SHA-256 of *path*. Raises HashMismatchError on mismatch."""
    from .security import compute_file_sha256

    actual = compute_file_sha256(path)
    if actual.lower() != expected_sha256.strip().lower():
        raise HashMismatchError(
            f"SHA-256 mismatch — expected {expected_sha256[:16]}..., "
            f"got {actual[:16]}... — refusing to install."
        )


def backup_install(install_dir: Path) -> Path:
    """Copy current install to a timestamped backup directory.

    Skips user-data directories (data/, logs/, run/, backups/, cache/) and
    the sensitive .env file so we backup only installable files.

    Returns the backup directory path.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = install_dir.parent / "backups" / f"pre-update-{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    _skip = {"data", "logs", "run", "backups", "cache", "keydb.json", ".env"}

    for item in install_dir.iterdir():
        if item.name in _skip or item.name.endswith(".env"):
            continue
        dest = backup_dir / item.name
        try:
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        except OSError:
            pass
    return backup_dir


def extract_package(zip_path: Path, install_dir: Path) -> list[str]:
    """Extract *zip_path* into *install_dir*.

    Security:
      - Skips any entry whose path starts with '/' or contains '..'.
      - Skips .env files regardless of path depth.
      - Verifies each resolved extractee path is within *install_dir*.

    Returns a list of extracted member filenames.
    """
    install_root = install_dir.resolve()
    extracted: list[str] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            name = member.filename

            # Guard: absolute path or traversal
            if name.startswith("/") or ".." in name.replace("\\", "/"):
                continue

            # Guard: .env files at any depth
            parts = name.replace("\\", "/").split("/")
            if any(part == ".env" or part.endswith(".env") for part in parts):
                continue

            # Guard: must resolve within install_dir
            resolved = (install_dir / name).resolve()
            try:
                resolved.relative_to(install_root)
            except ValueError:
                continue

            zf.extract(member, install_dir)
            extracted.append(name)

    return extracted


def rollback_install(backup_dir: Path, install_dir: Path) -> None:
    """Restore *backup_dir* contents over *install_dir*."""
    for item in backup_dir.iterdir():
        dest = install_dir / item.name
        try:
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        except OSError:
            pass


# ── Full update flow ──────────────────────────────────────────────────────────


def perform_update(
    config_path: Path,
    install_dir: Path,
    *,
    force: bool = False,
    verbose: bool = True,
) -> tuple[bool, str]:
    """Complete licensed package update flow.

    Returns ``(success, message)`` — never raises.
    Callers should inspect ``success`` and display ``message`` to the user.
    """
    from .security import secure_install_permissions

    try:
        from .constants import VERSION as CURRENT_VERSION
    except ImportError:
        CURRENT_VERSION = "0.0.0"

    # ── Read license config ────────────────────────────────────────────────────
    try:
        lic = load_license_config(config_path)
    except UpdaterError as exc:
        return False, str(exc)

    if not lic.get("enabled"):
        return False, (
            "License mode is disabled. "
            "Set license.enabled=true in config.json to enable licensed updates."
        )
    if lic.get("mode") != "remote":
        return False, (
            "License mode is not 'remote'. "
            "Switch to license.mode='remote' and set license.server_url to enable licensed updates."
        )

    key = (lic.get("key") or "").strip()
    server_url = (lic.get("server_url") or "").strip()
    channel = (lic.get("channel") or "stable").strip()
    api_secret = (lic.get("api_secret") or "").strip()
    device_model = (lic.get("device_label") or "termux-android")[:120]

    if not key:
        return False, "No license key configured in license.key."
    if not server_url:
        return False, "No server_url configured. Set license.server_url in config.json."

    install_id = _ensure_install_id(config_path, lic)

    if verbose:
        print(f"Checking for update... (key={_mask_key(key)}, channel={channel})")

    # ── Authorize download ────────────────────────────────────────────────────
    try:
        meta = request_download_token(
            server_url, key, install_id, device_model,
            CURRENT_VERSION, channel, api_secret=api_secret,
        )
    except LicenseCheckError as exc:
        save_license_status(config_path, "rejected")
        return False, str(exc)
    except UpdaterError as exc:
        return False, f"Update check failed: {exc}"

    remote_version = meta.get("version", "0.0.0")
    sha256 = meta.get("sha256", "")
    download_url = meta.get("download_url", "")
    filename = meta.get("filename", "package.zip")
    token = meta.get("download_token", "")

    if not force and not is_newer_version(remote_version, CURRENT_VERSION):
        return True, f"Already up to date (version {CURRENT_VERSION})."

    if verbose:
        print(f"Downloading version {remote_version} ({filename})...")

    # ── Download + verify ─────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory(prefix="deng-update-") as tmp:
        zip_path = Path(tmp) / filename
        try:
            download_package(download_url, zip_path, api_secret=token)
        except UpdaterError as exc:
            return False, f"Download failed: {exc}"

        if sha256:
            if verbose:
                print("Verifying package SHA-256...")
            try:
                verify_package(zip_path, sha256)
            except HashMismatchError as exc:
                return False, str(exc)

        # ── Backup + extract ──────────────────────────────────────────────────
        backup_dir = backup_install(install_dir)
        if verbose:
            print(f"Backup created: {backup_dir}")

        try:
            extracted = extract_package(zip_path, install_dir)
            if verbose:
                print(f"Extracted {len(extracted)} files.")
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"Extraction failed: {exc}. Rolling back...")
            rollback_install(backup_dir, install_dir)
            return False, f"Update failed and was rolled back: {exc}"

    save_license_status(config_path, "active")
    secure_install_permissions(install_dir)
    return True, f"Updated to version {remote_version}."
