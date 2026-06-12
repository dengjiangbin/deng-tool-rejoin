-- ============================================================
-- Migration 011: Inventory tracked Roblox accounts per Discord user
-- ============================================================

CREATE TABLE IF NOT EXISTS inventory_tracked_accounts (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    discord_user_id     TEXT        NOT NULL,
    site_user_id        UUID        REFERENCES site_users(id) ON DELETE SET NULL,
    roblox_username     TEXT        NOT NULL,
    roblox_username_key TEXT        NOT NULL,
    roblox_user_id      BIGINT,
    display_name        TEXT,
    sort_index          INTEGER     NOT NULL DEFAULT 0,
    last_seen_at        TIMESTAMPTZ,
    last_inventory_sync_at TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT inventory_tracked_accounts_username_key_unique
        UNIQUE (discord_user_id, roblox_username_key)
);

ALTER TABLE inventory_tracked_accounts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "inventory_tracked_accounts_service_role_full" ON inventory_tracked_accounts
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

CREATE INDEX IF NOT EXISTS idx_inventory_tracked_accounts_discord
    ON inventory_tracked_accounts(discord_user_id, sort_index, created_at);

CREATE OR REPLACE FUNCTION _inventory_tracked_accounts_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_inventory_tracked_accounts_updated_at ON inventory_tracked_accounts;
CREATE TRIGGER trg_inventory_tracked_accounts_updated_at
    BEFORE UPDATE ON inventory_tracked_accounts
    FOR EACH ROW EXECUTE FUNCTION _inventory_tracked_accounts_updated_at();
