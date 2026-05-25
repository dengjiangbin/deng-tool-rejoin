'use strict';

const crypto = require('crypto');
const supabase = require('./db');
const { decryptLicenseKeyCiphertext, encryptLicenseKeyPlaintext } = require('./licenseCrypto');
const { formatWibTimestamp } = require('./licenseFormat');

const FULL_KEY_UNAVAILABLE = 'Full key unavailable for this old key';
const KEY_RE = /^DENG-([0-9A-F]{4})-?([0-9A-F]{4})-?([0-9A-F]{4})-?([0-9A-F]{4})$/i;

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

function serviceError(code, message, status = 400, extra = {}) {
  const err = new Error(message || code);
  err.code = code;
  err.status = status;
  Object.assign(err, extra);
  return err;
}

function normalizeLicenseKey(raw) {
  const cleaned = String(raw || '').trim().toUpperCase();
  const match = cleaned.match(KEY_RE);
  if (!match) {
    throw serviceError('invalid_key_format', 'Invalid license key format.');
  }
  return `DENG-${match[1]}-${match[2]}-${match[3]}-${match[4]}`;
}

function hashLicenseKey(raw) {
  const normalized = normalizeLicenseKey(raw);
  return crypto.createHash('sha256').update(normalized).digest('hex');
}

function recoverableFullKey(row) {
  const full = row?.full_key || row?.full_key_plaintext || '';
  const text = String(full || '').trim();
  if (!text || text.includes('...') || text.includes('…')) return null;
  return text;
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
  if (provider === 'website') return 'Website';
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

function deviceStatus(row) {
  return isBound(row) ? 'Bound' : 'No Device Linked';
}

async function fetchByKeyId(table, columns, keyIds, field = 'key_id') {
  if (!keyIds.length) return [];
  const { data, error } = await supabase.from(table).select(columns).in(field, keyIds);
  if (error) return [];
  return data || [];
}

async function fetchLicenseRowsByOwner(owner, limit) {
  const normalized = String(owner || '').trim();
  if (!normalized) return [];
  let { data: keys, error } = await supabase
    .from('license_keys')
    .select('id, prefix, suffix, status, plan, created_at, expires_at, redeemed_at, owner_discord_id, site_user_id, key_ciphertext, key_export_available')
    .eq('owner_discord_id', normalized)
    .order('created_at', { ascending: false })
    .limit(limit);
  if (error && missingColumn(error)) {
    const retry = await supabase
      .from('license_keys')
      .select('id, prefix, suffix, status, plan, created_at, expires_at, redeemed_at, owner_discord_id, site_user_id')
      .eq('owner_discord_id', normalized)
      .order('created_at', { ascending: false })
      .limit(limit);
    keys = retry.data;
    error = retry.error;
  }
  if (error) {
    console.error('[licenseService/fetchLicenseRowsByOwner]', error.message || error);
    return [];
  }
  return keys || [];
}

async function fetchLicenseRowsBySiteUser(siteUserId, limit) {
  const normalized = String(siteUserId || '').trim();
  if (!normalized) return [];
  let { data: keys, error } = await supabase
    .from('license_keys')
    .select('id, prefix, suffix, status, plan, created_at, expires_at, redeemed_at, owner_discord_id, site_user_id, key_ciphertext, key_export_available')
    .eq('site_user_id', normalized)
    .order('created_at', { ascending: false })
    .limit(limit);
  if (error && missingColumn(error)) return [];
  if (error) {
    console.error('[licenseService/fetchLicenseRowsBySiteUser]', error.message || error);
    return [];
  }
  return keys || [];
}

function uniqueRows(rows) {
  const out = [];
  const seen = new Set();
  for (const row of rows || []) {
    if (!row || !row.id || seen.has(row.id)) continue;
    seen.add(row.id);
    out.push(row);
  }
  return out;
}

async function hydrateLicenseRows(keyRows, owner = '') {
  const rows = keyRows || [];
  const keyIds = rows.map((row) => row.id).filter(Boolean);
  const [bindings, challenges] = await Promise.all([
    fetchByKeyId('device_bindings', 'key_id, device_model, device_label, last_seen_at, is_active', keyIds),
    fetchByKeyId('license_ad_challenges', 'license_key_id, key_prefix, key_suffix, provider, completed_at, created_at, key_expires_at', keyIds, 'license_key_id'),
  ]);

  const bindingByKey = new Map(bindings.map((row) => [row.key_id, row]));
  const challengeByKey = new Map(challenges.map((row) => [row.license_key_id, row]));

  return rows.map((record) => {
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
      owner_discord_id: record.owner_discord_id || owner,
      site_user_id: record.site_user_id || null,
      created_at: challenge.completed_at || challenge.created_at || record.created_at,
      key_expires_at: challenge.key_expires_at || record.expires_at || null,
      expires_at: record.expires_at || null,
      redeemed_at: record.redeemed_at || null,
      plan: record.plan || 'standard',
      used: activeBinding,
      active_binding: activeBinding,
      device_display: activeBinding ? (device || '(bound)') : '',
      device_status: activeBinding ? 'Bound To A Device' : 'No Device Linked',
      last_seen_at: activeBinding ? binding.last_seen_at : null,
    };
  });
}

async function getUserLicenses(discordUserId, { limit = 20 } = {}) {
  const owner = String(discordUserId || '').trim();
  if (!owner) return [];
  return hydrateLicenseRows(await fetchLicenseRowsByOwner(owner, limit), owner);
}

async function getPortalUserLicenses({ discordUserId = '', siteUserId = '', limit = 20 } = {}) {
  const owners = [
    String(discordUserId || '').trim(),
    siteUserId ? `site:${siteUserId}` : '',
  ].filter(Boolean);
  const rowSets = await Promise.all([
    ...owners.map((owner) => fetchLicenseRowsByOwner(owner, limit)),
    fetchLicenseRowsBySiteUser(siteUserId, limit),
  ]);
  const rows = uniqueRows(rowSets.flat()).sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
  return hydrateLicenseRows(rows.slice(0, limit), discordUserId || siteUserId);
}

function isUnusedLicense(row) {
  return Boolean(row && !row.redeemed_at && !row.used && !row.active_binding);
}

async function markExpiredUnredeemedKeys({ discordUserId = '', siteUserId = '' } = {}) {
  const rows = await getPortalUserLicenses({ discordUserId, siteUserId, limit: 500 });
  const expired = rows.filter((row) => (
    isUnusedLicense(row) &&
    licenseStatus(row) === 'active' &&
    isoExpired(row.expires_at)
  ));
  await Promise.all(expired.map((row) => (
    supabase.from('license_keys').update({ status: 'expired' }).eq('id', row.id)
  )));
  return expired.length;
}

async function findActiveUnredeemedKey({ discordUserId = '', siteUserId = '' } = {}) {
  await markExpiredUnredeemedKeys({ discordUserId, siteUserId });
  const rows = await getPortalUserLicenses({ discordUserId, siteUserId, limit: 500 });
  return rows.find((row) => (
    isUnusedLicense(row) &&
    licenseStatus(row) === 'active' &&
    !isoExpired(row.expires_at)
  )) || null;
}

async function getActiveUserLicenses(discordUserId, opts = {}) {
  return filterActiveLicenses(await getUserLicenses(discordUserId, { limit: opts.limit || 200 }));
}

async function getRawLicenseById(keyId) {
  const { data, error } = await supabase
    .from('license_keys')
    .select('id, prefix, suffix, status, plan, created_at, expires_at, redeemed_at, owner_discord_id, key_ciphertext, key_export_available')
    .eq('id', keyId)
    .maybeSingle();
  if (error) throw serviceError('database_error', 'License database error.', 500);
  return data || null;
}

async function getBindingByKeyId(keyId) {
  const { data, error } = await supabase
    .from('device_bindings')
    .select('key_id, install_id_hash, device_model, device_label, last_seen_at, is_active')
    .eq('key_id', keyId)
    .maybeSingle();
  if (error) throw serviceError('database_error', 'License database error.', 500);
  return data || null;
}

async function ensureLicenseUser(discordUserId) {
  const owner = String(discordUserId || '').trim();
  if (!owner) throw serviceError('auth_required', 'Please login with Discord first.', 401);
  const { data, error } = await supabase
    .from('license_users')
    .select('discord_user_id, max_keys, is_blocked')
    .eq('discord_user_id', owner)
    .maybeSingle();
  if (error) throw serviceError('database_error', 'License database error.', 500);
  if (data) return data;
  const insert = await supabase.from('license_users').insert({
    discord_user_id: owner,
    discord_username: 'DENG Tool Portal User',
    max_keys: 999999,
    is_owner: false,
    is_blocked: false,
  });
  if (insert.error && !String(insert.error.message || '').toLowerCase().includes('duplicate')) {
    throw serviceError('database_error', 'License database error.', 500);
  }
  return { discord_user_id: owner, max_keys: 999999, is_blocked: false };
}

async function redeemLicenseKey(discordUserId, rawKey) {
  const owner = String(discordUserId || '').trim();
  if (!owner) throw serviceError('auth_required', 'Please login with Discord first.', 401);
  let normalized;
  let keyId;
  try {
    normalized = normalizeLicenseKey(rawKey);
    keyId = hashLicenseKey(normalized);
  } catch {
    throw serviceError('invalid_key_format', 'Invalid license key format.');
  }

  const record = await getRawLicenseById(keyId);
  if (!record) throw serviceError('key_not_found', 'Key not found.');
  const status = licenseStatus(record);
  if (status === 'revoked' || status === 'deleted' || status === 'disabled') {
    throw serviceError('key_not_redeemable', 'This key is revoked or disabled.');
  }
  if (status === 'expired' || isoExpired(record.expires_at)) {
    throw serviceError('key_expired', 'This key has expired.');
  }

  if (record.owner_discord_id === owner) {
    const encrypted = encryptLicenseKeyPlaintext(normalized);
    if (encrypted && !decryptLicenseKeyCiphertext(record.key_ciphertext || '')) {
      await supabase.from('license_keys').update({
        key_ciphertext: encrypted,
        key_export_available: true,
      }).eq('id', keyId);
    }
    return { status: 'already_owned', key: normalized, message: 'This key is already redeemed by you.' };
  }
  if (record.owner_discord_id && record.owner_discord_id !== owner) {
    throw serviceError('key_owned_by_another_user', 'This key belongs to another user.', 403);
  }

  const user = await ensureLicenseUser(owner);
  if (user.is_blocked) throw serviceError('user_blocked', 'This Discord account cannot redeem keys.', 403);
  const maxKeys = Number(user.max_keys || 999999);
  const { data: existingKeys, error: countError } = await supabase
    .from('license_keys')
    .select('id')
    .eq('owner_discord_id', owner);
  if (countError) throw serviceError('database_error', 'License database error.', 500);
  if ((existingKeys || []).length >= maxKeys) {
    throw serviceError('key_limit_reached', `You have reached your license key limit (${maxKeys}).`, 403);
  }

  const encrypted = encryptLicenseKeyPlaintext(normalized);
  const payload = {
    owner_discord_id: owner,
    expires_at: null,
    redeemed_at: new Date().toISOString(),
    ...(encrypted ? { key_ciphertext: encrypted, key_export_available: true } : {}),
  };
  const { error } = await supabase.from('license_keys').update(payload).eq('id', keyId);
  if (error) throw serviceError('database_error', 'Could not redeem key. Please try again.', 500);
  return { status: 'redeemed', key: normalized, message: 'Key Redeemed Successfully.' };
}

async function resetLicenseHwid(discordUserId, keyIdOrKey) {
  const owner = String(discordUserId || '').trim();
  if (!owner) throw serviceError('auth_required', 'Please login with Discord first.', 401);
  const raw = String(keyIdOrKey || '').trim();
  const keyId = raw.startsWith('DENG-') ? hashLicenseKey(raw) : raw;
  if (!/^[a-f0-9]{64}$/i.test(keyId)) throw serviceError('key_not_found', 'Key not found.');

  const record = await getRawLicenseById(keyId);
  if (!record) throw serviceError('key_not_found', 'Key not found.');
  if (record.owner_discord_id !== owner) {
    throw serviceError('key_not_owned', 'You do not own this key.', 403);
  }
  if (!isActiveLicense(record)) {
    throw serviceError('key_not_resettable', 'This key is revoked, expired, or inactive.');
  }

  const binding = await getBindingByKeyId(keyId);
  if (!binding || !binding.is_active) {
    throw serviceError('no_device_linked', 'No device is currently linked to this key.');
  }

  const { error: updateError } = await supabase
    .from('device_bindings')
    .update({ is_active: false })
    .eq('key_id', keyId);
  if (updateError) throw serviceError('database_error', 'Could not reset HWID. Please try again.', 500);

  const { error: logError } = await supabase.from('hwid_reset_logs').insert({
    key_id: keyId,
    owner_discord_id: owner,
    old_install_id_hash: binding.install_id_hash || null,
    reason: 'user_requested',
  });
  if (logError) throw serviceError('database_error', 'HWID was reset, but reset logging failed.', 500);

  return { status: 'reset', message: 'HWID Reset Successful. You Can Bind This Key On A New Device.' };
}

function downloadUserKeys(discordUserId, rows, username = '') {
  const activeRows = filterActiveLicenses(rows || []);
  const lines = [
    'DENG Tool: Rejoin Keys',
    `User: ${username || discordUserId}`,
    `Generated: ${formatWibTimestamp(new Date())}`,
    '',
  ];
  if (!activeRows.length) {
    lines.push('No active recoverable keys found.');
    return `${lines.join('\n')}\n`;
  }
  activeRows.forEach((row, idx) => {
    const full = recoverableFullKey(row) || FULL_KEY_UNAVAILABLE;
    lines.push(`${idx + 1}. Key: ${full}`);
    lines.push(`   Status: ${deviceStatus(row)}`);
    lines.push(`   Device: ${row.device_display || 'None'}`);
    lines.push(`   Created: ${formatWibTimestamp(row.created_at)}`);
    lines.push(`   Expires: ${formatWibTimestamp(row.expires_at || row.key_expires_at)}`);
    lines.push(`   Redeemed: ${formatWibTimestamp(row.redeemed_at)}`);
    lines.push(`   Provider: ${providerLabel(row.provider)}`);
    lines.push('');
  });
  return `${lines.join('\n').trimEnd()}\n`;
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
  findActiveUnredeemedKey,
  filterActiveLicenses,
  formatLicenseStatus,
  getActiveUserLicenses,
  getPortalUserLicenses,
  getUserLicenses,
  getUserLicenseStats,
  downloadUserKeys,
  formatWibTimestamp,
  getRecoverableFullKey: recoverableFullKey,
  isActiveLicense,
  normalizeLicenseKey,
  providerLabel,
  redeemLicenseKey,
  resetLicenseHwid,
};
