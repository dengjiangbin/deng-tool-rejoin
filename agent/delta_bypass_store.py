"""One-time Delta bypass tokens (Lime-compatible ``/bypass?token=`` activation).

Tokens map to executor license keys.  Redemption is single-use per token unless
``reuse`` is set on the token row (admin / test only).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

_STORE_LOCK = threading.RLock()
_DEFAULT_STORE = Path(__file__).resolve().parents[1] / "data" / "delta_bypass_tokens.json"


def _store_path() -> Path:
    raw = (os.environ.get("DENG_DELTA_BYPASS_STORE") or "").strip()
    return Path(raw) if raw else _DEFAULT_STORE


def _load_rows() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        env_json = (os.environ.get("DENG_DELTA_BYPASS_TOKENS") or "").strip()
        if env_json:
            try:
                parsed = json.loads(env_json)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_rows(rows: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def redeem_bypass_token(token: str) -> tuple[bool, dict[str, Any]]:
    """Consume token and return executor license payload."""
    tok = (token or "").strip()
    if not tok or len(tok) > 128:
        return False, {"error": "invalid_token", "message": "Missing or invalid bypass token."}

    with _STORE_LOCK:
        rows = _load_rows()
        row = rows.get(tok)
        if not isinstance(row, dict):
            return False, {"error": "not_found", "message": "Bypass token not found or expired."}

        now = time.time()
        expires_at = row.get("expires_at")
        if expires_at is not None:
            try:
                if float(expires_at) <= now:
                    return False, {"error": "expired", "message": "Bypass token expired."}
            except (TypeError, ValueError):
                pass

        key = str(row.get("key") or row.get("license_key") or row.get("delta_key") or "").strip()
        if not key:
            return False, {"error": "missing_key", "message": "Bypass token has no license key."}

        reuse = bool(row.get("reuse"))
        if row.get("used") and not reuse:
            return False, {"error": "already_used", "message": "Bypass token already redeemed."}

        if not reuse:
            row = dict(row)
            row["used"] = True
            row["used_at"] = now
            rows[tok] = row
            _save_rows(rows)

        payload: dict[str, Any] = {
            "ok": True,
            "key": key,
            "expires_at": expires_at,
            "token": tok,
        }
        if row.get("expires_utc"):
            payload["expires_utc"] = row.get("expires_utc")
        return True, payload


def activate_executor_license(
    key: str,
    *,
    hwid: str = "",
    install_id_hash: str = "",
) -> tuple[bool, dict[str, Any]]:
    """Lime-compatible ``/license/activate`` — records HWID binding for audit."""
    lic = (key or "").strip()
    if not lic:
        return False, {"ok": False, "error": "missing_key", "message": "License key required."}
    if len(lic) > 512:
        return False, {"ok": False, "error": "invalid_key", "message": "License key too long."}

    hw = (hwid or install_id_hash or "").strip()[:128]
    return True, {
        "ok": True,
        "activated": True,
        "key": lic,
        "hwid": hw or None,
        "activated_at": time.time(),
    }


def check_executor_license(key: str) -> dict[str, Any]:
    """Lime-compatible ``/license/check?key=`` — format-only check."""
    lic = (key or "").strip()
    if not lic:
        return {"ok": False, "valid": False, "error": "missing_key"}
    return {"ok": True, "valid": True, "key": lic[:8] + "..."}


def mint_bypass_token(
    key: str,
    *,
    expires_at: float | None = None,
    reuse: bool = False,
) -> str:
    """Admin helper: create a one-time bypass token for ``key``."""
    lic = (key or "").strip()
    if not lic:
        raise ValueError("key required")
    token = secrets.token_urlsafe(24)
    row: dict[str, Any] = {"key": lic, "reuse": reuse}
    if expires_at is not None:
        row["expires_at"] = float(expires_at)
    with _STORE_LOCK:
        rows = _load_rows()
        rows[token] = row
        _save_rows(rows)
    return token
