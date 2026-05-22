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

function safeError(code, message) {
  const err = new Error(message || code);
  err.code = code;
  return err;
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

  const { error } = await supabase.from('license_keys').insert(payload);
  if (!error) return;

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
  return Boolean(
    row &&
    req.session &&
    req.session.user &&
    row.site_user_id === req.session.user.id &&
    row.session_hash === hashSession(req),
  );
}

async function checkCooldown(siteUserId) {
  const since = new Date(Date.now() - COOLDOWN_SECONDS * 1000).toISOString();
  const { data } = await supabase
    .from('license_ad_challenges')
    .select('created_at, completed_at')
    .eq('site_user_id', siteUserId)
    .in('status', ['ad_completed', 'key_generated'])
    .gte('created_at', since)
    .order('created_at', { ascending: false })
    .limit(1);

  if (data && data.length > 0) {
    const lastMs = new Date(data[0].completed_at || data[0].created_at).getTime();
    const secondsLeft = Math.ceil((lastMs + COOLDOWN_SECONDS * 1000 - Date.now()) / 1000);
    // If the cooldown window has already expired (secondsLeft <= 0), treat as allowed.
    if (secondsLeft <= 0) return { allowed: true, secondsLeft: 0 };
    return { allowed: false, secondsLeft };
  }
  return { allowed: true, secondsLeft: 0 };
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
    .eq('session_hash', hashSession(req))
    .eq('status', 'created')
    .select()
    .single();

  if (error || !data) {
    const { data: existing } = await supabase
      .from('license_ad_challenges')
      .select('*')
      .eq('id', challengeId)
      .eq('site_user_id', siteUser.id)
      .eq('session_hash', hashSession(req))
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
    .eq('session_hash', hashSession(req))
    .in('status', ['provider_selected', 'pending_ad'])
    .select()
    .single();

  if (error || !data) throw safeError('PROVIDER_CHALLENGE_MISSING', 'Challenge not found or already advanced');
  return { ...data, return_token: returnToken };
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
    })
    .eq('id', challengeId)
    .eq('site_user_id', siteUser.id)
    .eq('session_hash', hashSession(req))
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
    })
    .eq('id', challengeId)
    .eq('site_user_id', siteUser.id)
    .eq('session_hash', hashSession(req))
    .in('status', ['provider_selected', 'pending_ad'])
    .select()
    .single();

  if (error || !data) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'Challenge not found or already advanced');
  }
  return data;
}

async function getActiveSessionChallenge(req, expectedProvider) {
  const challengeId = req.session?.pendingChallenge;
  if (!challengeId || !req.session?.user) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'No active provider challenge in session');
  }

  const { data: owned, error } = await supabase
    .from('license_ad_challenges')
    .select('*')
    .eq('id', challengeId)
    .maybeSingle();

  if (error || !owned) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'Provider challenge missing');
  }
  if (owned.site_user_id !== req.session.user.id || owned.session_hash !== hashSession(req)) {
    throw safeError('PROVIDER_CHALLENGE_OWNER_MISMATCH', 'Provider challenge owner mismatch');
  }
  if (
    owned.discord_user_id &&
    req.session.user.discord_user_id &&
    owned.discord_user_id !== req.session.user.discord_user_id
  ) {
    throw safeError('PROVIDER_CHALLENGE_OWNER_MISMATCH', 'Provider challenge Discord owner mismatch');
  }
  if (owned.provider !== expectedProvider) {
    throw safeError('PROVIDER_MISMATCH', 'Provider challenge mismatch');
  }
  if (new Date(owned.expires_at) < new Date()) {
    throw safeError('PROVIDER_CHALLENGE_EXPIRED', 'Provider challenge expired');
  }
  if (CONSUMED_STATUSES.includes(owned.status)) {
    throw safeError('CHALLENGE_ALREADY_USED', 'Provider challenge already used');
  }
  if (!COMPLETABLE_STATUSES.includes(owned.status)) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'Provider challenge is not ready');
  }
  return owned;
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
async function getActiveLinkvertiseChallenge(req) {
  const challengeId = req.session?.activeAdChallengeId || req.session?.pendingChallenge;
  if (!challengeId || !req.session?.user) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'No active Linkvertise challenge in session');
  }

  const { data: owned, error } = await supabase
    .from('license_ad_challenges')
    .select('*')
    .eq('id', challengeId)
    .maybeSingle();

  if (error || !owned) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'Linkvertise challenge missing');
  }
  if (owned.site_user_id !== req.session.user.id || owned.session_hash !== hashSession(req)) {
    throw safeError('PROVIDER_CHALLENGE_OWNER_MISMATCH', 'Linkvertise challenge owner mismatch');
  }
  if (
    owned.discord_user_id &&
    req.session.user.discord_user_id &&
    owned.discord_user_id !== req.session.user.discord_user_id
  ) {
    throw safeError('PROVIDER_CHALLENGE_OWNER_MISMATCH', 'Linkvertise challenge Discord owner mismatch');
  }
  if (owned.provider !== 'linkvertise') {
    throw safeError('PROVIDER_MISMATCH', 'Linkvertise challenge provider mismatch');
  }
  if (owned.license_key_id) {
    throw safeError('CHALLENGE_ALREADY_USED', 'Linkvertise challenge already produced a key');
  }
  if (new Date(owned.expires_at) < new Date()) {
    throw safeError('PROVIDER_CHALLENGE_EXPIRED', 'Linkvertise challenge expired');
  }
  if (CONSUMED_STATUSES.includes(owned.status)) {
    throw safeError('CHALLENGE_ALREADY_USED', 'Linkvertise challenge already used');
  }
  if (!COMPLETABLE_STATUSES.includes(owned.status)) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'Linkvertise challenge is not pending');
  }

  const payload = normalizeJson(owned.provider_payload);
  if (payload.linkvertise_started !== true) {
    throw safeError('PROVIDER_RETURN_UNVERIFIED', 'Linkvertise challenge not started');
  }
  return owned;
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
  if (!challengeId || !req.session?.user) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'No active LootLabs challenge in session');
  }

  const { data: owned, error } = await supabase
    .from('license_ad_challenges')
    .select('*')
    .eq('id', challengeId)
    .maybeSingle();

  if (error || !owned) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'LootLabs challenge missing');
  }
  if (owned.site_user_id !== req.session.user.id || owned.session_hash !== hashSession(req)) {
    throw safeError('PROVIDER_CHALLENGE_OWNER_MISMATCH', 'LootLabs challenge owner mismatch');
  }
  if (
    owned.discord_user_id &&
    req.session.user.discord_user_id &&
    owned.discord_user_id !== req.session.user.discord_user_id
  ) {
    throw safeError('PROVIDER_CHALLENGE_OWNER_MISMATCH', 'LootLabs challenge Discord owner mismatch');
  }
  if (owned.provider !== 'lootlabs') {
    throw safeError('PROVIDER_MISMATCH', 'LootLabs challenge provider mismatch');
  }
  if (owned.license_key_id) {
    throw safeError('CHALLENGE_ALREADY_USED', 'LootLabs challenge already produced a key');
  }
  if (new Date(owned.expires_at) < new Date()) {
    throw safeError('PROVIDER_CHALLENGE_EXPIRED', 'LootLabs challenge expired');
  }
  if (CONSUMED_STATUSES.includes(owned.status)) {
    throw safeError('CHALLENGE_ALREADY_USED', 'LootLabs challenge already used');
  }
  if (!COMPLETABLE_STATUSES.includes(owned.status)) {
    throw safeError('PROVIDER_CHALLENGE_MISSING', 'LootLabs challenge is not pending');
  }

  const payload = normalizeJson(owned.provider_payload);
  if (payload.lootlabs_started !== true) {
    throw safeError('PROVIDER_RETURN_UNVERIFIED', 'LootLabs challenge not started');
  }
  return owned;
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
      return { key: null, alreadyDone: true };
    }
    throw safeError('PROVIDER_CHALLENGE_ALREADY_USED', 'Challenge state conflict');
  }

  const { raw, id: keyId, prefix, suffix } = generateDengKey();
  const now = new Date().toISOString();
  const expiresAt = keyExpiresAt();

  try {
    await insertLicenseKey({
      id: keyId,
      prefix,
      suffix,
      owner_discord_id: discord_user_id || null,
      site_user_id: site_user_id || null,
      status: 'active',
      plan: 'free',
      expires_at: expiresAt,
    }, site_user_id, discord_user_id);
  } catch (keyErr) {
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
      key_prefix: prefix,
      key_suffix: suffix,
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
  hashSession,
  classifyChallengeInsertError,
};
