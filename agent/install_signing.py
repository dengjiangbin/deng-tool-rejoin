"""HMAC signatures for internal-only bootstrap URLs (/install/dev/main)."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import urlencode


def signing_secret() -> str:
    return (os.environ.get("REJOIN_INSTALL_SIGNING_SECRET") or "").strip()


def sign_internal_path(subpath: str, *, expires_at: int | None = None, ttl_seconds: int = 86400) -> str:
    """Return query string ``exp=...&sig=...`` for subpath (e.g. ``dev/main``)."""
    secret = signing_secret()
    if not secret:
        return ""
    exp = expires_at if expires_at is not None else int(time.time()) + ttl_seconds
    msg = f"{subpath}:{exp}".encode()
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return urlencode({"exp": str(exp), "sig": sig})


def verify_internal_path(subpath: str, exp_str: str, sig: str) -> bool:
    secret = signing_secret()
    if not secret or not exp_str or not sig:
        return False
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    if time.time() > exp:
        return False
    expected = hmac.new(secret.encode(), f"{subpath}:{exp}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
