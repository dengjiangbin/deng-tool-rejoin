-- ============================================================
-- Migration 005: DENG Tool Web Portal Tables
-- Created: 2025
-- ============================================================

-- ---------------------------------------------------------
-- site_users: web-portal accounts (Discord OR password login)
-- Unlike web_accounts which hard-requires discord_user_id,
-- this table allows password-only accounts as well.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS site_users (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    username                    TEXT        UNIQUE,
    email                       TEXT        UNIQUE,
    password_hash               TEXT,                           -- bcrypt; NULL if Discord-only
    discord_user_id             TEXT        UNIQUE,             -- NULL if password-only
    discord_username            TEXT,
    discord_avatar              TEXT,
    discord_access_token        TEXT,                           -- stored encrypted in production
    discord_refresh_token       TEXT,
    linked_license_user_discord_id  TEXT    REFERENCES license_users(discord_user_id) ON DELETE SET NULL,
    is_active                   BOOLEAN     NOT NULL DEFAULT TRUE,
    last_login_at               TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE site_users ENABLE ROW LEVEL SECURITY;

CREATE POLICY "site_users_service_role_full" ON site_users
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

CREATE INDEX IF NOT EXISTS idx_site_users_discord ON site_users(discord_user_id) WHERE discord_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_site_users_email   ON site_users(email)           WHERE email           IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_site_users_username ON site_users(username)       WHERE username        IS NOT NULL;

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION _site_users_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_site_users_updated_at ON site_users;
CREATE TRIGGER trg_site_users_updated_at
    BEFORE UPDATE ON site_users
    FOR EACH ROW EXECUTE FUNCTION _site_users_updated_at();

-- ---------------------------------------------------------
-- license_ad_challenges: tracks the ad-unlock flow per user.
-- One row per attempt. Status machine:
--   created → provider_selected → pending_ad → ad_completed
--   → key_generated | failed | expired
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS license_ad_challenges (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    site_user_id            UUID        REFERENCES site_users(id) ON DELETE SET NULL,
    discord_user_id         TEXT,                           -- filled when linked
    provider                TEXT        CHECK (provider IN ('lootlabs', 'linkvertise')),
    status                  TEXT        NOT NULL DEFAULT 'created'
                            CHECK (status IN (
                                'created', 'provider_selected', 'pending_ad',
                                'ad_completed', 'key_generated', 'expired', 'failed'
                            )),
    -- Security / anti-abuse fingerprints (hashed, never raw)
    session_hash            TEXT        NOT NULL,           -- SHA-256 of cookie session id
    ip_hash                 TEXT,                           -- SHA-256 of client IP
    user_agent_hash         TEXT,                           -- SHA-256 of User-Agent
    state_hash              TEXT        NOT NULL,           -- random per-challenge nonce (SHA-256)
    -- Challenge token
    signed_challenge        TEXT,                           -- HMAC-signed payload (shown once)
    signed_challenge_hash   TEXT    UNIQUE,                 -- SHA-256 of signed_challenge
    -- Key outcome
    license_key_id          TEXT,                           -- SHA-256 hash → license_keys.id
    key_prefix              TEXT,                           -- first 2 groups for masked display
    key_suffix              TEXT,                           -- last 2 groups for masked display
    -- Timestamps
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at              TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '30 minutes',
    key_expires_at          TIMESTAMPTZ,                    -- 24h unredeemed expiry
    completed_at            TIMESTAMPTZ,
    used_at                 TIMESTAMPTZ,
    -- Diagnostics
    failure_reason          TEXT,
    provider_payload        JSONB
);

ALTER TABLE license_ad_challenges ENABLE ROW LEVEL SECURITY;

CREATE POLICY "challenges_service_role_full" ON license_ad_challenges
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

CREATE INDEX IF NOT EXISTS idx_challenges_session   ON license_ad_challenges(session_hash);
CREATE INDEX IF NOT EXISTS idx_challenges_user      ON license_ad_challenges(site_user_id);
CREATE INDEX IF NOT EXISTS idx_challenges_discord   ON license_ad_challenges(discord_user_id) WHERE discord_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_challenges_signed    ON license_ad_challenges(signed_challenge_hash) WHERE signed_challenge_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_challenges_expires   ON license_ad_challenges(expires_at);
CREATE INDEX IF NOT EXISTS idx_challenges_status    ON license_ad_challenges(status);
CREATE INDEX IF NOT EXISTS idx_challenges_key       ON license_ad_challenges(license_key_id) WHERE license_key_id IS NOT NULL;
