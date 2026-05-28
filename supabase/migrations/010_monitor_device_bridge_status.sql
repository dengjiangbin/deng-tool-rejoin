-- ─────────────────────────────────────────────────────────────────────────────
-- 010_monitor_device_bridge_status.sql
--
-- Adds a small JSON diagnostics column to monitor_devices so the Termux
-- bridge can report its own view of the snapshot pipeline (capture result,
-- byte count, upload status, screencap availability, etc.) to the APK.
--
-- v1.0.4 was the first release that needed this: the v1.0.3 Snapshot tab
-- could show "Waiting for first snapshot…" forever with no real reason —
-- because all the diagnostic information lived only in the bridge's
-- in-memory state on the cloud phone. This column is how those numbers
-- reach the APK without leaking secrets.
--
-- Safety notes:
--   * Column is OPTIONAL (DEFAULT NULL). Old bridges that don't send a
--     `bridge_status` block in their push payload simply leave it NULL.
--   * The backend strips the payload to a known allow-list of keys
--     before writing this column — see monitorRoutes.js
--     (`bridgeStatusClean` block). It is never written raw.
--   * No sensitive fields (tokens, licence keys, URLs) are accepted.
--   * Idempotent: ADD COLUMN IF NOT EXISTS guards against re-runs.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE monitor_devices
    ADD COLUMN IF NOT EXISTS last_bridge_status JSONB;

COMMENT ON COLUMN monitor_devices.last_bridge_status IS
    'Optional bridge-self-reported diagnostics (snapshot pipeline, push pipeline). Written by /api/monitor/bridge/push after key allow-listing. May be NULL for old bridges or freshly issued devices.';
