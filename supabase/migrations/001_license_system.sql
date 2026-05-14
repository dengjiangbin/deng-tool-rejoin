-- DENG Tool: Rejoin — License System Migration
-- 001_license_system.sql
--
-- Run this in the Supabase SQL editor or via the Supabase CLI:
--   supabase db push
--
-- Row Level Security (RLS) is enabled on all tables.
-- Only the service-role key (used by the Discord bot backend) can write.
-- Regular users authenticate via discord_identities; anon cannot read any row.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. license_users — one row per Discord user
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS license_users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    discord_user_id     TEXT NOT NULL UNIQUE,
    discord_username    TEXT NOT NULL DEFAULT '',
    max_keys            INTEGER NOT NULL DEFAULT 1 CHECK (max_keys >= 1),
    is_owner            BOOLEAN NOT NULL DEFAULT FALSE,
    is_blocked          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE license_users ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_full_access" ON license_users
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. license_keys — each DENG-XXXX-XXXX-XXXX-XXXX key (stored as SHA-256 hash)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS license_keys (
    id                  TEXT PRIMARY KEY,      -- SHA-256 of normalized key
    prefix              TEXT NOT NULL,         -- "DENG-8F3A" (first 2 parts)
    suffix              TEXT NOT NULL,         -- "44F0"     (last group)
    owner_discord_id    TEXT REFERENCES license_users(discord_user_id) ON DELETE SET NULL,
    status              TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'expired', 'revoked', 'inactive')),
    plan                TEXT NOT NULL DEFAULT 'standard',
    expires_at          TIMESTAMPTZ,
    created_by          TEXT,                  -- discord_user_id of creator
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE license_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_full_access" ON license_keys
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. device_bindings — links a key to a hashed install_id
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS device_bindings (
    key_id              TEXT PRIMARY KEY REFERENCES license_keys(id) ON DELETE CASCADE,
    install_id_hash     TEXT NOT NULL,         -- SHA-256 of install_id (never raw)
    device_label        TEXT NOT NULL DEFAULT '',
    device_model        TEXT NOT NULL DEFAULT '',
    bound_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ,
    last_status         TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE
);

ALTER TABLE device_bindings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_full_access" ON device_bindings
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. hwid_reset_logs — audit trail for HWID resets
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hwid_reset_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_id              TEXT NOT NULL REFERENCES license_keys(id) ON DELETE CASCADE,
    owner_discord_id    TEXT,
    old_install_id_hash TEXT,
    reason              TEXT NOT NULL DEFAULT 'user_requested',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE hwid_reset_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_full_access" ON hwid_reset_logs
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- Index for rate-limit query: count resets in last 24h per key
CREATE INDEX IF NOT EXISTS idx_hwid_reset_logs_key_created
    ON hwid_reset_logs (key_id, created_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. license_check_logs — heartbeat / verification events
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS license_check_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_id              TEXT REFERENCES license_keys(id) ON DELETE SET NULL,
    install_id_hash     TEXT,
    result              TEXT NOT NULL,
    device_model        TEXT NOT NULL DEFAULT '',
    app_version         TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE license_check_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_full_access" ON license_check_logs
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

CREATE INDEX IF NOT EXISTS idx_license_check_logs_key
    ON license_check_logs (key_id, created_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. license_panel_config — per-guild Discord panel channel + message ID
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS license_panel_config (
    guild_id            TEXT PRIMARY KEY,
    channel_id          TEXT NOT NULL,
    message_id          TEXT NOT NULL,
    updated_by          TEXT NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE license_panel_config ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_full_access" ON license_panel_config
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. admin_audit_logs — administrator action audit trail
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS admin_audit_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_discord_id    TEXT NOT NULL,
    action              TEXT NOT NULL,
    target_type         TEXT,
    target_id           TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE admin_audit_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_full_access" ON admin_audit_logs
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. web_accounts — optional web dashboard accounts
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS web_accounts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    discord_user_id     TEXT NOT NULL UNIQUE REFERENCES license_users(discord_user_id) ON DELETE CASCADE,
    display_name        TEXT NOT NULL DEFAULT '',
    avatar_url          TEXT NOT NULL DEFAULT '',
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE web_accounts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_full_access" ON web_accounts
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ─────────────────────────────────────────────────────────────────────────────
-- 9. web_sessions — short-lived web dashboard sessions
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS web_sessions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    web_account_id      UUID NOT NULL REFERENCES web_accounts(id) ON DELETE CASCADE,
    session_token_hash  TEXT NOT NULL UNIQUE,  -- SHA-256 of bearer token
    ip_address          TEXT,
    user_agent          TEXT,
    expires_at          TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE web_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_full_access" ON web_sessions
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

CREATE INDEX IF NOT EXISTS idx_web_sessions_token ON web_sessions (session_token_hash);
CREATE INDEX IF NOT EXISTS idx_web_sessions_expires ON web_sessions (expires_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- 10. discord_identities — Discord OAuth tokens for web dashboard login
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS discord_identities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    web_account_id      UUID NOT NULL REFERENCES web_accounts(id) ON DELETE CASCADE,
    discord_user_id     TEXT NOT NULL UNIQUE,
    access_token_hash   TEXT,  -- DO NOT store plaintext OAuth tokens
    token_expires_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE discord_identities ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_full_access" ON discord_identities
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ─────────────────────────────────────────────────────────────────────────────
-- Helper: auto-update updated_at via trigger
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_license_users_updated_at
    BEFORE UPDATE ON license_users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_license_keys_updated_at
    BEFORE UPDATE ON license_keys
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_web_accounts_updated_at
    BEFORE UPDATE ON web_accounts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_discord_identities_updated_at
    BEFORE UPDATE ON discord_identities
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
