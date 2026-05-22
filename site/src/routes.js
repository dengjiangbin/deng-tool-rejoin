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

const PUBLIC_URL = (process.env.TOOL_SITE_PUBLIC_URL || 'https://tool.deng.my.id').replace(/\/+$/, '');
const LINKVERTISE_PUBLISHER_ID = process.env.LINKVERTISE_PUBLISHER_ID || '5914830';
const LOOTLABS_TEMPLATE_URL = process.env.LOOTLABS_TEMPLATE_URL || process.env.LOOTLABS_PUBLISHER_URL || '';

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

function buildUnlockUrl(provider, signed) {
  return `${PUBLIC_URL}/unlock/${provider}?challenge=${encodeURIComponent(signed)}`;
}

function buildLootlabsUrl(unlockUrl) {
  if (!LOOTLABS_TEMPLATE_URL) return unlockUrl;
  if (LOOTLABS_TEMPLATE_URL.includes('{url}')) {
    return LOOTLABS_TEMPLATE_URL.replace('{url}', encodeURIComponent(unlockUrl));
  }
  return unlockUrl;
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
    const { allowed, secondsLeft } = await challenge.checkCooldown(user.id);
    if (!allowed) {
      if (wantsJson(req)) {
        return res.status(429).json({ error: 'cooldown', secondsLeft });
      }
      req.session.flash = {
        error: `Please wait ${secondsLeft}s before generating another key.`,
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
    });
  } catch (err) {
    console.error('[api/key/start]', err.message || err);
    if (wantsJson(req)) return res.status(500).json({ error: 'start_failed' });
    safeFlash(req, 'error', 'Could not start key generation. Please try again.');
    return res.redirect('/license');
  }
}

async function handleProvider(req, res) {
  if (!verifyCsrf(req)) {
    if (wantsJson(req)) return res.status(403).json({ error: 'invalid_csrf' });
    safeFlash(req, 'error', 'Invalid request token.');
    return res.redirect('/license');
  }

  const provider = ensureProvider(String(req.body.provider || ''));
  const challengeId = String(req.body.challenge_id || '');
  const { user } = req.session;

  if (!provider) {
    safeFlash(req, 'error', 'Invalid provider selection.');
    return res.redirect('/license');
  }
  if (!challengeId || challengeId !== req.session.pendingChallenge) {
    safeFlash(req, 'error', 'Challenge mismatch. Please start again.');
    return res.redirect('/license');
  }

  try {
    const row = await challenge.selectProvider(challengeId, provider, req, user);
    const signed = row.signed_challenge;
    await challenge.markPendingAd(signed);

    req.session.pendingProvider = provider;
    req.session.pendingSignedChallenge = signed;

    const unlockUrl = buildUnlockUrl(provider, signed);
    const adUrl = provider === 'lootlabs' ? buildLootlabsUrl(unlockUrl) : unlockUrl;

    if (wantsJson(req)) {
      return res.json({ provider, unlock_url: unlockUrl, ad_url: adUrl });
    }

    return res.render('provider_unlock', {
      title: 'Unlock Key - DENG Tool',
      provider,
      unlockUrl,
      adUrl,
      publicUrl: PUBLIC_URL,
      publisherId: LINKVERTISE_PUBLISHER_ID,
    });
  } catch (err) {
    console.error('[api/key/provider]', err.message || err);
    safeFlash(req, 'error', 'Failed to set up ad unlock. Please try again.');
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
    console.error(`[unlock/${selected}]`, err.message || err);
    safeFlash(req, 'error', 'Failed to complete key generation.');
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

router.post('/api/key/start', requireLogin, generateLimiter, handleKeyStart);
router.post('/license/generate', requireLogin, generateLimiter, handleKeyStart);
router.post('/api/key/provider', requireLogin, generateLimiter, handleProvider);
router.post('/license/provider', requireLogin, generateLimiter, handleProvider);

router.get('/unlock/lootlabs', requireLogin, (req, res) => handleUnlock(req, res, 'lootlabs'));
router.get('/unlock/linkvertise', requireLogin, (req, res) => handleUnlock(req, res, 'linkvertise'));

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
