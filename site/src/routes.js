'use strict';
/**
 * HTTP routes for the DENG Tool portal.
 */
const express = require('express');
const rateLimit = require('express-rate-limit');
const path = require('path');
const { execFileSync } = require('child_process');

const auth = require('./auth');
const {
  LOGIN_HOME,
  requireLogin,
  verifyCsrf,
  buildDiscordAuthUrl,
  exchangeDiscordCode,
  fetchDiscordUser,
  upsertDiscordUser,
  ensureRealSiteUser,
  toSessionUser,
} = auth;
const challenge = require('./challenge');
const supabase = require('./db');
const licenseService = require('./licenseService');
const licenseEligibility = require('./licenseEligibility');
const { formatWibTimestamp, licenseExportFilename } = require('./licenseFormat');
const linkvertise = require('./providers/linkvertise');
const lootlabs = require('./providers/lootlabs');
const { signChallenge, verifyChallenge, isStateSecretConfigured } = require('./crypto');

const router = express.Router();

const DEFAULT_PROVIDER_CONFIG = {
  linkvertise: {
    // Linkvertise Target-Link Anti-Bypass approach: the start URL is the
    // configured link-hub.net link, the completion URL is the dashboard
    // callback. Verification happens server-side via the Anti-Bypass API.
    enabled: 'false',
    monetizedUrl: 'https://link-hub.net/5914830/XEpUhZ8TdtyV',
    completeUrl: 'https://tool.deng.my.id/unlock/linkvertise/complete',
    publisherId: '5914830',
  },
  lootlabs: {
    enabled: 'true',
    monetizedUrl: 'https://lootdest.org/s?TqZQAW38',
    completeUrl: 'https://tool.deng.my.id/unlock/lootlabs/complete',
  },
};

const SAFE_MESSAGES = {
  NO_PROVIDER_CONFIGURED: 'No ad provider is configured yet.',
  AUTH_REQUIRED: 'Please login with Discord first.',
  COOLDOWN_ACTIVE: 'Please wait before generating another key.',
  KEY_LIMIT_REACHED: 'Key limit reached for your account. Ask an admin if you need a higher limit.',
  EXISTING_UNUSED_KEY: 'You already have an unused key. Copy or redeem this key before generating another.',
  TOO_MANY_ATTEMPTS: 'Too many key generation attempts. Please wait before trying again.',
  CHALLENGE_TABLE_MISSING: 'Key generation database is not ready yet.',
  DB_FOREIGN_KEY_FAILED: 'Could not prepare your license account. Please try again.',
  DB_PERMISSION_DENIED: 'Key generation database permission error.',
  SITE_USER_UPSERT_FAILED: 'Could not prepare your license account. Please try again.',
  CHALLENGE_INSERT_FAILED: 'Could not start key generation. Please try again.',
  PROVIDER_NOT_CONFIGURED: 'This ad provider is not configured yet.',
  PROVIDER_CHALLENGE_MISSING: 'No active key generation attempt was found. Tap Generate Key to start again.',
  STATE_SECRET_NOT_CONFIGURED: 'Key generation is temporarily unavailable (server signing not configured). Contact an admin.',
  PROVIDER_CHALLENGE_EXPIRED: 'Your ad session expired. Tap Generate Key to start a new one.',
  PROVIDER_CHALLENGE_OWNER_MISMATCH: 'This ad completion belongs to another account. Please login with the correct Discord account.',
  PROVIDER_CHALLENGE_ALREADY_USED: 'This ad completion was already used. Check your keys below or wait for cooldown.',
  PROVIDER_ATTEMPT_PENDING: 'Your ad step is still pending. Finish the ad or wait a moment, then return here.',
  PROVIDER_ATTEMPT_REJECTED: 'The ad provider could not verify completion. Please try the ad again.',
  PROVIDER_TOKEN_REPLAYED: 'This ad completion link was already used and cannot generate another key.',
  PROVIDER_RETURN_UNVERIFIED: 'Could not verify ad completion. Please complete the ad step again.',
  PROVIDER_RETURN_SECRET_MISSING: 'Ad unlock security is not configured yet.',
  PROVIDER_RETURN_TOKEN_MISSING: 'Invalid or expired key generation session. Please start again.',
  PROVIDER_RETURN_TOKEN_INVALID: 'Invalid or expired key generation session. Please start again.',
  PROVIDER_RETURN_TOKEN_EXPIRED: 'This key generation session expired. Please start again.',
  PROVIDER_WAIT_INCOMPLETE: 'Please complete the ad step before continuing.',
  PROVIDER_MISMATCH: 'Invalid or expired key generation session. Please start again.',
  CHALLENGE_ALREADY_USED: 'Invalid or expired key generation session. Please start again.',
  KEY_GENERATION_FAILED: 'Could not generate key. Please try again.',
  UNEXPECTED_ERROR: 'Could not start key generation. Please try again.',
};

function cleanEnv(name, fallback = '') {
  const raw = Object.prototype.hasOwnProperty.call(process.env, name) ? process.env[name] : fallback;
  const cleaned = String(raw || '').trim().replace(/^['"]|['"]$/g, '').trim();
  if (cleaned) return cleaned;
  return String(fallback || '').trim().replace(/^['"]|['"]$/g, '').trim();
}

function envEnabled(name, fallback = 'false') {
  return ['1', 'true', 'yes', 'on'].includes(cleanEnv(name, fallback).toLowerCase());
}

function publicUrl() {
  return cleanEnv('TOOL_SITE_PUBLIC_URL', 'https://tool.deng.my.id').replace(/\/+$/, '');
}

function getProviderConfig(provider) {
  if (provider === 'linkvertise') {
    return {
      provider,
      enabled: envEnabled('LINKVERTISE_ENABLED', DEFAULT_PROVIDER_CONFIG.linkvertise.enabled),
      monetizedUrl: cleanEnv('LINKVERTISE_MONETIZED_URL', DEFAULT_PROVIDER_CONFIG.linkvertise.monetizedUrl),
      completeUrl: cleanEnv('LINKVERTISE_COMPLETE_URL', DEFAULT_PROVIDER_CONFIG.linkvertise.completeUrl),
      publisherId: cleanEnv('LINKVERTISE_PUBLISHER_ID', DEFAULT_PROVIDER_CONFIG.linkvertise.publisherId),
    };
  }
  if (provider === 'lootlabs') {
    return {
      provider,
      enabled: envEnabled('LOOTLABS_ENABLED', DEFAULT_PROVIDER_CONFIG.lootlabs.enabled),
      monetizedUrl: cleanEnv('LOOTLABS_MONETIZED_URL', DEFAULT_PROVIDER_CONFIG.lootlabs.monetizedUrl),
      completeUrl: cleanEnv('LOOTLABS_COMPLETE_URL', DEFAULT_PROVIDER_CONFIG.lootlabs.completeUrl),
    };
  }
  return null;
}

function enabledProviders() {
  return ['linkvertise', 'lootlabs']
    .map(getProviderConfig)
    .filter((item) => item && providerIsReady(item.provider));
}

function providerIsReady(provider) {
  if (provider === 'lootlabs') {
    // LootLabs Redirect API / Anti-Bypass: requires LOOTLABS_ENABLED=true,
    // a base shortlink, an API token, and an encrypt URL. The helper module
    // is the source of truth.
    return lootlabs.isLootLabsConfigured();
  }
  if (provider === 'linkvertise') {
    // Linkvertise is only ready when Target-Link Anti-Bypass is properly
    // configured (LINKVERTISE_ENABLED=true, target link set, anti-bypass
    // token set in env). The helper module is the source of truth.
    return linkvertise.isLinkvertiseConfigured();
  }
  const cfg = getProviderConfig(provider);
  if (!cfg || !cfg.enabled || !cfg.monetizedUrl || !cfg.completeUrl) return false;
  return true;
}

function codeFromError(err, fallback = 'UNEXPECTED_ERROR') {
  if (err?.code && SAFE_MESSAGES[err.code]) return err.code;
  const msg = String(err?.message || '');
  if (/TOOL_SITE_STATE_SECRET/i.test(msg)) return 'STATE_SECRET_NOT_CONFIGURED';
  return err && err.code && SAFE_MESSAGES[err.code] ? err.code : fallback;
}

function messageFor(code) {
  return SAFE_MESSAGES[code] || SAFE_MESSAGES.UNEXPECTED_ERROR;
}

function flashBlock(req, code, extra = {}) {
  safeFlash(req, 'error', messageFor(code));
  req.session.flash = {
    ...(req.session.flash || {}),
    error: messageFor(code),
    blockReason: extra.blockReason || codeToBlockReason(code),
    ...extra,
  };
}

function codeToBlockReason(code) {
  const map = {
    STATE_SECRET_NOT_CONFIGURED: 'server_error',
    PROVIDER_CHALLENGE_MISSING: 'provider_attempt_invalid',
    PROVIDER_CHALLENGE_EXPIRED: 'attempt_expired',
    PROVIDER_CHALLENGE_ALREADY_USED: 'provider_token_replayed',
    CHALLENGE_ALREADY_USED: 'provider_token_replayed',
    PROVIDER_RETURN_UNVERIFIED: 'provider_rejected',
    PROVIDER_RETURN_TOKEN_MISSING: 'provider_attempt_invalid',
    PROVIDER_RETURN_TOKEN_INVALID: 'provider_attempt_invalid',
    PROVIDER_RETURN_TOKEN_EXPIRED: 'attempt_expired',
    PROVIDER_WAIT_INCOMPLETE: 'provider_pending',
    PROVIDER_CHALLENGE_OWNER_MISMATCH: 'auth_required',
    COOLDOWN_ACTIVE: 'cooldown_active',
    KEY_LIMIT_REACHED: 'max_key_limit',
    AUTH_REQUIRED: 'auth_required',
  };
  return map[code] || 'server_error';
}

function logGenerateKeyStart(fields) {
  console.log('[GENERATE_KEY_START]', JSON.stringify({ ...fields, ts: new Date().toISOString() }));
}

function logGenerateKeyReturn(fields) {
  console.log('[GENERATE_KEY_RETURN]', JSON.stringify({ ...fields, ts: new Date().toISOString() }));
}

function accountIdsFromReq(req) {
  return {
    userId: req.session?.user?.discord_user_id || null,
    accountId: req.session?.user?.id || null,
  };
}

async function finishProviderCompletion(req, res, result, { provider, challengeRow = null } = {}) {
  const { key, alreadyDone, recoveredExisting, challengeRow: resultRow } = result || {};
  const row = challengeRow || resultRow || null;
  const ids = accountIdsFromReq(req);

  if (alreadyDone) {
    const recovered = key || (row ? await challenge.recoverGeneratedKeyFromChallenge(row) : null);
    logGenerateKeyReturn({
      ...ids,
      provider,
      attemptId: row?.id || null,
      stateValid: true,
      attemptFound: !!row,
      attemptStatus: row?.status || null,
      attemptExpired: row ? new Date(row.expires_at) < new Date() : null,
      keyIssued: !!recovered,
      recoveredExistingKey: true,
      failureReason: null,
    });
    if (recovered) {
      req.session.generatedKey = recovered;
      req.session.generatedKeyAt = Date.now();
      req.session.generatedKeyRecovery = true;
      delete req.session.pendingChallenge;
      delete req.session.pendingProvider;
      delete req.session.pendingSignedChallenge;
      delete req.session.activeAdChallengeId;
      return res.redirect('/key/result');
    }
    flashBlock(req, 'PROVIDER_CHALLENGE_ALREADY_USED', { blockReason: 'provider_token_replayed' });
    return res.redirect('/license');
  }

  if (recoveredExisting && key) {
    req.session.generatedKey = key;
    req.session.generatedKeyRecovery = true;
    delete req.session.pendingChallenge;
    delete req.session.pendingProvider;
    delete req.session.pendingSignedChallenge;
    delete req.session.activeAdChallengeId;
    return res.redirect('/key/result');
  }

  if (!key) {
    logGenerateKeyReturn({
      ...ids,
      provider,
      attemptId: row?.id || null,
      stateValid: true,
      attemptFound: !!row,
      attemptStatus: row?.status || null,
      keyIssued: false,
      failureReason: 'server_error',
    });
    flashBlock(req, 'KEY_GENERATION_FAILED');
    return res.redirect('/license');
  }

  logGenerateKeyReturn({
    ...ids,
    provider,
    attemptId: row?.id || null,
    stateValid: true,
    attemptFound: !!row,
    attemptStatus: row?.status || 'key_generated',
    attemptExpired: false,
    keyIssued: true,
    recoveredExistingKey: !!recoveredExisting,
    failureReason: null,
  });
  req.session.generatedKey = key;
  req.session.generatedKeyAt = Date.now();
  delete req.session.pendingChallenge;
  delete req.session.pendingProvider;
  delete req.session.pendingSignedChallenge;
  delete req.session.activeAdChallengeId;
  if (row?.id) req.session.lastCompletedChallengeId = row.id;
  return res.redirect('/key/result');
}

function logSafeError(scope, code, err) {
  const detail = err && err.message ? err.message : String(err || '');
  const constraint = detail.includes('license_ad_challenges_site_user_id_fkey')
    ? ' constraint=license_ad_challenges_site_user_id_fkey'
    : '';
  const table = detail.includes('license_ad_challenges') ? ' table=license_ad_challenges' : '';
  const expected = new Set([
    'COOLDOWN_ACTIVE',
    'TOO_MANY_ATTEMPTS',
    'PROVIDER_RETURN_UNVERIFIED',
    'PROVIDER_RETURN_TOKEN_MISSING',
    'PROVIDER_RETURN_TOKEN_INVALID',
    'PROVIDER_RETURN_TOKEN_EXPIRED',
    'PROVIDER_WAIT_INCOMPLETE',
    'PROVIDER_MISMATCH',
    'PROVIDER_CHALLENGE_MISSING',
    'PROVIDER_CHALLENGE_EXPIRED',
    'PROVIDER_CHALLENGE_OWNER_MISMATCH',
    'PROVIDER_CHALLENGE_ALREADY_USED',
    'CHALLENGE_ALREADY_USED',
  ]);
  const line = `[${scope}] code=${code}${table}${constraint} message=${detail.slice(0, 240)}`;
  if (expected.has(code)) console.log(line);
  else console.error(line);
}

const authLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 20,
  skip: () => process.env.NODE_ENV === 'test',
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many login attempts, please wait.' },
});

function wantsJson(req) {
  return (req.headers.accept || '').includes('application/json') ||
    (req.headers['content-type'] || '').includes('application/json');
}

function rateLimitsDisabled() {
  return process.env.NODE_ENV === 'test' && process.env.ENABLE_RATE_LIMIT_TEST !== '1';
}

const generateLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 5,
  skip: rateLimitsDisabled,
  standardHeaders: true,
  legacyHeaders: false,
  handler: (req, res) => {
    const code = 'TOO_MANY_ATTEMPTS';
    if (wantsJson(req)) {
      return res.status(429).json({ error: code, message: messageFor(code) });
    }
    safeFlash(req, 'error', messageFor(code));
    return res.redirect(303, '/license');
  },
});

const licenseActionLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 20,
  skip: rateLimitsDisabled,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'too_many_license_actions', message: 'Too many license actions. Please wait before trying again.' },
});

function safeFlash(req, key, value) {
  req.session.flash = { ...(req.session.flash || {}), [key]: value };
}

function tokenizedCompleteUrl(provider, returnToken) {
  const cfg = getProviderConfig(provider);
  const base = cfg?.completeUrl || `${publicUrl()}/unlock/${provider}/complete`;
  const url = new URL(base);
  url.searchParams.set('t', returnToken);
  return url.toString();
}

/**
 * Build a LootLabs redirect URL that embeds the signed return URL.
 * Requires LOOTLABS_TEMPLATE_URL (contains {url} placeholder) so that the
 * provider destination is set per-challenge rather than hard-coded.
 * Without a template URL, lootdest.org cannot return to the DENG portal.
 */
function lootlabsProviderUrl(returnToken) {
  const completeUrl = tokenizedCompleteUrl('lootlabs', returnToken);
  const templateUrl = cleanEnv('LOOTLABS_TEMPLATE_URL', '');
  if (templateUrl) {
    // Template approach: preserves the shortlink ID exactly as written.
    // Replace {url} placeholder with the encoded signed completion URL.
    const providerUrl = templateUrl.replace('{url}', encodeURIComponent(completeUrl));
    if (process.env.NODE_ENV !== 'test') {
      let providerHost = '';
      try { providerHost = new URL(providerUrl).hostname; } catch {}
      console.log('[lootlabs_provider_url_created] host=%s token_len=%d', providerHost, returnToken.length);
    }
    return providerUrl;
  }
  // Fallback: safe string-based append — do NOT use the URL searchParams API
  // because new URL('…s?TqZQAW38').searchParams.set(…) normalises the
  // valueless key to "TqZQAW38=" which breaks the LootDest shortlink lookup.
  // NOTE: this fallback is retained for backward-compat tests only.
  // In production, LOOTLABS_TEMPLATE_URL must be set (providerIsReady enforces this).
  const cfg = getProviderConfig('lootlabs');
  const base = cfg.monetizedUrl;
  const sep = base.includes('?') ? '&' : '?';
  if (process.env.NODE_ENV !== 'test') {
    console.warn('[key/provider] lootlabs LOOTLABS_TEMPLATE_URL not set; fallback url may not be forwarded by provider');
  }
  return `${base}${sep}return_url=${encodeURIComponent(completeUrl)}&deng_return=${encodeURIComponent(completeUrl)}`;
}

function providerRedirectUrl(providerCfg, returnToken) {
  if (providerCfg.provider === 'lootlabs') {
    return lootlabsProviderUrl(returnToken);
  }
  if (providerCfg.provider === 'linkvertise') {
    // Linkvertise Target-Link Anti-Bypass: redirect directly to the real
    // link-hub.net target. NEVER append the anti-bypass token to the URL,
    // and NEVER append a signed completion token — Linkvertise will return
    // to the configured callback URL with `?hash=<linkvertise_hash>`.
    return linkvertise.getLinkvertiseTargetLinkUrl();
  }
  // Generic fallback for any future provider
  const url = new URL(providerCfg.monetizedUrl);
  const complete = tokenizedCompleteUrl(providerCfg.provider, returnToken);
  url.searchParams.set('return_url', complete);
  url.searchParams.set('deng_return', complete);
  return url.toString();
}

async function repairSiteUser(req, _res, next) {
  try {
    await ensureRealSiteUser(req);
  } catch (err) {
    const code = codeFromError(err, 'SITE_USER_UPSERT_FAILED');
    logSafeError('site_user/repair', code, err);
  }
  next();
}

function maskKeyRow(row) {
  if (row.masked_key) return row.masked_key;
  const prefix = row.key_prefix || 'DENG-????-????';
  const suffix = row.key_suffix || '????-????';
  return `${prefix}-****-${String(suffix).split('-').pop() || '????'}`;
}

/**
 * Return full unmasked key for authenticated owner portal pages.
 * key_prefix = "DENG-XXXX-XXXX", key_suffix = "XXXX-XXXX"
 * Full key = "DENG-XXXX-XXXX-XXXX-XXXX"
 * Never use this in URLs, logs, or public Discord messages.
 */
function fullKeyRow(row) {
  if (row.key_display) return row.key_display;
  const prefix = row.key_prefix || 'DENG-????-????';
  const suffix = row.key_suffix || '????-????';
  return `${prefix}-${suffix}`;
}

function existingUnusedPayload(row) {
  if (!row) return null;
  const lifecycle = licenseService.classifyLicenseLifecycle(row);
  return {
    id: row.id,
    key: fullKeyRow(row),
    expires_at: row.expires_at || row.key_expires_at || null,
    expires_at_formatted: formatWibTimestamp(row.expires_at || row.key_expires_at),
    provider: providerLabel(row.provider),
    lifecycle_status: lifecycle.lifecycle_status,
    display_status: lifecycle.display_status,
    is_unredeemed: lifecycle.is_unredeemed,
    is_redeemed: lifecycle.is_redeemed,
    is_unbound: lifecycle.is_unbound,
    is_bound: lifecycle.is_bound,
    is_expired: lifecycle.is_expired,
    is_revoked: lifecycle.is_revoked,
    blocks_generation: lifecycle.blocks_generation,
    status: lifecycle.display_status,
    message: 'You already have an unused key. Copy or redeem this key before generating another.',
  };
}

function providerLabel(provider) {
  return licenseService.providerLabel(provider);
}

function friendlyStatus(row) {
  return licenseService.formatLicenseStatus(row);
}

function resolveServerCommit() {
  if (process.env.GIT_COMMIT) return String(process.env.GIT_COMMIT).trim();
  try {
    const root = path.join(__dirname, '..', '..');
    return execFileSync('git', ['rev-parse', '--short', 'HEAD'], {
      cwd: root,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch (_) {
    return 'unknown';
  }
}

async function buildLicenseUserDebugReport({ username = '', discordUserId = '' } = {}) {
  const cleanUsername = String(username || '').trim().toLowerCase();
  let siteUser = null;
  if (discordUserId) {
    const { data } = await supabase
      .from('site_users')
      .select('id, discord_user_id, username')
      .eq('discord_user_id', String(discordUserId))
      .maybeSingle();
    siteUser = data || null;
  } else if (cleanUsername) {
    const { data } = await supabase
      .from('site_users')
      .select('id, discord_user_id, username')
      .ilike('username', cleanUsername)
      .limit(1)
      .maybeSingle();
    siteUser = data || null;
    if (!siteUser?.id) {
      const { data: licenseUser } = await supabase
        .from('license_users')
        .select('discord_user_id, discord_username')
        .ilike('discord_username', cleanUsername)
        .limit(1)
        .maybeSingle();
      if (licenseUser?.discord_user_id) {
        const { data: byDiscord } = await supabase
          .from('site_users')
          .select('id, discord_user_id, username')
          .eq('discord_user_id', String(licenseUser.discord_user_id))
          .maybeSingle();
        siteUser = byDiscord ? { ...byDiscord, username: byDiscord.username || licenseUser.discord_username } : null;
      }
    }
  }
  if (!siteUser?.id) {
    return { ok: false, error: 'user_not_found', username: cleanUsername || null, discordUserId: discordUserId || null };
  }

  const eligibility = await licenseEligibility.getLicenseGenerationEligibility({
    discordUserId: siteUser.discord_user_id,
    siteUserId: siteUser.id,
    skipProviderCheck: false,
  });
  const attemptDiag = await challenge.getGenerationAttemptDiagnostic({
    discordUserId: siteUser.discord_user_id,
    siteUserId: siteUser.id,
  });
  const { data: attempts } = await supabase
    .from('license_ad_challenges')
    .select('id, status, provider, created_at, expires_at, completed_at, failure_reason, provider_payload, license_key_id')
    .eq('site_user_id', siteUser.id)
    .order('created_at', { ascending: false })
    .limit(15);
  const keys = await licenseService.getPortalUserLicenses({
    discordUserId: siteUser.discord_user_id,
    siteUserId: siteUser.id,
    limit: 50,
  });
  const activeKeys = licenseService.filterActiveLicenses(keys);
  const unredeemed = keys.filter(licenseService.isActiveUnredeemedKey);
  const expired = keys.filter((row) => licenseService.classifyLicenseLifecycle(row).is_expired);
  const pendingAd = (attempts || []).filter((row) => row.status === 'pending_ad' || row.status === 'ad_started');
  const resumable = (attempts || []).filter((row) => challenge.RESUMABLE_STATUSES.includes(row.status));

  let generateAction = 'create_new_attempt';
  if (!eligibility.canGenerate) {
    generateAction = `blocked:${eligibility.blockReason || 'unknown'}`;
  } else if (unredeemed.length > 0) {
    generateAction = 'recover_existing_unredeemed_key';
  } else if (resumable.some((row) => new Date(row.expires_at) >= new Date())) {
    generateAction = 'resume_open_attempt';
  }

  const mappedAttempts = (attempts || []).map((row) => {
    const payload = row.provider_payload && typeof row.provider_payload === 'object'
      ? row.provider_payload
      : (() => { try { return JSON.parse(row.provider_payload || '{}'); } catch { return {}; } })();
    return {
      id: row.id,
      status: row.status,
      provider: row.provider,
      created_at: row.created_at,
      expires_at: row.expires_at,
      completed_at: row.completed_at,
      failure_reason: row.failure_reason,
      linkvertise_hash: payload.linkvertise_hash || null,
      linkvertise_started: payload.linkvertise_started === true,
      lootlabs_started: payload.lootlabs_started === true,
      license_key_id: row.license_key_id || null,
      expired: new Date(row.expires_at) < new Date(),
    };
  });

  return {
    ok: true,
    serverCommit: resolveServerCommit(),
    stateSecretConfigured: isStateSecretConfigured(),
    pm2Process: {
      pid: process.pid,
      nodeEnv: process.env.NODE_ENV || null,
      uptimeSeconds: Math.floor(process.uptime()),
    },
    account: {
      siteUserId: siteUser.id,
      discordUserId: siteUser.discord_user_id,
      username: siteUser.username,
    },
    activeKeyCount: activeKeys.length,
    maxKeyLimit: eligibility.maxKeyPolicyUsed,
    canGenerate: eligibility.canGenerate,
    blockReason: eligibility.blockReason,
    existingRecoverableKey: unredeemed[0]
      ? { masked: unredeemed[0].masked_key || `${unredeemed[0].prefix}-****-${unredeemed[0].suffix}`, expires_at: unredeemed[0].expires_at }
      : null,
    generateKeyWould: generateAction,
    latestAttemptDiagnostic: attemptDiag,
    latestAttempts: mappedAttempts,
    pendingAdAttempts: mappedAttempts.filter((row) => row.status === 'pending_ad' || row.status === 'ad_started'),
    expiredKeys: expired.map((row) => ({
      id: row.id,
      prefix: row.prefix,
      suffix: row.suffix,
      status: row.status,
      expires_at: row.expires_at,
    })),
    recommendedAction: !isStateSecretConfigured()
      ? 'set_TOOL_SITE_STATE_SECRET_and_restart_deng-tool-site'
      : (!eligibility.canGenerate
        ? `blocked_by_${eligibility.blockReason}`
        : (generateAction === 'resume_open_attempt'
          ? 'select_provider_to_bind_hash'
          : 'generate_key_allowed')),
  };
}

function discordOwnerId(req) {
  return String(req.session?.user?.discord_user_id || '').trim();
}

function requireSiteAdmin(req, res, next) {
  const token = process.env.TOOL_SITE_ADMIN_TOKEN || process.env.FISHIT_GLOBAL_ADMIN_TOKEN;
  const provided = req.headers['x-admin-token'] || req.query.admin_token || req.body?.admin_token;
  if (!token || !provided || String(provided) !== String(token)) {
    if (wantsJson(req)) return res.status(401).json({ error: 'unauthorized', message: 'Admin token required.' });
    return res.status(401).send('Unauthorized');
  }
  return next();
}

async function resolveSiteUserIdForDiscord(discordUserId) {
  const owner = String(discordUserId || '').trim();
  if (!owner) return '';
  const { data } = await supabase
    .from('site_users')
    .select('id')
    .eq('discord_user_id', owner)
    .maybeSingle();
  return data?.id || '';
}

function handleLicenseApiError(res, err, fallback = 'license_action_failed') {
  const status = err?.status || 500;
  const code = err?.code || fallback;
  const message = err?.message || 'License action failed. Please try again.';
  return res.status(status).json({ error: code, message });
}

function requireLicenseApiLogin(req, res, next) {
  if (req.session && req.session.user) return next();
  return res.status(401).json({ error: 'auth_required', message: messageFor('AUTH_REQUIRED') });
}

function requireLicenseDownloadLogin(req, res, next) {
  if (req.session && req.session.user) return next();
  return res.status(401).type('text/plain').send(`${messageFor('AUTH_REQUIRED')}\n`);
}

function summarizeHistory(history) {
  const stats = licenseService.computeStats(history || []);
  return {
    ...stats,
    cooldownSeconds: challenge.COOLDOWN_SECONDS,
    keyExpiryHours: challenge.KEY_EXPIRY_HOURS,
  };
}

async function loadHistory(siteUserId, limit = 20, fallbackDiscordUserId = '', { activeOnly = true } = {}) {
  const { data } = await supabase
    .from('site_users')
    .select('discord_user_id')
    .eq('id', siteUserId)
    .maybeSingle();
  const owner = data?.discord_user_id || fallbackDiscordUserId;
  const rows = await licenseService.getPortalUserLicenses({ discordUserId: owner, siteUserId, limit });
  return activeOnly ? licenseService.filterActiveLicenses(rows) : rows;
}

function ensureProvider(provider) {
  return ['lootlabs', 'linkvertise'].includes(provider) ? provider : '';
}

async function handleKeyStart(req, res) {
  if (!verifyCsrf(req)) {
    if (wantsJson(req)) return res.status(403).json({ error: 'invalid_csrf' });
    safeFlash(req, 'error', 'Invalid request token.');
    return res.redirect('/license');
  }

  try {
    await ensureRealSiteUser(req);
    const { user } = req.session;

    const eligibility = await licenseEligibility.getLicenseGenerationEligibility({
      discordUserId: discordOwnerId(req),
      siteUserId: user.id,
      skipProviderCheck: true,
    });

    if (!eligibility.canGenerate) {
      if (eligibility.blockReason === 'active_unredeemed_key') {
        const existingUnused = await licenseService.findActiveUnredeemedKey({
          discordUserId: discordOwnerId(req),
          siteUserId: user.id,
        });
        const payload = existingUnusedPayload(existingUnused);
        logGenerateKeyStart({
          ...accountIdsFromReq(req),
          provider: null,
          existingActiveKeyFound: true,
          activeSlotCount: eligibility.activeKeySlotCount,
          maxKeyLimit: eligibility.maxKeyPolicyUsed,
          createdAttemptId: null,
          redirectUrlCreated: false,
          failureReason: 'existing_key_recovery',
        });
        if (wantsJson(req)) {
          return res.status(200).json({
            status: 'existing_unused_key',
            blockReason: 'active_unredeemed_key',
            existing_key: payload,
            message: payload.message,
            remainingSeconds: eligibility.remainingSeconds,
            expiresAt: eligibility.expiresAt,
          });
        }
        req.session.recoveredExistingKey = payload;
        safeFlash(req, 'success', payload.message);
        return res.redirect(303, '/license');
      }

      if (eligibility.blockReason === 'max_key_limit') {
        const msg = licenseEligibility.messageForBlockReason('max_key_limit');
        if (wantsJson(req)) {
          return res.status(429).json({
            error: 'KEY_LIMIT_REACHED',
            blockReason: 'max_key_limit',
            message: msg,
            activeCount: eligibility.activeKeySlotCount,
            maxKeys: eligibility.maxKeyPolicyUsed,
          });
        }
        req.session.flash = {
          error: msg,
          keyLimitReached: true,
          activeCount: eligibility.activeKeySlotCount,
          maxKeys: eligibility.maxKeyPolicyUsed,
        };
        return res.redirect('/license');
      }

      if (eligibility.blockReason === 'cooldown_active') {
        const msg = licenseEligibility.messageForBlockReason('cooldown_active', eligibility.remainingSeconds);
        if (wantsJson(req)) {
          return res.status(429).json({
            error: 'COOLDOWN_ACTIVE',
            blockReason: 'cooldown_active',
            message: msg,
            remainingSeconds: eligibility.remainingSeconds,
            cooldownUntil: eligibility.cooldownUntil,
          });
        }
        req.session.flash = {
          error: msg,
          cooldown: eligibility.remainingSeconds,
        };
        return res.redirect('/license');
      }

      const msg = eligibility.message || messageFor('UNEXPECTED_ERROR');
      if (wantsJson(req)) {
        return res.status(429).json({
          error: eligibility.blockReason || 'GENERATION_BLOCKED',
          blockReason: eligibility.blockReason,
          message: msg,
        });
      }
      safeFlash(req, 'error', msg);
      return res.redirect('/license');
    }

    if (enabledProviders().length === 0) {
      const err = new Error('No enabled ad providers');
      err.code = 'NO_PROVIDER_CONFIGURED';
      throw err;
    }

    const row = await challenge.findOrCreateResumableChallenge(req, user);
    req.session.pendingChallenge = row.row.id;
    req.session.activeAdChallengeId = row.row.id;

    logGenerateKeyStart({
      ...accountIdsFromReq(req),
      provider: null,
      existingActiveKeyFound: false,
      activeSlotCount: eligibility.activeKeySlotCount,
      maxKeyLimit: eligibility.maxKeyPolicyUsed,
      createdAttemptId: row.row.id,
      attemptExpiresAt: row.row.expires_at,
      attemptStatus: row.row.status,
      resumedExistingAttempt: row.resumed,
      redirectUrlCreated: false,
    });

    if (wantsJson(req)) return res.json({ challenge_id: row.row.id, status: row.row.status, resumed: row.resumed });
    return res.render('choose_provider', {
      title: 'Choose Unlock Method - DENG Tool',
      challengeId: row.row.id,
      providers: enabledProviders(),
      providerLabel,
    });
  } catch (err) {
    const code = codeFromError(err, err?.code === 'NO_PROVIDER_CONFIGURED' ? 'NO_PROVIDER_CONFIGURED' : 'CHALLENGE_INSERT_FAILED');
    logSafeError('api/key/start', code, err);
    const status = ['NO_PROVIDER_CONFIGURED', 'CHALLENGE_TABLE_MISSING', 'DB_PERMISSION_DENIED'].includes(code) ? 503 : 500;
    if (wantsJson(req)) return res.status(status).json({ error: code, message: messageFor(code) });
    safeFlash(req, 'error', messageFor(code));
    return res.redirect('/license');
  }
}

async function handleProvider(req, res) {
  if (!verifyCsrf(req)) {
    if (wantsJson(req)) return res.status(403).json({ error: 'invalid_csrf' });
    safeFlash(req, 'error', 'Invalid request token.');
    return res.redirect('/license');
  }

  const provider = ensureProvider(String(req.params.provider || req.body.provider || ''));
  const challengeIdFromBody = String(req.body.challenge_id || '');
  const { user } = req.session;

  if (!provider) {
    safeFlash(req, 'error', 'Invalid provider selection.');
    return res.redirect('/license');
  }
  if (!providerIsReady(provider)) {
    const code = 'PROVIDER_NOT_CONFIGURED';
    if (wantsJson(req)) return res.status(503).json({ error: code, message: messageFor(code) });
    safeFlash(req, 'error', messageFor(code));
    return res.redirect('/license');
  }
  if (!isStateSecretConfigured()) {
    const code = 'STATE_SECRET_NOT_CONFIGURED';
    logGenerateKeyStart({
      ...accountIdsFromReq(req),
      provider,
      existingActiveKeyFound: false,
      createdAttemptId: null,
      redirectUrlCreated: false,
      failureReason: 'state_secret_missing',
    });
    flashBlock(req, code, { blockReason: 'server_error', failureReason: 'state_secret_missing' });
    if (wantsJson(req)) return res.status(503).json({ error: code, message: messageFor(code) });
    return res.redirect('/license');
  }

  let resolved;
  try {
    resolved = await challenge.resolveChallengeForProvider(req, user, {
      challengeId: challengeIdFromBody,
      provider,
    });
  } catch (err) {
    const code = codeFromError(err, 'PROVIDER_CHALLENGE_MISSING');
    logGenerateKeyStart({
      ...accountIdsFromReq(req),
      provider,
      existingActiveKeyFound: false,
      activeSlotCount: null,
      maxKeyLimit: null,
      createdAttemptId: null,
      attemptExpiresAt: null,
      redirectUrlCreated: false,
      failureReason: challenge.mapErrorToFailureReason(err),
    });
    flashBlock(req, code, { blockReason: codeToBlockReason(code), failureReason: challenge.mapErrorToFailureReason(err) });
    return res.redirect('/license');
  }

  if (!resolved.row) {
    logGenerateKeyStart({
      ...accountIdsFromReq(req),
      provider,
      existingActiveKeyFound: false,
      activeSlotCount: null,
      maxKeyLimit: null,
      createdAttemptId: null,
      attemptExpiresAt: null,
      redirectUrlCreated: false,
      failureReason: 'missing_attempt_id',
      recoverySource: resolved.source,
      storageMissing: !req.session?.pendingChallenge,
    });
    flashBlock(req, 'PROVIDER_CHALLENGE_MISSING', {
      blockReason: 'provider_attempt_invalid',
      failureReason: 'missing_attempt_id',
    });
    if (wantsJson(req)) {
      return res.status(400).json({
        error: 'PROVIDER_CHALLENGE_MISSING',
        message: messageFor('PROVIDER_CHALLENGE_MISSING'),
        blockReason: 'provider_attempt_invalid',
        failureReason: 'missing_attempt_id',
      });
    }
    return res.redirect('/license');
  }

  const row = resolved.row;
  req.session.pendingChallenge = row.id;
  req.session.activeAdChallengeId = row.id;

  try {
    let workingRow = row;
    if (row.status === 'created' || (row.status === 'provider_selected' && row.provider !== provider)) {
      workingRow = await challenge.selectProvider(row.id, provider, req, user);
    } else if (row.status === 'provider_selected' && row.provider === provider) {
      workingRow = row;
    } else if (row.status === 'pending_ad' && row.provider === provider) {
      workingRow = row;
    } else if (row.status === 'pending_ad' && row.provider !== provider) {
      workingRow = await challenge.selectProvider(row.id, provider, req, user);
    } else {
      workingRow = await challenge.selectProvider(row.id, provider, req, user);
    }
    const providerCfg = getProviderConfig(provider);

    let redirectUrl;
    let returnTokenLen = 0;

    if (provider === 'linkvertise') {
      const targetLinkUrl = linkvertise.getLinkvertiseTargetLinkUrl();
      const callbackUrl = linkvertise.getLinkvertiseCallbackUrl();
      await challenge.markLinkvertisePendingById(workingRow.id, req, user, {
        targetLinkUrl,
        callbackUrl,
      });
      redirectUrl = targetLinkUrl;
      req.session.activeAdChallengeId = workingRow.id;
    } else if (provider === 'lootlabs') {
      const ttlMs = 30 * 60 * 1000;
      const signedState = signChallenge(workingRow.id, 'lootlabs', Date.now() + ttlMs);
      const callbackUrl = lootlabs.buildLootLabsCallbackUrl({
        signedState,
        publicUrl: publicUrl(),
      });

      const requestId = require('crypto').randomBytes(6).toString('hex');
      const enc = await lootlabs.encryptLootLabsDestination({
        destinationUrl: callbackUrl,
        requestId,
      });
      if (!enc.ok) {
        console.warn(
          '[key/provider] provider=lootlabs encrypt_failed reason=%s rid=%s',
          enc.reason, requestId,
        );
        const code = LOOTLABS_REASON_TO_CODE[enc.reason] || 'PROVIDER_RETURN_UNVERIFIED';
        if (wantsJson(req)) return res.status(503).json({ error: code, message: messageFor(code) });
        safeFlash(req, 'error', messageFor(code));
        return res.redirect('/license');
      }

      const baseLink = lootlabs.getLootLabsBaseLink();
      const startUrl = lootlabs.buildLootLabsStartUrl({
        encryptedData: enc.encrypted,
        baseLink,
      });

      await challenge.markLootLabsPendingById(workingRow.id, req, user, {
        baseLink,
        callbackPath: '/unlock/lootlabs/complete',
      });

      redirectUrl = startUrl;
      req.session.activeAdChallengeId = workingRow.id;
      returnTokenLen = 0;
    } else {
      const started = await challenge.markPendingAdById(workingRow.id, req, user, providerCfg.monetizedUrl);
      redirectUrl = providerRedirectUrl(providerCfg, started.return_token);
      returnTokenLen = (started.return_token || '').length;
    }

    req.session.pendingProvider = provider;

    let redirectHost = '';
    try { redirectHost = new URL(redirectUrl).hostname; } catch {}
    logGenerateKeyStart({
      ...accountIdsFromReq(req),
      provider,
      existingActiveKeyFound: false,
      activeSlotCount: null,
      maxKeyLimit: null,
      createdAttemptId: workingRow.id,
      attemptExpiresAt: workingRow.expires_at,
      attemptStatus: workingRow.status,
      recoverySource: resolved.source,
      redirectUrlCreated: true,
      redirectHost,
    });
    console.log(
      '[key/provider] provider=%s challenge_prefix=%s url_host=%s token_len=%d status=303',
      provider,
      String(workingRow.id).slice(0, 8),
      redirectHost,
      returnTokenLen,
    );

    if (wantsJson(req)) {
      // Verification happens via callback (Linkvertise hash / LootLabs signed
      // state). The JSON caller only sees the public redirect URL.
      return res.json({ provider, redirect_url: redirectUrl });
    }

    return res.redirect(303, redirectUrl);
  } catch (err) {
    const code = codeFromError(err, 'PROVIDER_CHALLENGE_MISSING');
    const failureReason = challenge.mapErrorToFailureReason(err);
    logGenerateKeyStart({
      ...accountIdsFromReq(req),
      provider,
      existingActiveKeyFound: false,
      createdAttemptId: resolved?.row?.id || null,
      redirectUrlCreated: false,
      failureReason,
    });
    logSafeError('api/key/provider', code, err);
    if (wantsJson(req)) return res.status(400).json({ error: code, message: messageFor(code), failureReason });
    flashBlock(req, code, { blockReason: codeToBlockReason(code), failureReason });
    return res.redirect('/license');
  }
}

async function handleUnlock(req, res, provider) {
  const selected = ensureProvider(provider);
  if (!selected) {
    safeFlash(req, 'error', 'Invalid unlock provider.');
    return res.redirect('/license');
  }

  safeFlash(req, 'error', messageFor('PROVIDER_RETURN_TOKEN_MISSING'));
  return res.redirect('/license');
}

async function handleProviderComplete(req, res, provider) {
  const selected = ensureProvider(provider);
  if (!selected) {
    safeFlash(req, 'error', messageFor('PROVIDER_CHALLENGE_MISSING'));
    return res.redirect('/license');
  }

  const returnToken = String(req.query.t || '');
  const refererHost = (() => {
    try {
      const h = req.headers.referer || req.headers.referrer || req.headers.origin || '';
      return h ? new URL(h).hostname : 'missing';
    } catch { return 'malformed'; }
  })();

  // Safe debug log for completion attempt
  console.log(
    '[unlock/%s/complete] token_present=%s referer_host=%s',
    selected,
    !!returnToken,
    refererHost,
  );

  try {
    const { key, alreadyDone, recoveredExisting } = await challenge.completeActiveProviderChallenge(req, selected, returnToken);
    if (recoveredExisting && key && !alreadyDone) {
      req.session.generatedKey = key;
      req.session.generatedKeyRecovery = true;
    }
    if (alreadyDone && req.session.generatedKey) {
      return res.redirect('/key/result');
    }
    if (alreadyDone) {
      safeFlash(req, 'error', messageFor('PROVIDER_CHALLENGE_ALREADY_USED'));
      return res.redirect('/license');
    }

    console.log('[unlock/%s/complete] status=success referer_host=%s', selected, refererHost);
    req.session.generatedKey = key;
    req.session.generatedKeyAt = Date.now();
    delete req.session.pendingChallenge;
    delete req.session.pendingProvider;
    delete req.session.pendingSignedChallenge;
    delete req.session.activeAdChallengeId;
    return res.redirect('/key/result');
  } catch (err) {
    const code = codeFromError(err, 'PROVIDER_RETURN_UNVERIFIED');
    logSafeError(`unlock/${selected}/complete`, code, err);
    safeFlash(req, 'error', messageFor(code));
    return res.redirect('/license');
  }
}

const LINKVERTISE_REASON_TO_CODE = Object.freeze({
  linkvertise_not_configured: 'PROVIDER_NOT_CONFIGURED',
  missing_hash: 'PROVIDER_RETURN_TOKEN_MISSING',
  bad_hash_format: 'PROVIDER_RETURN_TOKEN_INVALID',
  api_timeout: 'PROVIDER_RETURN_UNVERIFIED',
  api_error: 'PROVIDER_RETURN_UNVERIFIED',
  api_false: 'PROVIDER_RETURN_UNVERIFIED',
  api_invalid_token: 'PROVIDER_NOT_CONFIGURED',
  api_invalid_response: 'PROVIDER_RETURN_UNVERIFIED',
  success: 'success',
});

const LOOTLABS_REASON_TO_CODE = Object.freeze({
  lootlabs_not_configured: 'PROVIDER_NOT_CONFIGURED',
  missing_destination: 'PROVIDER_RETURN_UNVERIFIED',
  api_timeout: 'PROVIDER_RETURN_UNVERIFIED',
  api_error: 'PROVIDER_RETURN_UNVERIFIED',
  api_invalid_token: 'PROVIDER_NOT_CONFIGURED',
  api_invalid_response: 'PROVIDER_RETURN_UNVERIFIED',
  api_type_error: 'PROVIDER_RETURN_UNVERIFIED',
  success: 'success',
});

/**
 * Linkvertise Target-Link Anti-Bypass completion handler.
 *
 * Flow:
 *  1. require logged-in session
 *  2. require `hash` query param + format check
 *  3. load active linkvertise challenge from session (activeAdChallengeId)
 *  4. verify ownership/Discord/provider/status/expiry/no-key
 *  5. call linkvertise Anti-Bypass API
 *  6. only on TRUE: atomically consume challenge, generate one key, attach to history
 */
async function handleLinkvertiseComplete(req, res) {
  const requestId = require('crypto').randomBytes(6).toString('hex');
  const hash = typeof req.query.hash === 'string' ? req.query.hash : '';
  const safePrefix = hash && hash.length >= 8 ? hash.slice(0, 8) : '';

  console.log(
    '[unlock/linkvertise/complete] rid=%s hash_present=%s hash_prefix=%s',
    requestId, !!hash, safePrefix,
  );

  if (!linkvertise.isLinkvertiseConfigured()) {
    console.warn('[unlock/linkvertise/complete] rid=%s reason=linkvertise_not_configured', requestId);
    safeFlash(req, 'error', messageFor('PROVIDER_NOT_CONFIGURED'));
    return res.redirect('/license');
  }

  if (!hash) {
    console.warn('[unlock/linkvertise/complete] rid=%s reason=missing_hash', requestId);
    safeFlash(req, 'error', messageFor('PROVIDER_RETURN_TOKEN_MISSING'));
    return res.redirect('/license');
  }
  if (!linkvertise.isValidHashFormat(hash)) {
    console.warn('[unlock/linkvertise/complete] rid=%s reason=bad_hash_format hash_prefix=%s', requestId, safePrefix);
    safeFlash(req, 'error', messageFor('PROVIDER_RETURN_TOKEN_INVALID'));
    return res.redirect('/license');
  }

  let row;
  try {
    row = await challenge.getActiveLinkvertiseChallenge(req, hash);
  } catch (err) {
    const code = codeFromError(err, 'PROVIDER_CHALLENGE_MISSING');
    const failureReason = challenge.mapErrorToFailureReason(err);
    logGenerateKeyReturn({
      ...accountIdsFromReq(req),
      provider: 'linkvertise',
      attemptId: null,
      stateValid: !!hash,
      attemptFound: false,
      attemptStatus: null,
      keyIssued: false,
      failureReason,
    });
    console.warn('[unlock/linkvertise/complete] rid=%s reason=session_or_challenge code=%s failure=%s', requestId, code, failureReason);
    logSafeError('unlock/linkvertise/complete', code, err);
    flashBlock(req, code, { blockReason: codeToBlockReason(code), failureReason });
    return res.redirect('/license');
  }

  const verification = await linkvertise.verifyLinkvertiseAntiBypass({ hash, requestId });
  console.log(
    '[unlock/linkvertise/complete] rid=%s result=%s ok=%s',
    requestId, verification.reason, verification.ok,
  );

  if (!verification.ok) {
    const code = LINKVERTISE_REASON_TO_CODE[verification.reason] || 'PROVIDER_RETURN_UNVERIFIED';
    flashBlock(req, code, { blockReason: 'provider_rejected', providerVerifyReason: verification.reason });
    return res.redirect('/license');
  }

  try {
    await challenge.bindLinkvertiseHash(row.id, hash);
    if (row.status === 'key_generated') {
      return finishProviderCompletion(req, res, {
        key: await challenge.recoverGeneratedKeyFromChallenge(row),
        alreadyDone: true,
        challengeRow: row,
      }, { provider: 'linkvertise', challengeRow: row });
    }
    const result = await challenge.completeAdAndGenerateKey(row);
    console.log('[unlock/linkvertise/complete] rid=%s status=success', requestId);
    return finishProviderCompletion(req, res, result, { provider: 'linkvertise', challengeRow: row });
  } catch (err) {
    const code = codeFromError(err, 'KEY_GENERATION_FAILED');
    console.warn('[unlock/linkvertise/complete] rid=%s reason=consume_failed code=%s', requestId, code);
    logSafeError('unlock/linkvertise/complete', code, err);
    flashBlock(req, code, { blockReason: codeToBlockReason(code) });
    return res.redirect('/license');
  }
}

/**
 * LootLabs Redirect API / Anti-Bypass completion handler.
 *
 * Flow:
 *  1. require logged-in session
 *  2. require `s` query param (HMAC-signed state created at /key/provider/lootlabs)
 *  3. verifyChallenge(s) → {cid, p:'lootlabs', exp}
 *  4. load challenge by cid, verify session/ownership/Discord/provider/status/expiry/no-key
 *  5. atomically consume challenge and generate exactly one key
 *
 * The signed state is the only client-visible identifier. The challenge status
 * machine (pending_ad → ad_completed → key_generated) provides the one-time
 * consumption guarantee, so a replayed `?s=` returns ALREADY_USED.
 */
async function handleLootLabsComplete(req, res) {
  const requestId = require('crypto').randomBytes(6).toString('hex');
  const signedState = typeof req.query.s === 'string' ? req.query.s : '';
  const safePrefix = lootlabs.safeSignedStatePrefix(signedState);

  console.log(
    '[unlock/lootlabs/complete] rid=%s state_present=%s state_prefix=%s',
    requestId, !!signedState, safePrefix,
  );

  if (!lootlabs.isLootLabsConfigured()) {
    console.warn('[unlock/lootlabs/complete] rid=%s reason=lootlabs_not_configured', requestId);
    safeFlash(req, 'error', messageFor('PROVIDER_NOT_CONFIGURED'));
    return res.redirect('/license');
  }

  if (!signedState) {
    console.warn('[unlock/lootlabs/complete] rid=%s reason=missing_state', requestId);
    safeFlash(req, 'error', messageFor('PROVIDER_RETURN_TOKEN_MISSING'));
    return res.redirect('/license');
  }

  let decoded;
  try {
    decoded = verifyChallenge(signedState);
  } catch (err) {
    console.warn('[unlock/lootlabs/complete] rid=%s reason=verify_threw error=%s', requestId, (err && err.code) || 'unknown');
    safeFlash(req, 'error', messageFor('PROVIDER_RETURN_TOKEN_INVALID'));
    return res.redirect('/license');
  }
  if (!decoded || decoded.p !== 'lootlabs' || !decoded.cid) {
    console.warn('[unlock/lootlabs/complete] rid=%s reason=bad_state_format', requestId);
    safeFlash(req, 'error', messageFor('PROVIDER_RETURN_TOKEN_INVALID'));
    return res.redirect('/license');
  }
  if (typeof decoded.exp === 'number' && Date.now() > decoded.exp) {
    console.warn('[unlock/lootlabs/complete] rid=%s reason=state_expired', requestId);
    safeFlash(req, 'error', messageFor('PROVIDER_RETURN_TOKEN_EXPIRED'));
    return res.redirect('/license');
  }

  let row;
  try {
    row = await challenge.getActiveLootLabsChallengeById(decoded.cid, req);
  } catch (err) {
    const code = codeFromError(err, 'PROVIDER_CHALLENGE_MISSING');
    const failureReason = challenge.mapErrorToFailureReason(err);
    logGenerateKeyReturn({
      ...accountIdsFromReq(req),
      provider: 'lootlabs',
      attemptId: decoded.cid || null,
      stateValid: true,
      attemptFound: false,
      keyIssued: false,
      failureReason,
    });
    console.warn('[unlock/lootlabs/complete] rid=%s reason=session_or_challenge code=%s failure=%s', requestId, code, failureReason);
    logSafeError('unlock/lootlabs/complete', code, err);
    flashBlock(req, code, { blockReason: codeToBlockReason(code), failureReason });
    return res.redirect('/license');
  }

  try {
    if (row.status === 'key_generated') {
      return finishProviderCompletion(req, res, {
        key: await challenge.recoverGeneratedKeyFromChallenge(row),
        alreadyDone: true,
        challengeRow: row,
      }, { provider: 'lootlabs', challengeRow: row });
    }
    const result = await challenge.completeAdAndGenerateKey(row);
    console.log('[unlock/lootlabs/complete] rid=%s status=success', requestId);
    return finishProviderCompletion(req, res, result, { provider: 'lootlabs', challengeRow: row });
  } catch (err) {
    const code = codeFromError(err, 'KEY_GENERATION_FAILED');
    console.warn('[unlock/lootlabs/complete] rid=%s reason=consume_failed code=%s', requestId, code);
    logSafeError('unlock/lootlabs/complete', code, err);
    flashBlock(req, code, { blockReason: codeToBlockReason(code) });
    return res.redirect('/license');
  }
}

router.get('/', (req, res) => {
  if (req.session.user) return res.redirect('/dashboard');
  return res.render('home', {
    title: 'DENG Tool - Roblox Automation & Stat Tracker',
    metaDescription: 'DENG Tool is a Roblox automation and stat-tracking suite with live Fish It inventory, Rejoin agents, licenses, and monitoring in one dashboard.',
  });
});

router.get('/login', (req, res) => {
  if (req.session.user) return res.redirect('/dashboard');
  return res.render('login', { title: 'Sign In - DENG Tool' });
});

router.get('/health', (_req, res) => {
  res.json({
    status: 'ok',
    service: 'deng-tool-site',
    port: parseInt(process.env.TOOL_SITE_PORT || '8791', 10),
    timestamp: new Date().toISOString(),
  });
});

router.get('/auth/discord', (req, res) => {
  let authUrl;
  try {
    authUrl = buildDiscordAuthUrl(req);
  } catch (err) {
    console.error('[auth/discord]', err.message || err);
    safeFlash(req, 'error', 'Discord login is not configured.');
    return res.redirect(LOGIN_HOME);
  }
  return req.session.save((saveErr) => {
    if (saveErr) {
      console.error('[auth/discord] category=session_save_failed error=%s', saveErr.message);
      safeFlash(req, 'error', 'Session error. Please try again.');
      return res.redirect(LOGIN_HOME);
    }
    return res.redirect(authUrl);
  });
});

router.get('/auth/discord/callback', authLimiter, async (req, res) => {
  const { code, state, error: oauthError } = req.query;

  if (oauthError) {
    console.warn('[auth/discord/callback] category=oauth_denied discord_error=%s', String(oauthError).slice(0, 64));
    safeFlash(req, 'error', `Discord denied access: ${oauthError}`);
    return res.redirect(LOGIN_HOME);
  }

  const storedState = req.session.oauthState;
  delete req.session.oauthState;

  if (!code) {
    console.warn('[auth/discord/callback] category=code_missing state_present=%s', !!storedState);
    safeFlash(req, 'error', 'Invalid OAuth response. Please try again.');
    return res.redirect(LOGIN_HOME);
  }
  if (!storedState) {
    console.warn('[auth/discord/callback] category=state_missing code_present=true');
    safeFlash(req, 'error', 'Session expired. Please try again.');
    return res.redirect(LOGIN_HOME);
  }
  if (String(state) !== storedState) {
    console.warn('[auth/discord/callback] category=state_mismatch code_present=true');
    safeFlash(req, 'error', 'Invalid OAuth state. Please try again.');
    return res.redirect(LOGIN_HOME);
  }

  // Step 1: Exchange code for access token
  let tokens;
  try {
    tokens = await exchangeDiscordCode(String(code));
  } catch (_err) {
    // Structured error details are already logged inside exchangeDiscordCode.
    safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
    return res.redirect(LOGIN_HOME);
  }

  // Step 2: Fetch Discord user identity
  let discordUser;
  try {
    discordUser = await fetchDiscordUser(tokens.access_token);
  } catch (err) {
    const status = (err.response && err.response.status) || 'unknown';
    console.error('[auth/discord/callback] category=user_fetch_failed http_status=%s', status);
    safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
    return res.redirect(LOGIN_HOME);
  }

  // Step 3: Create or update portal user
  let siteUser;
  try {
    siteUser = await upsertDiscordUser(discordUser, tokens);
  } catch (err) {
    console.error('[auth/discord/callback] category=site_user_upsert_failed error=%s', err.message);
    safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
    return res.redirect(LOGIN_HOME);
  }

  // Step 4: Regenerate session and redirect
  return new Promise((resolve) => {
    req.session.regenerate((regenErr) => {
      if (regenErr) {
        console.error('[auth/discord/callback] category=session_regenerate_failed error=%s', regenErr.message);
        safeFlash(req, 'error', 'Session error. Please try again.');
        res.redirect(LOGIN_HOME);
        return resolve();
      }
      req.session.user  = toSessionUser(siteUser);
      req.session.flash = { success: `Welcome, ${req.session.user.username}!` };
      req.session.save((saveErr) => {
        if (saveErr) {
          console.error('[auth/discord/callback] category=session_save_failed error=%s', saveErr.message);
        }
        res.redirect('/dashboard');
        resolve();
      });
    });
  });
});

router.post('/auth/logout', (req, res) => {
  if (!verifyCsrf(req)) return res.redirect('/login');
  req.session.destroy(() => {
    res.clearCookie('deng_sid');
    res.redirect('/');
  });
});

router.get('/dashboard', requireLogin, repairSiteUser, async (req, res) => {
  try {
    const history = await loadHistory(req.session.user.id, 8, discordOwnerId(req));
    res.render('dashboard', {
      title: 'Dashboard - DENG Tool',
      history,
      stats: summarizeHistory(history),
      maskKeyRow,
      fullKeyRow,
      friendlyStatus,
      providerLabel,
      formatWibTimestamp,
    });
  } catch (err) {
    console.error('[dashboard]', err.message || err);
    res.render('dashboard', {
      title: 'Dashboard - DENG Tool',
      history: [],
      stats: summarizeHistory([]),
      maskKeyRow,
      fullKeyRow,
      friendlyStatus,
      providerLabel,
      formatWibTimestamp,
    });
  }
});

router.get('/fishit', requireLogin, repairSiteUser, (req, res) => {
  res.render('fishit', {
    title: 'Stats — DENG Tool',
    activePage: 'fishit',
  });
});

router.get('/stats', requireLogin, repairSiteUser, (req, res) => {
  res.redirect(301, '/fishit');
});

router.get('/api/license/eligibility', requireLogin, repairSiteUser, async (req, res) => {
  try {
    const eligibility = await licenseEligibility.getLicenseGenerationEligibility({
      discordUserId: discordOwnerId(req),
      siteUserId: req.session.user.id,
      skipProviderCheck: true,
    });
    return res.json(eligibility);
  } catch (err) {
    console.error('[api/license/eligibility]', err.message || err);
    return res.status(500).json({
      canGenerate: false,
      blockReason: 'server_error',
      message: licenseEligibility.messageForBlockReason('server_error'),
    });
  }
});

router.get('/api/admin/license/eligibility', requireSiteAdmin, async (req, res) => {
  try {
    const discordUserId = String(req.query.discord_user_id || '').trim();
    if (!discordUserId) {
      return res.status(400).json({ error: 'discord_user_id required' });
    }
    const siteUserId = await resolveSiteUserIdForDiscord(discordUserId);
    const eligibility = await licenseEligibility.getLicenseGenerationEligibility({
      discordUserId,
      siteUserId,
      skipProviderCheck: false,
    });
    const attempt = await challenge.getGenerationAttemptDiagnostic({
      discordUserId,
      siteUserId,
    });
    return res.json({
      ...eligibility,
      ...attempt,
      providerAttemptStatus: attempt.attemptStatus,
      cooldownRemainingSeconds: eligibility.remainingSeconds || 0,
    });
  } catch (err) {
    console.error('[api/admin/license/eligibility]', err.message || err);
    return res.status(500).json({
      canGenerate: false,
      blockReason: 'server_error',
      message: licenseEligibility.messageForBlockReason('server_error'),
    });
  }
});

async function handleAdminLicenseDebugUser(req, res) {
  try {
    const username = String(req.query.username || '').trim();
    const discordUserId = String(req.query.discord_user_id || '').trim();
    if (!username && !discordUserId) {
      return res.status(400).json({ ok: false, error: 'username_or_discord_user_id_required' });
    }
    const report = await buildLicenseUserDebugReport({ username, discordUserId });
    return res.json(report);
  } catch (err) {
    console.error('[admin/license/debug-user]', err.message || err);
    return res.status(500).json({ ok: false, error: 'server_error', message: err.message || 'debug failed' });
  }
}

router.get('/api/admin/license/debug-user', requireSiteAdmin, handleAdminLicenseDebugUser);
router.get('/admin/license/debug-user', requireSiteAdmin, handleAdminLicenseDebugUser);

router.get('/license', requireLogin, repairSiteUser, async (req, res) => {
  try {
    const history = await loadHistory(req.session.user.id, 20, discordOwnerId(req), { activeOnly: false });
    const activeHistory = licenseService.filterActiveLicenses(history);
    const eligibility = await licenseEligibility.getLicenseGenerationEligibility({
      discordUserId: discordOwnerId(req),
      siteUserId: req.session.user.id,
      skipProviderCheck: true,
    });
    const cooldown = {
      allowed: eligibility.blockReason !== 'cooldown_active',
      secondsLeft: eligibility.blockReason === 'cooldown_active' ? eligibility.remainingSeconds : 0,
      cooldownUntil: eligibility.cooldownUntil,
    };
    const existingUnused = eligibility.activeUnredeemedCount > 0
      ? await licenseService.findActiveUnredeemedKey({
        discordUserId: discordOwnerId(req),
        siteUserId: req.session.user.id,
      })
      : null;
    const recoveredExistingKey = req.session.recoveredExistingKey || null;
    delete req.session.recoveredExistingKey;
    res.render('license', {
      title: 'My License - DENG Tool',
      history,
      stats: summarizeHistory(activeHistory),
      cooldown,
      eligibility,
      existingUnusedKey: existingUnusedPayload(existingUnused) || recoveredExistingKey,
      maskKeyRow,
      fullKeyRow,
      friendlyStatus,
      providerLabel,
      formatWibTimestamp,
    });
  } catch (err) {
    console.error('[license]', err.message || err);
    res.render('license', {
      title: 'My License - DENG Tool',
      history: [],
      stats: summarizeHistory([]),
      cooldown: { allowed: true, secondsLeft: 0 },
      eligibility: { canGenerate: true, blockReason: null },
      existingUnusedKey: null,
      maskKeyRow,
      fullKeyRow,
      friendlyStatus,
      providerLabel,
      formatWibTimestamp,
    });
  }
});

router.get('/key/provider', requireLogin, repairSiteUser, async (req, res) => {
  let challengeId = req.session.pendingChallenge;
  if (!challengeId) {
    const recovered = await challenge.findLatestResumableChallengeForUser(req);
    if (recovered) {
      challengeId = recovered.id;
      req.session.pendingChallenge = recovered.id;
      req.session.activeAdChallengeId = recovered.id;
    }
  }
  if (!challengeId) {
    logGenerateKeyStart({
      ...accountIdsFromReq(req),
      provider: null,
      existingActiveKeyFound: false,
      createdAttemptId: null,
      redirectUrlCreated: false,
      failureReason: 'missing_attempt_id',
      storageMissing: true,
    });
    flashBlock(req, 'PROVIDER_CHALLENGE_MISSING', {
      blockReason: 'provider_attempt_invalid',
      failureReason: 'missing_attempt_id',
    });
    return res.redirect('/license');
  }
  return res.render('choose_provider', {
    title: 'Choose Unlock Method - DENG Tool',
    challengeId,
    providers: enabledProviders(),
    providerLabel,
  });
});

router.post('/api/key/start', requireLogin, generateLimiter, handleKeyStart);
router.post('/license/generate', requireLogin, generateLimiter, handleKeyStart);
router.post('/api/key/provider', requireLogin, repairSiteUser, handleProvider);
router.post('/api/key/provider/:provider', requireLogin, repairSiteUser, handleProvider);
router.post('/license/provider', requireLogin, repairSiteUser, handleProvider);
router.post('/license/provider/:provider', requireLogin, repairSiteUser, handleProvider);
router.post('/key/provider', requireLogin, repairSiteUser, handleProvider);
router.post('/key/provider/:provider', requireLogin, repairSiteUser, handleProvider);

router.get('/unlock/lootlabs', requireLogin, repairSiteUser, (req, res) => handleUnlock(req, res, 'lootlabs'));
router.get('/unlock/linkvertise', requireLogin, repairSiteUser, (req, res) => handleUnlock(req, res, 'linkvertise'));

// Legacy Linkvertise Full Script start route — kept reachable only to emit a
// styled failure so any bookmarked URL cannot bypass anti-bypass verification.
router.get('/unlock/linkvertise/start', requireLogin, repairSiteUser, (req, res) => {
  safeFlash(req, 'error', messageFor('PROVIDER_CHALLENGE_MISSING'));
  return res.redirect('/license');
});

router.get('/unlock/lootlabs/complete', requireLogin, repairSiteUser, handleLootLabsComplete);
router.get('/unlock/linkvertise/complete', requireLogin, repairSiteUser, handleLinkvertiseComplete);

router.get('/unlock/linkvertise/done', requireLogin, (_req, res) => {
  res.redirect('/license');
});

router.get('/key/result', requireLogin, repairSiteUser, async (req, res) => {
  let key = req.session.generatedKey;
  if (!key && req.session.lastCompletedChallengeId) {
    const row = await challenge.loadChallengeById(req.session.lastCompletedChallengeId);
    key = await challenge.recoverGeneratedKeyFromChallenge(row);
    if (key) req.session.generatedKey = key;
  }
  if (!key) {
    const attempt = await challenge.getLatestProviderAttemptStatus(req.session.user.id);
    if (attempt?.challengeId) {
      const row = await challenge.loadChallengeById(attempt.challengeId);
      if (row?.status === 'key_generated') {
        key = await challenge.recoverGeneratedKeyFromChallenge(row);
        if (key) req.session.generatedKey = key;
      }
    }
  }
  if (!key) {
    safeFlash(req, 'error', 'No key available. Please generate a new one.');
    return res.redirect('/license');
  }
  const recoveredExisting = Boolean(req.session.generatedKeyRecovery);
  delete req.session.generatedKeyRecovery;
  res.render('key_result', { title: 'Your Key - DENG Tool', key, recoveredExisting });
});

router.get('/api/key/attempt/status', requireLogin, repairSiteUser, async (req, res) => {
  try {
    const siteUserId = req.session.user.id;
    const discordUserId = discordOwnerId(req);
    const attempt = await challenge.getLatestProviderAttemptStatus(siteUserId);
    const eligibility = await licenseEligibility.getLicenseGenerationEligibility({
      discordUserId,
      siteUserId,
      skipProviderCheck: true,
    });
    const diagnostic = await challenge.getGenerationAttemptDiagnostic({
      discordUserId,
      siteUserId,
      challengeId: attempt.challengeId,
    });
    return res.json({
      ...diagnostic,
      discordUserId,
      canGenerate: eligibility.canGenerate,
      blockReason: eligibility.blockReason,
      activeUnredeemedKeyCount: eligibility.activeUnredeemedCount,
      cooldownRemainingSeconds: eligibility.remainingSeconds || 0,
      providerAttemptStatus: attempt.status,
    });
  } catch (err) {
    console.error('[api/key/attempt/status]', err.message || err);
    return res.status(500).json({ error: 'server_error', message: 'Could not load attempt status.' });
  }
});

async function handlePublicStats(_req, res) {
  try {
    const payload = await licenseService.getPublicStats();
    res.set('Cache-Control', 'public, max-age=10, stale-while-revalidate=10');
    return res.json(payload);
  } catch (err) {
    // Log enough detail for ops to diagnose schema/connectivity issues,
    // but never echo the underlying error (which may contain SQL,
    // table names, or supabase URLs) back to the browser.
    console.error(
      '[api/public-stats] failed: code=%s status=%s message=%s',
      err?.code || 'unknown',
      err?.status || 503,
      err?.message || String(err),
    );
    return res.status(503).json({
      error: 'public_stats_unavailable',
      message: 'Public stats are unavailable.',
    });
  }
}

router.get('/api/public-stats', handlePublicStats);
router.get('/api/stats/public', handlePublicStats);

router.get('/api/license/me', requireLogin, repairSiteUser, async (req, res) => {
  try {
    const history = await loadHistory(req.session.user.id, 20, discordOwnerId(req), { activeOnly: false });
    const stats = summarizeHistory(licenseService.filterActiveLicenses(history));
    res.json({
      account: req.session.user,
      stats,
      history: history.map((row) => ({
        id: row.id,
        status: friendlyStatus(row),
        lifecycle_status: row.lifecycle_status,
        display_status: row.display_status,
        is_unredeemed: row.is_unredeemed,
        is_redeemed: row.is_redeemed,
        is_unbound: row.is_unbound,
        is_bound: row.is_bound,
        is_expired: row.is_expired,
        is_revoked: row.is_revoked,
        blocks_generation: row.blocks_generation,
      })),
    });
  } catch {
    res.status(500).json({ error: 'license_summary_failed' });
  }
});

router.get('/api/license/history', requireLogin, repairSiteUser, async (req, res) => {
  try {
    const history = await loadHistory(req.session.user.id, 20, discordOwnerId(req), { activeOnly: false });
    res.json({
      history: history.map((row) => ({
        id: row.id,
        key: fullKeyRow(row),
        masked_key: maskKeyRow(row),
        status: friendlyStatus(row),
        lifecycle_status: row.lifecycle_status,
        display_status: row.display_status,
        is_unredeemed: row.is_unredeemed,
        is_redeemed: row.is_redeemed,
        is_unbound: row.is_unbound,
        is_bound: row.is_bound,
        is_expired: row.is_expired,
        is_revoked: row.is_revoked,
        blocks_generation: row.blocks_generation,
        provider: providerLabel(row.provider),
        created_at: row.created_at,
        created_at_formatted: formatWibTimestamp(row.created_at),
        key_expires_at: row.key_expires_at,
        key_expires_at_formatted: formatWibTimestamp(row.key_expires_at),
        device: row.device_display || null,
      })),
    });
  } catch {
    res.status(500).json({ error: 'license_history_failed' });
  }
});

router.get('/api/license/resettable', requireLicenseApiLogin, repairSiteUser, licenseActionLimiter, async (req, res) => {
  try {
    const owner = discordOwnerId(req);
    if (!owner) return res.status(401).json({ error: 'auth_required', message: messageFor('AUTH_REQUIRED') });
    const rows = await licenseService.getActiveUserLicenses(owner, { limit: 200 });
    res.json({
      keys: rows.map((row) => ({
        id: row.id,
        key: fullKeyRow(row),
        status: friendlyStatus(row),
        lifecycle_status: row.lifecycle_status,
        display_status: row.display_status,
        is_unredeemed: row.is_unredeemed,
        is_redeemed: row.is_redeemed,
        is_unbound: row.is_unbound,
        is_bound: row.is_bound,
        is_expired: row.is_expired,
        is_revoked: row.is_revoked,
        blocks_generation: row.blocks_generation,
        device_status: row.active_binding ? 'Bound To A Device' : 'No Device Linked',
        device_label: row.device_display || null,
        can_reset: Boolean(row.active_binding),
        reason: row.active_binding ? null : 'No Resettable Keys Found.',
      })),
    });
  } catch (err) {
    handleLicenseApiError(res, err);
  }
});

router.post('/api/license/reset-hwid', requireLicenseApiLogin, repairSiteUser, licenseActionLimiter, async (req, res) => {
  if (!verifyCsrf(req)) return res.status(403).json({ error: 'invalid_csrf', message: 'Invalid request token.' });
  try {
    const owner = discordOwnerId(req);
    if (!owner) return res.status(401).json({ error: 'auth_required', message: messageFor('AUTH_REQUIRED') });
    const result = await licenseService.resetLicenseHwid(owner, req.body?.key_id || req.body?.key || '');
    const history = await licenseService.getActiveUserLicenses(owner, { limit: 200 });
    res.json({ ...result, history_count: history.length });
  } catch (err) {
    handleLicenseApiError(res, err, 'reset_hwid_failed');
  }
});

router.post('/api/license/redeem', requireLicenseApiLogin, repairSiteUser, licenseActionLimiter, async (req, res) => {
  if (!verifyCsrf(req)) return res.status(403).json({ error: 'invalid_csrf', message: 'Invalid request token.' });
  try {
    const owner = discordOwnerId(req);
    if (!owner) return res.status(401).json({ error: 'auth_required', message: messageFor('AUTH_REQUIRED') });
    const result = await licenseService.redeemLicenseKey(owner, req.body?.key || '');
    const history = await licenseService.getActiveUserLicenses(owner, { limit: 200 });
    res.json({ ...result, history_count: history.length });
  } catch (err) {
    handleLicenseApiError(res, err, 'redeem_key_failed');
  }
});

router.get('/api/license/download', requireLicenseDownloadLogin, repairSiteUser, licenseActionLimiter, async (req, res) => {
  try {
    const owner = discordOwnerId(req);
    if (!owner) return res.status(401).type('text/plain').send('Please login with Discord first.\n');
    const rows = await licenseService.getActiveUserLicenses(owner, { limit: 500 });
    const username = req.session.user.username || owner;
    const body = licenseService.downloadUserKeys(owner, rows, username);
    const filename = licenseExportFilename(username, owner);
    res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    res.setHeader('Content-Disposition', `attachment; filename*=UTF-8''${encodeURIComponent(filename)}`);
    res.send(body);
  } catch (err) {
    const status = err?.status || 500;
    res.status(status).type('text/plain').send(`${err?.message || 'License export failed.'}\n`);
  }
});

// ───────────────────────────────────────────────────────────────────────────
// DENG Tool: Rejoin APK — public download page + binary serve
// ───────────────────────────────────────────────────────────────────────────
const fs   = require('fs');
const downloadStats = require('./downloadStats');

const APK_RELEASES_DIR = path.join(__dirname, '..', '..', 'releases', 'android');
const IOS_RELEASES_DIR = path.join(__dirname, '..', '..', 'releases', 'ios');
const IOS_FILENAME_RE = /^deng-tool-monitor-ios-v?[A-Za-z0-9._-]+\.ipa$/;

// New canonical filename pattern. Backward-compat: also accept legacy
// `deng-monitor-*.apk` for any old assets that may already be hosted, so
// existing bookmarks/links continue to work. Both still pass through the
// per-file basename + path-prefix traversal defense below.
const APK_FILENAME_NEW_RE    = /^deng-tool-rejoin-apk-v?[A-Za-z0-9._-]+\.apk$/;
const APK_FILENAME_LEGACY_RE = /^deng-monitor-v?[A-Za-z0-9._-]+\.apk$/;

function loadApkManifest() {
  try {
    const file = path.join(APK_RELEASES_DIR, 'latest.json');
    if (!fs.existsSync(file)) return null;
    const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
    return {
      version_name: String(raw.version_name || ''),
      version_code: Number(raw.version_code || 0),
      file_name:    String(raw.file_name || ''),
      sha256:       String(raw.sha256 || ''),
      size_bytes:   Number(raw.size_bytes || 0),
      released_at:  String(raw.released_at || ''),
      changelog:    Array.isArray(raw.changelog) ? raw.changelog.slice(0, 20) : [],
      min_sdk:      Number(raw.min_sdk || 26),
    };
  } catch (err) {
    console.warn('[apk] manifest load failed:', err.message);
    return null;
  }
}

function loadIosManifest() {
  try {
    const file = path.join(IOS_RELEASES_DIR, 'latest.json');
    if (!fs.existsSync(file)) return null;
    const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
    return {
      version_name: String(raw.version_name || '1.0.0'),
      version_code: Number(raw.version_code || 1),
      file_name: String(raw.file_name || ''),
      sha256: String(raw.sha256 || ''),
      size_bytes: Number(raw.size_bytes || 0),
      released_at: String(raw.released_at || ''),
      distribution: String(raw.distribution || 'coming_soon'),
      testflight_url: String(raw.testflight_url || process.env.IOS_TESTFLIGHT_URL || ''),
      app_store_url: String(raw.app_store_url || ''),
    };
  } catch (err) {
    console.warn('[ios] manifest load failed:', err.message);
    return null;
  }
}

function resolveIosDownloadMode(iosManifest) {
  const envMode = String(process.env.IOS_DOWNLOAD_MODE || '').toLowerCase().trim();
  if (envMode === 'testflight' || envMode === 'ipa' || envMode === 'coming_soon') return envMode;
  const dist = String(iosManifest?.distribution || 'coming_soon').toLowerCase();
  if (dist === 'testflight' && (iosManifest?.testflight_url || process.env.IOS_TESTFLIGHT_URL)) {
    return 'testflight';
  }
  if (dist === 'ipa' && iosManifest?.file_name) {
    const target = path.resolve(IOS_RELEASES_DIR, iosManifest.file_name);
    if (fs.existsSync(target)) return 'ipa';
  }
  return 'coming_soon';
}

router.get('/download', (_req, res) => {
  const manifest = loadApkManifest();
  const iosManifest = loadIosManifest();
  const iosMode = resolveIosDownloadMode(iosManifest);
  res.render('download', {
    title: 'DENG Tool Monitor — Download',
    manifest,
    iosManifest,
    iosMode,
    testflightUrl: (iosManifest?.testflight_url || process.env.IOS_TESTFLIGHT_URL || '').trim(),
  });
});

router.get('/app', (_req, res) => res.redirect('/download'));

function setDownloadStatsNoStore(res) {
  res.set('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0, s-maxage=0');
  res.set('Pragma', 'no-cache');
  res.set('Expires', '0');
  res.set('Surrogate-Control', 'no-store');
  res.set('CDN-Cache-Control', 'no-store');
  res.set('Cloudflare-CDN-Cache-Control', 'no-store');
}

router.get('/api/downloads/apk/stats', (_req, res) => {
  setDownloadStatsNoStore(res);
  try {
    const stats = downloadStats.getApkStats();
    return res.json(stats);
  } catch (err) {
    console.warn('[apk] stats failed:', err && err.message ? err.message : err);
    return res.json({ ok: false, latest: null });
  }
});

router.get('/api/downloads/ios/stats', (_req, res) => {
  setDownloadStatsNoStore(res);
  try {
    const stats = downloadStats.getPlatformStats('ios');
    return res.json(stats);
  } catch (err) {
    console.warn('[ios] stats failed:', err && err.message ? err.message : err);
    return res.json({ ok: false, platform: 'ios', latest: null });
  }
});

router.get('/api/downloads/stats', (_req, res) => {
  setDownloadStatsNoStore(res);
  try {
    const stats = downloadStats.getAllStats();
    return res.json(stats);
  } catch (err) {
    console.warn('[downloads] stats failed:', err && err.message ? err.message : err);
    return res.json({ ok: false, android: null, ios: null });
  }
});

// Canonical "latest" alias — reads manifest and redirects to the versioned
// file. Returns a friendly 404 if no APK has been published yet.
router.head('/downloads/deng-tool-rejoin-apk-latest.apk', (_req, res) => {
  setDownloadStatsNoStore(res);
  const manifest = loadApkManifest();
  if (!manifest || !manifest.file_name) {
    return res.status(404).type('text/plain').end();
  }
  const safeName = path.basename(manifest.file_name);
  return res.redirect(302, `/downloads/${encodeURIComponent(safeName)}`);
});

router.get('/downloads/deng-tool-rejoin-apk-latest.apk', (_req, res) => {
  setDownloadStatsNoStore(res);
  const manifest = loadApkManifest();
  if (!manifest || !manifest.file_name) {
    return res.status(404).type('text/plain').send('APK not available yet.\n');
  }
  const safeName = path.basename(manifest.file_name);
  return res.redirect(302, `/downloads/${encodeURIComponent(safeName)}`);
});

// Legacy alias — permanent redirect to the new canonical "latest" URL so
// existing bookmarks keep working.
router.head('/downloads/deng-monitor-latest.apk', (_req, res) => {
  setDownloadStatsNoStore(res);
  return res.redirect(301, '/downloads/deng-tool-rejoin-apk-latest.apk');
});

router.get('/downloads/deng-monitor-latest.apk', (_req, res) => {
  setDownloadStatsNoStore(res);
  return res.redirect(301, '/downloads/deng-tool-rejoin-apk-latest.apk');
});

// ── iOS IPA (private test builds only — no public install without signing) ───
router.get('/downloads/deng-tool-monitor-ios-latest.ipa', (_req, res) => {
  const iosManifest = loadIosManifest();
  if (!iosManifest || !iosManifest.file_name || resolveIosDownloadMode(iosManifest) !== 'ipa') {
    return res.status(404).type('text/plain').send('iOS build not available for direct download.\n');
  }
  const safeName = path.basename(iosManifest.file_name);
  return res.redirect(302, `/downloads/${encodeURIComponent(safeName)}`);
});

router.head('/downloads/:file', (req, res, next) => {
  const raw = String(req.params.file || '');

  if (IOS_FILENAME_RE.test(raw)) {
    const target = path.resolve(IOS_RELEASES_DIR, raw);
    if (!target.startsWith(path.resolve(IOS_RELEASES_DIR) + path.sep)
        && target !== path.resolve(IOS_RELEASES_DIR)) {
      return next();
    }
    if (fs.existsSync(target)) {
      setDownloadStatsNoStore(res);
      res.setHeader('Content-Type', 'application/octet-stream');
      res.setHeader('Content-Disposition', `attachment; filename="${raw}"`);
      res.setHeader('Content-Length', String(fs.statSync(target).size));
      return res.status(200).end();
    }
    return res.status(404).type('text/plain').end();
  }

  const isNew = APK_FILENAME_NEW_RE.test(raw);
  const isLegacy = APK_FILENAME_LEGACY_RE.test(raw);
  if (!isNew && !isLegacy) return next();

  const target = path.resolve(APK_RELEASES_DIR, raw);
  if (!target.startsWith(path.resolve(APK_RELEASES_DIR) + path.sep)
      && target !== path.resolve(APK_RELEASES_DIR)) {
    return next();
  }

  if (fs.existsSync(target)) {
    setDownloadStatsNoStore(res);
    res.setHeader('Content-Type', 'application/vnd.android.package-archive');
    res.setHeader('Content-Disposition', `attachment; filename="${raw}"`);
    res.setHeader('Content-Length', String(fs.statSync(target).size));
    return res.status(200).end();
  }

  if (isLegacy) {
    const suffix = raw.replace(/^deng-monitor-/, '');
    return res.redirect(301, `/downloads/deng-tool-rejoin-apk-${suffix}`);
  }

  return res.status(404).type('text/plain').end();
});

router.get('/downloads/:file', (req, res, next) => {
  const raw = String(req.params.file || '');

  if (IOS_FILENAME_RE.test(raw)) {
    const target = path.resolve(IOS_RELEASES_DIR, raw);
    if (!target.startsWith(path.resolve(IOS_RELEASES_DIR) + path.sep)
        && target !== path.resolve(IOS_RELEASES_DIR)) {
      return next();
    }
    if (fs.existsSync(target)) {
      if (req.method === 'GET') {
        try { downloadStats.recordDownload('ios', raw); } catch (_) { /* non-fatal */ }
      }
      setDownloadStatsNoStore(res);
      res.setHeader('Content-Type', 'application/octet-stream');
      res.setHeader('Content-Disposition', `attachment; filename="${raw}"`);
      return res.sendFile(target);
    }
    return res.status(404).type('text/plain').send('iOS build not found.\n');
  }

  const isNew = APK_FILENAME_NEW_RE.test(raw);
  const isLegacy = APK_FILENAME_LEGACY_RE.test(raw);
  if (!isNew && !isLegacy) return next();

  const target = path.resolve(APK_RELEASES_DIR, raw);
  if (!target.startsWith(path.resolve(APK_RELEASES_DIR) + path.sep)
      && target !== path.resolve(APK_RELEASES_DIR)) {
    return next();
  }

  if (fs.existsSync(target)) {
    if (req.method === 'GET') {
      try { downloadStats.recordDownload('android', raw); } catch (_) { /* non-fatal */ }
    }
    setDownloadStatsNoStore(res);
    res.setHeader('Content-Type', 'application/vnd.android.package-archive');
    res.setHeader('Content-Disposition', `attachment; filename="${raw}"`);
    return res.sendFile(target);
  }

  if (isLegacy) {
    const suffix = raw.replace(/^deng-monitor-/, '');
    return res.redirect(301, `/downloads/deng-tool-rejoin-apk-${suffix}`);
  }

  return res.status(404).type('text/plain').send('APK not found.\n');
});

module.exports = router;
module.exports.buildLicenseUserDebugReport = buildLicenseUserDebugReport;
module.exports.resolveServerCommit = resolveServerCommit;
