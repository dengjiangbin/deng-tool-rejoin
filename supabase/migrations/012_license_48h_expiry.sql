-- Migration 012: 48-hour server-side key expiry + non-resetting legacy window
--
-- Goals (idempotent, safe to run multiple times, NON-DESTRUCTIVE):
--   * Guarantee license_keys has created_at, expires_at, redeemed_at columns.
--   * Add migration_started_at as the audit marker for the one-time legacy
--     48-hour expiry window applied on a key's first validation after release.
--   * Keep a read-only backup snapshot of pre-migration expiry state so the
--     behavior change is auditable / reversible for investigation.
--
-- This migration NEVER:
--   * wipes or deletes keys,
--   * resets migration_started_at on keys that already have it,
--   * revives revoked/invalid keys,
--   * changes expires_at on keys that already have one.
--
-- The 48h enforcement itself is applied server-side at validation time
-- (agent/license_store.py). New keys are generated with expires_at = created_at
-- + 48h by the website; legacy keys whose expires_at is NULL get a single
-- expires_at = now() + 48h stamp on first validation (non-resetting).

-- ── 1. Ensure required columns exist ─────────────────────────────────────────
ALTER TABLE license_keys
    ADD COLUMN IF NOT EXISTS created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE license_keys
    ADD COLUMN IF NOT EXISTS expires_at  TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE license_keys
    ADD COLUMN IF NOT EXISTS redeemed_at TIMESTAMPTZ DEFAULT NULL;

-- Audit marker proving when a legacy key entered its one-time 48h window.
ALTER TABLE license_keys
    ADD COLUMN IF NOT EXISTS migration_started_at TIMESTAMPTZ DEFAULT NULL;

COMMENT ON COLUMN license_keys.migration_started_at IS
    'Set once, server-side, on the first validation after the 48h-expiry '
    'release for legacy keys that had no expires_at. expiry = this + 48h. '
    'Never reset on later validations.';

-- ── 2. Read-only backup snapshot of pre-migration expiry state ───────────────
-- Captures the expiry-relevant columns BEFORE 48h enforcement so the change is
-- auditable. Populated once; re-running this migration does not duplicate rows.
CREATE TABLE IF NOT EXISTS license_keys_expiry_backup_012 (
    key_id        TEXT        PRIMARY KEY,
    status        TEXT,
    created_at    TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ,
    redeemed_at   TIMESTAMPTZ,
    snapshot_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO license_keys_expiry_backup_012 (key_id, status, created_at, expires_at, redeemed_at)
SELECT id, status, created_at, expires_at, redeemed_at
FROM license_keys
ON CONFLICT (key_id) DO NOTHING;

ALTER TABLE license_keys_expiry_backup_012 ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'license_keys_expiry_backup_012'
          AND policyname = 'service_role_full_access_expiry_backup_012'
    ) THEN
        CREATE POLICY service_role_full_access_expiry_backup_012
            ON license_keys_expiry_backup_012
            USING (auth.role() = 'service_role')
            WITH CHECK (auth.role() = 'service_role');
    END IF;
END;
$$;

-- ── 3. Helpful index for expiry sweeps / audits (idempotent) ─────────────────
CREATE INDEX IF NOT EXISTS idx_license_keys_expires_at
    ON license_keys (expires_at)
    WHERE expires_at IS NOT NULL;
