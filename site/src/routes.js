'use strict';
/**
 * HTTP routes for the DENG Tool portal.
 */
const express = require('express');
const rateLimit = require('express-rate-limit');

const auth = require('./auth');
const {
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

const router = express.Router();

const DEFAULT_PROVIDER_CONFIG = {
  linkvertise: {
    enabled: 'true',
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
    .filter((item) => item && item.enabled && item.monetizedUrl && item.completeUrl);
}

function providerIsReady(provider) {
  const cfg = getProviderConfig(provider);
  return Boolean(cfg && cfg.enabled && cfg.monetizedUrl && cfg.completeUrl);
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
 * Prefers LOOTLABS_TEMPLATE_URL (contains {url} placeholder) so that the
 * provider destination is set per-challenge rather than hard-coded.
 */
function lootlabsProviderUrl(returnToken) {
  const completeUrl = tokenizedCompleteUrl('lootlabs', returnToken);
  const templateUrl = cleanEnv('LOOTLABS_TEMPLATE_URL', '');
  if (templateUrl) {
    // Template approach: preserves the shortlink ID exactly as written.
    // Replace {url} placeholder with the encoded signed completion URL.
    return templateUrl.replace('{url}', encodeURIComponent(completeUrl));
  }
  // Fallback: safe string-based append — do NOT use the URL searchParams API
  // because new URL('…s?TqZQAW38').searchParams.set(…) normalises the
  // valueless key to "TqZQAW38=" which breaks the LootDest shortlink lookup.
  const cfg = getProviderConfig('lootlabs');
  const base = cfg.monetizedUrl;
  const sep = base.includes('?') ? '&' : '?';
  const logWarn = process.env.NODE_ENV !== 'test';
  if (logWarn) {
    console.warn('[key/provider] lootlabs LOOTLABS_TEMPLATE_URL not set; fallback url may not be forwarded by provider');
  }
  return `${base}${sep}return_url=${encodeURIComponent(completeUrl)}&deng_return=${encodeURIComponent(completeUrl)}`;
}

function providerRedirectUrl(providerCfg, returnToken) {
  if (providerCfg.provider === 'lootlabs') {
    return lootlabsProviderUrl(returnToken);
  }
  if (providerCfg.provider === 'linkvertise') {
    // Linkvertise Full Script approach: redirect to our internal start page.
    // The start page includes the Linkvertise publisher JS which monetises
    // the link whose href is already set to the signed completion URL.
    // This preserves the signed token through the provider flow.
    return `${publicUrl()}/unlock/linkvertise/start?t=${encodeURIComponent(returnToken)}`;
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
  const prefix = row.key_prefix || 'DENG-????-????';
  const suffix = row.key_suffix || '????-????';
  return `${prefix}-****-${String(suffix).split('-').pop() || '????'}`;
}

function providerLabel(provider) {
  if (provider === 'linkvertise') return 'Linkvertise';
  if (provider === 'lootlabs') return 'LootLabs';
  return 'Provider';
}

function friendlyStatus(row) {
  if (!row) return 'Unknown';
  if (row.license_status === 'expired' || row.status === 'expired') return 'Expired';
  if (row.license_status === 'revoked' || row.status === 'revoked') return 'Expired';
  if (row.redeemed_at || row.license_status === 'redeemed' || row.license_status === 'used') return 'Redeemed';
  if (row.status === 'key_generated') {
    if (row.key_expires_at && new Date(row.key_expires_at) < new Date()) return 'Expired';
    return 'Generated';
  }
  if (row.status === 'ad_completed') return 'Completed';
  if (row.status === 'failed') return 'Expired';
  return 'Pending';
}

function summarizeHistory(history) {
  const rows = history || [];
  const unredeemed = rows.filter((row) => friendlyStatus(row) === 'Generated').length;
  const expired = rows.filter((row) => friendlyStatus(row) === 'Expired').length;
  return {
    total: rows.length,
    unredeemed,
    expired,
    latest: rows[0] || null,
    cooldownSeconds: challenge.COOLDOWN_SECONDS,
    keyExpiryHours: challenge.KEY_EXPIRY_HOURS,
  };
}

async function loadHistory(siteUserId, limit = 20) {
  const { data, error } = await supabase
    .from('license_ad_challenges')
    .select('id, key_prefix, key_suffix, status, provider, created_at, key_expires_at, completed_at, license_key_id')
    .eq('site_user_id', siteUserId)
    .eq('status', 'key_generated')
    .order('created_at', { ascending: false })
    .limit(limit);
  if (error) {
    const msg = error.message || '';
    if (msg.includes('schema cache') || msg.includes('does not exist') || msg.includes('license_ad_challenges')) {
      console.warn('[routes] license_ad_challenges table not found – apply migration 005_site_portal.sql');
    } else {
      console.error('[routes/loadHistory]', msg);
    }
    return [];
  }
  const generated = (data || []).filter((row) => row.license_key_id);
  const keyIds = generated.map((row) => row.license_key_id).filter(Boolean);
  if (keyIds.length === 0) return [];

  const { data: keys, error: keyError } = await supabase
    .from('license_keys')
    .select('id, status, redeemed_at, expires_at')
    .in('id', keyIds);

  const byId = new Map();
  if (!keyError && keys) {
    for (const key of keys) byId.set(key.id, key);
  }

  return generated.map((row) => {
    const key = byId.get(row.license_key_id) || {};
    return {
      ...row,
      license_status: key.status || null,
      redeemed_at: key.redeemed_at || null,
      key_expires_at: row.key_expires_at || key.expires_at || null,
    };
  });
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
    const started = await challenge.markPendingAdById(row.id, req, user, providerCfg.monetizedUrl);
    const redirectUrl = providerRedirectUrl(providerCfg, started.return_token);

    req.session.pendingProvider = provider;

    // Safe debug log: URL host only (never full signed token or complete URL)
    let redirectHost = '';
    try { redirectHost = new URL(redirectUrl).hostname; } catch {}
    console.log(
      '[key/provider] provider=%s challenge_prefix=%s url_host=%s token_len=%d status=303',
      provider,
      String(challengeId).slice(0, 8),
      redirectHost,
      (started.return_token || '').length,
    );

    if (wantsJson(req)) {
      return res.json({
        provider,
        redirect_url: redirectUrl,
        complete_url: tokenizedCompleteUrl(provider, started.return_token),
      });
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
    const { key, alreadyDone } = await challenge.completeActiveProviderChallenge(req, selected, returnToken);
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
    return res.redirect('/key/result');
  } catch (err) {
    const code = codeFromError(err, 'PROVIDER_RETURN_UNVERIFIED');
    logSafeError(`unlock/${selected}/complete`, code, err);
    safeFlash(req, 'error', messageFor(code));
    return res.redirect('/license');
  }
}

router.get('/', (req, res) => {
  res.redirect(req.session.user ? '/dashboard' : '/login');
});

router.get('/health', (_req, res) => {
  res.json({
    status: 'ok',
    service: 'deng-tool-site',
    port: parseInt(process.env.TOOL_SITE_PORT || '8791', 10),
    timestamp: new Date().toISOString(),
  });
});

router.get('/login', (req, res) => {
  if (req.session.user) return res.redirect('/dashboard');
  return res.render('login', { title: 'Sign In - DENG Tool' });
});



router.get('/auth/discord', (req, res) => {
  try {
    res.redirect(buildDiscordAuthUrl(req));
  } catch (err) {
    console.error('[auth/discord]', err.message || err);
    safeFlash(req, 'error', 'Discord login is not configured.');
    res.redirect('/login');
  }
});

router.get('/auth/discord/callback', authLimiter, async (req, res) => {
  const { code, state, error: oauthError } = req.query;

  if (oauthError) {
    console.warn('[auth/discord/callback] category=oauth_denied discord_error=%s', String(oauthError).slice(0, 64));
    safeFlash(req, 'error', `Discord denied access: ${oauthError}`);
    return res.redirect('/login');
  }

  const storedState = req.session.oauthState;
  delete req.session.oauthState;

  if (!code) {
    console.warn('[auth/discord/callback] category=code_missing state_present=%s', !!storedState);
    safeFlash(req, 'error', 'Invalid OAuth response. Please try again.');
    return res.redirect('/login');
  }
  if (!storedState) {
    console.warn('[auth/discord/callback] category=state_missing code_present=true');
    safeFlash(req, 'error', 'Session expired. Please try again.');
    return res.redirect('/login');
  }
  if (String(state) !== storedState) {
    console.warn('[auth/discord/callback] category=state_mismatch code_present=true');
    safeFlash(req, 'error', 'Invalid OAuth state. Please try again.');
    return res.redirect('/login');
  }

  // Step 1: Exchange code for access token
  let tokens;
  try {
    tokens = await exchangeDiscordCode(String(code));
  } catch (_err) {
    // Structured error details are already logged inside exchangeDiscordCode.
    safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
    return res.redirect('/login');
  }

  // Step 2: Fetch Discord user identity
  let discordUser;
  try {
    discordUser = await fetchDiscordUser(tokens.access_token);
  } catch (err) {
    const status = (err.response && err.response.status) || 'unknown';
    console.error('[auth/discord/callback] category=user_fetch_failed http_status=%s', status);
    safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
    return res.redirect('/login');
  }

  // Step 3: Create or update portal user
  let siteUser;
  try {
    siteUser = await upsertDiscordUser(discordUser, tokens);
  } catch (err) {
    console.error('[auth/discord/callback] category=site_user_upsert_failed error=%s', err.message);
    safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
    return res.redirect('/login');
  }

  // Step 4: Regenerate session and redirect
  return new Promise((resolve) => {
    req.session.regenerate((regenErr) => {
      if (regenErr) {
        console.error('[auth/discord/callback] category=session_regenerate_failed error=%s', regenErr.message);
        safeFlash(req, 'error', 'Session error. Please try again.');
        res.redirect('/login');
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
    res.redirect('/login');
  });
});

router.get('/dashboard', requireLogin, repairSiteUser, async (req, res) => {
  try {
    const history = await loadHistory(req.session.user.id, 8);
    res.render('dashboard', {
      title: 'Dashboard - DENG Tool',
      history,
      stats: summarizeHistory(history),
      maskKeyRow,
      friendlyStatus,
      providerLabel,
    });
  } catch (err) {
    console.error('[dashboard]', err.message || err);
    res.render('dashboard', {
      title: 'Dashboard - DENG Tool',
      history: [],
      stats: summarizeHistory([]),
      maskKeyRow,
      friendlyStatus,
      providerLabel,
    });
  }
});

router.get('/license', requireLogin, repairSiteUser, async (req, res) => {
  try {
    const history = await loadHistory(req.session.user.id, 20);
    const cooldown = await challenge.checkCooldown(req.session.user.id);
    res.render('license', {
      title: 'My License - DENG Tool',
      history,
      stats: summarizeHistory(history),
      cooldown,
      maskKeyRow,
      friendlyStatus,
      providerLabel,
    });
  } catch (err) {
    console.error('[license]', err.message || err);
    res.render('license', {
      title: 'My License - DENG Tool',
      history: [],
      stats: summarizeHistory([]),
      cooldown: { allowed: true, secondsLeft: 0 },
      maskKeyRow,
      friendlyStatus,
      providerLabel,
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

/**
 * Linkvertise Full Script intermediate page.
 * The provider POST redirects here (internal 303). This page renders the
 * Linkvertise publisher JS with a link whose href points to our signed
 * completion URL. The Linkvertise script monetises the click; after the
 * ad flow the user lands on /unlock/linkvertise/complete?t=<signed_token>.
 */
router.get('/unlock/linkvertise/start', requireLogin, repairSiteUser, (req, res) => {
  const returnToken = String(req.query.t || '');
  if (!returnToken || !challenge.verifyReturnToken(returnToken)) {
    safeFlash(req, 'error', messageFor('PROVIDER_RETURN_TOKEN_INVALID'));
    return res.redirect('/license');
  }
  const cfg = getProviderConfig('linkvertise');
  const publisherId = parseInt(String(cfg ? cfg.publisherId : '5914830'), 10) || 5914830;
  const completeUrl = tokenizedCompleteUrl('linkvertise', returnToken);
  return res.render('unlock_linkvertise', {
    title: 'Ad Step – DENG Tool',
    publisherId,
    completeUrl,
  });
});

router.get('/unlock/lootlabs/complete', requireLogin, repairSiteUser, (req, res) => handleProviderComplete(req, res, 'lootlabs'));
router.get('/unlock/linkvertise/complete', requireLogin, repairSiteUser, (req, res) => handleProviderComplete(req, res, 'linkvertise'));

router.get('/unlock/linkvertise/done', requireLogin, (_req, res) => {
  res.redirect('/license');
});

router.get('/key/result', requireLogin, (req, res) => {
  const key = req.session.generatedKey;
  if (!key) {
    safeFlash(req, 'error', 'No key available. Please generate a new one.');
    return res.redirect('/license');
  }
  res.render('key_result', { title: 'Your Key - DENG Tool', key });
});

router.get('/api/stats/public', (_req, res) => {
  res.json({
    service: 'deng-tool-site',
    cooldown_seconds: challenge.COOLDOWN_SECONDS,
    unredeemed_key_expiry_hours: challenge.KEY_EXPIRY_HOURS,
    tool_version: '1.0.0',
  });
});

router.get('/api/license/me', requireLogin, repairSiteUser, async (req, res) => {
  try {
    const history = await loadHistory(req.session.user.id, 20);
    res.json({ account: req.session.user, stats: summarizeHistory(history) });
  } catch {
    res.status(500).json({ error: 'license_summary_failed' });
  }
});

router.get('/api/license/history', requireLogin, repairSiteUser, async (req, res) => {
  try {
    const history = await loadHistory(req.session.user.id, 20);
    res.json({
      history: history.map((row) => ({
        id: row.id,
        key: maskKeyRow(row),
        status: friendlyStatus(row),
        provider: providerLabel(row.provider),
        created_at: row.created_at,
        key_expires_at: row.key_expires_at,
      })),
    });
  } catch {
    res.status(500).json({ error: 'license_history_failed' });
  }
});

module.exports = router;
