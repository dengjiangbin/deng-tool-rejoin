"""DENG Tool: Rejoin — Shared license key utilities.

Key format:  DENG-XXXX-XXXX-XXXX-XXXX
             └──┘ └──────────────────┘
            Prefix   16 hex chars (4 groups of 4)

Rules:
  • Uppercase on display
  • Accept lowercase input and input without inner dashes
  • Trim spaces; normalize inner grouping dashes
  • Display full key in user copy views (Discord panel, Key Stats when export exists)
  • Store only the SHA-256 hash in any database (plus optional encrypted export blob)
  • install_id is a privacy-safe random UUID persisted locally
  • Never read IMEI, phone number, or Roblox session/cookie/token
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any

from .constants import APP_HOME, DEFAULT_LICENSE_SERVER_URL, VERSION
from . import safe_http

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

    ``model`` uses :func:`get_public_device_model` (getprop-based).
    """
    return {"model": get_public_device_model()}


# ── Remote license API (POST /api/license/check) ───────────────────────────────

WRONG_DEVICE_USER_MESSAGE = (
    "Wrong device. Open DENG Tool: Rejoin Panel and use Reset HWID."
)

KEY_NOT_REDEEMED_API_MESSAGE = (
    "This key has not been redeemed yet. Redeem it in the DENG Tool: Rejoin Panel first."
)

REDEEM_IN_PANEL_HINT = "Redeem this key in the Discord panel first."


def _getprop(prop: str) -> str:
    """Read a single Android system property (Termux). Returns '' on failure."""
    try:
        result = subprocess.run(
            ["getprop", prop],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=4,
            shell=False,
        )
        if result.returncode == 0:
            v = (result.stdout or "").strip()
            if v.lower() in ("", "unknown"):
                return ""
            return v[:120]
    except Exception:  # noqa: BLE001
        pass
    return ""


def get_public_device_model() -> str:
    """Public handset identifier for license binding, webhooks, and stats.

    Priority:
      1. ``ro.product.model`` (e.g. SM-S9160, Pixel 9 Pro XL)
      2. ``ro.product.manufacturer`` + ``ro.product.model``
      3. ``ro.product.device``

    Never blocks license; returns ``Unknown`` if nothing usable.
    """
    model = _getprop("ro.product.model")
    manufacturer = _getprop("ro.product.manufacturer")
    device = _getprop("ro.product.device")

    if model:
        return model[:120]
    if manufacturer:
        extra = device or _getprop("ro.product.name")
        if extra and extra.lower() != manufacturer.lower():
            return f"{manufacturer} {extra}"[:120]
        return manufacturer[:120]
    if device:
        return device[:120]
    return "Unknown"


def sync_install_id_with_config(license_section: dict[str, Any]) -> str:
    """Ensure ``license_section['install_id']`` and ``INSTALL_ID_PATH`` agree.

    Preference order: valid id already in config → valid id on disk → generate new.
    """
    lic = license_section
    cfg_id = (lic.get("install_id") or "").strip().lower()
    file_id = ""
    if INSTALL_ID_PATH.exists():
        file_id = INSTALL_ID_PATH.read_text(encoding="utf-8").strip().lower()

    if re.fullmatch(r"[0-9a-f]{32}", cfg_id):
        if file_id != cfg_id:
            INSTALL_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
            INSTALL_ID_PATH.write_text(cfg_id + "\n", encoding="utf-8")
        return cfg_id

    if re.fullmatch(r"[0-9a-f]{32}", file_id):
        lic["install_id"] = file_id
        return file_id

    new_id = secrets.token_hex(16)
    lic["install_id"] = new_id
    INSTALL_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
    INSTALL_ID_PATH.write_text(new_id + "\n", encoding="utf-8")
    return new_id


def _license_api_post_json(url: str, payload: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
    """POST to the license API.  Uses safe_http which routes through curl on
    Termux to prevent Python ssl/OpenSSL SIGSEGV from killing the main process.
    """
    try:
        return safe_http.post_json(url, payload, timeout=timeout)
    except safe_http.SafeHttpStatusError as exc:
        # Try to parse error JSON embedded in the raised exception body.
        try:
            parsed: dict[str, Any] = json.loads(exc.body)
            if isinstance(parsed, dict) and parsed.get("result"):
                return parsed
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass
        return {"result": "server_unavailable", "message": f"HTTP {exc.status_code}"}
    except safe_http.SafeHttpNetworkError as exc:
        return {"result": "server_unavailable", "message": str(exc)}
    except safe_http.SafeHttpJsonError:
        return {"result": "server_unavailable", "message": "Invalid JSON from license server"}
    except Exception:  # noqa: BLE001
        return {"result": "server_unavailable", "message": "Network error"}


def check_remote_license_status(
    server_url: str,
    *,
    license_key: str,
    install_id: str,
    device_model: str,
    app_version: str,
    device_label: str = "",
    timeout: int = 30,
) -> tuple[str, str]:
    """Call the public license API; return ``(result, message)``.

    Sends only: hashed install id, key, device model, version, optional label —
    never Supabase secrets, tokens, or cookies.
    """
    base = (server_url or DEFAULT_LICENSE_SERVER_URL).strip().rstrip("/")
    url = f"{base}/api/license/check"
    install_id_hash = hash_install_id(install_id.strip())
    payload: dict[str, Any] = {
        "key": normalize_license_key(license_key),
        "install_id_hash": install_id_hash,
        "device_model": (device_model or "unknown")[:120],
        "app_version": (app_version or VERSION or "unknown")[:40],
    }
    label = (device_label or "").strip()[:80]
    if label:
        payload["device_label"] = label

    try:
        resp = _license_api_post_json(url, payload, timeout=timeout)
    except Exception:  # noqa: BLE001
        return "server_unavailable", "License server temporarily unavailable."

    result = str(resp.get("result") or "server_unavailable").strip().lower()
    message = str(resp.get("message") or "").strip()
    if result == "wrong_device":
        return result, WRONG_DEVICE_USER_MESSAGE
    if result == "key_not_redeemed":
        return result, REDEEM_IN_PANEL_HINT
    if not message:
        message = {
            "active": "License active.",
            "not_found": "Key not found.",
            "revoked": "This key has been revoked.",
            "expired": "This key has expired.",
            "inactive": "License inactive.",
            "server_unavailable": "License server temporarily unavailable.",
            "missing_key": "No license key provided.",
            "key_not_redeemed": KEY_NOT_REDEEMED_API_MESSAGE,
        }.get(result, result)
    return result, message
