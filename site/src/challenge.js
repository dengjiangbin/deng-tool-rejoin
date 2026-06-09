'use strict';
/**
 * Challenge lifecycle management for the Luarmor-style key flow.
 *
 * Status machine:
 *   created -> provider_selected -> pending_ad -> ad_completed -> key_generated
 */
const supabase = require('./db');
const { signChallenge, verifyChallenge, sha256, randomHex } = require('./crypto');
const { generateDengKey } = require('./keyGen');
const { encryptLicenseKeyPlaintext, decryptLicenseKeyCiphertext } = require('./licenseCrypto');
const licenseService = require('./licenseService');
const crypto = require('crypto');

const COOLDOWN_SECONDS = parseInt(process.env.KEY_GENERATION_COOLDOWN_SECONDS || '60', 10);
const CHALLENGE_TTL_MS = 30 * 60 * 1000;
const KEY_EXPIRY_HOURS = parseInt(process.env.UNREDEEMED_KEY_EXPIRY_HOURS || '24', 10);
const AD_MIN_COMPLETION_SECONDS = parseInt(process.env.AD_MIN_COMPLETION_SECONDS || '30', 10);
const RETURN_TOKEN_TTL_MS = 30 * 60 * 1000;

// Log configuration once at startup (only outside tests to avoid noise).
// We never log secret VALUES — only boolean presence and lengths.
if (process.env.NODE_ENV !== 'test') {
  console.log(
    '[challenge/cfg] AD_MIN_COMPLETION_SECONDS=%d LOOTLABS_API_TOKEN_present=%s LOOTLABS_BASE_LINK_present=%s AD_RETURN_SIGNING_SECRET_len=%d',
    AD_MIN_COMPLETION_SECONDS,
    !!(process.env.LOOTLABS_API_TOKEN || process.env.LOOTLABS_API_KEY),
    !!(process.env.LOOTLABS_BASE_LINK || process.env.LOOTLABS_MONETIZED_URL),
    (process.env.AD_RETURN_SIGNING_SECRET || '').length,
  );
}

const ALLOWED_PROVIDER_REFERERS = {
  linkvertise: [
    'link-hub.net',
    'linkvertise.com',
    'publisher.linkvertise.com',
  ],
  lootlabs: [
    'lootdest.org',
    'lootlabs.gg',
    'loot-link.com',
  ],
};

const CONSUMED_STATUSES = ['ad_completed', 'key_generated', 'completed', 'used'];
const COMPLETABLE_STATUSES = ['provider_selected', 'pending_ad', 'ad_started'];
const RESUMABLE_STATUSES = ['created', 'provider_selected', 'pending_ad', 'ad_started'];
const GENERATION_LOCKS = new Map();

function safeError(code, message) {
  const err = new Error(message || code);
  err.code = code;
  return err;
}

function recoverExistingResult(row) {
  return {
    key: licenseService.getRecoverableFullKey(row) || licenseService.FULL_KEY_UNAVAILABLE,
    alreadyDone: false,
    recoveredExisting: true,
    existingKey: row,
  };
}

async function withGenerationLock(lockKey, fn) {
  const key = String(lockKey || 'unknown');
  const previous = GENERATION_LOCKS.get(key) || Promise.resolve();
  let release;
  const current = new Promise((resolve) => { release = resolve; });
  const chained = previous.then(() => current, () => current);
  GENERATION_LOCKS.set(key, chained);
  await previous.catch(() => {});
  try {
    return await fn();
  } finally {
    release();
    if (GENERATION_LOCKS.get(key) === chained) {
      GENERATION_LOCKS.delete(key);
    }
  }
}

function returnSigningSecret() {
  const secret = process.env.AD_RETURN_SIGNING_SECRET || '';
  if (secret.length >= 32) return secret;
  throw safeError('PROVIDER_RETURN_SECRET_MISSING', 'AD_RETURN_SIGNING_SECRET is not configured');
}

function classifyChallengeInsertError(error) {
  const text = `${error?.code || ''} ${error?.message || ''} ${error?.details || ''} ${error?.hint || ''}`.toLowerCase();
  if (
    text.includes('23503') ||
    text.includes('violates foreign key constraint') ||
    text.includes('foreign key') ||
    text.includes('license_ad_challenges_site_user_id_fkey')
  ) {
    return 'DB_FOREIGN_KEY_FAILED';
  }
  if (
    text.includes('schema cache') ||
    text.includes('could not find the table') ||
    text.includes('does not exist') ||
    text.includes('relation') ||
    text.includes('42p01') ||
    text.includes('pgrst204') ||
    text.includes('pgrst205')
  ) {
    return 'CHALLENGE_TABLE_MISSING';
  }
  if (
    text.includes('42501') ||
    text.includes('permission denied') ||
    text.includes('rls') ||
    text.includes('row-level security')
  ) {
    return 'DB_PERMISSION_DENIED';
  }
  return 'CHALLENGE_INSERT_FAILED';
}

function keyExpiresAt() {
  return new Date(Date.now() + KEY_EXPIRY_HOURS * 3600 * 1000).toISOString();
}

function missingColumn(error, columnName) {
  const text = `${error?.message || ''} ${error?.details || ''} ${error?.hint || ''}`.toLowerCase();
  return text.includes(columnName.toLowerCase()) || text.includes('pgrst204') || text.includes('column');
}

function syntheticLicenseOwnerId(siteUserId) {
  return `site:${siteUserId}`;
}

async function ensureSyntheticLicenseUser(siteUserId) {
  const syntheticId = syntheticLicenseOwnerId(siteUserId);
  const { error } = await supabase
    .from('license_users')
    .upsert({
      discord_user_id: syntheticId,
      discord_username: 'DENG Tool Portal User',
      max_keys: 999999,
      is_owner: false,
      is_blocked: false,
    }, { onConflict: 'discord_user_id' });
  if (error) throw new Error(`Portal owner compatibility row failed: ${error.message}`);
  return syntheticId;
}

async function ensureDiscordLicenseUser(discordUserId) {
  if (!discordUserId) return null;
  const { data: existing, error: readError } = await supabase
    .from('license_users')
    .select('discord_user_id')
    .eq('discord_user_id', discordUserId)
    .maybeSingle();

  if (readError) throw new Error(`Discord owner lookup failed: ${readError.message}`);
  if (existing) return discordUserId;

  const { error } = await supabase
    .from('license_users')
    .insert({
      discord_user_id: discordUserId,
      discord_username: 'DENG Tool Portal User',
      max_keys: 999999,
      is_owner: false,
      is_blocked: false,
    });
  if (error && !String(error.message || '').toLowerCase().includes('duplicate')) {
    throw new Error(`Discord owner compatibility row failed: ${error.message}`);
  }
  return discordUserId;
}

async function insertLicenseKey(payload, siteUserId, discordUserId) {
  if (payload.owner_discord_id) {
    await ensureDiscordLicenseUser(payload.owner_discord_id);
  }

  let { error } = await supabase.from('license_keys').insert(payload);
  if (!error) return;

  if (missingColumn(error, 'key_ciphertext') || missingColumn(error, 'key_export_available')) {
    const retryPayload = { ...payload };
    delete retryPayload.key_ciphertext;
    delete retryPayload.key_export_available;
    const retryExport = await supabase.from('license_keys').insert(retryPayload);
    error = retryExport.error;
    if (!error) return;
    payload = retryPayload;
  }

  if (missingColumn(error, 'redeemed_at') || missingColumn(error, 'created_by')) {
    const retryPayload = { ...payload };
    delete retryPayload.redeemed_at;
    delete retryPayload.created_by;
    const retryCompat = await supabase.from('license_keys').insert(retryPayload);
    error = retryCompat.error;
    if (!error) return;
    payload = retryPayload;
  }

  if (!missingColumn(error, 'site_user_id')) {
    throw error;
  }

  const fallback = { ...payload };
  delete fallback.site_user_id;
  if (!fallback.owner_discord_id) {
    fallback.owner_discord_id = await ensureSyntheticLicenseUser(siteUserId);
  } else if (!discordUserId) {
    fallback.owner_discord_id = await ensureSyntheticLicenseUser(siteUserId);
  }

  const retry = await supabase.from('license_keys').insert(fallback);
  if (retry.error) throw retry.error;
}

function hashSession(req) {
  return sha256(req.sessionID || 'unknown');
}

function hashIp(req) {
  const ip = req.ip || req.connection?.remoteAddress || '';
  return sha256(ip);
}

function hashUA(req) {
  return sha256(req.headers['user-agent'] || '');
}

function normalizeJson(value) {
  if (!value) return {};
  if (typeof value === 'object') return value;
  try {
    return JSON.parse(String(value));
  } catch {
    return {};
  }
}

function urlHost(value) {
  if (!value) return '';
  try {
    return new URL(String(value)).hostname.toLowerCase();
  } catch {
    return '';
  }
}

function signReturnTokenNonce(nonce) {
  return crypto
    .createHmac('sha256', returnSigningSecret())
    .update(nonce)
    .digest('hex');
}

function createReturnToken() {
  const nonce = randomHex(32);
  const sig = signReturnTokenNonce(nonce);
  return `${nonce}.${sig}`;
}

function verifyReturnToken(token) {
  if (!token || typeof token !== 'string') return false;
  const dot = token.indexOf('.');
  if (dot < 16 || dot === token.length - 1) return false;
  const nonce = token.slice(0, dot);
  const sig = token.slice(dot + 1);
  if (!/^[a-f0-9]{64}$/i.test(nonce) || !/^[a-f0-9]{64}$/i.test(sig)) return false;

  const expected = Buffer.from(signReturnTokenNonce(nonce), 'hex');
  const actual = Buffer.from(sig, 'hex');
  return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
}

function hostAllowed(host, provider) {
  const normalized = String(host || '').toLowerCase();
  if (!normalized) return false;
  return (ALLOWED_PROVIDER_REFERERS[provider] || []).some((allowed) => (
    normalized === allowed || normalized.endsWith(`.${allowed}`)
  ));
}

function providerReturnHost(req) {
  const refererHost = urlHost(req.headers.referer || req.headers.referrer || '');
  if (refererHost) return { host: refererHost, source: 'referer' };
  const originHost = urlHost(req.headers.origin || '');
  if (originHost) return { host: originHost, source: 'origin' };
  return { host: '', source: 'missing' };
}

function buildProviderPayload(providerUrl, returnToken, previous = {}) {
  const issuedAt = new Date();
  return {
    ...normalizeJson(previous),
    redirect_started: true,
    provider_started_at: issuedAt.toISOString(),
    provider_redirect_host: urlHost(providerUrl),
    ad_min_completion_seconds: AD_MIN_COMPLETION_SECONDS,
    return_token_hash: sha256(returnToken),
    return_token_issued_at: issuedAt.toISOString(),
    return_token_expires_at: new Date(issuedAt.getTime() + RETURN_TOKEN_TTL_MS).toISOString(),
  };
}

function assertProviderReturnProof(req, row, expectedProvider, returnToken) {
  if (!row || row.provider !== expectedProvider) {
    throw safeError('PROVIDER_MISMATCH', 'Provider route does not match active challenge');
  }

  if (!returnToken) {
    throw safeError('PROVIDER_RETURN_TOKEN_MISSING', 'Provider return token missing');
  }
  if (!verifyReturnToken(returnToken)) {
    throw safeError('PROVIDER_RETURN_TOKEN_INVALID', 'Provider return token signature invalid');
  }

  if (
    row.discord_user_id &&
    req.session?.user?.discord_user_id &&
    row.discord_user_id !== req.session.user.discord_user_id
  ) {
    throw safeError('PROVIDER_MISMATCH', 'Discord owner does not match active challenge');
  }

  const payload = normalizeJson(row.provider_payload);
  if (payload.redirect_started !== true || !payload.provider_started_at) {
    throw safeError('PROVIDER_RETURN_UNVERIFIED', 'Provider redirect was not started');
  }
  if (!payload.return_token_hash || payload.return_token_hash !== sha256(returnToken)) {
    throw safeError('PROVIDER_RETURN_TOKEN_INVALID', 'Provider return token does not match active challenge');
  }
  if (!payload.return_token_expires_at || new Date(payload.return_token_expires_at) < new Date()) {
    throw safeError('PROVIDER_RETURN_TOKEN_EXPIRED', 'Provider return token expired');
  }

  const startedMs = new Date(payload.provider_started_at).getTime();
  if (!Number.isFinite(startedMs)) {
    throw safeError('PROVIDER_RETURN_UNVERIFIED', 'Provider redirect timestamp is invalid');
  }

  const elapsedSeconds = Math.floor((Date.now() - startedMs) / 1000);
  if (elapsedSeconds < AD_MIN_COMPLETION_SECONDS) {
    throw safeError(
      'PROVIDER_WAIT_INCOMPLETE',
      `Provider completion returned too quickly (elapsed=${elapsedSeconds}s min=${AD_MIN_COMPLETION_SECONDS}s)`,
    );
  }

  const { host, source } = providerReturnHost(req);
  // Linkvertise's interstitial page does not forward the Referer header to
  // the completion URL. Since all cryptographic checks (HMAC token, hash,
  // expiry, session binding) have passed, accept a missing/empty referer for
  // Linkvertise specifically. An incorrect referer (non-empty, wrong domain)
  // is still rejected for all providers.
  const linkvertiseMissingRefererOk = expectedProvider === 'linkvertise' && !host;
  if (!linkvertiseMissingRefererOk && (!host || !hostAllowed(host, expectedProvider))) {
    throw safeError(
      'PROVIDER_RETURN_UNVERIFIED',
      `Provider return host not verified: source=${source} host=${host || 'missing'}`,
    );
  }

  return {
    elapsedSeconds,
    returnHost: host || 'missing',
  };
}

function rowBelongsToRequest(row, req) {
  return challengeOwnedByUser(row, req);
}

function challengeOwnedByUser(row, req) {
  if (!row || !req?.session?.user) return false;
  const user = req.session.user;
  if (row.site_user_id) {
    if (!user.id || row.site_user_id !== user.id) return false;
    if (row.discord_user_id && user.discord_user_id && row.discord_user_id !== user.discord_user_id) {
      return false;
    }
    return true;
  }
  return Boolean(
    row.discord_user_id &&
    user.discord_user_id &&
    row.discord_user_id === user.discord_user_id,
  );
}

function sessionHashMatches(row, req) {
  return Boolean(row?.session_hash && row.session_hash === hashSession(req));
}

async function loadChallengeById(challengeId) {
  if (!challengeId) return null;
  const { data, error } = await supabase
    .from('license_ad_challenges')
    .select('*')
    .eq('id', challengeId)
    .maybeSingle();
  if (error || !data) return null;
  return data;
}

async function findLatestPendingChallengeForUser(req, provider) {
  const user = req?.session?.user;
  if (!user?.id) return null;
  const { data, error } = await supabase
    .from('license_ad_challenges')
    .select('*')
    .eq('site_user_id', user.id)
    .eq('provider', provider)
    .in('status', COMPLETABLE_STATUSES)
    .order('created_at', { ascending: false })
    .limit(1);
  if (error || !data?.length) return null;
  const row = data[0];
  return challengeOwnedByUser(row, req) ? row : null;
}

async function findLatestResumableChallengeForUser(req, { provider = null } = {}) {
  const user = req?.session?.user;
  if (!user?.id) return null;
  let query = supabase
    .from('license_ad_challenges')
    .select('*')
    .eq('site_user_id', user.id)
    .in('status', RESUMABLE_STATUSES)
    .order('created_at', { ascending: false })
    .limit(10);
  if (provider) query = query.eq('provider', provider);
  const { data, error } = await query;
  if (error || !data?.length) return null;
  for (const row of data) {
    if (new Date(row.expires_at) < new Date()) continue;
    if (challengeOwnedByUser(row, req)) return row;
  }
  return null;
}

async function supersedeOpenAttempts(siteUserId, reason = 'superseded_by_new_attempt') {
  if (!siteUserId) return;
  await supabase
    .from('license_ad_challenges')
    .update({
      status: 'failed',
      failure_reason: reason,
    })
    .eq('site_user_id', siteUserId)
    .in('status', RESUMABLE_STATUSES);
}

async function findOrCreateResumableChallenge(req, siteUser) {
  const existing = await findLatestResumableChallengeForUser(req);
  if (existing) {
    return { row: existing, resumed: true };
  }
  await supersedeOpenAttempts(siteUser.id);
  const row = await createChallenge(req, siteUser);
  return { row, resumed: false };
}

async function resolveChallengeForProvider(req, siteUser, { challengeId = '', provider = '' } = {}) {
  const id = String(
    challengeId || req.session?.pendingChallenge || req.session?.activeAdChallengeId || '',
  ).trim();

  if (id) {
    const row = await loadChallengeById(id);
    if (row && challengeOwnedByUser(row, req) && new Date(row.expires_at) >= new Date()) {
      if (!provider || !row.provider || row.provider === provider || row.status === 'created') {
        return { row, source: 'challenge_id' };
      }
    }
  }

  if (provider) {
    const byProvider = await findLatestResumableChallengeForUser(req, { provider });
    if (byProvider) return { row: byProvider, source: 'latest_resumable_provider' };
  }

  const latest = await findLatestResumableChallengeForUser(req);
  if (latest && (!provider || latest.provider === provider || latest.status === 'created')) {
    return { row: latest, source: 'latest_resumable' };
  }

  return { row: null, source: 'none' };
}

function mapErrorToFailureReason(err) {
  const code = err?.code || '';
  const map = {
    AUTH_REQUIRED: 'missing_user',
    PROVIDER_CHALLENGE_MISSING: 'missing_attempt_id',
    PROVIDER_CHALLENGE_EXPIRED: 'expired_attempt',
    CHALLENGE_ALREADY_USED: 'already_consumed',
    PROVIDER_CHALLENGE_ALREADY_USED: 'already_consumed',
    PROVIDER_RETURN_TOKEN_MISSING: 'provider_callback_missing',
    PROVIDER_RETURN_TOKEN_INVALID: 'provider_callback_missing',
    PROVIDER_CHALLENGE_OWNER_MISMATCH: 'session_mismatch',
    PROVIDER_MISMATCH: 'missing_state',
    KEY_LIMIT_REACHED: 'quota_blocked',
    COOLDOWN_ACTIVE: 'quota_blocked',
    KEY_GENERATION_FAILED: 'server_error',
  };
  return map[code] || 'server_error';
}

async function findChallengeByLinkvertiseHash(hash) {
  const needle = String(hash || '').trim();
  if (!needle) return null;
  const { data, error } = await supabase
    .from('license_ad_challenges')
    .select('*')
    .eq('provider', 'linkvertise')
    .order('created_at', { ascending: false })
    .limit(25);
  if (error || !data?.length) return null;
  return data.find((row) => {
    const payload = normalizeJson(row.provider_payload);
    return payload.linkvertise_hash === needle;
  }) || null;
}

async function bindLinkvertiseHash(challengeId, hash) {
  const row = await loadChallengeById(challengeId);
  if (!row) return;
  const payload = normalizeJson(row.provider_payload);
  if (payload.linkvertise_hash === hash) return;
  await supabase
    .from('license_ad_challenges')
    .update({
      provider_payload: {
        ...payload,
        linkvertise_hash: String(hash || '').trim(),
        linkvertise_hash_bound_at: new Date().toISOString(),
      },
    })
    .eq('id', challengeId);
}

function assertChallengeReadyForCompletion(row, req, provider, { allowConsumedRecovery = false } = {}) {
  if (!row) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'Provider challenge missing');
  }
  if (!challengeOwnedByUser(row, req)) {
    throw safeError('PROVIDER_CHALLENGE_OWNER_MISMATCH', 'Provider challenge owner mismatch');
  }
  if (row.provider !== provider) {
    throw safeError('PROVIDER_MISMATCH', 'Provider challenge provider mismatch');
  }
  if (new Date(row.expires_at) < new Date()) {
    throw safeError('PROVIDER_CHALLENGE_EXPIRED', 'Provider challenge expired');
  }
  if (CONSUMED_STATUSES.includes(row.status)) {
    if (allowConsumedRecovery && row.status === 'key_generated' && row.license_key_id) {
      return;
    }
    throw safeError('CHALLENGE_ALREADY_USED', 'Provider challenge already used');
  }
  if (!COMPLETABLE_STATUSES.includes(row.status)) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'Provider challenge is not pending');
  }
  const payload = normalizeJson(row.provider_payload);
  if (provider === 'linkvertise' && payload.linkvertise_started !== true) {
    throw safeError('PROVIDER_RETURN_UNVERIFIED', 'Linkvertise challenge not started');
  }
  if (provider === 'lootlabs' && payload.lootlabs_started !== true) {
    throw safeError('PROVIDER_RETURN_UNVERIFIED', 'LootLabs challenge not started');
  }
}

async function recoverGeneratedKeyFromChallenge(row) {
  if (!row) return null;
  if (row.status === 'key_generated' && row.license_key_id) {
    const { data } = await supabase
      .from('license_keys')
      .select('id, prefix, suffix, key_ciphertext, key_export_available')
      .eq('id', row.license_key_id)
      .maybeSingle();
    const decrypted = data?.key_ciphertext ? decryptLicenseKeyCiphertext(data.key_ciphertext) : '';
    if (decrypted) return decrypted;
  }
  if (row.key_prefix && row.key_suffix) {
    return `${row.key_prefix}-${row.key_suffix}`;
  }
  return null;
}

async function resolveProviderChallenge(req, provider, { challengeId = null, linkvertiseHash = '' } = {}) {
  if (!req?.session?.user) {
    throw safeError('AUTH_REQUIRED', 'Login required');
  }

  if (provider === 'linkvertise' && linkvertiseHash) {
    const byHash = await findChallengeByLinkvertiseHash(linkvertiseHash);
    if (byHash) {
      assertChallengeReadyForCompletion(byHash, req, provider, { allowConsumedRecovery: true });
      return byHash;
    }
  }

  const sessionIds = [
    challengeId,
    req.session?.activeAdChallengeId,
    req.session?.pendingChallenge,
  ].filter(Boolean);

  for (const id of sessionIds) {
    const row = await loadChallengeById(id);
    if (row && row.provider === provider && challengeOwnedByUser(row, req)) {
      try {
        assertChallengeReadyForCompletion(row, req, provider);
        return row;
      } catch (err) {
        if (err?.code === 'CHALLENGE_ALREADY_USED' && row.status === 'key_generated') {
          return row;
        }
        if (err?.code !== 'PROVIDER_CHALLENGE_MISSING') throw err;
      }
    }
  }

  const recovered = await findLatestPendingChallengeForUser(req, provider);
  if (recovered) {
    assertChallengeReadyForCompletion(recovered, req, provider);
    if (!sessionHashMatches(recovered, req) && process.env.NODE_ENV !== 'test') {
      console.log(
        '[challenge/recover] provider=%s challenge=%s recovered pending attempt without session binding (mobile-safe)',
        provider,
        String(recovered.id).slice(0, 8),
      );
    }
    return recovered;
  }

  throw safeError('PROVIDER_CHALLENGE_MISSING', 'No recoverable provider challenge');
}

async function getGenerationAttemptDiagnostic({ discordUserId = '', siteUserId = '', challengeId = null } = {}) {
  let row = null;
  if (challengeId) {
    row = await loadChallengeById(challengeId);
  } else if (siteUserId) {
    const { data } = await supabase
      .from('license_ad_challenges')
      .select('*')
      .eq('site_user_id', siteUserId)
      .order('created_at', { ascending: false })
      .limit(1);
    row = data?.[0] || null;
  }
  const payload = normalizeJson(row?.provider_payload);
  return {
    latestAttemptId: row?.id || null,
    provider: row?.provider || null,
    attemptStatus: row?.status || 'none',
    attemptCreatedAt: row?.created_at || null,
    attemptExpiresAt: row?.expires_at || null,
    providerVerifyStatus: payload.linkvertise_hash ? 'hash_bound' : (payload.lootlabs_started ? 'lootlabs_started' : null),
    providerVerifyReason: row?.failure_reason || null,
    consumedKeyId: row?.license_key_id ? String(row.license_key_id).slice(0, 8) + '…' : null,
    linkvertiseHashBound: Boolean(payload.linkvertise_hash),
    sessionHashStored: Boolean(row?.session_hash),
  };
}

async function checkCooldown(siteUserId) {
  const { data, error } = await supabase
    .from('license_ad_challenges')
    .select('completed_at')
    .eq('site_user_id', siteUserId)
    .in('status', ['ad_completed', 'key_generated'])
    .order('completed_at', { ascending: false })
    .limit(5);

  if (error || !data?.length) {
    return { allowed: true, secondsLeft: 0, cooldownUntil: null };
  }

  for (const row of data) {
    const lastMs = Date.parse(row.completed_at);
    if (!Number.isFinite(lastMs)) continue;

    const cooldownUntilMs = lastMs + COOLDOWN_SECONDS * 1000;
    const secondsLeft = Math.ceil((cooldownUntilMs - Date.now()) / 1000);
    if (secondsLeft <= 0) continue;

    return {
      allowed: false,
      secondsLeft,
      cooldownUntil: new Date(cooldownUntilMs).toISOString(),
    };
  }

  return { allowed: true, secondsLeft: 0, cooldownUntil: null };
}

async function getLatestProviderAttemptStatus(siteUserId) {
  const { data, error } = await supabase
    .from('license_ad_challenges')
    .select('id, status, expires_at, created_at, completed_at')
    .eq('site_user_id', siteUserId)
    .order('created_at', { ascending: false })
    .limit(10);

  if (error || !data?.length) {
    return { status: 'none', blocking: false, blockReason: null, challengeId: null };
  }

  const now = Date.now();
  for (const row of data) {
    if (row.status === 'failed' || row.status === 'key_generated') continue;

    const expMs = Date.parse(row.expires_at || '');
    const expired = Number.isFinite(expMs) && expMs <= now;
    if (expired) continue;

    if (row.status === 'ad_completed') {
      return {
        status: 'ad_completed',
        blocking: false,
        blockReason: null,
        challengeId: row.id,
      };
    }

    if (['created', 'provider_selected', 'pending_ad', 'ad_started'].includes(row.status)) {
      return {
        status: row.status,
        blocking: false,
        blockReason: null,
        challengeId: row.id,
      };
    }
  }

  return { status: 'none', blocking: false, blockReason: null, challengeId: null };
}

async function createChallenge(req, siteUser) {
  const row = {
    site_user_id: siteUser.id,
    discord_user_id: siteUser.discord_user_id || null,
    status: 'created',
    session_hash: hashSession(req),
    ip_hash: hashIp(req),
    user_agent_hash: hashUA(req),
    state_hash: sha256(randomHex(32)),
    expires_at: new Date(Date.now() + CHALLENGE_TTL_MS).toISOString(),
  };

  const { data, error } = await supabase
    .from('license_ad_challenges')
    .insert(row)
    .select()
    .single();

  if (error) {
    throw safeError(classifyChallengeInsertError(error), `Failed to create challenge: ${error.message}`);
  }
  return data;
}

async function selectProvider(challengeId, provider, req, siteUser) {
  if (!['lootlabs', 'linkvertise'].includes(provider)) {
    throw new Error('Invalid provider');
  }

  const signed = signChallenge(challengeId, provider, Date.now() + CHALLENGE_TTL_MS);
  const signedHash = sha256(signed);

  const { data, error } = await supabase
    .from('license_ad_challenges')
    .update({
      status: 'provider_selected',
      provider,
      signed_challenge_hash: signedHash,
      session_hash: hashSession(req),
    })
    .eq('id', challengeId)
    .eq('site_user_id', siteUser.id)
    .eq('status', 'created')
    .select()
    .single();

  if (error || !data) {
    const { data: existing } = await supabase
      .from('license_ad_challenges')
      .select('*')
      .eq('id', challengeId)
      .eq('site_user_id', siteUser.id)
      .maybeSingle();

    if (
      existing &&
      existing.provider === provider &&
      COMPLETABLE_STATUSES.includes(existing.status) &&
      new Date(existing.expires_at) >= new Date()
    ) {
      return { ...existing, signed_challenge: signed };
    }
    if (existing && existing.provider && existing.provider !== provider) {
      throw safeError('PROVIDER_MISMATCH', 'Challenge is already locked to another provider');
    }
    if (existing && CONSUMED_STATUSES.includes(existing.status)) {
      throw safeError('CHALLENGE_ALREADY_USED', 'Challenge already used');
    }
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'Challenge no longer available.');
  }
  return { ...data, signed_challenge: signed };
}

async function markPendingAd(signedToken) {
  const sigHash = sha256(signedToken);
  const { data, error } = await supabase
    .from('license_ad_challenges')
    .update({ status: 'pending_ad' })
    .eq('signed_challenge_hash', sigHash)
    .eq('status', 'provider_selected')
    .select()
    .single();

  if (error || !data) throw new Error('Challenge not found or already advanced');
  return data;
}

async function markPendingAdById(challengeId, req, siteUser, providerUrl = '') {
  const returnToken = createReturnToken();
  const { data, error } = await supabase
    .from('license_ad_challenges')
    .update({
      status: 'pending_ad',
      provider_payload: buildProviderPayload(providerUrl, returnToken),
    })
    .eq('id', challengeId)
    .eq('site_user_id', siteUser.id)
    .in('status', ['provider_selected', 'pending_ad'])
    .select()
    .single();

  if (error || !data) throw safeError('PROVIDER_CHALLENGE_MISSING', 'Challenge not found or already advanced');
  return { ...data, return_token: returnToken };
}

async function refreshChallengeSession(challengeId, req) {
  await supabase
    .from('license_ad_challenges')
    .update({ session_hash: hashSession(req) })
    .eq('id', challengeId);
}

/**
 * Mark a challenge as pending Linkvertise verification.
 *
 * Unlike `markPendingAdById`, this DOES NOT issue a signed return token —
 * Linkvertise returns control with `?hash=<linkvertise_hash>` instead. The
 * payload only records that the provider was started server-side, plus the
 * target link host and callback URL for safe audit logging.
 */
async function markLinkvertisePendingById(challengeId, req, siteUser, { targetLinkUrl = '', callbackUrl = '' } = {}) {
  const issuedAt = new Date();
  const payload = {
    linkvertise_started: true,
    target_link_host: urlHost(targetLinkUrl) || 'link-hub.net',
    callback_url: callbackUrl || '',
    provider_started_at: issuedAt.toISOString(),
    redirect_started: true,
    provider_redirect_host: urlHost(targetLinkUrl),
  };

  const { data, error } = await supabase
    .from('license_ad_challenges')
    .update({
      status: 'pending_ad',
      provider_payload: payload,
      session_hash: hashSession(req),
    })
    .eq('id', challengeId)
    .eq('site_user_id', siteUser.id)
    .in('status', ['provider_selected', 'pending_ad'])
    .select()
    .single();

  if (error || !data) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'Challenge not found or already advanced');
  }
  return data;
}

/**
 * Mark a challenge as pending LootLabs verification.
 *
 * Unlike `markPendingAdById`, this DOES NOT issue a signed return token —
 * LootLabs returns control with `?s=<signed_state>` where the signed state
 * was created and embedded in the destination URL that LootLabs encrypted.
 *
 * The payload only records safe audit metadata (base link host, callback
 * path, started_at). The signed state is NEVER stored anywhere — it lives
 * only in the encrypted blob returned by LootLabs.
 */
async function markLootLabsPendingById(challengeId, req, siteUser, { baseLink = '', callbackPath = '' } = {}) {
  const issuedAt = new Date();
  const payload = {
    lootlabs_started: true,
    base_link_host: urlHost(baseLink) || 'lootdest.org',
    callback_path: callbackPath || '/unlock/lootlabs/complete',
    encrypted_data_present: true,
    provider_started_at: issuedAt.toISOString(),
    redirect_started: true,
    provider_redirect_host: urlHost(baseLink) || 'lootdest.org',
  };

  const { data, error } = await supabase
    .from('license_ad_challenges')
    .update({
      status: 'pending_ad',
      provider_payload: payload,
      session_hash: hashSession(req),
    })
    .eq('id', challengeId)
    .eq('site_user_id', siteUser.id)
    .in('status', ['provider_selected', 'pending_ad'])
    .select()
    .single();

  if (error || !data) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'Challenge not found or already advanced');
  }
  return data;
}

async function getActiveSessionChallenge(req, expectedProvider) {
  return resolveProviderChallenge(req, expectedProvider);
}

async function completeActiveProviderChallenge(req, expectedProvider, returnToken) {
  const row = await getActiveSessionChallenge(req, expectedProvider);
  assertProviderReturnProof(req, row, expectedProvider, returnToken);
  return completeAdAndGenerateKey(row);
}

/**
 * Load the active Linkvertise challenge for this request based on
 * `req.session.activeAdChallengeId` and validate every ownership rule that
 * applies before Linkvertise hash verification is attempted.
 *
 * Throws codeful safeError on any mismatch (PROVIDER_CHALLENGE_*, etc).
 */
async function getActiveLinkvertiseChallenge(req, linkvertiseHash = '') {
  return resolveProviderChallenge(req, 'linkvertise', { linkvertiseHash });
}

/**
 * Load the active LootLabs challenge based on a verified signed-state
 * `cid` (challenge id) and validate every ownership rule before key
 * generation is attempted. The signed state itself MUST have already been
 * verified with `verifyChallenge()` by the caller; this function only
 * resolves the matching DB row and checks ownership / provider / status /
 * expiry / no-key.
 *
 * Throws codeful safeError on any mismatch (PROVIDER_CHALLENGE_*).
 */
async function getActiveLootLabsChallengeById(challengeId, req) {
  return resolveProviderChallenge(req, 'lootlabs', { challengeId });
}

async function getChallengeByToken(signedToken) {
  const sigHash = sha256(signedToken);
  const { data, error } = await supabase
    .from('license_ad_challenges')
    .select('*')
    .eq('signed_challenge_hash', sigHash)
    .single();

  if (error || !data) return null;
  if (new Date(data.expires_at) < new Date()) return null;
  return data;
}

async function verifyChallengeForRequest(signedToken, req, expectedProvider) {
  const decoded = verifyChallenge(signedToken);
  if (!decoded || decoded.p !== expectedProvider) return null;

  const row = await getChallengeByToken(signedToken);
  if (!row) return null;
  if (row.id !== decoded.cid) return null;
  if (row.provider !== expectedProvider) return null;
  if (!rowBelongsToRequest(row, req)) return null;
  if (!['provider_selected', 'pending_ad'].includes(row.status)) return null;
  return row;
}

async function completeAdAndGenerateKey(challengeRow) {
  const { id: challengeId, site_user_id, discord_user_id } = challengeRow;
  const lockKey = discord_user_id || site_user_id;

  return withGenerationLock(lockKey, async () => {
    const existingUnused = await licenseService.findActiveUnredeemedKey({ discordUserId: discord_user_id, siteUserId: site_user_id });
    if (existingUnused) {
      return recoverExistingResult(existingUnused);
    }

    const cooldown = await checkCooldown(site_user_id);
    if (!cooldown.allowed) {
      throw safeError('COOLDOWN_ACTIVE', `Cooldown active. Try again in ${cooldown.secondsLeft}s.`);
    }

    const { data: adDone, error: adErr } = await supabase
      .from('license_ad_challenges')
      .update({ status: 'ad_completed' })
      .eq('id', challengeId)
      .eq('status', 'pending_ad')
      .select()
      .single();

    if (adErr || !adDone) {
      const { data: existing } = await supabase
        .from('license_ad_challenges')
        .select('status')
        .eq('id', challengeId)
        .single();
      if (existing && ['ad_completed', 'key_generated'].includes(existing.status)) {
        const recovered = await recoverGeneratedKeyFromChallenge({ ...challengeRow, ...existing });
        return { key: recovered, alreadyDone: true, challengeRow: { ...challengeRow, ...existing } };
      }
      throw safeError('PROVIDER_CHALLENGE_ALREADY_USED', 'Challenge state conflict');
    }

    const existingAfterConsume = await licenseService.findActiveUnredeemedKey({ discordUserId: discord_user_id, siteUserId: site_user_id });
    if (existingAfterConsume) {
      return recoverExistingResult(existingAfterConsume);
    }

    // Check max active key limit before inserting key
    const limitCheck = await licenseService.canUserReceiveNewKey(discord_user_id, site_user_id);
    if (!limitCheck.allowed) {
      await supabase
        .from('license_ad_challenges')
        .update({ status: 'failed', failure_reason: 'key_limit_reached' })
        .eq('id', challengeId);
      throw safeError('KEY_LIMIT_REACHED', licenseService.KEY_SLOT_LIMIT_MESSAGE);
    }

    const { raw, id: keyId, prefix, suffix, displayPrefix, displaySuffix } = generateDengKey();
    const now = new Date().toISOString();
    const expiresAt = keyExpiresAt();
    const keyCiphertext = encryptLicenseKeyPlaintext(raw);

    const recheck = await licenseService.canUserReceiveNewKey(discord_user_id, site_user_id);
    if (!recheck.allowed) {
      await supabase
        .from('license_ad_challenges')
        .update({ status: 'failed', failure_reason: 'key_limit_reached' })
        .eq('id', challengeId);
      throw safeError('KEY_LIMIT_REACHED', licenseService.KEY_SLOT_LIMIT_MESSAGE);
    }

    try {
      await insertLicenseKey({
        id: keyId,
        prefix,
        suffix,
        owner_discord_id: discord_user_id || null,
        site_user_id: site_user_id || null,
        status: 'active',
        plan: 'standard',
        expires_at: expiresAt,
        redeemed_at: null,
        created_by: discord_user_id || null,
        ...(keyCiphertext ? { key_ciphertext: keyCiphertext, key_export_available: true } : { key_export_available: false }),
      }, site_user_id, discord_user_id);
    } catch (keyErr) {
      const duplicate = await licenseService.findActiveUnredeemedKey({ discordUserId: discord_user_id, siteUserId: site_user_id });
      if (duplicate) {
        return recoverExistingResult(duplicate);
      }
      await supabase
        .from('license_ad_challenges')
        .update({ status: 'failed', failure_reason: keyErr.message })
        .eq('id', challengeId);
      throw safeError('KEY_GENERATION_FAILED', `Key store failed: ${keyErr.message}`);
    }

    const { data: finalRow, error: finalErr } = await supabase
      .from('license_ad_challenges')
      .update({
        status: 'key_generated',
        license_key_id: keyId,
        key_prefix: displayPrefix,
        key_suffix: displaySuffix,
        key_expires_at: expiresAt,
        completed_at: now,
      })
      .eq('id', challengeId)
      .eq('status', 'ad_completed')
      .select()
      .single();

    if (finalErr || !finalRow) {
      console.error('[challenge] key stored but challenge row not finalized');
    }

    return { key: raw, alreadyDone: false };
  });
}

module.exports = {
  COOLDOWN_SECONDS,
  AD_MIN_COMPLETION_SECONDS,
  KEY_EXPIRY_HOURS,
  RETURN_TOKEN_TTL_MS,
  assertProviderReturnProof,
  createReturnToken,
  verifyReturnToken,
  checkCooldown,
  getLatestProviderAttemptStatus,
  createChallenge,
  selectProvider,
  markPendingAd,
  getChallengeByToken,
  verifyChallengeForRequest,
  completeAdAndGenerateKey,
  completeActiveProviderChallenge,
  markPendingAdById,
  markLinkvertisePendingById,
  markLootLabsPendingById,
  getActiveLinkvertiseChallenge,
  getActiveLootLabsChallengeById,
  resolveProviderChallenge,
  recoverGeneratedKeyFromChallenge,
  bindLinkvertiseHash,
  getGenerationAttemptDiagnostic,
  challengeOwnedByUser,
  loadChallengeById,
  findLatestResumableChallengeForUser,
  findOrCreateResumableChallenge,
  resolveChallengeForProvider,
  supersedeOpenAttempts,
  mapErrorToFailureReason,
  RESUMABLE_STATUSES,
  hashSession,
  classifyChallengeInsertError,
};
