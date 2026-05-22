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

const COOLDOWN_SECONDS = parseInt(process.env.KEY_GENERATION_COOLDOWN_SECONDS || '60', 10);
const CHALLENGE_TTL_MS = 30 * 60 * 1000;
const KEY_EXPIRY_HOURS = parseInt(process.env.UNREDEEMED_KEY_EXPIRY_HOURS || '24', 10);
const AD_MIN_COMPLETION_SECONDS = parseInt(process.env.AD_MIN_COMPLETION_SECONDS || '30', 10);

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

function buildProviderPayload(providerUrl, previous = {}) {
  return {
    ...normalizeJson(previous),
    redirect_started: true,
    provider_started_at: new Date().toISOString(),
    provider_redirect_host: urlHost(providerUrl),
    ad_min_completion_seconds: AD_MIN_COMPLETION_SECONDS,
  };
}

function assertProviderReturnProof(req, row, expectedProvider) {
  if (!row || row.provider !== expectedProvider) {
    throw safeError('PROVIDER_MISMATCH', 'Provider route does not match active challenge');
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

  const startedMs = new Date(payload.provider_started_at).getTime();
  if (!Number.isFinite(startedMs)) {
    throw safeError('PROVIDER_RETURN_UNVERIFIED', 'Provider redirect timestamp is invalid');
  }

  const elapsedSeconds = Math.floor((Date.now() - startedMs) / 1000);
  if (elapsedSeconds < AD_MIN_COMPLETION_SECONDS) {
    throw safeError('PROVIDER_WAIT_INCOMPLETE', 'Provider completion returned too quickly');
  }

  const { host, source } = providerReturnHost(req);
  if (!host || !hostAllowed(host, expectedProvider)) {
    throw safeError(
      'PROVIDER_RETURN_UNVERIFIED',
      `Provider return host not verified: source=${source} host=${host || 'missing'}`,
    );
  }

  return {
    elapsedSeconds,
    returnHost: host,
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
    return { allowed: false, secondsLeft: Math.max(0, secondsLeft) };
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
  const { data, error } = await supabase
    .from('license_ad_challenges')
    .update({
      status: 'pending_ad',
      provider_payload: buildProviderPayload(providerUrl),
    })
    .eq('id', challengeId)
    .eq('site_user_id', siteUser.id)
    .eq('session_hash', hashSession(req))
    .eq('status', 'provider_selected')
    .select()
    .single();

  if (error || !data) throw safeError('PROVIDER_CHALLENGE_MISSING', 'Challenge not found or already advanced');
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

async function completeActiveProviderChallenge(req, expectedProvider) {
  const row = await getActiveSessionChallenge(req, expectedProvider);
  assertProviderReturnProof(req, row, expectedProvider);
  return completeAdAndGenerateKey(row);
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
  assertProviderReturnProof,
  checkCooldown,
  createChallenge,
  selectProvider,
  markPendingAd,
  getChallengeByToken,
  verifyChallengeForRequest,
  completeAdAndGenerateKey,
  completeActiveProviderChallenge,
  markPendingAdById,
  hashSession,
  classifyChallengeInsertError,
};
