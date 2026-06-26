"""Persistent per-package Roblox username identity for lifecycle webhooks."""

from __future__ import annotations

import json
import time
from typing import Any

from .constants import DATA_DIR
from .config import validate_account_username, validate_package_name
from .package_username import NO_ACCOUNT_LABEL, SCANNER_ERROR_PREFIX

_IDENTITY_PATH = DATA_DIR / "package_identity.json"

_BLOCKED_USERNAME_MARKERS = frozenset({
    "unknown",
    "n/a",
    "na",
    "none",
    "null",
    "unavailable",
    "not set",
    "not_set",
})


def _normalize_username(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text == NO_ACCOUNT_LABEL or text.startswith(SCANNER_ERROR_PREFIX):
        return None
    validated = validate_account_username(text)
    if not validated:
        return None
    if validated.lower() in _BLOCKED_USERNAME_MARKERS:
        return None
    return validated


def _load_db() -> dict[str, Any]:
    try:
        if _IDENTITY_PATH.is_file():
            parsed = json.loads(_IDENTITY_PATH.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                packages = parsed.get("packages")
                if isinstance(packages, dict):
                    return {"packages": packages}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {"packages": {}}


def _save_db(data: dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _IDENTITY_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"packages": data.get("packages") or {}}, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(_IDENTITY_PATH)
    except OSError:
        pass


def record_package_identity(
    package: str,
    username: str,
    *,
    source: str = "",
    confidence: str = "",
    user_id: str | None = None,
) -> None:
    """Persist last-known username for a package (called on any successful detection)."""
    clean = _normalize_username(username)
    if not clean:
        return
    pkg = validate_package_name(package)
    db = _load_db()
    packages = db.setdefault("packages", {})
    row = dict(packages.get(pkg) or {})
    row.update({
        "package_name": pkg,
        "roblox_username": clean,
        "user_id": str(user_id or row.get("user_id") or "").strip() or None,
        "last_seen_at": time.time(),
        "source": str(source or row.get("source") or "").strip() or "unknown",
        "confidence": str(confidence or row.get("confidence") or "").strip() or "",
    })
    packages[pkg] = row
    _save_db(db)


def get_package_identity(package: str) -> dict[str, Any] | None:
    pkg = str(package or "").strip()
    if not pkg:
        return None
    row = _load_db().get("packages", {}).get(pkg)
    return dict(row) if isinstance(row, dict) else None


def resolve_lifecycle_username(
    package: str,
    *,
    entry: dict[str, Any] | None = None,
    supervisor: Any = None,
    cfg: dict[str, Any] | None = None,
) -> tuple[str | None, str]:
    """Resolve username for Package Dead / Recovered webhooks.

    Order:
      a. live supervisor package state (entry, entries, presence map, snapshot)
      b. persistent package_identity cache
      c. lifecycle webhook state last_username
      d. config username caches
    """
    try:
        pkg = validate_package_name(package)
    except Exception:  # noqa: BLE001
        return None, "username_resolution_failed"

    # (a) supervisor live state
    if entry:
        for key in ("account_username", "label"):
            user = _normalize_username(entry.get(key))
            if user:
                return user, f"supervisor_entry_{key}"

    if supervisor is not None:
        for e in getattr(supervisor, "entries", []) or []:
            if not isinstance(e, dict):
                continue
            if str(e.get("package") or "").strip() != pkg:
                continue
            user = _normalize_username(e.get("account_username") or e.get("label"))
            if user:
                return user, "supervisor_entries"

        presence_map = getattr(supervisor, "_presence_usernames", None)
        if isinstance(presence_map, dict):
            user = _normalize_username(presence_map.get(pkg))
            if user:
                return user, "supervisor_presence_map"

        try:
            for row in supervisor.get_status_snapshot():
                if not isinstance(row, dict):
                    continue
                if str(row.get("package") or "").strip() != pkg:
                    continue
                user = _normalize_username(row.get("username"))
                if user:
                    return user, "supervisor_snapshot"
        except Exception:  # noqa: BLE001
            pass

    # (b) persistent identity cache
    identity = get_package_identity(pkg)
    if identity:
        user = _normalize_username(identity.get("roblox_username"))
        if user:
            return user, "package_identity_cache"

    # (c) lifecycle webhook state
    from . import webhook as lifecycle_webhook

    lifecycle_row = lifecycle_webhook._load_package_lifecycle_state().get("packages", {}).get(pkg, {})
    if isinstance(lifecycle_row, dict):
        user = _normalize_username(lifecycle_row.get("last_username"))
        if user:
            return user, "lifecycle_state_cache"

    # (d) config caches
    if isinstance(cfg, dict):
        for cache_key in ("package_username_cache", "account_username_cache"):
            cache = cfg.get(cache_key)
            if not isinstance(cache, dict):
                continue
            user = _normalize_username(cache.get(pkg))
            if user:
                return user, f"config_{cache_key}"

    return None, "username_resolution_failed"


def format_discord_username_spoiler(username: str) -> str:
    """Discord embed value: spoiler-wrapped username only."""
    clean = _normalize_username(username)
    if not clean:
        return ""
    return f"||{clean}||"


def lifecycle_username_debug() -> dict[str, Any]:
    """Probe/debug snapshot for lifecycle username resolution."""
    from . import webhook as lifecycle_webhook

    lifecycle = lifecycle_webhook._load_package_lifecycle_state().get("packages", {})
    identity = _load_db().get("packages", {})
    pending: list[str] = []
    failures: list[str] = []
    for pkg, row in lifecycle.items():
        if not isinstance(row, dict):
            continue
        if row.get("dead_active") and row.get("dead_notified"):
            continue
        if row.get("dead_active") or row.get("username_resolution_failed"):
            if row.get("username_resolution_failed"):
                failures.append(pkg)
            elif not row.get("dead_notified"):
                pending.append(pkg)
    return {
        "identity_packages": len(identity),
        "pending_dead_notification": pending,
        "username_resolution_failed": failures,
    }
