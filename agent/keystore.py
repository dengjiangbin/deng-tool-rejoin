"""DENG Tool: Rejoin — Key licensing system.

Architecture
────────────
 ┌─────────────────────────────────────────────────────────┐
 │ Key format:  DENG-<hex≥8>  (e.g. DENG-38ab1234cd56ef78) │
 │ 1 key = 1 device UUID                                    │
 │ UUID = Android serial / MAC / generated UUID (persisted) │
 │ DB   = ~/.deng-tool/rejoin/keydb.json  (local, dev)      │
 └─────────────────────────────────────────────────────────┘

Development mode
────────────────
 Set ``DEV_MODE = True`` (or env DENG_DEV=1) to bypass server checks.
 In dev mode the local keydb.json is the source of truth.
 Generate keys with: python -m agent.keygen

Security note
─────────────
 This module reads/writes only local files under APP_HOME and makes
 no network calls. The UUID and key are stored in keydb.json (local
 only). Production builds should swap ``_load_keydb`` / ``_save_keydb``
 with authenticated API calls.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

from .constants import APP_HOME, LICENSE_KEY_PATTERN

# ── Dev mode flag ─────────────────────────────────────────────────────────────
DEV_MODE: bool = bool(os.environ.get("DENG_DEV", ""))

KEYDB_PATH = APP_HOME / "keydb.json"
DEVICE_UUID_PATH = APP_HOME / "device_id"

_KEY_RE = re.compile(LICENSE_KEY_PATTERN, re.IGNORECASE)


# ── Custom exceptions ──────────────────────────────────────────────────────────

class KeyError(Exception):          # noqa: A001 - shadows built-in intentionally
    """Raised when key validation fails."""

class KeyDeviceMismatch(KeyError):
    """Raised when the key is bound to a different device."""

class KeyNotFound(KeyError):
    """Raised when the key does not exist in the database."""

class KeyInvalid(KeyError):
    """Raised when the key format or signature is wrong."""


# ── Device UUID ────────────────────────────────────────────────────────────────

def _get_android_serial() -> str | None:
    """Try to read the Android device serial number."""
    try:
        import subprocess  # noqa: PLC0415
        for cmd in (
            ["getprop", "ro.serialno"],
            ["getprop", "ro.boot.serialno"],
        ):
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=4,
                shell=False,
            )
            if result.returncode == 0:
                serial = result.stdout.strip()
                if serial and serial not in ("unknown", "0", ""):
                    return serial
    except Exception:  # noqa: BLE001
        pass
    return None


def _derive_device_uuid(raw: str) -> str:
    """Derive a stable hex UUID from a raw device identifier string."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def get_device_uuid() -> str:
    """Return the stable device UUID for this installation.

    Priority:
    1. Previously persisted UUID file (fastest; works offline).
    2. Android serial number (hashed to 32 hex chars).
    3. Generated random UUID (saved immediately for future runs).
    """
    DEVICE_UUID_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DEVICE_UUID_PATH.exists():
        stored = DEVICE_UUID_PATH.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{32}", stored):
            return stored

    serial = _get_android_serial()
    if serial:
        uuid = _derive_device_uuid(serial)
    else:
        uuid = secrets.token_hex(16)  # 32-char random hex

    DEVICE_UUID_PATH.write_text(uuid + "\n", encoding="utf-8")
    return uuid


def reset_device_uuid() -> str:
    """Wipe and regenerate the device UUID (key reset helper)."""
    if DEVICE_UUID_PATH.exists():
        DEVICE_UUID_PATH.unlink()
    return get_device_uuid()


# ── Local key database (JSON) ──────────────────────────────────────────────────

def _load_keydb() -> dict[str, Any]:
    KEYDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not KEYDB_PATH.exists():
        return {}
    try:
        return json.loads(KEYDB_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_keydb(db: dict[str, Any]) -> None:
    KEYDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEYDB_PATH.write_text(json.dumps(db, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ── Key generator ──────────────────────────────────────────────────────────────

def generate_key(prefix: str = "DENG") -> str:
    """Generate a new random license key in ``PREFIX-<32hex>`` format."""
    return f"{prefix}-{secrets.token_hex(16).upper()}"


def create_key_in_db(key: str | None = None, *, note: str = "") -> str:
    """Generate and store a new key in the local keydb (dev mode).

    Returns the key string.
    """
    key = key or generate_key()
    key = key.upper()
    if not _KEY_RE.match(key):
        raise KeyInvalid(f"Key format invalid: {key}")
    db = _load_keydb()
    if key in db:
        raise KeyError(f"Key already exists: {key}")
    db[key] = {
        "device_uuid": None,
        "bound_at": None,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": note,
        "valid": True,
    }
    _save_keydb(db)
    return key


def revoke_key_in_db(key: str) -> None:
    """Mark a key as revoked in the local keydb."""
    key = key.upper()
    db = _load_keydb()
    if key not in db:
        raise KeyNotFound(f"Key not found: {key}")
    db[key]["valid"] = False
    _save_keydb(db)


def list_keys_in_db() -> list[dict[str, Any]]:
    """List all keys in the local keydb (dev utility)."""
    db = _load_keydb()
    rows: list[dict[str, Any]] = []
    for key, entry in db.items():
        rows.append({
            "key": key,
            "device_uuid": entry.get("device_uuid") or "(unbound)",
            "bound_at": entry.get("bound_at") or "(never)",
            "note": entry.get("note") or "",
            "valid": entry.get("valid", True),
        })
    return rows


# ── Key binding ────────────────────────────────────────────────────────────────

def _validate_format(key: str) -> str:
    upper = key.strip().upper()
    if not _KEY_RE.match(upper):
        raise KeyInvalid("Key must be in format DENG-<hex8+> (e.g. DENG-38ab1234cd56ef78)")
    return upper


def bind_key(key: str) -> str:
    """Bind (or verify existing binding of) key to this device UUID.

    Returns device_uuid on success.
    Raises:
      KeyInvalid       — bad format
      KeyNotFound      — key not in DB
      KeyDeviceMismatch — key is already bound to a different device
    """
    key = _validate_format(key)
    device_uuid = get_device_uuid()
    db = _load_keydb()

    if key not in db:
        raise KeyNotFound(
            f"License key not found in the key database.\n"
            f"  Key: {key[:12]}...\n"
            "  Contact the distributor to obtain a valid key."
        )

    entry = db[key]
    if not entry.get("valid", True):
        raise KeyInvalid("This license key has been revoked.")

    bound_uuid = entry.get("device_uuid")
    if bound_uuid and bound_uuid != device_uuid:
        raise KeyDeviceMismatch(
            "⚠  This key is already bound to a different device.\n"
            "   1 key = 1 device.  Contact support to reset the key binding.\n"
            f"   Bound device: {bound_uuid[:8]}...  This device: {device_uuid[:8]}..."
        )

    if not bound_uuid:
        # First use — bind to this device
        db[key]["device_uuid"] = device_uuid
        db[key]["bound_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _save_keydb(db)

    return device_uuid


def unbind_key(key: str) -> None:
    """Remove the device binding from a key (admin / key-reset)."""
    key = _validate_format(key)
    db = _load_keydb()
    if key not in db:
        raise KeyNotFound(f"Key not found: {key}")
    db[key]["device_uuid"] = None
    db[key]["bound_at"] = None
    _save_keydb(db)


def verify_key(key: str) -> tuple[bool, str]:
    """Verify a key against this device.  Returns (ok, message).

    Does NOT bind the key — call bind_key() first to lock it.
    """
    try:
        device_uuid = bind_key(key)
        return True, f"Key verified. Device: {device_uuid[:8]}..."
    except KeyDeviceMismatch as exc:
        return False, str(exc)
    except (KeyNotFound, KeyInvalid) as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"Key verification failed: {exc}"


# ── Interactive key entry ──────────────────────────────────────────────────────

def prompt_and_verify_key() -> bool:
    """Prompt the user to enter a license key, verify it, and bind it.

    Returns True if verification succeeded, False if user chose to exit.
    Prints status messages directly.
    """
    print()
    print("─" * 48)
    print("  DENG Tool: Rejoin — License Verification")
    print("─" * 48)
    print("  Paste your license key to continue.")
    print("  Format: DENG-<hex>  (e.g. DENG-38ab1234cd56ef78)")
    print()
    while True:
        try:
            raw = input("  Paste your license key (or 'q' to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if raw.lower() in ("q", "quit", "exit"):
            return False
        if not raw:
            print("  Please enter your license key.")
            continue
        ok, message = verify_key(raw)
        if ok:
            print(f"  ✓ {message}")
            print()
            return True
        print(f"  ✗ {message}")
        print("  Try again or press 'q' to quit.")
        print()
