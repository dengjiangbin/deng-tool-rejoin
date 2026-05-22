from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent.license import generate_license_key, hash_license_key
from agent.license_store import (
    RESULT_ACTIVE,
    RESULT_EXPIRED,
    RESULT_KEY_NOT_REDEEMED,
    SupabaseLicenseStore,
)


class _Result:
    def __init__(self, data=None, count=0):
        self.data = data or []
        self.count = count


class _Query:
    def __init__(self, db: dict[str, list[dict]], table: str):
        self.db = db
        self.table = table
        self.action = "select"
        self.payload = None
        self.filters: list[tuple[str, object]] = []

    def select(self, *_args, **_kwargs):
        return self

    def update(self, payload: dict):
        self.action = "update"
        self.payload = payload
        return self

    def insert(self, payload: dict):
        self.action = "insert"
        self.payload = payload
        return self

    def eq(self, key: str, value: object):
        self.filters.append((key, value))
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        if self.action == "insert":
            rows.append(dict(self.payload))
            return _Result([dict(self.payload)], 1)
        matched = [row for row in rows if all(row.get(k) == v for k, v in self.filters)]
        if self.action == "update":
            for row in matched:
                row.update(self.payload)
            return _Result([dict(row) for row in matched], len(matched))
        return _Result([dict(row) for row in matched], len(matched))


class _Client:
    def __init__(self, db: dict[str, list[dict]]):
        self.db = db

    def table(self, name: str):
        return _Query(self.db, name)


def _store_with_record(record: dict) -> tuple[SupabaseLicenseStore, dict[str, list[dict]], str]:
    raw = generate_license_key()
    key_id = hash_license_key(raw)
    db = {
        "license_keys": [{"id": key_id, "status": "active", **record}],
        "device_bindings": [],
        "license_check_logs": [],
    }
    store = SupabaseLicenseStore.__new__(SupabaseLicenseStore)
    store._client = _Client(db)
    return store, db, raw


class TestPortalOwnedSupabaseKeys(unittest.TestCase):
    def test_site_user_owned_key_allows_install_download(self) -> None:
        store, _db, raw = _store_with_record({
            "owner_discord_id": None,
            "site_user_id": "site-user-1",
            "expires_at": None,
        })

        self.assertEqual(
            store.check_install_download_access(raw, "aa" * 32),
            RESULT_ACTIVE,
        )

    def test_site_user_owned_key_binds_device_and_clears_unredeemed_expiry(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        store, db, raw = _store_with_record({
            "owner_discord_id": None,
            "site_user_id": "site-user-1",
            "expires_at": future,
        })

        self.assertEqual(
            store.bind_or_check_device(raw, "bb" * 32, "Pixel", "1.0.0"),
            RESULT_ACTIVE,
        )
        self.assertEqual(db["license_keys"][0]["expires_at"], None)
        self.assertEqual(len(db["device_bindings"]), 1)

    def test_expired_site_user_key_is_rejected(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        store, _db, raw = _store_with_record({
            "owner_discord_id": None,
            "site_user_id": "site-user-1",
            "expires_at": past,
        })

        self.assertEqual(
            store.check_install_download_access(raw, "aa" * 32),
            RESULT_EXPIRED,
        )

    def test_key_without_discord_or_site_owner_is_still_unredeemed(self) -> None:
        store, _db, raw = _store_with_record({
            "owner_discord_id": None,
            "site_user_id": None,
            "expires_at": None,
        })

        self.assertEqual(
            store.check_install_download_access(raw, "aa" * 32),
            RESULT_KEY_NOT_REDEEMED,
        )


if __name__ == "__main__":
    unittest.main()
