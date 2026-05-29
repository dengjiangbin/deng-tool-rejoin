'use strict';
/**
 * HTTP routes for the DENG Tool portal.
 */
const express = require('express');
const rateLimit = require('express-rate-limit');

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
const { formatWibTimestamp, licenseExportFilename } = require('./licenseFormat');
const linkvertise = require('./providers/linkvertise');
const lootlabs = require('./providers/lootlabs');
const { signChallenge, verifyChallenge } = require('./crypto');

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
  EXISTING_UNUSED_KEY: 'You already have an unused key. Copy or redeem this key before generating another.',
  TOO_MANY_ATTEMPTS: 'Too many key generation attempts. Please wait before trying again.',
  CHALLENGE_TABLE_MISSING: 'Key generation database is not ready yet.',
  DB_FOREIGN_KEY_FAILED: 'Could not prepare your license account. Please try again.',
  DB_PERMISSION_DENIED: 'Key generation database permission error.',
  SITE_USER_UPSERT_FAILED: 'Could not prepare your license account. Please try again.',
  CHALLENGE_INSERT_FAILED: 'Could not start key generation. Please try again.',
  PROVIDER_NOT_CONFIGURED: 'This ad provider is not configured yet.',
  PROVIDER_CHALLENGE_MISSING: 'Please start key generation again.',
  PROVIDER_CHALLENGE_EXPIRED: 'This key generation session expired. Please start again.',
  PROVIDER_CHALLENGE_OWNER_MISMATCH: 'Please start key generation again.',
  PROVIDER_CHALLENGE_ALREADY_USED: 'Please start key generation again.',
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
  return err && err.code && SAFE_MESSAGES[err.code] ? err.code : fallback;
}

function messageFor(code) {
  return SAFE_MESSAGES[code] || SAFE_MESSAGES.UNEXPECTED_ERROR;
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

function discordOwnerId(req) {
  return String(req.session?.user?.discord_user_id || '').trim();
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

    const existingUnused = await licenseService.findActiveUnredeemedKey({
      discordUserId: discordOwnerId(req),
      siteUserId: user.id,
    });
    if (existingUnused) {
      const payload = existingUnusedPayload(existingUnused);
      if (wantsJson(req)) {
        return res.status(200).json({
          status: 'existing_unused_key',
          existing_key: payload,
          message: payload.message,
        });
      }
      req.session.recoveredExistingKey = payload;
      safeFlash(req, 'success', payload.message);
      return res.redirect(303, '/license');
    }

    // Check max active key limit before starting an ad challenge
    const limitResult = await licenseService.canUserReceiveNewKey(
      discordOwnerId(req), user.id
    );
    if (!limitResult.allowed) {
      if (wantsJson(req)) {
        return res.status(429).json({
          error: 'KEY_LIMIT_REACHED',
          message: `Key Limit Reached. You have ${limitResult.activeCount} / ${limitResult.maxKeys} active keys. Ask an admin if you need a higher limit.`,
          activeCount: limitResult.activeCount,
          maxKeys: limitResult.maxKeys,
        });
      }
      req.session.flash = {
        error: `Key Limit Reached. You have ${limitResult.activeCount} / ${limitResult.maxKeys} active keys. Ask an admin if you need a higher limit.`,
        keyLimitReached: true,
        activeCount: limitResult.activeCount,
        maxKeys: limitResult.maxKeys,
      };
      return res.redirect('/license');
    }

    if (enabledProviders().length === 0) {
      const err = new Error('No enabled ad providers');
      err.code = 'NO_PROVIDER_CONFIGURED';
      throw err;
    }

    const { allowed, secondsLeft } = await challenge.checkCooldown(user.id);
    if (!allowed) {
      if (wantsJson(req)) {
        return res.status(429).json({
          error: 'COOLDOWN_ACTIVE',
          message: messageFor('COOLDOWN_ACTIVE'),
          secondsLeft,
        });
      }
      req.session.flash = {
        error: messageFor('COOLDOWN_ACTIVE'),
        cooldown: secondsLeft,
      };
      return res.redirect('/license');
    }

    const row = await challenge.createChallenge(req, user);
    req.session.pendingChallenge = row.id;

    if (wantsJson(req)) return res.json({ challenge_id: row.id, status: row.status });
    return res.render('choose_provider', {
      title: 'Choose Unlock Method - DENG Tool',
      challengeId: row.id,
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
  const challengeId = String(req.body.challenge_id || req.session.pendingChallenge || '');
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
  if (!challengeId || challengeId !== req.session.pendingChallenge) {
    const code = 'PROVIDER_CHALLENGE_MISSING';
    if (wantsJson(req)) return res.status(400).json({ error: code, message: messageFor(code) });
    safeFlash(req, 'error', messageFor(code));
    return res.redirect('/license');
  }

  try {
    const row = await challenge.selectProvider(challengeId, provider, req, user);
    const providerCfg = getProviderConfig(provider);

    let redirectUrl;
    let returnTokenLen = 0;

    if (provider === 'linkvertise') {
      // Linkvertise Target-Link Anti-Bypass flow: no signed return token,
      // verification happens server-side using the hash that Linkvertise
      // appends to the configured callback URL.
      const targetLinkUrl = linkvertise.getLinkvertiseTargetLinkUrl();
      const callbackUrl = linkvertise.getLinkvertiseCallbackUrl();
      await challenge.markLinkvertisePendingById(row.id, req, user, {
        targetLinkUrl,
        callbackUrl,
      });
      redirectUrl = targetLinkUrl;
      req.session.activeAdChallengeId = row.id;
    } else if (provider === 'lootlabs') {
      // LootLabs Redirect API / Anti-Bypass flow:
      //   1. Sign a one-time state {cid, provider, exp}.
      //   2. Build the DENG callback URL with `?s=<signed_state>`.
      //   3. Encrypt that URL server-side through LootLabs' encrypt API
      //      (API token sent in Authorization header, never in URL/logs).
      //   4. Append `&data=<encrypted>` to the canonical lootdest.org link
      //      WITHOUT touching the shortlink id.
      const ttlMs = 30 * 60 * 1000; // 30 minutes
      const signedState = signChallenge(row.id, 'lootlabs', Date.now() + ttlMs);
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

      await challenge.markLootLabsPendingById(row.id, req, user, {
        baseLink,
        callbackPath: '/unlock/lootlabs/complete',
      });

      redirectUrl = startUrl;
      req.session.activeAdChallengeId = row.id;
      returnTokenLen = 0; // no plaintext return token in the redirect
    } else {
      const started = await challenge.markPendingAdById(row.id, req, user, providerCfg.monetizedUrl);
      redirectUrl = providerRedirectUrl(providerCfg, started.return_token);
      returnTokenLen = (started.return_token || '').length;
    }

    req.session.pendingProvider = provider;

    // Safe debug log: URL host only (never full signed token, never encrypted data, never API token)
    let redirectHost = '';
    try { redirectHost = new URL(redirectUrl).hostname; } catch {}
    console.log(
      '[key/provider] provider=%s challenge_prefix=%s url_host=%s token_len=%d status=303',
      provider,
      String(challengeId).slice(0, 8),
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
    logSafeError('api/key/provider', code, err);
    if (wantsJson(req)) return res.status(400).json({ error: code, message: messageFor(code) });
    safeFlash(req, 'error', messageFor(code));
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
    row = await challenge.getActiveLinkvertiseChallenge(req);
  } catch (err) {
    const code = codeFromError(err, 'PROVIDER_CHALLENGE_MISSING');
    console.warn('[unlock/linkvertise/complete] rid=%s reason=session_or_challenge code=%s', requestId, code);
    logSafeError('unlock/linkvertise/complete', code, err);
    safeFlash(req, 'error', messageFor(code));
    return res.redirect('/license');
  }

  const verification = await linkvertise.verifyLinkvertiseAntiBypass({ hash, requestId });
  console.log(
    '[unlock/linkvertise/complete] rid=%s result=%s ok=%s',
    requestId, verification.reason, verification.ok,
  );

  if (!verification.ok) {
    const code = LINKVERTISE_REASON_TO_CODE[verification.reason] || 'PROVIDER_RETURN_UNVERIFIED';
    safeFlash(req, 'error', messageFor(code));
    return res.redirect('/license');
  }

  try {
    const { key, alreadyDone, recoveredExisting } = await challenge.completeAdAndGenerateKey(row);
    if (recoveredExisting && key && !alreadyDone) {
      req.session.generatedKey = key;
      req.session.generatedKeyRecovery = true;
    }
    if (alreadyDone && req.session.generatedKey) {
      return res.redirect('/key/result');
    }
    if (alreadyDone) {
      console.warn('[unlock/linkvertise/complete] rid=%s reason=already_completed', requestId);
      safeFlash(req, 'error', messageFor('PROVIDER_CHALLENGE_ALREADY_USED'));
      return res.redirect('/license');
    }
    console.log('[unlock/linkvertise/complete] rid=%s status=success', requestId);
    req.session.generatedKey = key;
    req.session.generatedKeyAt = Date.now();
    delete req.session.pendingChallenge;
    delete req.session.pendingProvider;
    delete req.session.pendingSignedChallenge;
    delete req.session.activeAdChallengeId;
    return res.redirect('/key/result');
  } catch (err) {
    const code = codeFromError(err, 'KEY_GENERATION_FAILED');
    console.warn('[unlock/linkvertise/complete] rid=%s reason=consume_failed code=%s', requestId, code);
    logSafeError('unlock/linkvertise/complete', code, err);
    safeFlash(req, 'error', messageFor(code));
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
    console.warn('[unlock/lootlabs/complete] rid=%s reason=session_or_challenge code=%s', requestId, code);
    logSafeError('unlock/lootlabs/complete', code, err);
    safeFlash(req, 'error', messageFor(code));
    return res.redirect('/license');
  }

  try {
    const { key, alreadyDone, recoveredExisting } = await challenge.completeAdAndGenerateKey(row);
    if (recoveredExisting && key && !alreadyDone) {
      req.session.generatedKey = key;
      req.session.generatedKeyRecovery = true;
    }
    if (alreadyDone && req.session.generatedKey) {
      return res.redirect('/key/result');
    }
    if (alreadyDone) {
      console.warn('[unlock/lootlabs/complete] rid=%s reason=already_completed', requestId);
      safeFlash(req, 'error', messageFor('PROVIDER_CHALLENGE_ALREADY_USED'));
      return res.redirect('/license');
    }
    console.log('[unlock/lootlabs/complete] rid=%s status=success', requestId);
    req.session.generatedKey = key;
    req.session.generatedKeyAt = Date.now();
    delete req.session.pendingChallenge;
    delete req.session.pendingProvider;
    delete req.session.pendingSignedChallenge;
    delete req.session.activeAdChallengeId;
    return res.redirect('/key/result');
  } catch (err) {
    const code = codeFromError(err, 'KEY_GENERATION_FAILED');
    console.warn('[unlock/lootlabs/complete] rid=%s reason=consume_failed code=%s', requestId, code);
    logSafeError('unlock/lootlabs/complete', code, err);
    safeFlash(req, 'error', messageFor(code));
    return res.redirect('/license');
  }
}

router.get('/', (req, res) => {
  if (req.session.user) return res.redirect('/dashboard');
  return res.render('login', { title: 'Sign In - DENG Tool' });
});

/** Legacy URL — permanent redirect to the public landing page. */
router.get('/login', (req, res) => {
  return res.redirect(301, LOGIN_HOME);
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
  try {
    res.redirect(buildDiscordAuthUrl(req));
  } catch (err) {
    console.error('[auth/discord]', err.message || err);
    safeFlash(req, 'error', 'Discord login is not configured.');
    res.redirect(LOGIN_HOME);
  }
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
  if (!verifyCsrf(req)) return res.redirect('/');
  req.session.destroy(() => {
    res.clearCookie('deng_sid');
    res.redirect(LOGIN_HOME);
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
    title: 'Fish It Stats - DENG Tool',
    activePage: 'fishit',
  });
});

router.get('/license', requireLogin, repairSiteUser, async (req, res) => {
  try {
    const history = await loadHistory(req.session.user.id, 20, discordOwnerId(req), { activeOnly: false });
    const activeHistory = licenseService.filterActiveLicenses(history);
    const cooldown = await challenge.checkCooldown(req.session.user.id);
    const existingUnused = await licenseService.findActiveUnredeemedKey({
      discordUserId: discordOwnerId(req),
      siteUserId: req.session.user.id,
    });
    const recoveredExistingKey = req.session.recoveredExistingKey || null;
    delete req.session.recoveredExistingKey;
    res.render('license', {
      title: 'My License - DENG Tool',
      history,
      stats: summarizeHistory(activeHistory),
      cooldown,
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
      existingUnusedKey: null,
      maskKeyRow,
      fullKeyRow,
      friendlyStatus,
      providerLabel,
      formatWibTimestamp,
    });
  }
});

router.get('/key/provider', requireLogin, repairSiteUser, (req, res) => {
  if (!req.session.pendingChallenge) {
    safeFlash(req, 'error', messageFor('PROVIDER_CHALLENGE_MISSING'));
    return res.redirect('/license');
  }
  return res.render('choose_provider', {
    title: 'Choose Unlock Method - DENG Tool',
    challengeId: req.session.pendingChallenge,
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

router.get('/key/result', requireLogin, (req, res) => {
  const key = req.session.generatedKey;
  if (!key) {
    safeFlash(req, 'error', 'No key available. Please generate a new one.');
    return res.redirect('/license');
  }
  const recoveredExisting = Boolean(req.session.generatedKeyRecovery);
  delete req.session.generatedKeyRecovery;
  res.render('key_result', { title: 'Your Key - DENG Tool', key, recoveredExisting });
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
const path = require('path');
const fs   = require('fs');

const APK_RELEASES_DIR = path.join(__dirname, '..', '..', 'releases', 'android');

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

router.get('/download', (_req, res) => {
  const manifest = loadApkManifest();
  res.render('download', {
    title: 'DENG Tool: Rejoin APK — DENG Tool',
    manifest,
  });
});

router.get('/app', (_req, res) => res.redirect('/download'));

// Canonical "latest" alias — reads manifest and redirects to the versioned
// file. Returns a friendly 404 if no APK has been published yet.
router.get('/downloads/deng-tool-rejoin-apk-latest.apk', (_req, res) => {
  const manifest = loadApkManifest();
  if (!manifest || !manifest.file_name) {
    return res.status(404).type('text/plain').send('APK not available yet.\n');
  }
  const safeName = path.basename(manifest.file_name);
  return res.redirect(302, `/downloads/${encodeURIComponent(safeName)}`);
});

// Legacy alias — permanent redirect to the new canonical "latest" URL so
// existing bookmarks keep working.
router.get('/downloads/deng-monitor-latest.apk', (_req, res) => {
  return res.redirect(301, '/downloads/deng-tool-rejoin-apk-latest.apk');
});

router.get('/downloads/:file', (req, res, next) => {
  const raw = String(req.params.file || '');
  const isNew    = APK_FILENAME_NEW_RE.test(raw);
  const isLegacy = APK_FILENAME_LEGACY_RE.test(raw);
  if (!isNew && !isLegacy) return next();

  // Resolve against the releases dir, then enforce that the resolved path
  // is still inside it (defense in depth on top of the regex).
  const target = path.resolve(APK_RELEASES_DIR, raw);
  if (!target.startsWith(path.resolve(APK_RELEASES_DIR) + path.sep)
      && target !== path.resolve(APK_RELEASES_DIR)) {
    return next();
  }

  if (fs.existsSync(target)) {
    res.setHeader('Content-Type', 'application/vnd.android.package-archive');
    res.setHeader('Content-Disposition', `attachment; filename="${raw}"`);
    return res.sendFile(target);
  }

  // Legacy filename requested but no legacy file on disk — redirect to the
  // equivalent new-pattern filename (same version suffix) so older links
  // continue to resolve once the publisher ships only new-named APKs.
  if (isLegacy) {
    const suffix = raw.replace(/^deng-monitor-/, '');
    return res.redirect(301, `/downloads/deng-tool-rejoin-apk-${suffix}`);
  }

  return res.status(404).type('text/plain').send('APK not found.\n');
});

module.exports = router;
