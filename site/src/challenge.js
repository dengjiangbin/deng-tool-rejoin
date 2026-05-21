'use strict';
/**
 * Challenge lifecycle management.
 * Each attempt creates one row in license_ad_challenges.
 * Status machine: created → provider_selected → pending_ad → ad_completed → key_generated
 */
const supabase       = require('./db');
const { signChallenge, sha256, randomHex } = require('./crypto');
const { generateDengKey } = require('./keyGen');

const COOLDOWN_SECONDS   = parseInt(process.env.KEY_GENERATION_COOLDOWN_SECONDS || '60', 10);
const CHALLENGE_TTL_MS   = 30 * 60 * 1000;               // 30 minute window to complete flow
const KEY_EXPIRY_HOURS   = parseInt(process.env.UNREDEEMED_KEY_EXPIRY_HOURS || '24', 10);

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

/** Return ISO string KEY_EXPIRY_HOURS from now */
function keyExpiresAt() {
  return new Date(Date.now() + KEY_EXPIRY_HOURS * 3600 * 1000).toISOString();
}

/** Hash the express-session cookie value for fingerprinting */
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

// ---------------------------------------------------------------
// Public API
// ---------------------------------------------------------------

/**
 * Enforce cooldown: reject if user generated a key within COOLDOWN_SECONDS.
 * Returns { allowed: boolean, secondsLeft: number }
 */
async function checkCooldown(siteUserId) {
  const since = new Date(Date.now() - COOLDOWN_SECONDS * 1000).toISOString();
  const { data } = await supabase
    .from('license_ad_challenges')
    .select('created_at')
    .eq('site_user_id', siteUserId)
    .in('status', ['ad_completed', 'key_generated'])
    .gte('created_at', since)
    .order('created_at', { ascending: false })
    .limit(1);

  if (data && data.length > 0) {
    const lastMs   = new Date(data[0].created_at).getTime();
    const secondsLeft = Math.ceil((lastMs + COOLDOWN_SECONDS * 1000 - Date.now()) / 1000);
    return { allowed: false, secondsLeft: Math.max(0, secondsLeft) };
  }
  return { allowed: true, secondsLeft: 0 };
}

/**
 * Create a fresh challenge row (status='created').
 * Returns the full DB row.
 */
async function createChallenge(req, siteUser) {
  const stateHash = sha256(randomHex(32));
  const expiresAt = new Date(Date.now() + CHALLENGE_TTL_MS).toISOString();

  const row = {
    site_user_id:    siteUser.id,
    discord_user_id: siteUser.discord_user_id || null,
    status:          'created',
    session_hash:    hashSession(req),
    ip_hash:         hashIp(req),
    user_agent_hash: hashUA(req),
    state_hash:      stateHash,
    expires_at:      expiresAt,
  };

  const { data, error } = await supabase
    .from('license_ad_challenges')
    .insert(row)
    .select()
    .single();

  if (error) throw new Error(`Failed to create challenge: ${error.message}`);
  return data;
}

/**
 * Select a provider for an existing challenge (status: created → provider_selected).
 * Returns updated row or throws.
 */
async function selectProvider(challengeId, provider, req) {
  if (!['lootlabs', 'linkvertise'].includes(provider)) {
    throw new Error('Invalid provider');
  }

  const expMs = Date.now() + CHALLENGE_TTL_MS;
  const signed       = signChallenge(challengeId, provider, expMs);
  const signedHash   = sha256(signed);

  // Optimistic lock: only update if status is still 'created'
  const { data, error } = await supabase
    .from('license_ad_challenges')
    .update({
      status:                 'provider_selected',
      provider,
      signed_challenge:       signed,
      signed_challenge_hash:  signedHash,
      session_hash:           hashSession(req),
    })
    .eq('id', challengeId)
    .eq('status', 'created')    // only succeeds if not already progressed
    .select()
    .single();

  if (error || !data) throw new Error('Challenge no longer available (race condition or expired)');
  return data;
}

/**
 * Mark a challenge as 'pending_ad' (user is on the ad page).
 * Validates that the signed token is still valid.
 */
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

/**
 * Load a challenge by its signed-challenge hash (for unlock callbacks).
 */
async function getChallengeByToken(signedToken) {
  const sigHash = sha256(signedToken);
  const { data, error } = await supabase
    .from('license_ad_challenges')
    .select('*')
    .eq('signed_challenge_hash', sigHash)
    .single();

  if (error || !data) return null;
  if (new Date(data.expires_at) < new Date()) return null;  // expired
  return data;
}

/**
 * Complete an ad challenge and generate a DENG key.
 * Performs an optimistic lock so duplicate callbacks are idempotent.
 *
 * Returns { key: string } on first call,
 *         { key: null, alreadyDone: true } on replay.
 */
async function completeAdAndGenerateKey(challengeRow) {
  const { id: challengeId, site_user_id, discord_user_id } = challengeRow;

  // Atomically advance from pending_ad → ad_completed
  const { data: adDone, error: adErr } = await supabase
    .from('license_ad_challenges')
    .update({ status: 'ad_completed' })
    .eq('id', challengeId)
    .eq('status', 'pending_ad')
    .select()
    .single();

  if (adErr || !adDone) {
    // Check if already completed
    const { data: existing } = await supabase
      .from('license_ad_challenges')
      .select('status')
      .eq('id', challengeId)
      .single();
    if (existing && ['ad_completed', 'key_generated'].includes(existing.status)) {
      return { key: null, alreadyDone: true };
    }
    throw new Error('Challenge state conflict');
  }

  // Generate key
  const { raw, id: keyId, prefix, suffix } = generateDengKey();
  const now = new Date().toISOString();

  // Store in license_keys (matches existing schema)
  const { error: keyErr } = await supabase
    .from('license_keys')
    .insert({
      id:               keyId,       // SHA-256 of raw key
      prefix:           prefix,
      suffix:           suffix,
      owner_discord_id: discord_user_id || null,
      status:           'active',
      plan:             'free',
      expires_at:       keyExpiresAt(),  // 24h unredeemed expiry
    });

  if (keyErr) {
    // Roll back challenge status
    await supabase
      .from('license_ad_challenges')
      .update({ status: 'failed', failure_reason: keyErr.message })
      .eq('id', challengeId);
    throw new Error(`Key store failed: ${keyErr.message}`);
  }

  // Atomically mark key_generated (optimistic lock)
  const { data: finalRow, error: finalErr } = await supabase
    .from('license_ad_challenges')
    .update({
      status:         'key_generated',
      license_key_id: keyId,
      key_prefix:     prefix,
      key_suffix:     suffix,
      key_expires_at: keyExpiresAt(),
      completed_at:   now,
    })
    .eq('id', challengeId)
    .eq('status', 'ad_completed')
    .select()
    .single();

  if (finalErr || !finalRow) {
    // Key was stored but row couldn't update — log but don't fail user
    console.error('[challenge] Warning: key stored but challenge row not finalized:', finalErr);
  }

  return { key: raw, alreadyDone: false };
}

module.exports = {
  checkCooldown,
  createChallenge,
  selectProvider,
  markPendingAd,
  getChallengeByToken,
  completeAdAndGenerateKey,
};
