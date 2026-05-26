"""DENG Tool: Rejoin — License store abstraction.

Architecture
────────────
BaseLicenseStore defines the interface.
LocalJsonLicenseStore implements it using a local JSON file (dev / tests).
SupabaseLicenseStore is planned for future remote integration; see docs.

Data model
──────────
• 1 Discord user   = up to max_keys license keys (default 1).
• 1 license key    = 1 active device binding.
• Device identity  = hash of install_id (privacy-safe; no IMEI).
• HWID reset limit = 5 per 24 hours per key.
• Active key guard = warn/block reset if last heartbeat < 5 minutes ago.

Check result codes
──────────────────
active | expired | revoked | wrong_device | key_not_redeemed | not_found |
inactive | missing_key | server_unavailable
"""

from __future__ import annotations

import json
import secrets
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import APP_HOME
from .license import generate_license_key, hash_license_key, normalize_license_key, LicenseKeyError

# ── Public constants ───────────────────────────────────────────────────────────

STORE_PATH = APP_HOME / "license_store.json"

RESULT_ACTIVE              = "active"
RESULT_EXPIRED             = "expired"
RESULT_REVOKED             = "revoked"
RESULT_WRONG_DEVICE        = "wrong_device"
RESULT_KEY_NOT_REDEEMED    = "key_not_redeemed"
RESULT_NOT_FOUND           = "not_found"
RESULT_INACTIVE            = "inactive"
RESULT_MISSING_KEY         = "missing_key"
RESULT_SERVER_UNAVAILABLE  = "server_unavailable"
RESULT_REQUIRES_MANUAL_REBIND = "requires_manual_rebind"

MAX_HWID_RESETS_PER_24H    = 5
ACTIVE_HEARTBEAT_WINDOW_S  = 300          # 5 minutes
DEFAULT_MAX_KEYS            = 1
DEFAULT_GLOBAL_MAX_KEYS     = 2           # default global max active keys per user
DEFAULT_GLOBAL_MAX_PANEL    = 1           # default max Reset HWID panel uses per user per WIB day
GENERATION_COOLDOWN_SECONDS = 60          # minimum seconds between key generations
UNREDEEMED_KEY_EXPIRY_SECONDS = 86400    # 24 hours — unredeemed keys expire


# ── Custom exceptions ──────────────────────────────────────────────────────────

class StoreError(Exception):
    """Base class for license store errors."""

class UserLimitError(StoreError):
    """User has reached their license key limit."""

class PanelLimitError(StoreError):
    """User has reached their daily Reset HWID panel limit."""

class KeyNotFoundError(StoreError):
    """Key does not exist in the store."""

class KeyOwnershipError(StoreError):
    """Key belongs to a different Discord user."""

class KeyAlreadySelfOwned(StoreError):
    """Key is already attached to the requesting Discord user."""

    def __init__(self, message: str, *, export_backfilled: bool = False) -> None:
        super().__init__(message)
        self.export_backfilled = export_backfilled


class ExportStorageUnavailable(StoreError):
    """Cannot persist encrypted full keys (missing secret, crypto, or DB columns)."""


class NoActiveBindingError(StoreError):
    """No active device binding exists for this key; nothing to reset."""

class ResetLimitError(StoreError):
    """HWID reset limit exceeded (5 per 24 hours)."""

class ActiveKeyWarning(StoreError):
    """Key heartbeat was recently active; recommend waiting before reset."""

class GenerationCooldownError(StoreError):
    """User must wait before generating another key (1-minute cooldown)."""

    def __init__(self, message: str, *, remaining_seconds: int) -> None:
        super().__init__(message)
        self.remaining_seconds = remaining_seconds

class ExpiredKeyError(StoreError):
    """Key has expired (unredeemed for more than 24 hours)."""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _seconds_since(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except (ValueError, TypeError):
        return None


def get_wib_day(now: datetime | None = None) -> str:
    """Return the current date in WIB (Asia/Jakarta, UTC+7) as 'YYYY-MM-DD'.

    Uses zoneinfo when available; falls back to a fixed +07:00 offset so the
    function works on Windows hosts that lack system timezone data.
    """
    from datetime import timedelta
    try:
        from zoneinfo import ZoneInfo
        wib = ZoneInfo("Asia/Jakarta")
    except Exception:
        wib = timezone(timedelta(hours=7))
    dt = (now or datetime.now(timezone.utc)).astimezone(wib)
    return dt.strftime("%Y-%m-%d")


def _license_record_has_owner(record: dict[str, Any]) -> bool:
    """Return True when a key is owned by Discord or a web portal account."""
    owner_id = record.get("owner_discord_id")
    site_user_id = record.get("site_user_id")
    return bool(str(owner_id or "").strip() or str(site_user_id or "").strip())


def _iso_expired(iso_str: str | None) -> bool:
    if not iso_str:
        return False
    try:
        normalized = str(iso_str).replace("Z", "+00:00")
        exp_dt = datetime.fromisoformat(normalized)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > exp_dt
    except (ValueError, TypeError):
        return False


# ── Base interface ─────────────────────────────────────────────────────────────

class BaseLicenseStore(ABC):
    """Interface that all license store implementations must satisfy."""

    @abstractmethod
    def get_or_create_user(
        self, discord_user_id: str, discord_username: str | None = None
    ) -> dict[str, Any]:
        """Return existing user record or create one.  Returns user dict."""

    @abstractmethod
    def get_user_by_discord_id(self, discord_user_id: str) -> dict[str, Any] | None:
        """Return user dict or None."""

    @abstractmethod
    def set_user_max_keys(self, discord_user_id: str, max_keys: int) -> None:
        """Set the maximum number of keys a user may own."""

    @abstractmethod
    def count_user_keys(self, discord_user_id: str) -> int:
        """Count the active (non-revoked) keys owned by a user."""

    # ── Key limit helpers (new: global + per-user limit table) ────────────────

    def get_global_max_keys(self) -> int:
        """Return the global default max active keys per user.

        Subclasses backed by Supabase should read from license_key_limits
        where scope='global'.  The base default is DEFAULT_GLOBAL_MAX_KEYS.
        """
        return DEFAULT_GLOBAL_MAX_KEYS

    def get_user_key_limit(self, discord_user_id: str) -> int | None:
        """Return per-user key limit override, or None if no override exists."""
        return None

    def get_effective_max_keys(self, discord_user_id: str) -> int:
        """Return the effective max active keys for a user.

        Per-user override wins over global default.
        """
        override = self.get_user_key_limit(discord_user_id)
        if override is not None:
            return override
        return self.get_global_max_keys()

    def count_active_keys_for_limit(self, discord_user_id: str) -> int:
        """Count active usable keys that count toward the limit.

        Counts:
        - Owned/bound keys (redeemed + active device)
        - Owned/unbound keys (redeemed, no active device)
        - Active unredeemed generated keys (not expired, still copyable)

        Excludes expired, revoked, inactive, deleted, and test/dev keys.
        """
        from agent.key_stats_format import filter_active_visible_license_rows
        rows = self.list_user_keys_for_stats(discord_user_id)
        return len(filter_active_visible_license_rows(rows))

    def set_global_max_keys(self, max_keys: int, updated_by: str) -> None:
        """Set the global default max active keys per user.

        Subclasses backed by Supabase should upsert license_key_limits
        where scope='global'.  Base implementation is a no-op.
        """

    def set_user_key_limit(
        self, discord_user_id: str, max_keys: int, updated_by: str
    ) -> None:
        """Set a per-user key limit override.

        Subclasses backed by Supabase should upsert license_key_limits
        where scope='user' and discord_user_id matches.
        Base implementation is a no-op.
        """

    # ── Panel reset limit helpers (max_panel column in license_key_limits) ────

    def get_global_max_panel(self) -> int:
        """Return the global default max Reset HWID panel uses per user per WIB day."""
        return DEFAULT_GLOBAL_MAX_PANEL

    def get_user_panel_limit(self, discord_user_id: str) -> int | None:
        """Return per-user panel limit override, or None if no override exists."""
        return None

    def get_effective_max_panel(self, discord_user_id: str) -> int:
        """Return the effective max panel resets for a user (per-user override wins)."""
        override = self.get_user_panel_limit(discord_user_id)
        if override is not None:
            return override
        return self.get_global_max_panel()

    def get_panel_reset_usage_today(self, discord_user_id: str) -> int:
        """Return how many successful Reset HWID panel uses the user has today (WIB)."""
        return 0

    def can_user_reset_panel_today(
        self, discord_user_id: str
    ) -> tuple[bool, int, int]:
        """Return (allowed, used_count, max_panel).

        allowed = True when the user has not yet reached their daily limit.
        """
        max_panel = self.get_effective_max_panel(discord_user_id)
        used_count = self.get_panel_reset_usage_today(discord_user_id)
        return (used_count < max_panel, used_count, max_panel)

    def record_successful_panel_reset(
        self, discord_user_id: str, unbound_key_count: int
    ) -> int:
        """Atomically check limit and increment daily panel-reset counter.

        Returns the new used_count on success.
        Raises PanelLimitError if the user is already at their daily limit.
        Base implementation is a no-op (always allows, returns 1).
        """
        return 1

    def set_global_max_panel(self, max_panel: int, updated_by: str) -> None:
        """Set the global default max panel resets per user per WIB day.

        Base implementation is a no-op.
        """

    def set_user_panel_limit(
        self, discord_user_id: str, max_panel: int, updated_by: str
    ) -> None:
        """Set a per-user panel limit override. Base implementation is a no-op."""

    @abstractmethod
    def create_key_for_user(
        self, discord_user_id: str, created_by: str | None = None
    ) -> str:
        """Generate and store a new key for the user.
        Returns the FULL key (only time it is returned).
        Raises UserLimitError if the user is at their limit.
        """

    @abstractmethod
    def redeem_key_for_user(self, discord_user_id: str, raw_key: str) -> str:
        """Attach an existing key to a Discord user.
        Returns the normalized **full** key on success (for copy).
        Raises KeyNotFoundError, KeyOwnershipError, or UserLimitError.
        """

    @abstractmethod
    def recover_key_export_for_user(self, discord_user_id: str, raw_key: str) -> str:
        """Store ciphertext for a key the user owns when export data is missing.

        Returns ``\"stored\"`` after writing ciphertext, or ``\"already_exportable\"``
        when a working ciphertext already exists. Never logs the plaintext key.

        Raises:
            ExportStorageUnavailable: encryption unavailable or not configured.
            KeyNotFoundError: invalid key string or no such key.
            KeyOwnershipError: key belongs to someone else.
        """

    @abstractmethod
    def list_user_keys(self, discord_user_id: str) -> list[dict[str, Any]]:
        """Return a list of key summary dicts for a user.
        Each dict: {id, masked_key, full_key_plaintext (optional), status, plan,
        bound_device, created_at}
        """

    @abstractmethod
    def reset_hwid(self, discord_user_id: str, key_id: str) -> None:
        """Clear the active device binding for a key.
        Raises NoActiveBindingError if no active binding exists (nothing to reset).
        Raises ResetLimitError if >= 5 resets in last 24 h.
        Raises ActiveKeyWarning if last heartbeat < 5 minutes ago.
        Raises KeyNotFoundError if key does not exist.
        """

    @abstractmethod
    def get_reset_count_24h(self, key_id: str) -> int:
        """Number of HWID resets in the last 24 hours for a key."""

    @abstractmethod
    def get_last_seen_at(self, key_id: str) -> str | None:
        """ISO timestamp of the last heartbeat for a key, or None."""

    @abstractmethod
    def validate_existing_binding(
        self,
        raw_key: str,
        install_id_hash: str,
        device_model: str = "",
        app_version: str = "",
        device_label: str = "",
    ) -> str:
        """Read-only validation for ``/api/license/check``.

        Must never create, update, or reactivate device bindings.
        Returns ``active`` only when an active binding matches *install_id_hash*.
        Returns ``requires_manual_rebind`` when unbound or inactive (HWID reset).
        """

    @abstractmethod
    def bind_or_check_device(
        self,
        raw_key: str,
        install_id_hash: str,
        device_model: str,
        app_version: str,
        device_label: str = "",
    ) -> str:
        """Bind or rebind a device to a key (``/api/license/bind`` only).

        Creates a new binding or reactivates an inactive binding after HWID reset.
        Never raises; errors are returned as result codes.
        """

    @abstractmethod
    def check_install_download_access(self, raw_key: str, install_id_hash: str) -> str:
        """Authorize protected artifact download during Termux bootstrap.

        Must **not** create or update device bindings (binding stays on first tool run).

        If the key has an **active** binding and *install_id_hash* is empty or does not
        match the stored hash, returns ``wrong_device`` so bound keys cannot be used to
        fetch artifacts from a fresh Termux without proving the same device (use Reset HWID).
        """

    @abstractmethod
    def get_owner_discord_id_for_license_key(self, raw_key: str) -> str | None:
        """Return the redeemed owner's Discord user ID for *raw_key*, or None."""

    @abstractmethod
    def log_license_check(self, **kwargs: Any) -> None:
        """Record a license check event (for audit / rate-limit analysis)."""

    @abstractmethod
    def save_panel_config(
        self, guild_id: str, channel_id: str, message_id: str, updated_by: str
    ) -> None:
        """Persist the Discord panel channel + message ID for a guild."""

    @abstractmethod
    def get_panel_config(self, guild_id: str) -> dict[str, Any] | None:
        """Return panel config dict {channel_id, message_id, updated_by, updated_at} or None."""

    @abstractmethod
    def clear_panel_config(self, guild_id: str) -> None:
        """Remove the saved panel config (does NOT delete keys)."""

    @abstractmethod
    def list_user_keys_with_binding_state(
        self, discord_user_id: str
    ) -> list[dict[str, Any]]:
        """Return key dicts with binding state info for the HWID reset selector.

        Each dict contains:
          key_id, masked_key, full_key_plaintext (optional), status,
          active_binding (bool), device_model, device_label, last_seen_at,
          reset_count_24h, can_reset (bool), reason_if_not_resettable (str | None).

        Revoked keys are excluded from the result.
        """

    @abstractmethod
    def list_user_keys_for_stats(self, discord_user_id: str) -> list[dict[str, Any]]:
        """Rows for Key Stats / download. Never includes key hash/id.

        Each dict may include:
          masked_key, full_key_plaintext (optional), has_stored_ciphertext,
          export_storage_configured, license_status, used, device_display,
          last_seen_at, created_at, plan, reset_count_24h
        """

    def get_user_key_export_rows(self, discord_user_id: str) -> list[dict[str, Any]]:
        """Alias for the same data used by Download Keys (all pages)."""
        return self.list_user_keys_for_stats(discord_user_id)

    @abstractmethod
    def audit_admin_action(
        self,
        actor_discord_user_id: str,
        action: str,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record an admin action in the audit log."""

    def save_license_log_config(
        self, guild_id: str, channel_id: str, updated_by: str
    ) -> None:
        """Persist the channel ID for license event logs in a guild."""

    def get_license_log_config(self, guild_id: str) -> dict[str, Any] | None:
        """Return license log config dict or None."""
        return None

    def clear_license_log_config(self, guild_id: str) -> None:
        """Remove the license log channel config for a guild."""

    def get_license_stats_for_discord_user(
        self, discord_user_id: str
    ) -> dict[str, Any]:
        """Return license stats dict for a Discord user.

        Subclasses should override this for efficient DB-backed stats.
        Base default returns zeroed stats.
        """
        return {
            "discord_user_id": discord_user_id,
            "key_generated_count": 0,
            "key_redeemed_count": 0,
            "unbound_key_count": 0,
            "bound_key_count": 0,
            "reset_hwid_count": 0,
            "key_executed_count": 0,
        }

    def record_key_execution(
        self,
        key_id: str,
        owner_discord_id: str,
        version: str,
        channel: str,
        *,
        is_public_release: bool,
    ) -> None:
        """Record one public-release tool execution. No-op by default."""


# ── Local JSON implementation (dev / tests) ────────────────────────────────────

class LocalJsonLicenseStore(BaseLicenseStore):
    """File-backed license store that uses a local JSON file.

    Suitable for:
    - Offline development
    - Automated tests (pass a temp path)
    - Environments without Supabase access

    Thread-safety: single-file; not concurrent-safe.  Fine for tests and CLI.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or STORE_PATH

    # ── I/O ───────────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            return self._empty_db()
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_db()

    def _save(self, db: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(db, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    @staticmethod
    def _empty_db() -> dict[str, Any]:
        return {
            "users": {},
            "keys": {},
            "bindings": {},
            "reset_logs": [],
            "check_logs": [],
            "panel_configs": {},
            "license_log_configs": {},
            "audit_logs": [],
            "key_limits": {"global": DEFAULT_GLOBAL_MAX_KEYS, "users": {}},
            "panel_limits": {"global": DEFAULT_GLOBAL_MAX_PANEL, "users": {}},
            "panel_usage": {},
        }

    # ── User helpers ──────────────────────────────────────────────────────────

    def get_or_create_user(
        self, discord_user_id: str, discord_username: str | None = None
    ) -> dict[str, Any]:
        db = self._load()
        if discord_user_id not in db["users"]:
            db["users"][discord_user_id] = {
                "discord_username": discord_username or "",
                "max_keys": DEFAULT_MAX_KEYS,
                "is_owner": False,
                "is_blocked": False,
                "last_key_generated_at": None,
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
            }
            self._save(db)
        else:
            changed = False
            if discord_username and discord_username != db["users"][discord_user_id].get("discord_username"):
                db["users"][discord_user_id]["discord_username"] = discord_username
                db["users"][discord_user_id]["updated_at"] = _utc_now()
                changed = True
            if "last_key_generated_at" not in db["users"][discord_user_id]:
                db["users"][discord_user_id]["last_key_generated_at"] = None
                changed = True
            if changed:
                self._save(db)
        return dict(db["users"][discord_user_id])

    def get_user_by_discord_id(self, discord_user_id: str) -> dict[str, Any] | None:
        db = self._load()
        user = db["users"].get(discord_user_id)
        return dict(user) if user else None

    def set_user_max_keys(self, discord_user_id: str, max_keys: int) -> None:
        db = self._load()
        if discord_user_id not in db["users"]:
            raise KeyNotFoundError(f"User not found: {discord_user_id}")
        capped = max(1, int(max_keys))
        db["users"][discord_user_id]["max_keys"] = capped
        db["users"][discord_user_id]["updated_at"] = _utc_now()
        # Sync to key_limits so create_key_for_user respects this value
        limits = self._limits(db)
        if "users" not in limits:
            limits["users"] = {}
        limits["users"][discord_user_id] = {
            "max_keys": capped,
            "updated_by": "legacy_set_user_max_keys",
            "updated_at": _utc_now(),
        }
        self._save(db)

    # ── Global/per-user limit table (license_key_limits) ─────────────────────

    def _limits(self, db: dict[str, Any]) -> dict[str, Any]:
        """Return the key_limits section, creating it if missing."""
        if "key_limits" not in db:
            db["key_limits"] = {"global": DEFAULT_GLOBAL_MAX_KEYS, "users": {}}
        return db["key_limits"]

    def get_global_max_keys(self) -> int:
        db = self._load()
        return int(self._limits(db).get("global", DEFAULT_GLOBAL_MAX_KEYS))

    def get_user_key_limit(self, discord_user_id: str) -> int | None:
        db = self._load()
        users = self._limits(db).get("users", {})
        entry = users.get(discord_user_id)
        if entry is None:
            return None
        return int(entry.get("max_keys", DEFAULT_GLOBAL_MAX_KEYS))

    def set_global_max_keys(self, max_keys: int, updated_by: str) -> None:
        db = self._load()
        limits = self._limits(db)
        limits["global"] = int(max_keys)
        limits["global_updated_by"] = updated_by
        limits["global_updated_at"] = _utc_now()
        self._save(db)

    def set_user_key_limit(
        self, discord_user_id: str, max_keys: int, updated_by: str
    ) -> None:
        db = self._load()
        limits = self._limits(db)
        if "users" not in limits:
            limits["users"] = {}
        limits["users"][discord_user_id] = {
            "max_keys": int(max_keys),
            "updated_by": updated_by,
            "updated_at": _utc_now(),
        }
        self._save(db)

    # ── Panel reset limit helpers (LocalJson) ────────────────────────────────

    def _panel_limits(self, db: dict[str, Any]) -> dict[str, Any]:
        if "panel_limits" not in db:
            db["panel_limits"] = {"global": DEFAULT_GLOBAL_MAX_PANEL, "users": {}}
        return db["panel_limits"]

    def get_global_max_panel(self) -> int:
        db = self._load()
        return int(self._panel_limits(db).get("global", DEFAULT_GLOBAL_MAX_PANEL))

    def get_user_panel_limit(self, discord_user_id: str) -> int | None:
        db = self._load()
        users = self._panel_limits(db).get("users", {})
        entry = users.get(discord_user_id)
        if entry is None:
            return None
        return int(entry.get("max_panel", DEFAULT_GLOBAL_MAX_PANEL))

    def set_global_max_panel(self, max_panel: int, updated_by: str) -> None:
        db = self._load()
        limits = self._panel_limits(db)
        limits["global"] = int(max_panel)
        limits["global_updated_by"] = updated_by
        limits["global_updated_at"] = _utc_now()
        self._save(db)

    def set_user_panel_limit(
        self, discord_user_id: str, max_panel: int, updated_by: str
    ) -> None:
        db = self._load()
        limits = self._panel_limits(db)
        if "users" not in limits:
            limits["users"] = {}
        limits["users"][discord_user_id] = {
            "max_panel": int(max_panel),
            "updated_by": updated_by,
            "updated_at": _utc_now(),
        }
        self._save(db)

    def get_panel_reset_usage_today(self, discord_user_id: str) -> int:
        db = self._load()
        wib_day = get_wib_day()
        usage = db.get("panel_usage", {})
        entry = usage.get(f"{discord_user_id}:{wib_day}")
        if not entry:
            return 0
        return int(entry.get("used_count", 0))

    def record_successful_panel_reset(
        self, discord_user_id: str, unbound_key_count: int
    ) -> int:
        db = self._load()
        wib_day = get_wib_day()
        if "panel_usage" not in db:
            db["panel_usage"] = {}
        key = f"{discord_user_id}:{wib_day}"
        entry = db["panel_usage"].get(key, {"used_count": 0})
        current_count = int(entry.get("used_count", 0))
        # Atomic check + increment
        max_panel = self.get_effective_max_panel(discord_user_id)
        if current_count >= max_panel:
            raise PanelLimitError(
                f"Daily Reset Limit Reached. Reset Uses: {current_count} / {max_panel}. "
                "Resets again at 12:00 AM WIB."
            )
        new_count = current_count + 1
        db["panel_usage"][key] = {
            "discord_user_id": discord_user_id,
            "reset_day_wib": wib_day,
            "used_count": new_count,
            "last_reset_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        self._save(db)
        return new_count

    def count_user_keys(self, discord_user_id: str) -> int:
        db = self._load()
        return sum(
            1 for k in db["keys"].values()
            if k.get("owner_discord_id") == discord_user_id
            and k.get("status") != "revoked"
        )

    # ── Key creation and redemption ───────────────────────────────────────────

    def create_key_for_user(
        self, discord_user_id: str, created_by: str | None = None
    ) -> str:
        user = self.get_or_create_user(discord_user_id)
        if user.get("is_blocked"):
            raise UserLimitError("This account is blocked from generating keys.")

        # Cooldown: first generation has no cooldown; subsequent generations
        # require a 60-second wait (DB-backed, survives restarts).
        last_gen = user.get("last_key_generated_at")
        if last_gen:
            elapsed = _seconds_since(last_gen)
            if elapsed is not None and elapsed < GENERATION_COOLDOWN_SECONDS:
                remaining = int(GENERATION_COOLDOWN_SECONDS - elapsed) + 1
                raise GenerationCooldownError(
                    f"Please wait {remaining} seconds before generating another key.",
                    remaining_seconds=remaining,
                )

        # Lazy-expire unredeemed generated keys older than 24 hours so they
        # don't silently accumulate in the stats as "active".
        self._expire_unredeemed_keys(discord_user_id)

        # Check max active key limit after expiring old keys
        effective_max = self.get_effective_max_keys(discord_user_id)
        active_count = self.count_active_keys_for_limit(discord_user_id)
        if active_count >= effective_max:
            raise UserLimitError(
                f"Key Limit Reached. Active Keys: {active_count} / {effective_max}. "
                "Ask an admin if you need a higher limit."
            )

        raw_key = generate_license_key()
        key_hash = hash_license_key(raw_key)
        parts = raw_key.split("-")
        from . import license_key_export as lke

        ciphertext = lke.encrypt_license_key_plaintext(raw_key)
        now = _utc_now()
        db = self._load()
        db["keys"][key_hash] = {
            "id": key_hash,
            "prefix": f"{parts[0]}-{parts[1]}",
            "suffix": parts[-1],
            "owner_discord_id": discord_user_id,
            "status": "active",
            "plan": "standard",
            "expires_at": None,
            "redeemed_at": None,
            "created_by": created_by or discord_user_id,
            "created_at": now,
            "updated_at": now,
            "key_ciphertext": ciphertext,
            "key_export_available": bool(ciphertext),
        }
        db["users"][discord_user_id]["last_key_generated_at"] = now
        db["users"][discord_user_id]["updated_at"] = now
        self._save(db)
        self.audit_admin_action(
            created_by or discord_user_id,
            "create_key",
            target_type="key",
            target_id=key_hash[:8],
            metadata={"owner": discord_user_id},
        )
        # Return the FULL key — this is the only time it is returned
        return raw_key

    def _expire_unredeemed_keys(self, discord_user_id: str) -> int:
        """Mark expired unredeemed keys as 'expired' status.

        A key is expired-unredeemed when: it belongs to the user, has no
        ``redeemed_at`` timestamp, has no device binding, and is older than
        ``UNREDEEMED_KEY_EXPIRY_SECONDS``.  Modifies the DB in-place.
        Returns the number of keys expired.
        """
        db = self._load()
        expired_count = 0
        for key_hash, record in db["keys"].items():
            if record.get("owner_discord_id") != discord_user_id:
                continue
            if record.get("status") in ("revoked", "expired"):
                continue
            if record.get("redeemed_at"):
                continue
            binding = db.get("bindings", {}).get(key_hash, {})
            if binding.get("install_id_hash"):
                continue
            created = record.get("created_at")
            if not created:
                continue
            age = _seconds_since(created)
            if age is not None and age > UNREDEEMED_KEY_EXPIRY_SECONDS:
                db["keys"][key_hash]["status"] = "expired"
                db["keys"][key_hash]["updated_at"] = _utc_now()
                expired_count += 1
        if expired_count:
            self._save(db)
        return expired_count

    def redeem_key_for_user(self, discord_user_id: str, raw_key: str) -> str:
        try:
            normalized = normalize_license_key(raw_key)
        except LicenseKeyError as exc:
            raise KeyNotFoundError(str(exc)) from exc
        key_hash = hash_license_key(normalized)
        db = self._load()
        key_record = db["keys"].get(key_hash)
        if not key_record:
            raise KeyNotFoundError("Key not found. Check the key and try again.")
        if key_record.get("status") == "revoked":
            raise KeyNotFoundError("This key has been revoked.")
        if key_record.get("status") == "expired":
            raise ExpiredKeyError("This key has expired (unredeemed for more than 24 hours).")

        owner = key_record.get("owner_discord_id")
        if owner == discord_user_id:
            from . import license_key_export as lke

            backfilled = False
            ct = lke.encrypt_license_key_plaintext(normalized)
            if ct:
                existing_ct = (key_record.get("key_ciphertext") or "").strip()
                plain = (
                    lke.decrypt_license_key_ciphertext(existing_ct)
                    if existing_ct
                    else None
                )
                if not existing_ct or not plain:
                    key_record["key_ciphertext"] = ct
                    key_record["key_export_available"] = True
                    key_record["updated_at"] = _utc_now()
                    db["keys"][key_hash] = key_record
                    self._save(db)
                    backfilled = True
            raise KeyAlreadySelfOwned(
                "This key is already attached to your account.",
                export_backfilled=backfilled,
            )
        if owner and owner != discord_user_id:
            raise KeyOwnershipError("This key belongs to another user.")

        # Unowned key: check expiry before attaching.
        # An unowned key older than 24h with no prior binding is expired.
        if not key_record.get("redeemed_at"):
            binding = db.get("bindings", {}).get(key_hash, {})
            if not binding.get("install_id_hash"):
                created = key_record.get("created_at")
                if created:
                    age = _seconds_since(created)
                    if age is not None and age > UNREDEEMED_KEY_EXPIRY_SECONDS:
                        raise ExpiredKeyError(
                            "This key has expired (not claimed within 24 hours of creation)."
                        )

        self.get_or_create_user(discord_user_id)
        # Check max active key limit before attaching the key
        effective_max = self.get_effective_max_keys(discord_user_id)
        active_count = self.count_active_keys_for_limit(discord_user_id)
        if active_count >= effective_max:
            raise UserLimitError(
                f"Key Limit Reached. Active Keys: {active_count} / {effective_max}. "
                "You cannot redeem another key unless your limit is increased or an active key is removed."
            )
        now = _utc_now()
        db["keys"][key_hash]["owner_discord_id"] = discord_user_id
        db["keys"][key_hash]["redeemed_at"] = now
        db["keys"][key_hash]["updated_at"] = now
        self._save(db)
        self.audit_admin_action(discord_user_id, "redeem_key", target_type="key", target_id=key_hash[:8])
        return normalized

    def list_user_keys(self, discord_user_id: str) -> list[dict[str, Any]]:
        from . import license_key_export as lke

        db = self._load()
        result: list[dict[str, Any]] = []
        for key_hash, record in db["keys"].items():
            if record.get("owner_discord_id") != discord_user_id:
                continue
            binding = db["bindings"].get(key_hash, {})
            active = bool(binding.get("is_active"))
            masked = f"{record.get('prefix', 'DENG-????')}...{record.get('suffix', '????')}"
            ciphertext = record.get("key_ciphertext") or ""
            full_plain = lke.decrypt_license_key_ciphertext(ciphertext) if ciphertext else None
            if active:
                bound_device = binding.get("device_model") or "(unbound)"
                last_seen = binding.get("last_seen_at")
            else:
                bound_device = "(unbound)"
                last_seen = None
            result.append({
                "id": key_hash,
                "masked_key": masked,
                "full_key_plaintext": full_plain,
                "status": record.get("status", "unknown"),
                "plan": record.get("plan", "standard"),
                "bound_device": bound_device,
                "last_seen_at": last_seen,
                "created_at": record.get("created_at"),
            })
        return result

    def list_user_keys_with_binding_state(
        self, discord_user_id: str
    ) -> list[dict[str, Any]]:
        from . import license_key_export as lke

        db = self._load()
        result: list[dict[str, Any]] = []
        for key_hash, record in db["keys"].items():
            if record.get("owner_discord_id") != discord_user_id:
                continue
            if record.get("status") == "revoked":
                continue
            binding = db.get("bindings", {}).get(key_hash, {})
            active_binding = bool(binding.get("is_active"))
            masked = f"{record.get('prefix', 'DENG-????')}...{record.get('suffix', '????')}"
            ciphertext = record.get("key_ciphertext") or ""
            full_plain = lke.decrypt_license_key_ciphertext(ciphertext) if ciphertext else None
            last_seen_at = binding.get("last_seen_at")
            reset_count = self.get_reset_count_24h(key_hash)
            # Determine can_reset and reason
            reason: str | None = None
            if not active_binding:
                can_reset = False
                reason = "No device bound — start the tool first"
            else:
                # Cooldown is based only on actual reset history, not on last_seen_at.
                # First reset is always allowed immediately if no previous reset has occurred.
                can_reset = True
            result.append({
                "key_id": key_hash,
                "masked_key": masked,
                "full_key_plaintext": full_plain,
                "status": record.get("status", "unknown"),
                "active_binding": active_binding,
                "device_model": binding.get("device_model", ""),
                "device_label": binding.get("device_label", ""),
                "last_seen_at": last_seen_at,
                "reset_count_24h": reset_count,
                "can_reset": can_reset,
                "reason_if_not_resettable": reason,
            })
        return result

    def list_user_keys_for_stats(self, discord_user_id: str) -> list[dict[str, Any]]:
        from . import license_key_export as lke

        db = self._load()
        rows: list[dict[str, Any]] = []
        exp_cfg = lke.is_export_secret_configured()
        for key_hash, record in db["keys"].items():
            if record.get("owner_discord_id") != discord_user_id:
                continue
            binding = db.get("bindings", {}).get(key_hash, {})
            active_binding = bool(binding.get("is_active"))
            masked = f"{record.get('prefix', 'DENG-????')}...{record.get('suffix', '????')}"
            lic_status = record.get("status", "active")
            ciphertext = record.get("key_ciphertext") or ""
            has_blob = bool(ciphertext)
            full_plain = lke.decrypt_license_key_ciphertext(ciphertext) if has_blob else None
            device = (
                (binding.get("device_model") or binding.get("device_label") or "").strip()
                if active_binding
                else ""
            ) or None
            reset_count = self.get_reset_count_24h(key_hash)
            plan = record.get("plan", "standard") or "standard"
            rows.append({
                "masked_key": masked,
                "full_key_plaintext": full_plain,
                "has_stored_ciphertext": has_blob,
                "export_storage_configured": exp_cfg,
                "license_status": lic_status,
                "used": active_binding,
                "device_display": device,
                "last_seen_at": binding.get("last_seen_at"),
                "created_at": record.get("created_at"),
                "expires_at": record.get("expires_at"),
                "redeemed_at": record.get("redeemed_at"),
                "plan": plan,
                "reset_count_24h": reset_count,
            })
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return rows

    def recover_key_export_for_user(self, discord_user_id: str, raw_key: str) -> str:
        from . import license_key_export as lke

        if not lke.is_export_secret_configured():
            raise ExportStorageUnavailable(
                "Full key export storage is not enabled on this server."
            )
        try:
            normalized = normalize_license_key(raw_key)
        except LicenseKeyError as exc:
            raise KeyNotFoundError(str(exc)) from exc
        key_hash = hash_license_key(normalized)
        db = self._load()
        key_record = db["keys"].get(key_hash)
        if not key_record:
            raise KeyNotFoundError("Key not found. Check the key and try again.")
        if key_record.get("owner_discord_id") != discord_user_id:
            raise KeyOwnershipError("That key does not belong to your account.")
        if key_record.get("status") == "revoked":
            raise KeyNotFoundError("This key has been revoked.")

        existing_ct = (key_record.get("key_ciphertext") or "").strip()
        if existing_ct:
            if lke.decrypt_license_key_ciphertext(existing_ct):
                return "already_exportable"

        ciphertext = lke.encrypt_license_key_plaintext(normalized)
        if not ciphertext:
            raise ExportStorageUnavailable("Could not encrypt key for storage.")

        key_record["key_ciphertext"] = ciphertext
        key_record["key_export_available"] = True
        key_record["updated_at"] = _utc_now()
        db["keys"][key_hash] = key_record
        self._save(db)
        self.audit_admin_action(
            discord_user_id,
            "recover_key_export",
            target_type="key",
            target_id=key_hash[:8],
        )
        return "stored"

    # ── HWID reset ────────────────────────────────────────────────────────────

    def reset_hwid(self, discord_user_id: str, key_id: str) -> None:
        db = self._load()
        key_record = db["keys"].get(key_id)
        if not key_record:
            raise KeyNotFoundError(f"Key not found: {key_id}")
        if key_record.get("owner_discord_id") != discord_user_id:
            raise KeyOwnershipError("You do not own this key.")
        # Check for an active device binding FIRST.
        # If none exists, return without logging a reset or consuming a reset slot.
        existing_binding = db.get("bindings", {}).get(key_id)
        if not existing_binding or not existing_binding.get("is_active"):
            raise NoActiveBindingError(
                "No device is currently bound to this key. "
                "Start the tool once to activate your device binding."
            )
        # Check reset count — based only on actual HWID reset history, never on last_seen_at.
        # A key used for license verification 1 minute ago must still be resettable on first attempt.
        old_hash = existing_binding.get("install_id_hash")
        # Deactivate binding
        db["bindings"][key_id]["is_active"] = False
        # Log the reset (only written when an actual binding is cleared)
        db["reset_logs"].append({
            "key_id": key_id,
            "owner_discord_id": discord_user_id,
            "old_install_id_hash": old_hash,
            "reason": "user_requested",
            "created_at": _utc_now(),
        })
        self._save(db)
        self.audit_admin_action(
            discord_user_id, "reset_hwid",
            target_type="key", target_id=key_id[:8],
            metadata={"old_install_id_hash": (old_hash or "")[:8]},
        )

    def get_reset_count_24h(self, key_id: str) -> int:
        db = self._load()
        cutoff = time.time() - 86400
        count = 0
        for entry in db.get("reset_logs", []):
            if entry.get("key_id") != key_id:
                continue
            elapsed = _seconds_since(entry.get("created_at"))
            if elapsed is not None and elapsed <= 86400:
                count += 1
        return count

    def get_last_seen_at(self, key_id: str) -> str | None:
        db = self._load()
        return db.get("bindings", {}).get(key_id, {}).get("last_seen_at")

    # ── Device binding ────────────────────────────────────────────────────────

    def validate_existing_binding(
        self,
        raw_key: str,
        install_id_hash: str,
        device_model: str = "",
        app_version: str = "",
        device_label: str = "",
    ) -> str:
        """Validate-only: never writes bindings or key rows."""
        try:
            normalized = normalize_license_key(raw_key)
        except LicenseKeyError:
            return RESULT_NOT_FOUND
        key_hash = hash_license_key(normalized)
        db = self._load()
        record = db["keys"].get(key_hash)
        if not record:
            return RESULT_NOT_FOUND
        if record.get("status") == "revoked":
            return RESULT_REVOKED
        expires = record.get("expires_at")
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires)
                if datetime.now(timezone.utc) > exp_dt:
                    return RESULT_EXPIRED
            except (ValueError, TypeError):
                pass
        owner_id = record.get("owner_discord_id")
        if owner_id is None or str(owner_id).strip() == "":
            return RESULT_KEY_NOT_REDEEMED
        binding = db.get("bindings", {}).get(key_hash)
        if binding and binding.get("is_active"):
            bound_hash = binding.get("install_id_hash")
            if bound_hash and bound_hash != install_id_hash:
                return RESULT_WRONG_DEVICE
            return RESULT_ACTIVE
        return RESULT_REQUIRES_MANUAL_REBIND

    def get_binding_snapshot(self, raw_key: str) -> dict[str, Any]:
        """Return masked binding state for audit tests (read-only)."""
        try:
            normalized = normalize_license_key(raw_key)
        except LicenseKeyError:
            return {"found": False}
        key_hash = hash_license_key(normalized)
        db = self._load()
        record = db["keys"].get(key_hash) or {}
        binding = db.get("bindings", {}).get(key_hash) or {}
        inst = str(binding.get("install_id_hash") or "")
        return {
            "found": True,
            "key_id_prefix": key_hash[:8],
            "is_active": bool(binding.get("is_active")),
            "install_id_hash_prefix": inst[:8] if inst else "",
            "bound_at": binding.get("bound_at"),
            "redeemed_at": record.get("redeemed_at"),
            "updated_at": record.get("updated_at"),
        }

    def bind_or_check_device(
        self,
        raw_key: str,
        install_id_hash: str,
        device_model: str,
        app_version: str,
        device_label: str = "",
    ) -> str:
        lbl = (device_label or "").strip()[:80]
        try:
            normalized = normalize_license_key(raw_key)
        except LicenseKeyError:
            return RESULT_NOT_FOUND
        key_hash = hash_license_key(normalized)
        db = self._load()
        record = db["keys"].get(key_hash)
        if not record:
            self.log_license_check(
                key_id=None, install_id_hash=install_id_hash,
                result=RESULT_NOT_FOUND, device_model=device_model, app_version=app_version,
            )
            return RESULT_NOT_FOUND
        if record.get("status") == "revoked":
            self.log_license_check(
                key_id=key_hash, install_id_hash=install_id_hash,
                result=RESULT_REVOKED, device_model=device_model, app_version=app_version,
            )
            return RESULT_REVOKED
        expires = record.get("expires_at")
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires)
                if datetime.now(timezone.utc) > exp_dt:
                    self.log_license_check(
                        key_id=key_hash, install_id_hash=install_id_hash,
                        result=RESULT_EXPIRED, device_model=device_model, app_version=app_version,
                    )
                    return RESULT_EXPIRED
            except (ValueError, TypeError):
                pass
        owner_id = record.get("owner_discord_id")
        if owner_id is None or str(owner_id).strip() == "":
            self.log_license_check(
                key_id=key_hash,
                install_id_hash=install_id_hash,
                result=RESULT_KEY_NOT_REDEEMED,
                device_model=device_model,
                app_version=app_version,
            )
            return RESULT_KEY_NOT_REDEEMED
        # Check device binding
        binding = db.get("bindings", {}).get(key_hash)
        now_ts = _utc_now()
        if binding and binding.get("is_active"):
            bound_hash = binding.get("install_id_hash")
            if bound_hash and bound_hash != install_id_hash:
                self.log_license_check(
                    key_id=key_hash, install_id_hash=install_id_hash,
                    result=RESULT_WRONG_DEVICE, device_model=device_model, app_version=app_version,
                )
                return RESULT_WRONG_DEVICE
            # Same device — update heartbeat + device info
            db["bindings"][key_hash]["last_seen_at"] = now_ts
            db["bindings"][key_hash]["last_status"] = RESULT_ACTIVE
            db["bindings"][key_hash]["device_model"] = (device_model or "")[:120] or binding.get("device_model", "")
            db["bindings"][key_hash]["device_label"] = lbl
        elif binding and not binding.get("is_active"):
            # Inactive binding (e.g. after HWID reset) — manual rebind only.
            db["bindings"][key_hash].update({
                "install_id_hash": install_id_hash,
                "device_label": lbl,
                "device_model": (device_model or "")[:120],
                "bound_at": now_ts,
                "last_seen_at": now_ts,
                "last_status": RESULT_ACTIVE,
                "is_active": True,
            })
        else:
            # New binding — also mark the key as redeemed (first activation)
            db.setdefault("bindings", {})[key_hash] = {
                "install_id_hash": install_id_hash,
                "device_label": lbl,
                "device_model": (device_model or "")[:120],
                "bound_at": now_ts,
                "last_seen_at": now_ts,
                "last_status": RESULT_ACTIVE,
                "is_active": True,
            }
            if not record.get("redeemed_at"):
                db["keys"][key_hash]["redeemed_at"] = now_ts
                db["keys"][key_hash]["updated_at"] = now_ts
        if not db["keys"][key_hash].get("redeemed_at"):
            db["keys"][key_hash]["redeemed_at"] = now_ts
            db["keys"][key_hash]["updated_at"] = now_ts
        self._save(db)
        self.log_license_check(
            key_id=key_hash, install_id_hash=install_id_hash,
            result=RESULT_ACTIVE, device_model=device_model, app_version=app_version,
        )
        return RESULT_ACTIVE

    def check_install_download_access(self, raw_key: str, install_id_hash: str) -> str:
        try:
            normalized = normalize_license_key(raw_key)
        except LicenseKeyError:
            return RESULT_NOT_FOUND
        key_hash = hash_license_key(normalized)
        db = self._load()
        record = db["keys"].get(key_hash)
        if not record:
            self.log_license_check(
                key_id=None,
                install_id_hash=install_id_hash,
                result=RESULT_NOT_FOUND,
                device_model="bootstrap",
                app_version="install",
            )
            return RESULT_NOT_FOUND
        if record.get("status") == "revoked":
            self.log_license_check(
                key_id=key_hash,
                install_id_hash=install_id_hash,
                result=RESULT_REVOKED,
                device_model="bootstrap",
                app_version="install",
            )
            return RESULT_REVOKED
        if _iso_expired(record.get("expires_at")):
            self.log_license_check(
                key_id=key_hash,
                install_id_hash=install_id_hash,
                result=RESULT_EXPIRED,
                device_model="bootstrap",
                app_version="install",
            )
            return RESULT_EXPIRED
        owner_id = record.get("owner_discord_id")
        if owner_id is None or str(owner_id).strip() == "":
            self.log_license_check(
                key_id=key_hash,
                install_id_hash=install_id_hash,
                result=RESULT_KEY_NOT_REDEEMED,
                device_model="bootstrap",
                app_version="install",
            )
            return RESULT_KEY_NOT_REDEEMED
        binding = db.get("bindings", {}).get(key_hash)
        if binding and binding.get("is_active"):
            bound_hash = binding.get("install_id_hash")
            if not install_id_hash.strip():
                self.log_license_check(
                    key_id=key_hash,
                    install_id_hash=install_id_hash,
                    result=RESULT_WRONG_DEVICE,
                    device_model="bootstrap",
                    app_version="install",
                )
                return RESULT_WRONG_DEVICE
            if bound_hash and bound_hash != install_id_hash:
                self.log_license_check(
                    key_id=key_hash,
                    install_id_hash=install_id_hash,
                    result=RESULT_WRONG_DEVICE,
                    device_model="bootstrap",
                    app_version="install",
                )
                return RESULT_WRONG_DEVICE
        self.log_license_check(
            key_id=key_hash,
            install_id_hash=install_id_hash,
            result=RESULT_ACTIVE,
            device_model="bootstrap",
            app_version="install",
        )
        return RESULT_ACTIVE

    def get_owner_discord_id_for_license_key(self, raw_key: str) -> str | None:
        try:
            normalized = normalize_license_key(raw_key)
        except LicenseKeyError:
            return None
        key_hash = hash_license_key(normalized)
        db = self._load()
        record = db["keys"].get(key_hash)
        if not record:
            return None
        owner_id = record.get("owner_discord_id")
        if owner_id is None or str(owner_id).strip() == "":
            return None
        return str(owner_id).strip()

    def log_license_check(self, **kwargs: Any) -> None:
        db = self._load()
        entry = {
            "key_id": kwargs.get("key_id"),
            "install_id_hash": kwargs.get("install_id_hash"),
            "result": kwargs.get("result", "unknown"),
            "device_model": kwargs.get("device_model", ""),
            "app_version": kwargs.get("app_version", ""),
            "created_at": _utc_now(),
        }
        db.setdefault("check_logs", []).append(entry)
        # Keep last 1000 check log entries to avoid unbounded growth
        db["check_logs"] = db["check_logs"][-1000:]
        self._save(db)

    # ── Panel config ──────────────────────────────────────────────────────────

    def save_panel_config(
        self, guild_id: str, channel_id: str, message_id: str, updated_by: str
    ) -> None:
        db = self._load()
        db.setdefault("panel_configs", {})[guild_id] = {
            "channel_id": channel_id,
            "message_id": message_id,
            "updated_by": updated_by,
            "updated_at": _utc_now(),
        }
        self._save(db)

    def get_panel_config(self, guild_id: str) -> dict[str, Any] | None:
        db = self._load()
        cfg = db.get("panel_configs", {}).get(guild_id)
        return dict(cfg) if cfg else None

    def clear_panel_config(self, guild_id: str) -> None:
        db = self._load()
        db.setdefault("panel_configs", {}).pop(guild_id, None)
        self._save(db)

    # ── Audit log ─────────────────────────────────────────────────────────────

    def audit_admin_action(
        self,
        actor_discord_user_id: str,
        action: str,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        db = self._load()
        db.setdefault("audit_logs", []).append({
            "actor_discord_id": actor_discord_user_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "metadata": metadata or {},
            "created_at": _utc_now(),
        })
        # Keep last 5000 audit entries
        db["audit_logs"] = db["audit_logs"][-5000:]
        self._save(db)

    # ── License log channel config ─────────────────────────────────────────────

    def save_license_log_config(
        self, guild_id: str, channel_id: str, updated_by: str
    ) -> None:
        db = self._load()
        db.setdefault("license_log_configs", {})[guild_id] = {
            "channel_id": channel_id,
            "updated_by": updated_by,
            "updated_at": _utc_now(),
        }
        self._save(db)

    def get_license_log_config(self, guild_id: str) -> dict[str, Any] | None:
        db = self._load()
        cfg = db.get("license_log_configs", {}).get(guild_id)
        return dict(cfg) if cfg else None

    def clear_license_log_config(self, guild_id: str) -> None:
        db = self._load()
        db.setdefault("license_log_configs", {}).pop(guild_id, None)
        self._save(db)

    # ── Stats ──────────────────────────────────────────────────────────────────

    def get_license_stats_for_discord_user(
        self, discord_user_id: str
    ) -> dict[str, Any]:
        """Return license key statistics for a Discord user.

        This is the canonical stats function — designed to be called from
        Discord embeds, the /license admin command, and future ecosystem
        integrations.  All counts are computed from the local JSON store.

        Returns dict with:
          key_generated_count  — active visible keys owned by this user
          key_redeemed_count   — active visible keys redeemed/activated
          unbound_key_count    — active visible keys with no active device binding
          bound_key_count      — active visible keys with an active device binding
          reset_hwid_count     — total HWID resets on this user's keys
          key_executed_count   — public-release tool executions (always 0 for local store)
        """
        from agent.key_stats_format import (
            compute_active_visible_stats,
            filter_active_visible_license_rows,
        )

        rows = self.list_user_keys_for_stats(discord_user_id)
        active_rows = filter_active_visible_license_rows(rows)
        counts = compute_active_visible_stats(active_rows)
        db = self._load()
        reset_count = sum(
            1 for entry in db.get("reset_logs", [])
            if entry.get("owner_discord_id") == discord_user_id
        )
        return {
            "discord_user_id": discord_user_id,
            **counts,
            "reset_hwid_count": reset_count,
            "key_executed_count": 0,  # local store: execution tracking not available
        }


def get_license_stats_for_discord_user(
    store: "BaseLicenseStore", discord_user_id: str
) -> dict[str, Any]:
    """Module-level wrapper — callable from any ecosystem code without holding
    a store reference directly.  Delegates to the store's own method if
    available; falls back to a safe empty stats dict."""
    try:
        return store.get_license_stats_for_discord_user(discord_user_id)
    except Exception:  # noqa: BLE001
        return {
            "discord_user_id": discord_user_id,
            "key_generated_count": 0,
            "key_redeemed_count": 0,
            "unbound_key_count": 0,
            "bound_key_count": 0,
            "reset_hwid_count": 0,
            "key_executed_count": 0,
        }


# ── Supabase implementation (production) ──────────────────────────────────────

class SupabaseLicenseStore(BaseLicenseStore):
    """Production Supabase-backed license store.

    Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the environment.
    The service-role key is used server-side only and is never exposed to clients.
    """

    def __init__(self) -> None:
        import os
        from dotenv import load_dotenv

        load_dotenv()
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            raise RuntimeError(
                "Both SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set."
            )
        try:
            from supabase import create_client
        except ImportError as exc:
            raise RuntimeError(
                "supabase-py is not installed. Run: pip install supabase"
            ) from exc
        self._client = create_client(url, key)

    # ── User helpers ──────────────────────────────────────────────────────────

    def get_or_create_user(
        self, discord_user_id: str, discord_username: str | None = None
    ) -> dict[str, Any]:
        res = (
            self._client.table("license_users")
            .select("*")
            .eq("discord_user_id", discord_user_id)
            .execute()
        )
        if res.data:
            user = res.data[0]
            if discord_username and discord_username != user.get("discord_username"):
                self._client.table("license_users").update(
                    {"discord_username": discord_username}
                ).eq("discord_user_id", discord_user_id).execute()
                user["discord_username"] = discord_username
            return user
        # Create new user
        res = self._client.table("license_users").insert(
            {
                "discord_user_id": discord_user_id,
                "discord_username": discord_username or "",
            }
        ).execute()
        return res.data[0]

    def get_user_by_discord_id(self, discord_user_id: str) -> dict[str, Any] | None:
        res = (
            self._client.table("license_users")
            .select("*")
            .eq("discord_user_id", discord_user_id)
            .execute()
        )
        return res.data[0] if res.data else None

    def set_user_max_keys(self, discord_user_id: str, max_keys: int) -> None:
        res = (
            self._client.table("license_users")
            .update({"max_keys": max(1, int(max_keys))})
            .eq("discord_user_id", discord_user_id)
            .execute()
        )
        if not res.data:
            raise KeyNotFoundError(f"User not found: {discord_user_id}")

    # ── Global/per-user limit table (license_key_limits) ─────────────────────

    def get_global_max_keys(self) -> int:
        try:
            res = (
                self._client.table("license_key_limits")
                .select("max_keys")
                .eq("scope", "global")
                .execute()
            )
            if res.data:
                return int(res.data[0]["max_keys"])
        except Exception:
            pass
        return DEFAULT_GLOBAL_MAX_KEYS

    def get_user_key_limit(self, discord_user_id: str) -> int | None:
        try:
            res = (
                self._client.table("license_key_limits")
                .select("max_keys")
                .eq("scope", "user")
                .eq("discord_user_id", discord_user_id)
                .execute()
            )
            if res.data:
                return int(res.data[0]["max_keys"])
        except Exception:
            pass
        return None

    def set_global_max_keys(self, max_keys: int, updated_by: str) -> None:
        now = _utc_now()
        try:
            res = (
                self._client.table("license_key_limits")
                .update({
                    "max_keys": int(max_keys),
                    "updated_by_discord_id": updated_by,
                    "updated_at": now,
                })
                .eq("scope", "global")
                .execute()
            )
            if not (res.data):
                self._client.table("license_key_limits").insert({
                    "scope": "global",
                    "max_keys": int(max_keys),
                    "updated_by_discord_id": updated_by,
                    "created_at": now,
                    "updated_at": now,
                }).execute()
        except Exception as exc:
            raise StoreError(f"Failed to set global max keys: {exc}") from exc

    def set_user_key_limit(
        self, discord_user_id: str, max_keys: int, updated_by: str
    ) -> None:
        now = _utc_now()
        try:
            res = (
                self._client.table("license_key_limits")
                .update({
                    "max_keys": int(max_keys),
                    "updated_by_discord_id": updated_by,
                    "updated_at": now,
                })
                .eq("scope", "user")
                .eq("discord_user_id", discord_user_id)
                .execute()
            )
            if not (res.data):
                self._client.table("license_key_limits").insert({
                    "scope": "user",
                    "discord_user_id": discord_user_id,
                    "max_keys": int(max_keys),
                    "updated_by_discord_id": updated_by,
                    "created_at": now,
                    "updated_at": now,
                }).execute()
        except Exception as exc:
            raise StoreError(f"Failed to set user key limit: {exc}") from exc

    # ── Panel reset limit helpers (Supabase) ─────────────────────────────────

    def get_global_max_panel(self) -> int:
        try:
            res = (
                self._client.table("license_key_limits")
                .select("max_panel")
                .eq("scope", "global")
                .execute()
            )
            if res.data and res.data[0].get("max_panel") is not None:
                return int(res.data[0]["max_panel"])
        except Exception:
            pass
        return DEFAULT_GLOBAL_MAX_PANEL

    def get_user_panel_limit(self, discord_user_id: str) -> int | None:
        try:
            res = (
                self._client.table("license_key_limits")
                .select("max_panel")
                .eq("scope", "user")
                .eq("discord_user_id", discord_user_id)
                .execute()
            )
            if res.data and res.data[0].get("max_panel") is not None:
                return int(res.data[0]["max_panel"])
        except Exception:
            pass
        return None

    def set_global_max_panel(self, max_panel: int, updated_by: str) -> None:
        now = _utc_now()
        try:
            res = (
                self._client.table("license_key_limits")
                .update({
                    "max_panel": int(max_panel),
                    "updated_by_discord_id": updated_by,
                    "updated_at": now,
                })
                .eq("scope", "global")
                .execute()
            )
            if not res.data:
                self._client.table("license_key_limits").insert({
                    "scope": "global",
                    "max_keys": DEFAULT_GLOBAL_MAX_KEYS,
                    "max_panel": int(max_panel),
                    "updated_by_discord_id": updated_by,
                    "created_at": now,
                    "updated_at": now,
                }).execute()
        except Exception as exc:
            raise StoreError(f"Failed to set global max panel: {exc}") from exc

    def set_user_panel_limit(
        self, discord_user_id: str, max_panel: int, updated_by: str
    ) -> None:
        now = _utc_now()
        try:
            res = (
                self._client.table("license_key_limits")
                .update({
                    "max_panel": int(max_panel),
                    "updated_by_discord_id": updated_by,
                    "updated_at": now,
                })
                .eq("scope", "user")
                .eq("discord_user_id", discord_user_id)
                .execute()
            )
            if not res.data:
                self._client.table("license_key_limits").insert({
                    "scope": "user",
                    "discord_user_id": discord_user_id,
                    "max_keys": DEFAULT_GLOBAL_MAX_KEYS,
                    "max_panel": int(max_panel),
                    "updated_by_discord_id": updated_by,
                    "created_at": now,
                    "updated_at": now,
                }).execute()
        except Exception as exc:
            raise StoreError(f"Failed to set user panel limit: {exc}") from exc

    def get_panel_reset_usage_today(self, discord_user_id: str) -> int:
        try:
            wib_day = get_wib_day()
            res = (
                self._client.table("license_panel_reset_usage")
                .select("used_count")
                .eq("discord_user_id", discord_user_id)
                .eq("reset_day_wib", wib_day)
                .execute()
            )
            if res.data:
                return int(res.data[0]["used_count"])
        except Exception:
            pass
        return 0

    def record_successful_panel_reset(
        self, discord_user_id: str, unbound_key_count: int
    ) -> int:
        """Atomically check limit and increment daily panel-reset counter."""
        now = _utc_now()
        wib_day = get_wib_day()
        try:
            res = (
                self._client.table("license_panel_reset_usage")
                .select("used_count")
                .eq("discord_user_id", discord_user_id)
                .eq("reset_day_wib", wib_day)
                .execute()
            )
            current_count = int(res.data[0]["used_count"]) if res.data else 0
            max_panel = self.get_effective_max_panel(discord_user_id)
            if current_count >= max_panel:
                raise PanelLimitError(
                    f"Daily Reset Limit Reached. Reset Uses: {current_count} / {max_panel}. "
                    "Resets again at 12:00 AM WIB."
                )
            new_count = current_count + 1
            if res.data:
                self._client.table("license_panel_reset_usage").update({
                    "used_count": new_count,
                    "last_reset_at": now,
                    "updated_at": now,
                }).eq("discord_user_id", discord_user_id).eq(
                    "reset_day_wib", wib_day
                ).execute()
            else:
                self._client.table("license_panel_reset_usage").insert({
                    "discord_user_id": discord_user_id,
                    "reset_day_wib": wib_day,
                    "used_count": 1,
                    "last_reset_at": now,
                    "created_at": now,
                    "updated_at": now,
                }).execute()
            return new_count
        except PanelLimitError:
            raise
        except Exception as exc:
            raise StoreError(f"Failed to record panel reset: {exc}") from exc

    def count_user_keys(self, discord_user_id: str) -> int:
        res = (
            self._client.table("license_keys")
            .select("id", count="exact")
            .eq("owner_discord_id", discord_user_id)
            .neq("status", "revoked")
            .execute()
        )
        return res.count or 0

    # ── Key creation and redemption ───────────────────────────────────────────

    def create_key_for_user(
        self, discord_user_id: str, created_by: str | None = None
    ) -> str:
        user = self.get_or_create_user(discord_user_id)
        if user.get("is_blocked"):
            raise UserLimitError("This account is blocked from generating keys.")

        # Cooldown check — use last_key_generated_at from user record
        last_gen = user.get("last_key_generated_at")
        if last_gen:
            try:
                elapsed = _seconds_since(last_gen)
                if elapsed is not None and elapsed < GENERATION_COOLDOWN_SECONDS:
                    remaining = int(GENERATION_COOLDOWN_SECONDS - elapsed) + 1
                    raise GenerationCooldownError(
                        f"Please wait {remaining} seconds before generating another key.",
                        remaining_seconds=remaining,
                    )
            except GenerationCooldownError:
                raise
            except Exception:
                pass

        # Check max active key limit
        effective_max = self.get_effective_max_keys(discord_user_id)
        active_count = self.count_active_keys_for_limit(discord_user_id)
        if active_count >= effective_max:
            raise UserLimitError(
                f"Key Limit Reached. Active Keys: {active_count} / {effective_max}. "
                "Ask an admin if you need a higher limit."
            )

        raw_key = generate_license_key()
        key_hash = hash_license_key(raw_key)
        parts = raw_key.split("-")
        from . import license_key_export as lke

        ciphertext = lke.encrypt_license_key_plaintext(raw_key)
        row: dict[str, Any] = {
            "id": key_hash,
            "prefix": f"{parts[0]}-{parts[1]}",
            "suffix": parts[-1],
            "owner_discord_id": discord_user_id,
            "status": "active",
            "plan": "standard",
            "expires_at": None,
            "redeemed_at": None,
            "created_by": created_by or discord_user_id,
        }
        if ciphertext:
            row["key_ciphertext"] = ciphertext
            row["key_export_available"] = True
        else:
            row["key_export_available"] = False
        try:
            self._client.table("license_keys").insert(row).execute()
        except Exception as exc:
            err = str(exc).lower()
            if (
                "key_ciphertext" in err
                or "key_export_available" in err
                or "redeemed_at" in err
                or "column" in err
                or "pgrst204" in err
            ):
                row.pop("key_ciphertext", None)
                row.pop("key_export_available", None)
                row.pop("redeemed_at", None)
                self._client.table("license_keys").insert(row).execute()
            else:
                raise
        # Update last_key_generated_at in user record
        try:
            self._client.table("license_users").update(
                {"last_key_generated_at": _utc_now()}
            ).eq("discord_user_id", discord_user_id).execute()
        except Exception:
            pass
        self.audit_admin_action(
            created_by or discord_user_id,
            "create_key",
            target_type="key",
            target_id=key_hash[:8],
            metadata={"owner": discord_user_id},
        )
        return raw_key

    def redeem_key_for_user(self, discord_user_id: str, raw_key: str) -> str:
        try:
            normalized = normalize_license_key(raw_key)
        except LicenseKeyError as exc:
            raise KeyNotFoundError(str(exc)) from exc
        key_hash = hash_license_key(normalized)
        res = (
            self._client.table("license_keys")
            .select("*")
            .eq("id", key_hash)
            .execute()
        )
        if not res.data:
            raise KeyNotFoundError("Key not found. Check the key and try again.")
        record = res.data[0]
        if record.get("status") == "revoked":
            raise KeyNotFoundError("This key has been revoked.")
        if _iso_expired(record.get("expires_at")):
            raise ExpiredKeyError(
                "This key has expired (not claimed within 24 hours of creation)."
            )
        owner = record.get("owner_discord_id")
        if owner == discord_user_id:
            from . import license_key_export as lke

            backfilled = False
            ct = lke.encrypt_license_key_plaintext(normalized)
            if ct:
                existing_ct = (record.get("key_ciphertext") or "").strip()
                plain = (
                    lke.decrypt_license_key_ciphertext(existing_ct)
                    if existing_ct
                    else None
                )
                if not existing_ct or not plain:
                    try:
                        self._client.table("license_keys").update({
                            "key_ciphertext": ct,
                            "key_export_available": True,
                        }).eq("id", key_hash).execute()
                        backfilled = True
                    except Exception as exc:
                        err = str(exc).lower()
                        if (
                            "column" in err
                            or "pgrst204" in err
                            or "key_ciphertext" in err
                            or "key_export_available" in err
                        ):
                            pass
                        else:
                            raise
            raise KeyAlreadySelfOwned(
                "This key is already attached to your account.",
                export_backfilled=backfilled,
            )
        if owner and owner != discord_user_id:
            raise KeyOwnershipError("This key belongs to another user.")
        # Check max active key limit before attaching
        effective_max = self.get_effective_max_keys(discord_user_id)
        active_count = self.count_active_keys_for_limit(discord_user_id)
        if active_count >= effective_max:
            raise UserLimitError(
                f"Key Limit Reached. Active Keys: {active_count} / {effective_max}. "
                "You cannot redeem another key unless your limit is increased or an active key is removed."
            )
        update_payload = {"owner_discord_id": discord_user_id, "expires_at": None}
        try:
            update_payload["redeemed_at"] = _utc_now()
            self._client.table("license_keys").update(update_payload).eq("id", key_hash).execute()
        except Exception as exc:
            err = str(exc).lower()
            if "redeemed_at" in err or "column" in err or "pgrst204" in err:
                update_payload.pop("redeemed_at", None)
                self._client.table("license_keys").update(update_payload).eq("id", key_hash).execute()
            else:
                raise
        self.audit_admin_action(
            discord_user_id,
            "redeem_key",
            target_type="key",
            target_id=key_hash[:8],
        )
        return normalized

    def list_user_keys(self, discord_user_id: str) -> list[dict[str, Any]]:
        from . import license_key_export as lke

        try:
            res = (
                self._client.table("license_keys")
                .select(
                    "id, prefix, suffix, status, plan, created_at, key_ciphertext, key_export_available"
                )
                .eq("owner_discord_id", discord_user_id)
                .execute()
            )
            records = res.data or []
        except Exception:
            res = (
                self._client.table("license_keys")
                .select("id, prefix, suffix, status, plan, created_at")
                .eq("owner_discord_id", discord_user_id)
                .execute()
            )
            records = res.data or []

        result: list[dict[str, Any]] = []
        for record in records:
            key_id = record["id"]
            b_res = (
                self._client.table("device_bindings")
                .select("device_model, last_seen_at, is_active")
                .eq("key_id", key_id)
                .execute()
            )
            binding = b_res.data[0] if b_res.data else {}
            active = bool(binding.get("is_active"))
            masked = f"{record.get('prefix', 'DENG-????')}...{record.get('suffix', '????')}"
            ciphertext = (record.get("key_ciphertext") or "") if "key_ciphertext" in record else ""
            full_plain = lke.decrypt_license_key_ciphertext(ciphertext) if ciphertext else None
            if active:
                bound_device = binding.get("device_model") or "(unbound)"
                last_seen = binding.get("last_seen_at")
            else:
                bound_device = "(unbound)"
                last_seen = None
            result.append(
                {
                    "id": key_id,
                    "masked_key": masked,
                    "full_key_plaintext": full_plain,
                    "status": record.get("status", "unknown"),
                    "plan": record.get("plan", "standard"),
                    "bound_device": bound_device,
                    "last_seen_at": last_seen,
                    "created_at": record.get("created_at"),
                }
            )
        return result

    def list_user_keys_with_binding_state(
        self, discord_user_id: str
    ) -> list[dict[str, Any]]:
        from . import license_key_export as lke

        try:
            res = (
                self._client.table("license_keys")
                .select("id, prefix, suffix, status, key_ciphertext, key_export_available")
                .eq("owner_discord_id", discord_user_id)
                .neq("status", "revoked")
                .execute()
            )
            records = res.data or []
        except Exception:
            res = (
                self._client.table("license_keys")
                .select("id, prefix, suffix, status")
                .eq("owner_discord_id", discord_user_id)
                .neq("status", "revoked")
                .execute()
            )
            records = res.data or []

        result: list[dict[str, Any]] = []
        for record in records:
            key_id = record["id"]
            b_res = (
                self._client.table("device_bindings")
                .select("device_model, device_label, last_seen_at, is_active")
                .eq("key_id", key_id)
                .execute()
            )
            binding = b_res.data[0] if b_res.data else {}
            active_binding = bool(binding.get("is_active"))
            masked = f"{record.get('prefix', 'DENG-????')}...{record.get('suffix', '????')}"
            ciphertext = (record.get("key_ciphertext") or "") if "key_ciphertext" in record else ""
            full_plain = lke.decrypt_license_key_ciphertext(ciphertext) if ciphertext else None
            last_seen_at = binding.get("last_seen_at")
            reset_count = self.get_reset_count_24h(key_id)
            reason: str | None = None
            if not active_binding:
                can_reset = False
                reason = "No device bound — start the tool first"
            else:
                # Cooldown is based only on actual reset history, not on last_seen_at.
                # First reset is always allowed immediately if no previous reset has occurred.
                can_reset = True
            result.append({
                "key_id": key_id,
                "masked_key": masked,
                "full_key_plaintext": full_plain,
                "status": record.get("status", "unknown"),
                "active_binding": active_binding,
                "device_model": binding.get("device_model", ""),
                "device_label": binding.get("device_label", ""),
                "last_seen_at": last_seen_at,
                "reset_count_24h": reset_count,
                "can_reset": can_reset,
                "reason_if_not_resettable": reason,
            })
        return result

    def list_user_keys_for_stats(self, discord_user_id: str) -> list[dict[str, Any]]:
        from . import license_key_export as lke

        try:
            res = (
                self._client.table("license_keys")
                .select(
                    "id, prefix, suffix, status, plan, created_at, expires_at, redeemed_at, key_ciphertext, key_export_available"
                )
                .eq("owner_discord_id", discord_user_id)
                .execute()
            )
            records = res.data or []
        except Exception:
            res = (
                self._client.table("license_keys")
                .select("id, prefix, suffix, status, plan, created_at, expires_at, redeemed_at")
                .eq("owner_discord_id", discord_user_id)
                .execute()
            )
            records = res.data or []

        rows: list[dict[str, Any]] = []
        exp_cfg = lke.is_export_secret_configured()
        for record in records:
            key_id = record["id"]
            b_res = (
                self._client.table("device_bindings")
                .select("device_model, device_label, last_seen_at, is_active")
                .eq("key_id", key_id)
                .execute()
            )
            binding = b_res.data[0] if b_res.data else {}
            active_binding = bool(binding.get("is_active"))
            masked = f"{record.get('prefix', 'DENG-????')}...{record.get('suffix', '????')}"
            lic_status = record.get("status", "active")
            ciphertext = (record.get("key_ciphertext") or "") if "key_ciphertext" in record else ""
            has_blob = bool(ciphertext)
            full_plain = lke.decrypt_license_key_ciphertext(ciphertext) if has_blob else None
            device = (
                (binding.get("device_model") or binding.get("device_label") or "").strip()
                if active_binding
                else ""
            ) or None
            reset_count = self.get_reset_count_24h(key_id)
            plan = record.get("plan", "standard") or "standard"
            rows.append({
                "masked_key": masked,
                "full_key_plaintext": full_plain,
                "has_stored_ciphertext": has_blob,
                "export_storage_configured": exp_cfg,
                "license_status": lic_status,
                "used": active_binding,
                "device_display": device,
                "last_seen_at": binding.get("last_seen_at"),
                "created_at": record.get("created_at"),
                "expires_at": record.get("expires_at"),
                "redeemed_at": record.get("redeemed_at"),
                "plan": plan,
                "reset_count_24h": reset_count,
            })
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return rows

    def recover_key_export_for_user(self, discord_user_id: str, raw_key: str) -> str:
        from . import license_key_export as lke

        if not lke.is_export_secret_configured():
            raise ExportStorageUnavailable(
                "Full key export storage is not enabled on this server."
            )
        try:
            normalized = normalize_license_key(raw_key)
        except LicenseKeyError as exc:
            raise KeyNotFoundError(str(exc)) from exc
        key_hash = hash_license_key(normalized)
        res = (
            self._client.table("license_keys")
            .select("*")
            .eq("id", key_hash)
            .execute()
        )
        if not res.data:
            raise KeyNotFoundError("Key not found. Check the key and try again.")
        record = res.data[0]
        if record.get("owner_discord_id") != discord_user_id:
            raise KeyOwnershipError("That key does not belong to your account.")
        if record.get("status") == "revoked":
            raise KeyNotFoundError("This key has been revoked.")

        existing_ct = ""
        if "key_ciphertext" in record:
            existing_ct = (record.get("key_ciphertext") or "").strip()
        if existing_ct:
            if lke.decrypt_license_key_ciphertext(existing_ct):
                return "already_exportable"

        ciphertext = lke.encrypt_license_key_plaintext(normalized)
        if not ciphertext:
            raise ExportStorageUnavailable("Could not encrypt key for storage.")

        try:
            self._client.table("license_keys").update({
                "key_ciphertext": ciphertext,
                "key_export_available": True,
            }).eq("id", key_hash).execute()
        except Exception as exc:
            err = str(exc).lower()
            if "column" in err or "pgrst204" in err or "key_ciphertext" in err:
                raise ExportStorageUnavailable(
                    "Full key export columns are not available in the database."
                ) from exc
            raise
        self.audit_admin_action(
            discord_user_id,
            "recover_key_export",
            target_type="key",
            target_id=key_hash[:8],
        )
        return "stored"

    # ── HWID reset ────────────────────────────────────────────────────────────

    def reset_hwid(self, discord_user_id: str, key_id: str) -> None:
        res = (
            self._client.table("license_keys")
            .select("owner_discord_id")
            .eq("id", key_id)
            .execute()
        )
        if not res.data:
            raise KeyNotFoundError(f"Key not found: {key_id}")
        if res.data[0].get("owner_discord_id") != discord_user_id:
            raise KeyOwnershipError("You do not own this key.")
        # Check for active binding BEFORE counting resets.
        # No active binding → nothing to clear; do not consume a reset slot.
        b_res = (
            self._client.table("device_bindings")
            .select("install_id_hash, is_active")
            .eq("key_id", key_id)
            .execute()
        )
        binding_row = b_res.data[0] if b_res.data else None
        if not binding_row or not binding_row.get("is_active"):
            raise NoActiveBindingError(
                "No device is currently bound to this key. "
                "Start the tool once to activate your device binding."
            )
        # No last_seen_at cooldown — HWID reset is gated only on reset history, not heartbeat.
        old_hash = binding_row.get("install_id_hash")
        self._client.table("device_bindings").update(
            {"is_active": False}
        ).eq("key_id", key_id).execute()
        self._client.table("hwid_reset_logs").insert(
            {
                "key_id": key_id,
                "owner_discord_id": discord_user_id,
                "old_install_id_hash": old_hash,
                "reason": "user_requested",
            }
        ).execute()
        self.audit_admin_action(
            discord_user_id,
            "reset_hwid",
            target_type="key",
            target_id=key_id[:8],
            metadata={"old_install_id_hash": (old_hash or "")[:8]},
        )

    def get_reset_count_24h(self, key_id: str) -> int:
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        res = (
            self._client.table("hwid_reset_logs")
            .select("id", count="exact")
            .eq("key_id", key_id)
            .gte("created_at", cutoff)
            .execute()
        )
        return res.count or 0

    def get_last_seen_at(self, key_id: str) -> str | None:
        res = (
            self._client.table("device_bindings")
            .select("last_seen_at")
            .eq("key_id", key_id)
            .execute()
        )
        return res.data[0].get("last_seen_at") if res.data else None

    def check_install_download_access(self, raw_key: str, install_id_hash: str) -> str:
        try:
            normalized = normalize_license_key(raw_key)
        except LicenseKeyError:
            return RESULT_NOT_FOUND
        key_hash = hash_license_key(normalized)
        key_res = (
            self._client.table("license_keys")
            .select("status, expires_at, owner_discord_id, site_user_id")
            .eq("id", key_hash)
            .execute()
        )
        if not key_res.data:
            self.log_license_check(
                key_id=None,
                install_id_hash=install_id_hash,
                result=RESULT_NOT_FOUND,
                device_model="bootstrap",
                app_version="install",
            )
            return RESULT_NOT_FOUND
        record = key_res.data[0]
        if record.get("status") == "revoked":
            self.log_license_check(
                key_id=key_hash,
                install_id_hash=install_id_hash,
                result=RESULT_REVOKED,
                device_model="bootstrap",
                app_version="install",
            )
            return RESULT_REVOKED
        expires = record.get("expires_at")
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires)
                if datetime.now(timezone.utc) > exp_dt:
                    self.log_license_check(
                        key_id=key_hash,
                        install_id_hash=install_id_hash,
                        result=RESULT_EXPIRED,
                        device_model="bootstrap",
                        app_version="install",
                    )
                    return RESULT_EXPIRED
            except (ValueError, TypeError):
                pass
        if not _license_record_has_owner(record):
            self.log_license_check(
                key_id=key_hash,
                install_id_hash=install_id_hash,
                result=RESULT_KEY_NOT_REDEEMED,
                device_model="bootstrap",
                app_version="install",
            )
            return RESULT_KEY_NOT_REDEEMED
        b_res = (
            self._client.table("device_bindings")
            .select("*")
            .eq("key_id", key_hash)
            .execute()
        )
        if b_res.data:
            binding = b_res.data[0]
            if binding.get("is_active"):
                bound_hash = binding.get("install_id_hash")
                if not install_id_hash.strip():
                    self.log_license_check(
                        key_id=key_hash,
                        install_id_hash=install_id_hash,
                        result=RESULT_WRONG_DEVICE,
                        device_model="bootstrap",
                        app_version="install",
                    )
                    return RESULT_WRONG_DEVICE
                if bound_hash and bound_hash != install_id_hash:
                    self.log_license_check(
                        key_id=key_hash,
                        install_id_hash=install_id_hash,
                        result=RESULT_WRONG_DEVICE,
                        device_model="bootstrap",
                        app_version="install",
                    )
                    return RESULT_WRONG_DEVICE
        self.log_license_check(
            key_id=key_hash,
            install_id_hash=install_id_hash,
            result=RESULT_ACTIVE,
            device_model="bootstrap",
            app_version="install",
        )
        return RESULT_ACTIVE

    def get_owner_discord_id_for_license_key(self, raw_key: str) -> str | None:
        try:
            normalized = normalize_license_key(raw_key)
        except LicenseKeyError:
            return None
        key_hash = hash_license_key(normalized)
        key_res = (
            self._client.table("license_keys")
            .select("owner_discord_id")
            .eq("id", key_hash)
            .execute()
        )
        if not key_res.data:
            return None
        owner_id = key_res.data[0].get("owner_discord_id")
        if owner_id is None or str(owner_id).strip() == "":
            return None
        return str(owner_id).strip()

    # ── Device binding ────────────────────────────────────────────────────────

    def validate_existing_binding(
        self,
        raw_key: str,
        install_id_hash: str,
        device_model: str = "",
        app_version: str = "",
        device_label: str = "",
    ) -> str:
        try:
            normalized = normalize_license_key(raw_key)
        except LicenseKeyError:
            return RESULT_NOT_FOUND
        key_hash = hash_license_key(normalized)
        key_res = (
            self._client.table("license_keys")
            .select("status, expires_at, owner_discord_id, site_user_id")
            .eq("id", key_hash)
            .execute()
        )
        if not key_res.data:
            return RESULT_NOT_FOUND
        record = key_res.data[0]
        if record.get("status") == "revoked":
            return RESULT_REVOKED
        if _iso_expired(record.get("expires_at")):
            return RESULT_EXPIRED
        if not _license_record_has_owner(record):
            return RESULT_KEY_NOT_REDEEMED
        b_res = (
            self._client.table("device_bindings")
            .select("install_id_hash,is_active")
            .eq("key_id", key_hash)
            .execute()
        )
        if b_res.data:
            binding = b_res.data[0]
            if binding.get("is_active"):
                bound_hash = binding.get("install_id_hash")
                if bound_hash and bound_hash != install_id_hash:
                    return RESULT_WRONG_DEVICE
                return RESULT_ACTIVE
        return RESULT_REQUIRES_MANUAL_REBIND

    def bind_or_check_device(
        self,
        raw_key: str,
        install_id_hash: str,
        device_model: str,
        app_version: str,
        device_label: str = "",
    ) -> str:
        lbl = (device_label or "").strip()[:80]
        try:
            normalized = normalize_license_key(raw_key)
        except LicenseKeyError:
            return RESULT_NOT_FOUND
        key_hash = hash_license_key(normalized)
        key_res = (
            self._client.table("license_keys")
            .select("status, expires_at, owner_discord_id, site_user_id")
            .eq("id", key_hash)
            .execute()
        )
        if not key_res.data:
            self.log_license_check(
                key_id=None, install_id_hash=install_id_hash,
                result=RESULT_NOT_FOUND, device_model=device_model, app_version=app_version,
            )
            return RESULT_NOT_FOUND
        record = key_res.data[0]
        if record.get("status") == "revoked":
            self.log_license_check(
                key_id=key_hash, install_id_hash=install_id_hash,
                result=RESULT_REVOKED, device_model=device_model, app_version=app_version,
            )
            return RESULT_REVOKED
        if _iso_expired(record.get("expires_at")):
            self.log_license_check(
                key_id=key_hash, install_id_hash=install_id_hash,
                result=RESULT_EXPIRED, device_model=device_model, app_version=app_version,
            )
            return RESULT_EXPIRED
        if not _license_record_has_owner(record):
            self.log_license_check(
                key_id=key_hash,
                install_id_hash=install_id_hash,
                result=RESULT_KEY_NOT_REDEEMED,
                device_model=device_model,
                app_version=app_version,
            )
            return RESULT_KEY_NOT_REDEEMED
        b_res = (
            self._client.table("device_bindings")
            .select("*")
            .eq("key_id", key_hash)
            .execute()
        )
        now_ts = _utc_now()
        if b_res.data:
            binding = b_res.data[0]
            if binding.get("is_active"):
                bound_hash = binding.get("install_id_hash")
                if bound_hash and bound_hash != install_id_hash:
                    self.log_license_check(
                        key_id=key_hash, install_id_hash=install_id_hash,
                        result=RESULT_WRONG_DEVICE, device_model=device_model, app_version=app_version,
                    )
                    return RESULT_WRONG_DEVICE
                self._client.table("device_bindings").update(
                    {
                        "last_seen_at": now_ts,
                        "last_status": RESULT_ACTIVE,
                        "device_model": (device_model or "")[:120],
                        "device_label": lbl,
                    }
                ).eq("key_id", key_hash).execute()
            else:
                # Inactive binding — reactivate with current device (manual bind only)
                self._client.table("device_bindings").update(
                    {
                        "install_id_hash": install_id_hash,
                        "device_model": (device_model or "")[:120],
                        "device_label": lbl,
                        "last_seen_at": now_ts,
                        "last_status": RESULT_ACTIVE,
                        "is_active": True,
                    }
                ).eq("key_id", key_hash).execute()
        else:
            self._client.table("device_bindings").insert(
                {
                    "key_id": key_hash,
                    "install_id_hash": install_id_hash,
                    "device_label": lbl,
                    "device_model": (device_model or "")[:120],
                    "last_seen_at": now_ts,
                    "last_status": RESULT_ACTIVE,
                    "is_active": True,
                }
            ).execute()
        self.log_license_check(
            key_id=key_hash, install_id_hash=install_id_hash,
            result=RESULT_ACTIVE, device_model=device_model, app_version=app_version,
        )
        if record.get("expires_at") or not record.get("redeemed_at"):
            try:
                self._client.table("license_keys").update(
                    {"expires_at": None, "redeemed_at": record.get("redeemed_at") or now_ts}
                ).eq("id", key_hash).execute()
            except Exception as exc:
                err = str(exc).lower()
                if "redeemed_at" in err or "column" in err or "pgrst204" in err:
                    try:
                        self._client.table("license_keys").update(
                            {"expires_at": None}
                        ).eq("id", key_hash).execute()
                    except Exception:
                        pass
                else:
                    pass
        return RESULT_ACTIVE

    def log_license_check(self, **kwargs: Any) -> None:
        self._client.table("license_check_logs").insert(
            {
                "key_id": kwargs.get("key_id"),
                "install_id_hash": kwargs.get("install_id_hash"),
                "result": kwargs.get("result", "unknown"),
                "device_model": kwargs.get("device_model", ""),
                "app_version": kwargs.get("app_version", ""),
            }
        ).execute()

    # ── Panel config ──────────────────────────────────────────────────────────

    def save_panel_config(
        self, guild_id: str, channel_id: str, message_id: str, updated_by: str
    ) -> None:
        self._client.table("license_panel_config").upsert(
            {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "updated_by": updated_by,
            }
        ).execute()

    def get_panel_config(self, guild_id: str) -> dict[str, Any] | None:
        res = (
            self._client.table("license_panel_config")
            .select("*")
            .eq("guild_id", guild_id)
            .execute()
        )
        return res.data[0] if res.data else None

    def clear_panel_config(self, guild_id: str) -> None:
        self._client.table("license_panel_config").delete().eq(
            "guild_id", guild_id
        ).execute()

    # ── Audit log ─────────────────────────────────────────────────────────────

    def audit_admin_action(
        self,
        actor_discord_user_id: str,
        action: str,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._client.table("admin_audit_logs").insert(
            {
                "actor_discord_id": actor_discord_user_id,
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "metadata": metadata or {},
            }
        ).execute()

    # ── License log channel config (Supabase) ─────────────────────────────────

    def save_license_log_config(
        self, guild_id: str, channel_id: str, updated_by: str
    ) -> None:
        try:
            self._client.table("license_log_configs").upsert(
                {"guild_id": guild_id, "channel_id": channel_id, "updated_by": updated_by}
            ).execute()
        except Exception:
            pass

    def get_license_log_config(self, guild_id: str) -> dict[str, Any] | None:
        try:
            res = (
                self._client.table("license_log_configs")
                .select("*")
                .eq("guild_id", guild_id)
                .execute()
            )
            return res.data[0] if res.data else None
        except Exception:
            return None

    def clear_license_log_config(self, guild_id: str) -> None:
        try:
            self._client.table("license_log_configs").delete().eq(
                "guild_id", guild_id
            ).execute()
        except Exception:
            pass

    # ── Stats (Supabase) ──────────────────────────────────────────────────────

    def get_license_stats_for_discord_user(
        self, discord_user_id: str
    ) -> dict[str, Any]:
        from agent.key_stats_format import (
            compute_active_visible_stats,
            filter_active_visible_license_rows,
        )

        try:
            rows = self.list_user_keys_for_stats(discord_user_id)
            active_rows = filter_active_visible_license_rows(rows)
            counts = compute_active_visible_stats(active_rows)
        except Exception:
            counts = {
                "key_generated_count": 0,
                "key_redeemed_count": 0,
                "unbound_key_count": 0,
                "bound_key_count": 0,
            }
        try:
            reset_res = (
                self._client.table("hwid_reset_logs")
                .select("id", count="exact")
                .eq("owner_discord_id", discord_user_id)
                .execute()
            )
            reset_count = reset_res.count or 0
        except Exception:
            reset_count = 0
        try:
            exec_res = (
                self._client.table("license_key_executions")
                .select("id", count="exact")
                .eq("owner_discord_id", discord_user_id)
                .eq("is_public_release", True)
                .execute()
            )
            exec_count = exec_res.count or 0
        except Exception:
            exec_count = 0
        return {
            "discord_user_id": discord_user_id,
            **counts,
            "reset_hwid_count": reset_count,
            "key_executed_count": exec_count,
        }

    def record_key_execution(
        self,
        key_id: str,
        owner_discord_id: str,
        version: str,
        channel: str,
        *,
        is_public_release: bool,
    ) -> None:
        """Record one tool execution for stats tracking.

        Only public-release builds should set is_public_release=True.
        main-dev / internal / test builds must pass is_public_release=False.
        Never raises — failures are silently swallowed.
        """
        if not is_public_release:
            return
        try:
            self._client.table("license_key_executions").insert(
                {
                    "key_id": key_id,
                    "owner_discord_id": owner_discord_id,
                    "version": (version or "")[:64],
                    "channel": (channel or "")[:64],
                    "is_public_release": True,
                }
            ).execute()
        except Exception:
            pass


# ── Convenience factory ────────────────────────────────────────────────────────

def get_default_store() -> BaseLicenseStore:
    """Return the configured license store.

    Reads DENG_LICENSE_STORE from the environment:
      supabase → SupabaseLicenseStore (production)
      local    → LocalJsonLicenseStore (dev / tests / fallback)
    """
    import os
    from dotenv import load_dotenv

    load_dotenv()
    mode = os.environ.get("DENG_LICENSE_STORE", "local").strip().lower()
    if mode == "supabase":
        return SupabaseLicenseStore()
    return LocalJsonLicenseStore()
