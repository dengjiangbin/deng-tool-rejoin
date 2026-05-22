-- ============================================================
-- Migration 006: DENG Tool Portal hardening
-- Purpose:
--   - Allow database-login portal accounts to own generated keys.
--   - Keep generated keys expiring after 24h until redeemed/bound.
--   - Store only the signed challenge hash during the ad-unlock flow.
-- ============================================================

ALTER TABLE license_keys
    ADD COLUMN IF NOT EXISTS site_user_id UUID REFERENCES site_users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_license_keys_site_user
    ON license_keys(site_user_id)
    WHERE site_user_id IS NOT NULL;

COMMENT ON COLUMN license_keys.site_user_id IS
    'Portal account that generated the key. Used for database-login users who do not have Discord.';

COMMENT ON COLUMN license_keys.expires_at IS
    'For portal-generated keys, this is the 24-hour unredeemed expiry. It is cleared after successful redemption/binding.';

COMMENT ON COLUMN license_ad_challenges.signed_challenge IS
    'Deprecated plaintext token column. New portal code stores only signed_challenge_hash.';
