'use strict';

const crypto = require('crypto');
const supabase = require('./db');
const { decryptLicenseKeyCiphertext, encryptLicenseKeyPlaintext } = require('./licenseCrypto');
const { formatWibTimestamp } = require('./licenseFormat');

const FULL_KEY_UNAVAILABLE = 'Full key unavailable for this old key';
const KEY_RE = /^DENG-([0-9A-F]{4})-?([0-9A-F]{4})-?([0-9A-F]{4})-?([0-9A-F]{4})$/i;
const PUBLIC_STATS_CACHE_MS = 10_000;
let publicStatsCache = null;

const DEFAULT_GLOBAL_MAX_KEYS = 2;
const DEFAULT_GLOBAL_MAX_PANEL = 1;
const KEY_SLOT_LIMIT_MESSAGE = (
  'You have reached the 2 active-key limit. Reset HWID only moves a key to another device; '
  + 'to free a slot, ask an admin to revoke an old key.'
);
const HWID_RESET_LIMIT_MESSAGE = (
  'You already used your 1 HWID reset for today. '
  + 'Your reset limit refreshes at 00:00 WIB.'
);
const KEY_LIMIT_CACHE_MS = 30_000;
let keyLimitGlobalCache = null;
let panelLimitGlobalCache = null;

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

function hasOwner(row) {
  return Boolean(row?.owner_discord_id || row?.site_user_id);
}

function hasUnredeemedExpiry(row) {
  return Boolean(row?.expires_at || row?.key_expires_at);
}

function effectiveExpiryIso(row) {
  return row?.expires_at || row?.key_expires_at || null;
}

function isActiveUnredeemedKey(row) {
  if (!row) return false;
  const status = licenseStatus(row);
  if (['revoked', 'deleted', 'disabled', 'expired'].includes(status)) return false;
  if (row?.is_deleted || row?.deleted || row?.is_disabled || row?.disabled) return false;
  if (Boolean(row?.redeemed_at) || isBound(row)) return false;
  const expiry = effectiveExpiryIso(row);
  if (expiry && isoExpired(expiry)) return false;
  if (status !== 'active' && status !== 'expired') return false;
  if (status === 'expired') return false;
  return true;
}

function classifyLicenseLifecycle(row) {
  const status = licenseStatus(row);
  const revoked = ['revoked', 'deleted', 'disabled'].includes(status) ||
    Boolean(row?.is_deleted || row?.deleted || row?.is_disabled || row?.disabled);
  const bound = !revoked && isBound(row);
  const redeemed = !revoked && (Boolean(row?.redeemed_at) || bound);
  const expiry = effectiveExpiryIso(row);
  const expired = !revoked && (status === 'expired' || (!redeemed && expiry && isoExpired(expiry)));
  const unredeemed = !revoked && !expired && !redeemed && !bound && status === 'active';
  const unbound = !revoked && !expired && redeemed && !bound;
  const lifecycleStatus = revoked
    ? 'revoked'
    : expired
      ? 'expired'
      : bound
        ? 'bound'
        : unbound
          ? 'unbound'
          : unredeemed
            ? 'unredeemed'
            : status || 'unknown';
  const displayStatus = {
    unredeemed: 'Unredeemed',
    unbound: 'Unbound',
    bound: 'Bound',
    expired: 'Expired',
    revoked: 'Revoked',
  }[lifecycleStatus] || 'Unknown';
  return {
    lifecycle_status: lifecycleStatus,
    display_status: displayStatus,
    is_unredeemed: lifecycleStatus === 'unredeemed',
    is_redeemed: unbound || bound,
    is_unbound: lifecycleStatus === 'unbound',
    is_bound: lifecycleStatus === 'bound',
    is_expired: lifecycleStatus === 'expired',
    is_revoked: lifecycleStatus === 'revoked',
    blocks_generation: lifecycleStatus === 'unredeemed',
  };
}

function isActiveLicense(row) {
  const lifecycle = classifyLicenseLifecycle(row);
  if (lifecycle.is_revoked || lifecycle.is_expired) return false;
  if (row?.is_hidden || row?.archived || row?.hidden) return false;
  return lifecycle.is_unredeemed || lifecycle.is_redeemed;
}

function filterActiveLicenses(rows) {
  return (rows || []).filter(isActiveLicense);
}

function flagEnabled(row, names) {
  return names.some((name) => {
    const value = row ? row[name] : null;
    if (value === true || value === 1) return true;
    if (typeof value === 'string') return ['1', 'true', 'yes', 'on'].includes(value.trim().toLowerCase());
    return false;
  });
}

function publicStatsUserAllowed(row) {
  if (!row) return true;
  return !flagEnabled(row, [
    'admin',
    'dev',
    'fake',
    'is_admin',
    'is_blocked',
    'is_dev',
    'is_fake',
    'is_internal',
    'is_owner',
    'is_test',
    'test',
  ]);
}

function publicStatsLicenseAllowed(row, userByDiscord = new Map(), userBySiteId = new Map()) {
  if (!row) return false;
  const status = licenseStatus(row);
  if (['revoked', 'deleted', 'disabled', 'inactive', 'expired'].includes(status)) return false;
  if (flagEnabled(row, [
    'archived',
    'deleted',
    'disabled',
    'fake',
    'hidden',
    'is_admin',
    'is_archived',
    'is_deleted',
    'is_dev',
    'is_disabled',
    'is_fake',
    'is_hidden',
    'is_internal',
    'is_test',
    'test',
  ])) return false;
  const owner = String(row.owner_discord_id || row.created_by || '').trim();
  if (owner && !publicStatsUserAllowed(userByDiscord.get(owner))) return false;
  const siteUserId = String(row.site_user_id || '').trim();
  if (siteUserId && !publicStatsUserAllowed(userBySiteId.get(siteUserId))) return false;
  return true;
}

// Only columns that ACTUALLY exist in the live Supabase schema (verified
// against supabase/migrations/001–009). Listing a non-existent column makes
// PostgREST reject the whole SELECT, which previously caused
// `/api/public-stats` to 503 in production.
//
// The classifier functions further down (`publicStatsUserAllowed`,
// `publicStatsLicenseAllowed`, etc.) intentionally also probe defensive
// flag names like `is_test`, `is_admin`, `is_fake`, `is_internal`. Those
// are only read from the row object — if the live row does not carry
// such a column, the check is simply skipped, which is exactly what we
// want for the real schema.
const PUBLIC_STATS_COLUMNS = Object.freeze({
  device_bindings: 'key_id, install_id_hash, is_active',
  license_keys: 'id, status, owner_discord_id, site_user_id, created_by, redeemed_at, expires_at',
  license_users: 'discord_user_id, is_owner, is_blocked',
  site_users: 'id, discord_user_id, is_active',
});

// Fallback projection used when the curated SELECT fails for any reason
// (column drift, new optional flag column, etc.). `*` always succeeds for
// service-role queries and the response payload still aggregates to plain
// counts, so private fields can never leak to the public response.
const PUBLIC_STATS_FALLBACK_COLUMNS = '*';

async function selectPublicStatsRows(table) {
  const columns = PUBLIC_STATS_COLUMNS[table];
  const first = await supabase.from(table).select(columns || '*');
  if (!first.error) {
    return Array.isArray(first.data) ? first.data : [];
  }

  console.warn(
    '[public-stats] curated SELECT failed for table=%s message=%s; retrying with *',
    table,
    first.error?.message || first.error || 'unknown',
  );

  const fallback = await supabase.from(table).select(PUBLIC_STATS_FALLBACK_COLUMNS);
  if (fallback.error) {
    console.error(
      '[public-stats] fallback SELECT also failed for table=%s message=%s',
      table,
      fallback.error?.message || fallback.error || 'unknown',
    );
    throw serviceError('public_stats_unavailable', 'Public stats are unavailable.', 503);
  }
  return Array.isArray(fallback.data) ? fallback.data : [];
}

function buildPublicStatsPayload({ keys, bindings, licenseUsers, siteUsers }) {
  const licenseUserByDiscord = new Map();
  for (const row of licenseUsers || []) {
    const id = String(row.discord_user_id || '').trim();
    if (id) licenseUserByDiscord.set(id, row);
  }
  const siteUserById = new Map();
  for (const row of siteUsers || []) {
    const id = String(row.id || '').trim();
    if (id) siteUserById.set(id, row);
  }

  const eligibleKeys = (keys || []).filter((row) => (
    publicStatsLicenseAllowed(row, licenseUserByDiscord, siteUserById)
  ));
  const eligibleKeyIds = new Set(eligibleKeys.map((row) => row.id).filter(Boolean));
  const activeBindings = (bindings || []).filter((row) => (
    row &&
    row.is_active !== false &&
    eligibleKeyIds.has(row.key_id)
  ));
  const activeBindingKeyIds = new Set(activeBindings.map((row) => row.key_id).filter(Boolean));

  // Registered/authenticated portal users: all active site_users with Discord,
  // plus license_users and eligible key owners (deduped). Never filtered by
  // host/domain, session recency, or tracker build.
  const uniqueUsers = new Set();
  let siteUsersCounted = 0;
  let licenseUsersCounted = 0;
  let keyOwnersCounted = 0;
  for (const row of siteUsers || []) {
    if (row && row.is_active === false) continue;
    if (!publicStatsUserAllowed(row)) continue;
    if (row.discord_user_id) {
      uniqueUsers.add(`discord:${row.discord_user_id}`);
      siteUsersCounted += 1;
    } else if (row.id) {
      uniqueUsers.add(`site:${row.id}`);
      siteUsersCounted += 1;
    }
  }
  for (const row of licenseUsers || []) {
    if (!publicStatsUserAllowed(row)) continue;
    if (row.discord_user_id) {
      const key = `discord:${row.discord_user_id}`;
      if (!uniqueUsers.has(key)) licenseUsersCounted += 1;
      uniqueUsers.add(key);
    }
  }
  for (const row of eligibleKeys) {
    if (row.owner_discord_id) {
      const key = `discord:${row.owner_discord_id}`;
      if (!uniqueUsers.has(key)) keyOwnersCounted += 1;
      uniqueUsers.add(key);
    } else if (row.site_user_id) {
      const key = `site:${row.site_user_id}`;
      if (!uniqueUsers.has(key)) keyOwnersCounted += 1;
      uniqueUsers.add(key);
    }
  }

  const activeDevices = new Set();
  const totalDevices = new Set();
  for (const row of activeBindings) {
    const deviceKey = String(row.install_id_hash || row.key_id || '').trim();
    if (deviceKey) activeDevices.add(deviceKey);
  }
  for (const row of bindings || []) {
    if (!eligibleKeyIds.has(row.key_id)) continue;
    const deviceKey = String(row.install_id_hash || row.key_id || '').trim();
    if (deviceKey) totalDevices.add(deviceKey);
  }

  return {
    generatedKeys: eligibleKeys.length,
    uniqueUsers: uniqueUsers.size,
    redeemedKeys: eligibleKeys.filter((row) => Boolean(row.redeemed_at) || activeBindingKeyIds.has(row.id)).length,
    activeDevices: activeDevices.size,
    totalDevices: totalDevices.size,
    updatedAt: new Date().toISOString(),
    _internalSources: {
      uniqueUsers: {
        service: 'licenseService',
        method: 'COUNT DISTINCT discord/site ids from site_users + license_users + eligible key owners',
        siteUsersRows: (siteUsers || []).length,
        siteUsersCounted,
        licenseUsersRows: (licenseUsers || []).length,
        licenseUsersCounted,
        keyOwnersCounted,
        excludes: ['tracker sessions', 'online presence', 'host/domain', 'recent login window'],
      },
    },
  };
}

async function getPublicStats({ now = Date.now(), forceRefresh = false } = {}) {
  if (!forceRefresh && publicStatsCache && now - publicStatsCache.cachedAt < PUBLIC_STATS_CACHE_MS) {
    return publicStatsCache.payload;
  }
  const [keys, bindings, licenseUsers, siteUsers] = await Promise.all([
    selectPublicStatsRows('license_keys'),
    selectPublicStatsRows('device_bindings'),
    selectPublicStatsRows('license_users'),
    selectPublicStatsRows('site_users'),
  ]);
  const built = buildPublicStatsPayload({ keys, bindings, licenseUsers, siteUsers });
  const { _internalSources, ...payload } = built;
  publicStatsCache = { cachedAt: now, payload, sources: _internalSources };
  return payload;
}

function clearPublicStatsCache() {
  publicStatsCache = null;
}

function peekPublicStatsCache() {
  return publicStatsCache;
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
  const lifecycles = rows.map(classifyLicenseLifecycle);
  const unredeemed = lifecycles.filter((item) => item.is_unredeemed).length;
  const unbound = lifecycles.filter((item) => item.is_unbound).length;
  const bound = lifecycles.filter((item) => item.is_bound).length;
  const redeemed = lifecycles.filter((item) => item.is_redeemed).length;
  const expired = lifecycles.filter((item) => item.is_expired).length;
  const revoked = lifecycles.filter((item) => item.is_revoked).length;
  const primary = unredeemed > 0
    ? { label: 'Unused Keys', value: unredeemed, note: 'Ready to redeem in Rejoin', kind: 'unused' }
    : unbound > 0
      ? { label: 'Unbound Keys', value: unbound, note: 'Ready to bind in Rejoin', kind: 'unbound' }
      : { label: 'Bound Keys', value: bound, note: 'Bound to a device', kind: 'bound' };
  return {
    total: generated,
    unredeemed,
    expired,
    revoked,
    key_generated_count: generated,
    key_redeemed_count: redeemed,
    unused_key_count: unredeemed,
    unbound_key_count: unbound,
    bound_key_count: bound,
    reset_hwid_count: resetCount,
    key_executed_count: executionCount,
    cooldownSeconds: 0,
    keyExpiryHours: 24,
    latest: rows[0] || null,
    primary_key_card: primary,
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
  return classifyLicenseLifecycle(row).display_status;
}

function deviceStatus(row) {
  const lifecycle = classifyLicenseLifecycle(row);
  if (lifecycle.is_bound) return 'Bound';
  if (lifecycle.is_unbound) return 'Unbound';
  if (lifecycle.is_unredeemed) return 'Unredeemed';
  return lifecycle.display_status;
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
    const row = {
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
    return { ...row, ...classifyLicenseLifecycle(row) };
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
  return isActiveUnredeemedKey(row);
}

function isUnredeemedCandidate(row) {
  const status = licenseStatus(row);
  if (['revoked', 'deleted', 'disabled', 'expired'].includes(status)) return false;
  return Boolean(row && !row.redeemed_at && !isBound(row));
}

async function markExpiredUnredeemedKeys({ discordUserId = '', siteUserId = '' } = {}) {
  const rows = await getPortalUserLicenses({ discordUserId, siteUserId, limit: 500 });
  const expired = rows.filter((row) => (
    isUnredeemedCandidate(row) &&
    licenseStatus(row) === 'active' &&
    isoExpired(effectiveExpiryIso(row))
  ));
  await Promise.all(expired.map((row) => (
    supabase.from('license_keys').update({ status: 'expired' }).eq('id', row.id)
  )));
  return expired.length;
}

async function findActiveUnredeemedKey({ discordUserId = '', siteUserId = '' } = {}) {
  await markExpiredUnredeemedKeys({ discordUserId, siteUserId });
  const rows = await getPortalUserLicenses({ discordUserId, siteUserId, limit: 500 });
  return rows.find((row) => isActiveUnredeemedKey(row)) || null;
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

// Redeem has been removed. Ad-generated keys are directly usable in DENG Tool
// Rejoin with no redeem step. This stub never mutates state; it exists only so
// any legacy caller fails closed with a clear 410 instead of touching the DB.
async function redeemLicenseKey() {
  throw serviceError(
    'feature_removed',
    'Redeem has been removed. Your generated key works directly in DENG Tool Rejoin.',
    410,
  );
}

// Reset HWID has been removed. One key binds to one device for its 48h lifetime;
// to use a new device, generate a new key. This stub never mutates state.
async function resetLicenseHwid() {
  throw serviceError(
    'feature_removed',
    'Reset HWID has been removed. Generate a new key for a new device.',
    410,
  );
}

function downloadUserKeys(discordUserId, rows, username = '') {
  const activeRows = filterActiveLicenses(rows || []);
  const lines = [
    'DENG All In One License Keys',
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

// ── Key limit helpers (global + per-user from license_key_limits table) ────────

function _clearKeyLimitCache() {
  keyLimitGlobalCache = null;
}

async function getGlobalMaxKeys() {
  const now = Date.now();
  if (keyLimitGlobalCache && now - keyLimitGlobalCache.cachedAt < KEY_LIMIT_CACHE_MS) {
    return keyLimitGlobalCache.value;
  }
  try {
    const { data } = await supabase
      .from('license_key_limits')
      .select('max_keys')
      .eq('scope', 'global')
      .maybeSingle();
    const value = (data && typeof data.max_keys === 'number') ? data.max_keys : DEFAULT_GLOBAL_MAX_KEYS;
    keyLimitGlobalCache = { cachedAt: now, value };
    return value;
  } catch {
    return DEFAULT_GLOBAL_MAX_KEYS;
  }
}

async function getUserKeyLimit(discordUserId) {
  if (!discordUserId) return null;
  try {
    const { data } = await supabase
      .from('license_key_limits')
      .select('max_keys')
      .eq('scope', 'user')
      .eq('discord_user_id', String(discordUserId))
      .maybeSingle();
    return (data && typeof data.max_keys === 'number') ? data.max_keys : null;
  } catch {
    return null;
  }
}

async function getEffectiveMaxKeys(discordUserId) {
  if (discordUserId) {
    const userLimit = await getUserKeyLimit(discordUserId);
    if (userLimit !== null) return userLimit;
  }
  return getGlobalMaxKeys();
}

async function countActiveKeysForLimit(discordUserId, siteUserId) {
  const rows = await getPortalUserLicenses({
    discordUserId: discordUserId || '',
    siteUserId: siteUserId || '',
    limit: 500,
  });
  return filterActiveLicenses(rows).length;
}

async function canUserReceiveNewKey(discordUserId, siteUserId) {
  // Max user/key-slot limit removed: generation is never blocked by how many
  // keys already exist (HWID binding + 48h expiry protect ad revenue instead).
  let activeCount = 0;
  try {
    activeCount = await countActiveKeysForLimit(discordUserId, siteUserId);
  } catch {
    activeCount = 0;
  }
  return { allowed: true, activeCount, maxKeys: Infinity };
}

function getWibDay(date = new Date()) {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Jakarta',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date);
}

async function getGlobalMaxPanel() {
  const now = Date.now();
  if (panelLimitGlobalCache && now - panelLimitGlobalCache.cachedAt < KEY_LIMIT_CACHE_MS) {
    return panelLimitGlobalCache.value;
  }
  try {
    const { data } = await supabase
      .from('license_key_limits')
      .select('max_panel')
      .eq('scope', 'global')
      .maybeSingle();
    const value = (data && typeof data.max_panel === 'number') ? data.max_panel : DEFAULT_GLOBAL_MAX_PANEL;
    panelLimitGlobalCache = { cachedAt: now, value };
    return value;
  } catch {
    return DEFAULT_GLOBAL_MAX_PANEL;
  }
}

async function getUserPanelLimit(discordUserId) {
  if (!discordUserId) return null;
  try {
    const { data } = await supabase
      .from('license_key_limits')
      .select('max_panel')
      .eq('scope', 'user')
      .eq('discord_user_id', String(discordUserId))
      .maybeSingle();
    return (data && typeof data.max_panel === 'number') ? data.max_panel : null;
  } catch {
    return null;
  }
}

async function getEffectiveMaxPanel(discordUserId) {
  const userLimit = await getUserPanelLimit(discordUserId);
  if (userLimit !== null) return userLimit;
  return getGlobalMaxPanel();
}

async function getPanelResetUsageToday(discordUserId) {
  if (!discordUserId) return 0;
  try {
    const { data } = await supabase
      .from('license_panel_reset_usage')
      .select('used_count')
      .eq('discord_user_id', String(discordUserId))
      .eq('reset_day_wib', getWibDay())
      .maybeSingle();
    return (data && typeof data.used_count === 'number') ? data.used_count : 0;
  } catch {
    return 0;
  }
}

async function canUserResetPanelToday(discordUserId) {
  const [usedCount, maxPanel] = await Promise.all([
    getPanelResetUsageToday(discordUserId),
    getEffectiveMaxPanel(discordUserId),
  ]);
  return { allowed: usedCount < maxPanel, usedCount, maxPanel };
}

async function recordSuccessfulPanelReset(discordUserId, unboundKeyCount = 1) {
  const owner = String(discordUserId || '').trim();
  if (!owner) throw serviceError('auth_required', 'Please login with Discord first.', 401);
  const wibDay = getWibDay();
  const maxPanel = await getEffectiveMaxPanel(owner);
  const { data: existing } = await supabase
    .from('license_panel_reset_usage')
    .select('used_count')
    .eq('discord_user_id', owner)
    .eq('reset_day_wib', wibDay)
    .maybeSingle();
  const currentCount = (existing && typeof existing.used_count === 'number') ? existing.used_count : 0;
  if (currentCount >= maxPanel) {
    throw serviceError(
      'panel_reset_limit_reached',
      HWID_RESET_LIMIT_MESSAGE,
      403,
      { usedCount: currentCount, maxPanel },
    );
  }
  const now = new Date().toISOString();
  if (existing) {
    const { error } = await supabase
      .from('license_panel_reset_usage')
      .update({ used_count: currentCount + 1, last_reset_at: now, updated_at: now })
      .eq('discord_user_id', owner)
      .eq('reset_day_wib', wibDay);
    if (error) throw serviceError('database_error', 'Could not record HWID reset usage.', 500);
    return currentCount + 1;
  }
  const { error } = await supabase.from('license_panel_reset_usage').insert({
    discord_user_id: owner,
    reset_day_wib: wibDay,
    used_count: 1,
    last_reset_at: now,
    created_at: now,
    updated_at: now,
  });
  if (error) throw serviceError('database_error', 'Could not record HWID reset usage.', 500);
  return 1;
}

module.exports = {
  FULL_KEY_UNAVAILABLE,
  classifyLicenseLifecycle,
  computeStats,
  findActiveUnredeemedKey,
  isActiveUnredeemedKey,
  filterActiveLicenses,
  formatLicenseStatus,
  getActiveUserLicenses,
  getPortalUserLicenses,
  getPublicStats,
  getUserLicenses,
  getUserLicenseStats,
  downloadUserKeys,
  formatWibTimestamp,
  getRecoverableFullKey: recoverableFullKey,
  isActiveLicense,
  isUnusedLicense,
  normalizeLicenseKey,
  providerLabel,
  redeemLicenseKey,
  resetLicenseHwid,
  getGlobalMaxKeys,
  getUserKeyLimit,
  getEffectiveMaxKeys,
  countActiveKeysForLimit,
  canUserReceiveNewKey,
  canUserResetPanelToday,
  getEffectiveMaxPanel,
  getPanelResetUsageToday,
  getWibDay,
  recordSuccessfulPanelReset,
  markExpiredUnredeemedKeys,
  KEY_SLOT_LIMIT_MESSAGE,
  HWID_RESET_LIMIT_MESSAGE,
  _buildPublicStatsPayload: buildPublicStatsPayload,
  _clearPublicStatsCache: clearPublicStatsCache,
  _peekPublicStatsCache: peekPublicStatsCache,
  _clearKeyLimitCache,
};
