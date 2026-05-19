-- Migration 004: Key execution tracking for public stable releases.
--
-- Records each time a user runs the tool in a public (non-dev, non-test) build.
-- Only rows with is_public_release = TRUE count towards the "Key Executed" stat.
-- main-dev / test / internal builds do NOT count and are either blocked by the
-- agent or filtered out at query time.

-- ── Table: license_key_executions ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS license_key_executions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_id          TEXT NOT NULL,
    owner_discord_id TEXT NOT NULL,
    version         TEXT NOT NULL DEFAULT '',
    channel         TEXT NOT NULL DEFAULT '',
    is_public_release BOOLEAN NOT NULL DEFAULT FALSE,
    executed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for stats lookup (owner) and filtering (public releases only)
CREATE INDEX IF NOT EXISTS idx_key_executions_owner
    ON license_key_executions (owner_discord_id);

CREATE INDEX IF NOT EXISTS idx_key_executions_public
    ON license_key_executions (is_public_release, executed_at DESC);

CREATE INDEX IF NOT EXISTS idx_key_executions_key
    ON license_key_executions (key_id);

-- RLS: same pattern as other tables — service-role bypasses, anon blocked.
ALTER TABLE license_key_executions ENABLE ROW LEVEL SECURITY;

-- Service role can do everything (needed by the bot).
CREATE POLICY "service_role_all_executions"
    ON license_key_executions
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
