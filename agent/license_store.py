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
active | expired | revoked | wrong_device | not_found | inactive |
missing_key | server_unavailable
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
RESULT_NOT_FOUND           = "not_found"
RESULT_INACTIVE            = "inactive"
RESULT_MISSING_KEY         = "missing_key"
RESULT_SERVER_UNAVAILABLE  = "server_unavailable"

MAX_HWID_RESETS_PER_24H    = 5
ACTIVE_HEARTBEAT_WINDOW_S  = 300          # 5 minutes
DEFAULT_MAX_KEYS            = 1


# ── Custom exceptions ──────────────────────────────────────────────────────────

class StoreError(Exception):
    """Base class for license store errors."""

class UserLimitError(StoreError):
    """User has reached their license key limit."""

class KeyNotFoundError(StoreError):
    """Key does not exist in the store."""

class KeyOwnershipError(StoreError):
    """Key belongs to a different Discord user."""

class KeyAlreadySelfOwned(StoreError):
    """Key is already attached to the requesting Discord user."""

class NoActiveBindingError(StoreError):
    """No active device binding exists for this key; nothing to reset."""

class ResetLimitError(StoreError):
    """HWID reset limit exceeded (5 per 24 hours)."""

class ActiveKeyWarning(StoreError):
    """Key heartbeat was recently active; recommend waiting before reset."""


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
        Returns the masked key on success.
        Raises KeyNotFoundError, KeyOwnershipError, or UserLimitError.
        """

    @abstractmethod
    def list_user_keys(self, discord_user_id: str) -> list[dict[str, Any]]:
        """Return a list of key summary dicts for a user.
        Each dict: {id, masked_key, status, plan, bound_device, created_at}
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
    def bind_or_check_device(
        self,
        raw_key: str,
        install_id_hash: str,
        device_model: str,
        app_version: str,
        device_label: str = "",
    ) -> str:
        """Bind or verify a device against a key.
        Returns a RESULT_* string.
        Never raises; errors are returned as result codes.
        """

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
          key_id, masked_key, status, active_binding (bool), device_model,
          device_label, last_seen_at, reset_count_24h, can_reset (bool),
          reason_if_not_resettable (str | None).

        Revoked keys are excluded from the result.
        """

    @abstractmethod
    def list_user_keys_for_stats(self, discord_user_id: str) -> list[dict[str, Any]]:
        """Rows for Key Stats / download. Never includes key hash/id.

        Each dict may include:
          masked_key, full_key_plaintext (optional), has_stored_ciphertext,
          license_status, used, device_display, last_seen_at, created_at,
          plan, reset_count_24h
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
            "audit_logs": [],
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
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
            }
            self._save(db)
        elif discord_username:
            db["users"][discord_user_id]["discord_username"] = discord_username
            db["users"][discord_user_id]["updated_at"] = _utc_now()
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
        db["users"][discord_user_id]["max_keys"] = max(1, int(max_keys))
        db["users"][discord_user_id]["updated_at"] = _utc_now()
        self._save(db)

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
        current = self.count_user_keys(discord_user_id)
        if current >= user.get("max_keys", DEFAULT_MAX_KEYS):
            raise UserLimitError(
                f"User has reached their license key limit ({user.get('max_keys', DEFAULT_MAX_KEYS)})."
            )
        raw_key = generate_license_key()
        key_hash = hash_license_key(raw_key)
        parts = raw_key.split("-")
        from . import license_key_export as lke

        ciphertext = lke.encrypt_license_key_plaintext(raw_key)
        db = self._load()
        db["keys"][key_hash] = {
            "id": key_hash,
            "prefix": f"{parts[0]}-{parts[1]}",
            "suffix": parts[-1],
            "owner_discord_id": discord_user_id,
            "status": "active",
            "plan": "standard",
            "expires_at": None,
            "created_by": created_by or discord_user_id,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "key_ciphertext": ciphertext,
            "key_export_available": bool(ciphertext),
        }
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

    def redeem_key_for_user(self, discord_user_id: str, raw_key: str) -> str:
        from .license import mask_license_key as _mask
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
        owner = key_record.get("owner_discord_id")
        if owner == discord_user_id:
            # Same user redeeming their own key — already attached
            raise KeyAlreadySelfOwned(
                f"This key is already attached to your account ({_mask(normalized)})."
            )
        if owner and owner != discord_user_id:
            raise KeyOwnershipError("This key belongs to another user.")
        user = self.get_or_create_user(discord_user_id)
        # Key is unowned; check limit before attaching
        current = self.count_user_keys(discord_user_id)
        if current >= user.get("max_keys", DEFAULT_MAX_KEYS):
            raise UserLimitError(
                f"You have reached your license key limit ({user.get('max_keys', DEFAULT_MAX_KEYS)})."
            )
        db["keys"][key_hash]["owner_discord_id"] = discord_user_id
        db["keys"][key_hash]["updated_at"] = _utc_now()
        self._save(db)
        self.audit_admin_action(discord_user_id, "redeem_key", target_type="key", target_id=key_hash[:8])
        return _mask(normalized)

    def list_user_keys(self, discord_user_id: str) -> list[dict[str, Any]]:
        from .license import mask_license_key as _mask
        db = self._load()
        result: list[dict[str, Any]] = []
        for key_hash, record in db["keys"].items():
            if record.get("owner_discord_id") != discord_user_id:
                continue
            binding = db["bindings"].get(key_hash, {})
            active = bool(binding.get("is_active"))
            masked = f"{record.get('prefix', 'DENG-????')}...{record.get('suffix', '????')}"
            if active:
                bound_device = binding.get("device_model") or "(unbound)"
                last_seen = binding.get("last_seen_at")
            else:
                bound_device = "(unbound)"
                last_seen = None
            result.append({
                "id": key_hash,
                "masked_key": masked,
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
            last_seen_at = binding.get("last_seen_at")
            reset_count = self.get_reset_count_24h(key_hash)
            # Determine can_reset and reason
            reason: str | None = None
            if not active_binding:
                can_reset = False
                reason = "No device bound — start the tool first"
            elif reset_count >= MAX_HWID_RESETS_PER_24H:
                can_reset = False
                reason = f"Reset limit reached ({reset_count}/{MAX_HWID_RESETS_PER_24H} today)"
            else:
                elapsed = _seconds_since(last_seen_at)
                if elapsed is not None and elapsed < ACTIVE_HEARTBEAT_WINDOW_S:
                    can_reset = False
                    m = int(elapsed) // 60
                    s = int(elapsed) % 60
                    reason = f"Key active {m}m {s}s ago — wait 5 min"
                else:
                    can_reset = True
            result.append({
                "key_id": key_hash,
                "masked_key": masked,
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
            device = (binding.get("device_model") or binding.get("device_label") or "").strip() or None
            reset_count = self.get_reset_count_24h(key_hash)
            plan = record.get("plan", "standard") or "standard"
            rows.append({
                "masked_key": masked,
                "full_key_plaintext": full_plain,
                "has_stored_ciphertext": has_blob,
                "license_status": lic_status,
                "used": active_binding,
                "device_display": device,
                "last_seen_at": binding.get("last_seen_at"),
                "created_at": record.get("created_at"),
                "plan": plan,
                "reset_count_24h": reset_count,
            })
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return rows

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
        # Now check reset count (only counts if there is something to clear)
        resets_24h = self.get_reset_count_24h(key_id)
        if resets_24h >= MAX_HWID_RESETS_PER_24H:
            raise ResetLimitError(
                f"HWID reset limit reached ({MAX_HWID_RESETS_PER_24H} per 24 hours). "
                "Please wait before trying again."
            )
        # Warn if key was recently active
        last_seen = self.get_last_seen_at(key_id)
        elapsed = _seconds_since(last_seen)
        if elapsed is not None and elapsed < ACTIVE_HEARTBEAT_WINDOW_S:
            raise ActiveKeyWarning(
                f"This key was active {int(elapsed)}s ago. "
                "Stop using the tool and wait at least 5 minutes before resetting HWID."
            )
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
        # Check device binding
        binding = db.get("bindings", {}).get(key_hash)
        if binding and binding.get("is_active"):
            bound_hash = binding.get("install_id_hash")
            if bound_hash and bound_hash != install_id_hash:
                self.log_license_check(
                    key_id=key_hash, install_id_hash=install_id_hash,
                    result=RESULT_WRONG_DEVICE, device_model=device_model, app_version=app_version,
                )
                return RESULT_WRONG_DEVICE
            # Same device — update heartbeat + device info
            db["bindings"][key_hash]["last_seen_at"] = _utc_now()
            db["bindings"][key_hash]["last_status"] = RESULT_ACTIVE
            db["bindings"][key_hash]["device_model"] = (device_model or "")[:120] or binding.get("device_model", "")
            db["bindings"][key_hash]["device_label"] = lbl
        else:
            # New binding
            db.setdefault("bindings", {})[key_hash] = {
                "install_id_hash": install_id_hash,
                "device_label": lbl,
                "device_model": (device_model or "")[:120],
                "bound_at": _utc_now(),
                "last_seen_at": _utc_now(),
                "last_status": RESULT_ACTIVE,
                "is_active": True,
            }
        self._save(db)
        self.log_license_check(
            key_id=key_hash, install_id_hash=install_id_hash,
            result=RESULT_ACTIVE, device_model=device_model, app_version=app_version,
        )
        return RESULT_ACTIVE

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
        current = self.count_user_keys(discord_user_id)
        max_keys = user.get("max_keys", DEFAULT_MAX_KEYS)
        if current >= max_keys:
            raise UserLimitError(
                f"User has reached their license key limit ({max_keys})."
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
                or "column" in err
                or "pgrst204" in err
            ):
                row.pop("key_ciphertext", None)
                row.pop("key_export_available", None)
                self._client.table("license_keys").insert(row).execute()
            else:
                raise
        self.audit_admin_action(
            created_by or discord_user_id,
            "create_key",
            target_type="key",
            target_id=key_hash[:8],
            metadata={"owner": discord_user_id},
        )
        return raw_key

    def redeem_key_for_user(self, discord_user_id: str, raw_key: str) -> str:
        from .license import mask_license_key as _mask

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
        owner = record.get("owner_discord_id")
        if owner == discord_user_id:
            raise KeyAlreadySelfOwned(
                f"This key is already attached to your account ({_mask(normalized)})."
            )
        if owner and owner != discord_user_id:
            raise KeyOwnershipError("This key belongs to another user.")
        # Key is unowned; check limit before attaching
        user = self.get_or_create_user(discord_user_id)
        current = self.count_user_keys(discord_user_id)
        max_keys = user.get("max_keys", DEFAULT_MAX_KEYS)
        if current >= max_keys:
            raise UserLimitError(
                f"You have reached your license key limit ({max_keys})."
            )
        self._client.table("license_keys").update(
            {"owner_discord_id": discord_user_id}
        ).eq("id", key_hash).execute()
        self.audit_admin_action(
            discord_user_id,
            "redeem_key",
            target_type="key",
            target_id=key_hash[:8],
        )
        return _mask(normalized)

    def list_user_keys(self, discord_user_id: str) -> list[dict[str, Any]]:
        res = (
            self._client.table("license_keys")
            .select("id, prefix, suffix, status, plan, created_at")
            .eq("owner_discord_id", discord_user_id)
            .execute()
        )
        result: list[dict[str, Any]] = []
        for record in res.data:
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
        res = (
            self._client.table("license_keys")
            .select("id, prefix, suffix, status")
            .eq("owner_discord_id", discord_user_id)
            .neq("status", "revoked")
            .execute()
        )
        result: list[dict[str, Any]] = []
        for record in res.data:
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
            last_seen_at = binding.get("last_seen_at")
            reset_count = self.get_reset_count_24h(key_id)
            reason: str | None = None
            if not active_binding:
                can_reset = False
                reason = "No device bound — start the tool first"
            elif reset_count >= MAX_HWID_RESETS_PER_24H:
                can_reset = False
                reason = f"Reset limit reached ({reset_count}/{MAX_HWID_RESETS_PER_24H} today)"
            else:
                elapsed = _seconds_since(last_seen_at)
                if elapsed is not None and elapsed < ACTIVE_HEARTBEAT_WINDOW_S:
                    can_reset = False
                    m = int(elapsed) // 60
                    s = int(elapsed) % 60
                    reason = f"Key active {m}m {s}s ago — wait 5 min"
                else:
                    can_reset = True
            result.append({
                "key_id": key_id,
                "masked_key": masked,
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

        rows: list[dict[str, Any]] = []
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
            device = (binding.get("device_model") or binding.get("device_label") or "").strip() or None
            reset_count = self.get_reset_count_24h(key_id)
            plan = record.get("plan", "standard") or "standard"
            rows.append({
                "masked_key": masked,
                "full_key_plaintext": full_plain,
                "has_stored_ciphertext": has_blob,
                "license_status": lic_status,
                "used": active_binding,
                "device_display": device,
                "last_seen_at": binding.get("last_seen_at"),
                "created_at": record.get("created_at"),
                "plan": plan,
                "reset_count_24h": reset_count,
            })
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return rows

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
        resets_24h = self.get_reset_count_24h(key_id)
        if resets_24h >= MAX_HWID_RESETS_PER_24H:
            raise ResetLimitError(
                f"HWID reset limit reached ({MAX_HWID_RESETS_PER_24H} per 24 hours). "
                "Please wait before trying again."
            )
        last_seen = self.get_last_seen_at(key_id)
        elapsed = _seconds_since(last_seen)
        if elapsed is not None and elapsed < ACTIVE_HEARTBEAT_WINDOW_S:
            raise ActiveKeyWarning(
                f"This key was active {int(elapsed)}s ago. "
                "Stop using the tool and wait at least 5 minutes before resetting HWID."
            )
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

    # ── Device binding ────────────────────────────────────────────────────────

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
            .select("status, expires_at")
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
                if bound_hash and bound_hash != install_id_hash:
                    self.log_license_check(
                        key_id=key_hash, install_id_hash=install_id_hash,
                        result=RESULT_WRONG_DEVICE, device_model=device_model, app_version=app_version,
                    )
                    return RESULT_WRONG_DEVICE
                self._client.table("device_bindings").update(
                    {
                        "last_seen_at": _utc_now(),
                        "last_status": RESULT_ACTIVE,
                        "device_model": (device_model or "")[:120],
                        "device_label": lbl,
                    }
                ).eq("key_id", key_hash).execute()
            else:
                # Inactive binding — reactivate with current device
                self._client.table("device_bindings").update(
                    {
                        "install_id_hash": install_id_hash,
                        "device_model": (device_model or "")[:120],
                        "device_label": lbl,
                        "last_seen_at": _utc_now(),
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
                    "last_seen_at": _utc_now(),
                    "last_status": RESULT_ACTIVE,
                    "is_active": True,
                }
            ).execute()
        self.log_license_check(
            key_id=key_hash, install_id_hash=install_id_hash,
            result=RESULT_ACTIVE, device_model=device_model, app_version=app_version,
        )
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
