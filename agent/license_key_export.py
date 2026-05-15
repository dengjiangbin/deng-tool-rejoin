"""Server-side optional encryption for exportable license keys (Fernet).

Full raw keys are never stored in plaintext. When LICENSE_KEY_EXPORT_SECRET is set
and the cryptography package is installed, newly generated keys can store a
Fernet ciphertext so the owning Discord user can export the full key from
Key Stats / Download. Old rows without ciphertext cannot be recovered.
"""

from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache

EXPORT_SECRET_ENV = "LICENSE_KEY_EXPORT_SECRET"


@lru_cache(maxsize=1)
def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    raw = os.environ.get(EXPORT_SECRET_ENV, "").strip()
    if not raw:
        return None
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def is_export_secret_configured() -> bool:
    return bool(os.environ.get(EXPORT_SECRET_ENV, "").strip()) and _fernet() is not None


def encrypt_license_key_plaintext(plain_key: str) -> str | None:
    """Return Fernet token string, or None if encryption is unavailable."""
    f = _fernet()
    if not f:
        return None
    try:
        return f.encrypt(plain_key.strip().encode("utf-8")).decode("ascii")
    except Exception:
        return None


def decrypt_license_key_ciphertext(token: str) -> str | None:
    """Decrypt ciphertext to full key, or None on failure."""
    f = _fernet()
    if not f or not token:
        return None
    try:
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except Exception:
        return None


def clear_export_key_cache() -> None:
    """Clear Fernet cache (tests / secret rotation)."""
    _fernet.cache_clear()
