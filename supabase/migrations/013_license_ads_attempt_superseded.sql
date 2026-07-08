-- ============================================================
-- Migration 013: Ads attempt superseded status for provider retry/switch
-- ============================================================

ALTER TABLE license_ad_challenges
    DROP CONSTRAINT IF EXISTS license_ad_challenges_status_check;

ALTER TABLE license_ad_challenges
    ADD CONSTRAINT license_ad_challenges_status_check
    CHECK (status IN (
        'created', 'provider_selected', 'pending_ad',
        'ad_completed', 'key_generated', 'expired', 'failed', 'superseded'
    ));

COMMENT ON COLUMN license_ad_challenges.status IS
    'Ads attempt lifecycle. superseded = replaced by a newer provider click in the same session.';

CREATE INDEX IF NOT EXISTS idx_challenges_superseded
    ON license_ad_challenges(site_user_id, status, created_at DESC)
    WHERE status = 'superseded';
