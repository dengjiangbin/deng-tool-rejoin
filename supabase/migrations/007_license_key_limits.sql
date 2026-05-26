-- Migration 007: license_key_limits
--
-- Adds a table for storing global and per-user maximum active key limits.
-- Idempotent — safe to run multiple times.
--
-- Schema:
--   scope 'global'  → one row, sets the default limit for all users
--   scope 'user'    → one row per discord_user_id, overrides the global default
--
-- Default global limit: 2 active keys per user.

-- ── Create table ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS license_key_limits (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    scope                   TEXT        NOT NULL CHECK (scope IN ('global', 'user')),
    discord_user_id         TEXT        DEFAULT NULL,
    max_keys                INTEGER     NOT NULL CHECK (max_keys >= 0),
    updated_by_discord_id   TEXT        DEFAULT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Unique constraints ───────────────────────────────────────────────────────

-- Only one global row allowed
CREATE UNIQUE INDEX IF NOT EXISTS idx_license_key_limits_global
    ON license_key_limits (scope)
    WHERE scope = 'global';

-- Only one override per Discord user
CREATE UNIQUE INDEX IF NOT EXISTS idx_license_key_limits_user
    ON license_key_limits (discord_user_id)
    WHERE scope = 'user' AND discord_user_id IS NOT NULL;

-- ── Row-level security ───────────────────────────────────────────────────────

ALTER TABLE license_key_limits ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'license_key_limits'
          AND policyname = 'service_role_full_access_key_limits'
    ) THEN
        CREATE POLICY service_role_full_access_key_limits
            ON license_key_limits
            USING (auth.role() = 'service_role')
            WITH CHECK (auth.role() = 'service_role');
    END IF;
END;
$$;

-- ── Auto-update updated_at ───────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION set_license_key_limits_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_license_key_limits_updated_at ON license_key_limits;
CREATE TRIGGER trg_license_key_limits_updated_at
    BEFORE UPDATE ON license_key_limits
    FOR EACH ROW EXECUTE FUNCTION set_license_key_limits_updated_at();

-- ── Seed default global row (limit = 2, idempotent) ─────────────────────────

INSERT INTO license_key_limits (scope, max_keys, updated_by_discord_id)
VALUES ('global', 2, 'system')
ON CONFLICT DO NOTHING;
