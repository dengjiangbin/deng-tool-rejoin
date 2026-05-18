-- DENG Tool: Rejoin — License Unlimited + Cooldown + Expiry Migration
-- 003_license_unlimited.sql
--
-- Idempotent: uses IF NOT EXISTS / DO $$ / ALTER TABLE ... ADD COLUMN IF NOT EXISTS
-- Run: supabase db push  OR paste in Supabase SQL editor.
--
-- Changes:
--   1. license_keys      — add redeemed_at   (when key was first activated)
--   2. license_users     — add last_key_generated_at  (generation cooldown)
--   3. license_log_configs — new table for license event log channel per guild
--   4. Indexes for expiry/cooldown/stats queries.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. license_keys — redeemed_at
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE license_keys
    ADD COLUMN IF NOT EXISTS redeemed_at TIMESTAMPTZ DEFAULT NULL;

COMMENT ON COLUMN license_keys.redeemed_at IS
    'When this key was first activated (first device binding OR Redeem Key button). '
    'NULL = never activated. Keys older than 24 h with redeemed_at NULL are expired-unredeemed.';

CREATE INDEX IF NOT EXISTS idx_license_keys_redeemed_at
    ON license_keys (redeemed_at)
    WHERE redeemed_at IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. license_users — last_key_generated_at  (generation cooldown)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE license_users
    ADD COLUMN IF NOT EXISTS last_key_generated_at TIMESTAMPTZ DEFAULT NULL;

COMMENT ON COLUMN license_users.last_key_generated_at IS
    'Timestamp of the most recent key generation by this user. '
    'Used to enforce the 60-second cooldown between key generations.';

-- Remove the hard max_keys=1 default — users may now have unlimited keys.
-- Existing rows keep their current max_keys; new rows get a high ceiling.
ALTER TABLE license_users
    ALTER COLUMN max_keys SET DEFAULT 9999;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. license_log_configs — license event log channel per guild
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS license_log_configs (
    guild_id    TEXT PRIMARY KEY,
    channel_id  TEXT NOT NULL,
    updated_by  TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE license_log_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY IF NOT EXISTS "service_role_full_access" ON license_log_configs
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Indexes for stats queries
-- ─────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_license_keys_created_by
    ON license_keys (created_by);

CREATE INDEX IF NOT EXISTS idx_license_keys_owner_redeemed
    ON license_keys (owner_discord_id, redeemed_at);

CREATE INDEX IF NOT EXISTS idx_hwid_reset_logs_owner
    ON hwid_reset_logs (owner_discord_id);
