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
  CHALLENGE_TABLE_MISSING: 'Key generation database is not ready yet.',
  CHALLENGE_INSERT_FAILED: 'Could not start key generation. Please try again.',
  PROVIDER_NOT_CONFIGURED: 'This ad provider is not configured yet.',
  PROVIDER_CHALLENGE_MISSING: 'Please start key generation again.',
  PROVIDER_CHALLENGE_EXPIRED: 'This key generation session expired. Please start again.',
  PROVIDER_CHALLENGE_OWNER_MISMATCH: 'Please start key generation again.',
  PROVIDER_CHALLENGE_ALREADY_USED: 'Please start key generation again.',
  KEY_GENERATION_FAILED: 'Could not generate key. Please try again.',
  UNEXPECTED_ERROR: 'Could not start key generation. Please try again.',
};

function cleanEnv(name, fallback = '') {
  const raw = Object.prototype.hasOwnProperty.call(process.env, name) ? process.env[name] : fallback;
  return String(raw || '').trim().replace(/^['"]|['"]$/g, '').trim();
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
  console.error(`[${scope}] code=${code} message=${detail.slice(0, 240)}`);
}

const authLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 20,
  skip: () => process.env.NODE_ENV === 'test',
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many login attempts, please wait.' },
});

const generateLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 5,
  skip: () => process.env.NODE_ENV === 'test',
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many key generation attempts.' },
});

function wantsJson(req) {
  return (req.headers.accept || '').includes('application/json') ||
    (req.headers['content-type'] || '').includes('application/json');
}

function safeFlash(req, key, value) {
  req.session.flash = { ...(req.session.flash || {}), [key]: value };
}

function maskKeyRow(row) {
  const prefix = row.key_prefix || 'DENG-????-????';
  const suffix = row.key_suffix || '????-????';
  return `${prefix}-****-${String(suffix).split('-').pop() || '????'}`;
}

function friendlyStatus(row) {
  if (!row) return 'Unknown';
  if (row.status === 'key_generated') {
    if (row.key_expires_at && new Date(row.key_expires_at) < new Date()) return 'Expired';
    return 'Unredeemed';
  }
  if (row.status === 'failed') return 'Failed';
  if (row.status === 'expired') return 'Expired';
  if (row.status === 'revoked') return 'Revoked';
  return 'Pending';
}

function summarizeHistory(history) {
  const rows = history || [];
  const unredeemed = rows.filter((row) => friendlyStatus(row) === 'Unredeemed').length;
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
  return data || [];
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

  const { user } = req.session;
  try {
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
    });
  } catch (err) {
    const code = codeFromError(err, err?.code === 'NO_PROVIDER_CONFIGURED' ? 'NO_PROVIDER_CONFIGURED' : 'CHALLENGE_INSERT_FAILED');
    logSafeError('api/key/start', code, err);
    const status = code === 'NO_PROVIDER_CONFIGURED' ? 503 : (code === 'CHALLENGE_TABLE_MISSING' ? 503 : 500);
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
    await challenge.markPendingAdById(row.id, req, user);

    req.session.pendingProvider = provider;
    const providerCfg = getProviderConfig(provider);

    if (wantsJson(req)) {
      return res.json({
        provider,
        redirect_url: providerCfg.monetizedUrl,
        complete_url: providerCfg.completeUrl,
      });
    }

    return res.redirect(providerCfg.monetizedUrl);
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

  const signed = String(req.query.challenge || req.session.pendingSignedChallenge || '');
  if (!signed) {
    safeFlash(req, 'error', 'Missing challenge token. Please start again.');
    return res.redirect('/license');
  }

  try {
    let row = await challenge.verifyChallengeForRequest(signed, req, selected);
    if (!row) {
      safeFlash(req, 'error', 'Challenge expired or invalid. Please start again.');
      return res.redirect('/license');
    }

    if (row.status === 'provider_selected') {
      await challenge.markPendingAd(signed);
      row = await challenge.verifyChallengeForRequest(signed, req, selected);
    }

    const { key, alreadyDone } = await challenge.completeAdAndGenerateKey(row);
    if (alreadyDone && req.session.generatedKey) {
      return res.redirect('/key/result');
    }
    if (alreadyDone) {
      safeFlash(req, 'error', 'This challenge was already used.');
      return res.redirect('/license');
    }

    req.session.generatedKey = key;
    req.session.generatedKeyAt = Date.now();
    delete req.session.pendingChallenge;
    delete req.session.pendingProvider;
    delete req.session.pendingSignedChallenge;
    return res.redirect('/key/result');
  } catch (err) {
    const code = codeFromError(err, 'KEY_GENERATION_FAILED');
    logSafeError(`unlock/${selected}`, code, err);
    safeFlash(req, 'error', messageFor(code));
    return res.redirect('/license');
  }
}

async function handleProviderComplete(req, res, provider) {
  const selected = ensureProvider(provider);
  if (!selected) {
    safeFlash(req, 'error', messageFor('PROVIDER_CHALLENGE_MISSING'));
    return res.redirect('/license');
  }

  try {
    const { key, alreadyDone } = await challenge.completeActiveProviderChallenge(req, selected);
    if (alreadyDone && req.session.generatedKey) {
      return res.redirect('/key/result');
    }
    if (alreadyDone) {
      safeFlash(req, 'error', messageFor('PROVIDER_CHALLENGE_ALREADY_USED'));
      return res.redirect('/license');
    }

    req.session.generatedKey = key;
    req.session.generatedKeyAt = Date.now();
    delete req.session.pendingChallenge;
    delete req.session.pendingProvider;
    delete req.session.pendingSignedChallenge;
    return res.redirect('/key/result');
  } catch (err) {
    const code = codeFromError(err, 'KEY_GENERATION_FAILED');
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

router.get('/dashboard', requireLogin, async (req, res) => {
  try {
    const history = await loadHistory(req.session.user.id, 8);
    res.render('dashboard', {
      title: 'Dashboard - DENG Tool',
      history,
      stats: summarizeHistory(history),
    });
  } catch (err) {
    console.error('[dashboard]', err.message || err);
    res.render('dashboard', {
      title: 'Dashboard - DENG Tool',
      history: [],
      stats: summarizeHistory([]),
    });
  }
});

router.get('/license', requireLogin, async (req, res) => {
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
    });
  }
});

router.get('/key/provider', requireLogin, (req, res) => {
  if (!req.session.pendingChallenge) {
    safeFlash(req, 'error', messageFor('PROVIDER_CHALLENGE_MISSING'));
    return res.redirect('/license');
  }
  return res.render('choose_provider', {
    title: 'Choose Unlock Method - DENG Tool',
    challengeId: req.session.pendingChallenge,
    providers: enabledProviders(),
  });
});

router.post('/api/key/start', requireLogin, generateLimiter, handleKeyStart);
router.post('/license/generate', requireLogin, generateLimiter, handleKeyStart);
router.post('/api/key/provider', requireLogin, generateLimiter, handleProvider);
router.post('/api/key/provider/:provider', requireLogin, generateLimiter, handleProvider);
router.post('/license/provider', requireLogin, generateLimiter, handleProvider);
router.post('/license/provider/:provider', requireLogin, generateLimiter, handleProvider);

router.get('/unlock/lootlabs', requireLogin, (req, res) => handleUnlock(req, res, 'lootlabs'));
router.get('/unlock/linkvertise', requireLogin, (req, res) => handleUnlock(req, res, 'linkvertise'));
router.get('/unlock/lootlabs/complete', requireLogin, (req, res) => handleProviderComplete(req, res, 'lootlabs'));
router.get('/unlock/linkvertise/complete', requireLogin, (req, res) => handleProviderComplete(req, res, 'linkvertise'));

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

router.get('/api/license/me', requireLogin, async (req, res) => {
  try {
    const history = await loadHistory(req.session.user.id, 20);
    res.json({ account: req.session.user, stats: summarizeHistory(history) });
  } catch {
    res.status(500).json({ error: 'license_summary_failed' });
  }
});

router.get('/api/license/history', requireLogin, async (req, res) => {
  try {
    const history = await loadHistory(req.session.user.id, 20);
    res.json({
      history: history.map((row) => ({
        id: row.id,
        key: maskKeyRow(row),
        status: friendlyStatus(row),
        provider: row.provider || null,
        created_at: row.created_at,
        key_expires_at: row.key_expires_at,
      })),
    });
  } catch {
    res.status(500).json({ error: 'license_history_failed' });
  }
});

module.exports = router;
