-- ============================================================
-- Migration 009: DENG Monitor (public Android app + Termux bridge)
-- ============================================================
--
-- Adds tables required for the DENG Monitor Android app and the
-- outbound monitor bridge from Termux/cloud-phone agents.
--
-- IMPORTANT:
--   * All tables use RLS; only the service-role key (backend) can read/write.
--   * No secrets / license keys / private URLs are stored here.
--   * Snapshots are stored as bounded-size BYTEA so we don't depend on
--     external object storage. Retention keeps only the latest N per device.
--   * Idempotent — safe to re-run.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. monitor_devices — one row per cloud phone / Termux install per user
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitor_devices (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_discord_user_id       TEXT        NOT NULL,
    license_user_id             UUID        REFERENCES license_users(id) ON DELETE SET NULL,
    device_label                TEXT        NOT NULL DEFAULT 'Cloud Phone',
    device_fingerprint_hash     TEXT        NOT NULL,        -- SHA-256(install_id) — never raw
    tool_version                TEXT,
    channel                     TEXT        NOT NULL DEFAULT 'stable'
                                CHECK (channel IN ('stable', 'beta', 'dev', 'latest', 'test')),
    status_connected            BOOLEAN     NOT NULL DEFAULT FALSE,
    last_seen_at                TIMESTAMPTZ,
    last_disconnect_reason      TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_monitor_devices_owner_fp
    ON monitor_devices (owner_discord_user_id, device_fingerprint_hash);
CREATE INDEX IF NOT EXISTS idx_monitor_devices_owner
    ON monitor_devices (owner_discord_user_id);
CREATE INDEX IF NOT EXISTS idx_monitor_devices_last_seen
    ON monitor_devices (last_seen_at);

ALTER TABLE monitor_devices ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
        WHERE tablename='monitor_devices' AND policyname='monitor_devices_service_role_full')
    THEN
        CREATE POLICY monitor_devices_service_role_full ON monitor_devices
            USING (auth.role() = 'service_role')
            WITH CHECK (auth.role() = 'service_role');
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. monitor_package_states — latest per-package status per device
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitor_package_states (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    monitor_device_id           UUID        NOT NULL REFERENCES monitor_devices(id) ON DELETE CASCADE,
    package_name                TEXT        NOT NULL,
    display_name                TEXT,
    username                    TEXT,
    state                       TEXT        NOT NULL DEFAULT 'Unknown',
    ram_mb                      INTEGER     NOT NULL DEFAULT 0 CHECK (ram_mb >= 0),
    runtime_seconds             INTEGER     NOT NULL DEFAULT 0 CHECK (runtime_seconds >= 0),
    restart_count               INTEGER     NOT NULL DEFAULT 0 CHECK (restart_count >= 0),
    pid                         INTEGER,
    private_url_configured      BOOLEAN     NOT NULL DEFAULT FALSE,
    safe_error_reason           TEXT,
    last_launch_at              TIMESTAMPTZ,
    last_heartbeat_at           TIMESTAMPTZ,
    last_state_change_at        TIMESTAMPTZ,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pkg_states_device_pkg
    ON monitor_package_states (monitor_device_id, package_name);
CREATE INDEX IF NOT EXISTS idx_pkg_states_device
    ON monitor_package_states (monitor_device_id);

ALTER TABLE monitor_package_states ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
        WHERE tablename='monitor_package_states' AND policyname='monitor_package_states_service_role_full')
    THEN
        CREATE POLICY monitor_package_states_service_role_full ON monitor_package_states
            USING (auth.role() = 'service_role')
            WITH CHECK (auth.role() = 'service_role');
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. monitor_snapshots — bounded, retention-limited screenshots
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitor_snapshots (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    monitor_device_id           UUID        NOT NULL REFERENCES monitor_devices(id) ON DELETE CASCADE,
    mime_type                   TEXT        NOT NULL DEFAULT 'image/webp',
    image_data                  BYTEA       NOT NULL,
    size_bytes                  INTEGER     NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 2 * 1024 * 1024),
    width                       INTEGER,
    height                      INTEGER,
    captured_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_snapshots_device_captured
    ON monitor_snapshots (monitor_device_id, captured_at DESC);

ALTER TABLE monitor_snapshots ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
        WHERE tablename='monitor_snapshots' AND policyname='monitor_snapshots_service_role_full')
    THEN
        CREATE POLICY monitor_snapshots_service_role_full ON monitor_snapshots
            USING (auth.role() = 'service_role')
            WITH CHECK (auth.role() = 'service_role');
    END IF;
END $$;

-- Trigger: keep at most 10 snapshots per device (delete oldest)
CREATE OR REPLACE FUNCTION trim_monitor_snapshots()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM monitor_snapshots
    WHERE id IN (
        SELECT id FROM monitor_snapshots
        WHERE monitor_device_id = NEW.monitor_device_id
        ORDER BY captured_at DESC
        OFFSET 10
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_trim_monitor_snapshots ON monitor_snapshots;
CREATE TRIGGER trg_trim_monitor_snapshots
    AFTER INSERT ON monitor_snapshots
    FOR EACH ROW EXECUTE FUNCTION trim_monitor_snapshots();

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. monitor_settings — per-device app/agent settings
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitor_settings (
    monitor_device_id           UUID        PRIMARY KEY REFERENCES monitor_devices(id) ON DELETE CASCADE,
    snapshot_interval_seconds   INTEGER     NOT NULL DEFAULT 30
                                CHECK (snapshot_interval_seconds IN (0, 15, 30, 60, 300)),
    monitor_enabled             BOOLEAN     NOT NULL DEFAULT TRUE,
    app_refresh_interval_seconds INTEGER    NOT NULL DEFAULT 5
                                CHECK (app_refresh_interval_seconds BETWEEN 2 AND 60),
    app_display_name            TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE monitor_settings ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
        WHERE tablename='monitor_settings' AND policyname='monitor_settings_service_role_full')
    THEN
        CREATE POLICY monitor_settings_service_role_full ON monitor_settings
            USING (auth.role() = 'service_role')
            WITH CHECK (auth.role() = 'service_role');
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. monitor_bridge_tokens — short-lived Termux bridge auth tokens
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitor_bridge_tokens (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    monitor_device_id           UUID        NOT NULL REFERENCES monitor_devices(id) ON DELETE CASCADE,
    token_hash                  TEXT        NOT NULL UNIQUE,   -- SHA-256, never raw
    issued_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at                  TIMESTAMPTZ NOT NULL,
    revoked_at                  TIMESTAMPTZ,
    last_used_at                TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_bridge_tokens_device
    ON monitor_bridge_tokens (monitor_device_id);
CREATE INDEX IF NOT EXISTS idx_bridge_tokens_expires
    ON monitor_bridge_tokens (expires_at);

ALTER TABLE monitor_bridge_tokens ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
        WHERE tablename='monitor_bridge_tokens' AND policyname='monitor_bridge_tokens_service_role_full')
    THEN
        CREATE POLICY monitor_bridge_tokens_service_role_full ON monitor_bridge_tokens
            USING (auth.role() = 'service_role')
            WITH CHECK (auth.role() = 'service_role');
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. monitor_pairing_codes — short-lived codes to pair the Android app
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitor_pairing_codes (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code_hash                   TEXT        NOT NULL UNIQUE,   -- SHA-256, never raw
    owner_discord_user_id       TEXT        NOT NULL,
    site_user_id                UUID,
    issued_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at                  TIMESTAMPTZ NOT NULL,
    used_at                     TIMESTAMPTZ,
    consumed_by_app_session_id  UUID
);

CREATE INDEX IF NOT EXISTS idx_pairing_codes_owner
    ON monitor_pairing_codes (owner_discord_user_id);
CREATE INDEX IF NOT EXISTS idx_pairing_codes_expires
    ON monitor_pairing_codes (expires_at);

ALTER TABLE monitor_pairing_codes ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
        WHERE tablename='monitor_pairing_codes' AND policyname='monitor_pairing_codes_service_role_full')
    THEN
        CREATE POLICY monitor_pairing_codes_service_role_full ON monitor_pairing_codes
            USING (auth.role() = 'service_role')
            WITH CHECK (auth.role() = 'service_role');
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. monitor_app_sessions — long-lived app session tokens (Android)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitor_app_sessions (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_discord_user_id       TEXT        NOT NULL,
    token_hash                  TEXT        NOT NULL UNIQUE,   -- SHA-256, never raw
    device_name                 TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at                  TIMESTAMPTZ NOT NULL,
    revoked_at                  TIMESTAMPTZ,
    last_used_at                TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_app_sessions_owner
    ON monitor_app_sessions (owner_discord_user_id);
CREATE INDEX IF NOT EXISTS idx_app_sessions_expires
    ON monitor_app_sessions (expires_at);

ALTER TABLE monitor_app_sessions ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
        WHERE tablename='monitor_app_sessions' AND policyname='monitor_app_sessions_service_role_full')
    THEN
        CREATE POLICY monitor_app_sessions_service_role_full ON monitor_app_sessions
            USING (auth.role() = 'service_role')
            WITH CHECK (auth.role() = 'service_role');
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- updated_at triggers
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION _monitor_touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_monitor_devices_updated_at ON monitor_devices;
CREATE TRIGGER trg_monitor_devices_updated_at
    BEFORE UPDATE ON monitor_devices
    FOR EACH ROW EXECUTE FUNCTION _monitor_touch_updated_at();

DROP TRIGGER IF EXISTS trg_monitor_package_states_updated_at ON monitor_package_states;
CREATE TRIGGER trg_monitor_package_states_updated_at
    BEFORE UPDATE ON monitor_package_states
    FOR EACH ROW EXECUTE FUNCTION _monitor_touch_updated_at();

DROP TRIGGER IF EXISTS trg_monitor_settings_updated_at ON monitor_settings;
CREATE TRIGGER trg_monitor_settings_updated_at
    BEFORE UPDATE ON monitor_settings
    FOR EACH ROW EXECUTE FUNCTION _monitor_touch_updated_at();
