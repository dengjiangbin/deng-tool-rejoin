"""DENG Tool: Rejoin — Shared license key utilities.

Key format:  DENG-XXXX-XXXX-XXXX-XXXX
             └──┘ └──────────────────┘
            Prefix   16 hex chars (4 groups of 4)

Rules:
  • Uppercase on display
  • Accept lowercase input and input without inner dashes
  • Trim spaces; normalize inner grouping dashes
  • Display full key only once (at generation time)
  • Everywhere else: masked key  DENG-8F3A...44F0
  • Store only the SHA-256 hash in any database
  • install_id is a privacy-safe random UUID persisted locally
  • Never read IMEI, phone number, or Roblox session/cookie/token
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any

from .constants import APP_HOME

# ── Constants ──────────────────────────────────────────────────────────────────

# Path for persisted install ID (not the legacy device_id used by keystore.py)
INSTALL_ID_PATH = APP_HOME / "install_id"

# Canonical display format: DENG-XXXX-XXXX-XXXX-XXXX
_CANONICAL_RE = re.compile(
    r"^DENG-([0-9A-F]{4})-([0-9A-F]{4})-([0-9A-F]{4})-([0-9A-F]{4})$"
)

# Input pattern: accept with or without inner grouping dashes, any case
_INPUT_HEX_RE = re.compile(r"^[0-9A-Fa-f]{16}$")


# ── Custom exceptions ──────────────────────────────────────────────────────────

class LicenseKeyError(ValueError):
    """Raised for invalid license key format."""


# ── Key generation ─────────────────────────────────────────────────────────────

def generate_license_key() -> str:
    """Generate a new random license key in DENG-XXXX-XXXX-XXXX-XXXX format."""
    raw = secrets.token_hex(8).upper()  # 16 uppercase hex chars
    return f"DENG-{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"


# ── Normalization and validation ───────────────────────────────────────────────

def normalize_license_key(raw: str) -> str:
    """Normalize a license key to canonical DENG-XXXX-XXXX-XXXX-XXXX form.

    Accepts:
      DENG-8f3a-b3c4-d5e6-44f0   (with inner dashes, lowercase)
      DENG-8F3AB3C4D5E644F0       (without inner dashes)
      deng-8f3a-b3c4-d5e6-44f0   (all lowercase)

    Raises:
      LicenseKeyError for any format that cannot be normalized.
    """
    s = (raw or "").strip().upper()
    if not s.startswith("DENG-"):
        raise LicenseKeyError(
            "License key must start with DENG-  (e.g. DENG-8F3A-B3C4-D5E6-44F0)"
        )
    payload = s[5:]                   # Everything after "DENG-"
    hex_only = payload.replace("-", "")  # Remove optional inner dashes
    if not _INPUT_HEX_RE.match(hex_only):
        raise LicenseKeyError(
            "License key must contain exactly 16 hex characters after DENG- "
            "(e.g. DENG-8F3A-B3C4-D5E6-44F0)"
        )
    return f"DENG-{hex_only[:4]}-{hex_only[4:8]}-{hex_only[8:12]}-{hex_only[12:16]}"


def validate_license_key(raw: str) -> str:
    """Validate and normalize a license key.  Empty string = key not set.

    Returns the normalized key string or empty string.
    Raises LicenseKeyError if non-empty and invalid.
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        return ""
    return normalize_license_key(cleaned)


def mask_license_key(key: str) -> str:
    """Return a display-safe masked version of a license key.

    DENG-8F3A-B3C4-D5E6-44F0  →  DENG-8F3A...44F0
    Old flat format:            →  DENG-8F3A...44F0
    Empty / not set:            →  Not set
    """
    if not key:
        return "Not set"
    s = (key or "").strip().upper()
    if not s.startswith("DENG-"):
        return "Not set"
    parts = s.split("-")
    if len(parts) == 5:
        # New format: DENG-XXXX-XXXX-XXXX-XXXX
        return f"DENG-{parts[1]}...{parts[4]}"
    if len(parts) == 2:
        # Old flat format: DENG-XXXXXXXXXXXXXXXX
        hex_part = parts[1]
        if len(hex_part) >= 8:
            return f"DENG-{hex_part[:4]}...{hex_part[-4:].lower()}"
    return "DENG-***"


def hash_license_key(key: str) -> str:
    """Return the SHA-256 hex digest of the normalized license key.

    This is the value stored in the database.  The raw key is NEVER stored.
    """
    normalized = normalize_license_key(key)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ── Install ID (privacy-safe device identity) ──────────────────────────────────

def get_or_create_install_id() -> str:
    """Return the install ID for this device, creating one if needed.

    The install ID is a randomly generated 32-char hex string that persists
    locally at ``~/.deng-tool/rejoin/install_id``.

    Privacy guarantee:
      • No IMEI, MAC address, phone number, or hardware serial is read.
      • Only the SHA-256 hash of this ID is ever transmitted to any server.
      • The ID can be reset by deleting the file or resetting from Discord.
    """
    INSTALL_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
    if INSTALL_ID_PATH.exists():
        stored = INSTALL_ID_PATH.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{32}", stored):
            return stored
    install_id = secrets.token_hex(16)  # 32 lowercase hex chars
    INSTALL_ID_PATH.write_text(install_id + "\n", encoding="utf-8")
    return install_id


def hash_install_id(install_id: str) -> str:
    """Return the SHA-256 hash of the install ID.

    Always send this hash to remote servers — never the raw install_id.
    """
    return hashlib.sha256(install_id.encode("utf-8")).hexdigest()


# ── Device summary (public / safe properties only) ────────────────────────────

def get_device_summary() -> dict[str, str]:
    """Return a privacy-safe dict of public device properties.

    Only reads public Android system properties via getprop.
    Never reads IMEI, phone number, account credentials, or private files.
    """
    model = "unknown"
    try:
        result = subprocess.run(
            ["getprop", "ro.product.model"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=4,
            shell=False,
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
            if raw and raw not in ("", "unknown"):
                model = raw[:64]
    except Exception:  # noqa: BLE001
        pass
    return {"model": model}
