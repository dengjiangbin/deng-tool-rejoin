'use strict';

const supabase = require('./db');
const { decryptLicenseKeyCiphertext } = require('./licenseCrypto');

const FULL_KEY_UNAVAILABLE = 'Full key unavailable for this old key';

function isoExpired(value) {
  if (!value) return false;
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return false;
  return Date.now() > ts;
}

function licenseStatus(row) {
  return String(row?.license_status || row?.status || 'active').toLowerCase();
}

function isBound(row) {
  if (row?.used || row?.active_binding) return true;
  const device = String(row?.device_display || row?.bound_device || '').trim();
  return Boolean(device && device !== '(unbound)');
}

function isActiveLicense(row) {
  const status = licenseStatus(row);
  if (['revoked', 'expired', 'deleted', 'disabled'].includes(status)) return false;
  if (row?.is_deleted || row?.deleted || row?.is_disabled || row?.disabled) return false;
  if (row?.is_hidden || row?.archived || row?.hidden) return false;
  if (row?.redeemed_at || isBound(row)) return true;
  if (status !== 'active') return false;
  if (isoExpired(row?.expires_at)) return false;
  return true;
}

function filterActiveLicenses(rows) {
  return (rows || []).filter(isActiveLicense);
}

function computeStats(activeRows, { resetCount = 0, executionCount = 0 } = {}) {
  const rows = activeRows || [];
  const generated = rows.length;
  const bound = rows.filter(isBound).length;
  const redeemed = rows.filter((row) => row.redeemed_at || isBound(row)).length;
  return {
    total: generated,
    unredeemed: Math.max(0, generated - bound),
    expired: 0,
    key_generated_count: generated,
    key_redeemed_count: redeemed,
    unbound_key_count: Math.max(0, generated - bound),
    bound_key_count: bound,
    reset_hwid_count: resetCount,
    key_executed_count: executionCount,
    cooldownSeconds: 0,
    keyExpiryHours: 24,
    latest: rows[0] || null,
  };
}

function splitFullKey(raw) {
  const parts = String(raw || '').trim().split('-');
  if (parts.length !== 5 || parts[0] !== 'DENG') return null;
  return {
    key_prefix: `${parts[0]}-${parts[1]}-${parts[2]}`,
    key_suffix: `${parts[3]}-${parts[4]}`,
  };
}

function reconstructChallengeKey(challenge) {
  if (!challenge?.key_prefix || !challenge?.key_suffix) return null;
  const full = `${challenge.key_prefix}-${challenge.key_suffix}`;
  return /^DENG-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$/i.test(full) ? full : null;
}

function maskedKey(record) {
  const prefix = record?.prefix || 'DENG-????';
  const suffix = record?.suffix || '????';
  return `${prefix}...${suffix}`;
}

function providerLabel(provider) {
  if (provider === 'linkvertise') return 'Linkvertise';
  if (provider === 'lootlabs') return 'LootLabs';
  if (provider === 'discord') return 'Discord Panel';
  return 'License';
}

function formatLicenseStatus(row) {
  if (!row) return 'Unknown';
  const status = licenseStatus(row);
  if (status === 'revoked') return 'Revoked';
  if (status === 'expired' || isoExpired(row.expires_at)) return 'Expired';
  if (isBound(row)) return 'Bound';
  if (row.owner_discord_id || row.license_key_id) return 'Unbound';
  if (row.redeemed_at) return 'Unbound';
  return 'Generated';
}

async function fetchByKeyId(table, columns, keyIds, field = 'key_id') {
  if (!keyIds.length) return [];
  const { data, error } = await supabase.from(table).select(columns).in(field, keyIds);
  if (error) return [];
  return data || [];
}

async function getUserLicenses(discordUserId, { limit = 20 } = {}) {
  const owner = String(discordUserId || '').trim();
  if (!owner) return [];

  let { data: keys, error } = await supabase
    .from('license_keys')
    .select('id, prefix, suffix, status, plan, created_at, expires_at, redeemed_at, key_ciphertext, key_export_available')
    .eq('owner_discord_id', owner)
    .order('created_at', { ascending: false })
    .limit(limit);
  if (error && missingColumn(error)) {
    const retry = await supabase
      .from('license_keys')
      .select('id, prefix, suffix, status, plan, created_at, expires_at, redeemed_at')
      .eq('owner_discord_id', owner)
      .order('created_at', { ascending: false })
      .limit(limit);
    keys = retry.data;
    error = retry.error;
  }
  if (error) {
    console.error('[licenseService/getUserLicenses]', error.message || error);
    return [];
  }

  const keyRows = keys || [];
  const keyIds = keyRows.map((row) => row.id).filter(Boolean);
  const [bindings, challenges] = await Promise.all([
    fetchByKeyId('device_bindings', 'key_id, device_model, device_label, last_seen_at, is_active', keyIds),
    fetchByKeyId('license_ad_challenges', 'license_key_id, key_prefix, key_suffix, provider, completed_at, created_at, key_expires_at', keyIds, 'license_key_id'),
  ]);

  const bindingByKey = new Map(bindings.map((row) => [row.key_id, row]));
  const challengeByKey = new Map(challenges.map((row) => [row.license_key_id, row]));

  return keyRows.map((record) => {
    const binding = bindingByKey.get(record.id) || {};
    const challenge = challengeByKey.get(record.id) || {};
    const activeBinding = Boolean(binding.is_active);
    const challengeFullKey = reconstructChallengeKey(challenge);
    const encryptedFullKey = decryptLicenseKeyCiphertext(record.key_ciphertext || '');
    const full = encryptedFullKey || challengeFullKey || null;
    const split = splitFullKey(full);
    const device = String(binding.device_model || binding.device_label || '').trim();
    return {
      id: record.id,
      license_key_id: record.id,
      prefix: record.prefix,
      suffix: record.suffix,
      key_prefix: split?.key_prefix || record.prefix || 'DENG-????',
      key_suffix: split?.key_suffix || record.suffix || '????',
      full_key: full,
      full_key_unavailable: !full,
      key_display: full || FULL_KEY_UNAVAILABLE,
      masked_key: maskedKey(record),
      provider: challenge.provider || 'discord',
      status: record.status || 'active',
      license_status: record.status || 'active',
      owner_discord_id: owner,
      created_at: challenge.completed_at || challenge.created_at || record.created_at,
      key_expires_at: challenge.key_expires_at || record.expires_at || null,
      expires_at: record.expires_at || null,
      redeemed_at: record.redeemed_at || null,
      plan: record.plan || 'standard',
      used: activeBinding,
      active_binding: activeBinding,
      device_display: activeBinding ? (device || '(bound)') : '',
      last_seen_at: activeBinding ? binding.last_seen_at : null,
    };
  });
}

function missingColumn(error) {
  const text = `${error?.code || ''} ${error?.message || ''} ${error?.details || ''} ${error?.hint || ''}`.toLowerCase();
  return text.includes('pgrst204') || text.includes('column') || text.includes('schema cache');
}

async function countRows(table, filters) {
  let query = supabase.from(table).select('id', { count: 'exact' });
  for (const filter of filters) {
    query = query.eq(filter.field, filter.value);
  }
  const { count, error } = await query;
  if (error) return 0;
  return count || 0;
}

async function getUserLicenseStats(discordUserId, { limit = 200 } = {}) {
  const rows = await getUserLicenses(discordUserId, { limit });
  const active = filterActiveLicenses(rows);
  const [resetCount, executionCount] = await Promise.all([
    countRows('hwid_reset_logs', [{ field: 'owner_discord_id', value: String(discordUserId) }]),
    countRows('license_key_executions', [
      { field: 'owner_discord_id', value: String(discordUserId) },
      { field: 'is_public_release', value: true },
    ]),
  ]);
  return { rows: active, stats: computeStats(active, { resetCount, executionCount }) };
}

module.exports = {
  FULL_KEY_UNAVAILABLE,
  computeStats,
  filterActiveLicenses,
  formatLicenseStatus,
  getUserLicenses,
  getUserLicenseStats,
  isActiveLicense,
  providerLabel,
};
