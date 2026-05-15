-- DENG Tool: Rejoin — Optional encrypted full-key export for license_keys
-- Apply after 001_license_system.sql. Safe to run multiple times.

ALTER TABLE license_keys
    ADD COLUMN IF NOT EXISTS key_ciphertext TEXT,
    ADD COLUMN IF NOT EXISTS key_export_available BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN license_keys.key_ciphertext IS
    'Optional Fernet ciphertext of full key; only for keys generated after export was enabled.';
COMMENT ON COLUMN license_keys.key_export_available IS
    'True when key_ciphertext is present and the user may export the full key.';
