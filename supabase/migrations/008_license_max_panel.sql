-- Migration 008: license_max_panel
--
-- Adds max_panel column to the existing license_key_limits table and
-- creates the license_panel_reset_usage daily-counter table.
-- Idempotent — safe to run multiple times.
--
-- Schema:
--   license_key_limits.max_panel   → max Reset HWID panel uses per WIB day
--   license_panel_reset_usage      → per-user daily usage counters

-- ── Extend license_key_limits with max_panel ─────────────────────────────────

ALTER TABLE license_key_limits
    ADD COLUMN IF NOT EXISTS max_panel INTEGER DEFAULT NULL CHECK (max_panel >= 0);

-- Seed global default max_panel = 1 on the existing global row
UPDATE license_key_limits
    SET max_panel = 1
    WHERE scope = 'global' AND max_panel IS NULL;

-- ── Create license_panel_reset_usage table ────────────────────────────────────

CREATE TABLE IF NOT EXISTS license_panel_reset_usage (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    discord_user_id     TEXT        NOT NULL,
    reset_day_wib       TEXT        NOT NULL,   -- YYYY-MM-DD in Asia/Jakarta (UTC+7)
    used_count          INTEGER     NOT NULL DEFAULT 0 CHECK (used_count >= 0),
    last_reset_at       TIMESTAMPTZ DEFAULT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Only one row per (user, WIB day)
CREATE UNIQUE INDEX IF NOT EXISTS idx_panel_reset_usage_user_day
    ON license_panel_reset_usage (discord_user_id, reset_day_wib);

-- ── Row-level security ───────────────────────────────────────────────────────

ALTER TABLE license_panel_reset_usage ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'license_panel_reset_usage'
          AND policyname = 'service_role_full_access_panel_usage'
    ) THEN
        CREATE POLICY service_role_full_access_panel_usage
            ON license_panel_reset_usage
            USING (auth.role() = 'service_role')
            WITH CHECK (auth.role() = 'service_role');
    END IF;
END;
$$;

-- ── Auto-update updated_at ───────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION set_panel_reset_usage_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_panel_reset_usage_updated_at ON license_panel_reset_usage;
CREATE TRIGGER trg_panel_reset_usage_updated_at
    BEFORE UPDATE ON license_panel_reset_usage
    FOR EACH ROW EXECUTE FUNCTION set_panel_reset_usage_updated_at();
