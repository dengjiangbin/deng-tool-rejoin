'use strict';
/**
 * All HTTP routes for the DENG Tool web portal.
 *
 * Route map:
 *   GET  /                       → redirect to /dashboard or /login
 *   GET  /login                  → login page
 *   POST /auth/login             → local auth
 *   GET  /auth/discord           → Discord OAuth redirect
 *   GET  /auth/discord/callback  → Discord OAuth callback
 *   POST /auth/logout            → destroy session
 *   GET  /dashboard              → protected dashboard page
 *   GET  /license                → protected My License page
 *   POST /license/generate       → start key generation flow
 *   POST /license/provider       → choose provider (lootlabs|linkvertise)
 *   GET  /unlock/linkvertise     → intermediate page (Linkvertise script)
 *   GET  /unlock/lootlabs        → LootLabs callback landing
 *   GET  /key/result             → show generated key (once)
 *   GET  /health                 → JSON health endpoint (public)
 */
const express    = require('express');
const rateLimit  = require('express-rate-limit');

const auth = require('./auth');
const {
  requireLogin, verifyCsrf,
  buildDiscordAuthUrl, exchangeDiscordCode, fetchDiscordUser,
  upsertDiscordUser, localLogin, toSessionUser,
} = auth;
const challenge = require('./challenge');
const { verifyChallenge } = require('./crypto');
const supabase  = require('./db');

const router = express.Router();
const PUBLIC_URL = process.env.TOOL_SITE_PUBLIC_URL || 'https://tool.deng.my.id';
const LOOTLABS_URL = process.env.LOOTLABS_PUBLISHER_URL || '';

// ---------------------------------------------------------------
// Per-route rate limiters
// ---------------------------------------------------------------
const authLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 20,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many login attempts, please wait.' },
});

const generateLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 5,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many key generation attempts.' },
});

// ---------------------------------------------------------------
// Public: root redirect
// ---------------------------------------------------------------
router.get('/', (req, res) => {
  if (req.session.user) return res.redirect('/dashboard');
  res.redirect('/login');
});

// ---------------------------------------------------------------
// Health check (public)
// ---------------------------------------------------------------
router.get('/health', (_req, res) => {
  res.json({ status: 'ok', service: 'deng-tool-site', timestamp: new Date().toISOString() });
});

// ---------------------------------------------------------------
// Login page
// ---------------------------------------------------------------
router.get('/login', (req, res) => {
  if (req.session.user) return res.redirect('/dashboard');
  res.render('login', { title: 'Sign In – DENG Tool' });
});

// ---------------------------------------------------------------
// Local login (POST)
// ---------------------------------------------------------------
router.post('/auth/login', authLimiter, async (req, res) => {
  if (!verifyCsrf(req)) {
    req.session.flash = { error: 'Invalid request. Please try again.' };
    return res.redirect('/login');
  }

  const { username, password } = req.body;
  if (!username || !password) {
    req.session.flash = { error: 'Username and password are required.' };
    return res.redirect('/login');
  }

  try {
    const user = await localLogin(String(username).trim(), String(password));
    if (!user) {
      req.session.flash = { error: 'Invalid username or password.' };
      return res.redirect('/login');
    }
    // Regenerate session to prevent fixation
    req.session.regenerate((err) => {
      if (err) throw err;
      req.session.user = toSessionUser(user);
      req.session.flash = { success: `Welcome back, ${req.session.user.username}!` };
      res.redirect('/dashboard');
    });
  } catch (err) {
    console.error('[auth/login]', err);
    req.session.flash = { error: 'Login service unavailable. Please try again.' };
    res.redirect('/login');
  }
});

// ---------------------------------------------------------------
// Discord OAuth2 – start
// ---------------------------------------------------------------
router.get('/auth/discord', (req, res) => {
  const url = buildDiscordAuthUrl(req);
  res.redirect(url);
});

// ---------------------------------------------------------------
// Discord OAuth2 – callback
// ---------------------------------------------------------------
router.get('/auth/discord/callback', authLimiter, async (req, res) => {
  const { code, state, error } = req.query;

  if (error) {
    req.session.flash = { error: `Discord denied access: ${error}` };
    return res.redirect('/login');
  }

  const storedState = req.session.oauthState;
  delete req.session.oauthState;

  if (!storedState || String(state) !== storedState) {
    req.session.flash = { error: 'Invalid OAuth state. Please try again.' };
    return res.redirect('/login');
  }

  if (!code) {
    req.session.flash = { error: 'No authorization code received.' };
    return res.redirect('/login');
  }

  try {
    const tokens      = await exchangeDiscordCode(String(code));
    const discordUser = await fetchDiscordUser(tokens.access_token);
    const siteUser    = await upsertDiscordUser(discordUser, tokens);

    req.session.regenerate((err) => {
      if (err) throw err;
      req.session.user = toSessionUser(siteUser);
      req.session.flash = { success: `Welcome, ${req.session.user.username}!` };
      res.redirect('/dashboard');
    });
  } catch (err) {
    console.error('[auth/discord/callback]', err);
    req.session.flash = { error: 'Discord sign-in failed. Please try again.' };
    res.redirect('/login');
  }
});

// ---------------------------------------------------------------
// Logout (POST only, CSRF-protected)
// ---------------------------------------------------------------
router.post('/auth/logout', (req, res) => {
  if (!verifyCsrf(req)) return res.redirect('/');
  req.session.destroy(() => {
    res.clearCookie('deng_sid');
    res.redirect('/login');
  });
});

// ---------------------------------------------------------------
// Dashboard (protected)
// ---------------------------------------------------------------
router.get('/dashboard', requireLogin, async (req, res) => {
  const { user } = req.session;
  try {
    // Recent key history for the user
    const { data: history } = await supabase
      .from('license_ad_challenges')
      .select('key_prefix, key_suffix, status, created_at, key_expires_at, completed_at')
      .eq('site_user_id', user.id)
      .in('status', ['key_generated'])
      .order('created_at', { ascending: false })
      .limit(5);

    res.render('dashboard', {
      title:   'Dashboard – DENG Tool',
      history: history || [],
    });
  } catch (err) {
    console.error('[dashboard]', err);
    res.render('dashboard', { title: 'Dashboard – DENG Tool', history: [] });
  }
});

// ---------------------------------------------------------------
// My License (protected)
// ---------------------------------------------------------------
router.get('/license', requireLogin, async (req, res) => {
  const { user } = req.session;
  try {
    const { data: history } = await supabase
      .from('license_ad_challenges')
      .select('key_prefix, key_suffix, status, created_at, key_expires_at, provider')
      .eq('site_user_id', user.id)
      .order('created_at', { ascending: false })
      .limit(20);

    res.render('license', {
      title:   'My License – DENG Tool',
      history: history || [],
    });
  } catch (err) {
    console.error('[license]', err);
    res.render('license', { title: 'My License – DENG Tool', history: [] });
  }
});

// ---------------------------------------------------------------
// Generate Key flow – step 1: create challenge
// ---------------------------------------------------------------
router.post('/license/generate', requireLogin, generateLimiter, async (req, res) => {
  if (!verifyCsrf(req)) {
    req.session.flash = { error: 'Invalid request token.' };
    return res.redirect('/license');
  }

  const { user } = req.session;
  try {
    const { allowed, secondsLeft } = await challenge.checkCooldown(user.id);
    if (!allowed) {
      req.session.flash = { error: `Please wait ${secondsLeft}s before generating another key.`, cooldown: secondsLeft };
      return res.redirect('/license');
    }

    const row = await challenge.createChallenge(req, user);
    req.session.pendingChallenge = row.id;

    res.render('choose_provider', {
      title:       'Choose Unlock Method – DENG Tool',
      challengeId: row.id,
    });
  } catch (err) {
    console.error('[license/generate]', err);
    req.session.flash = { error: 'Could not start key generation. Please try again.' };
    res.redirect('/license');
  }
});

// ---------------------------------------------------------------
// Generate Key flow – step 2: choose provider
// ---------------------------------------------------------------
router.post('/license/provider', requireLogin, generateLimiter, async (req, res) => {
  if (!verifyCsrf(req)) {
    req.session.flash = { error: 'Invalid request token.' };
    return res.redirect('/license');
  }

  const { provider, challenge_id } = req.body;
  const { user } = req.session;

  if (!['lootlabs', 'linkvertise'].includes(provider)) {
    req.session.flash = { error: 'Invalid provider selection.' };
    return res.redirect('/license');
  }

  // Verify challenge belongs to this session/user
  if (!challenge_id || challenge_id !== req.session.pendingChallenge) {
    req.session.flash = { error: 'Challenge mismatch. Please start again.' };
    return res.redirect('/license');
  }

  try {
    const row = await challenge.selectProvider(challenge_id, provider, req);
    const signed = row.signed_challenge;

    if (provider === 'lootlabs') {
      // Store signed token in session so the /unlock/lootlabs callback can verify
      req.session.pendingLootlabs = signed;
      if (!LOOTLABS_URL) {
        req.session.flash = { error: 'LootLabs is not configured.' };
        return res.redirect('/license');
      }
      return res.redirect(LOOTLABS_URL);
    }

    if (provider === 'linkvertise') {
      // Redirect to intermediate page that embeds Linkvertise script
      return res.redirect(`/unlock/linkvertise?challenge=${encodeURIComponent(signed)}`);
    }
  } catch (err) {
    console.error('[license/provider]', err);
    req.session.flash = { error: 'Failed to set up ad unlock. Please try again.' };
    res.redirect('/license');
  }
});

// ---------------------------------------------------------------
// Unlock: Linkvertise intermediate page
// ---------------------------------------------------------------
router.get('/unlock/linkvertise', requireLogin, async (req, res) => {
  const { challenge: signed } = req.query;
  if (!signed) {
    req.session.flash = { error: 'Missing challenge token.' };
    return res.redirect('/license');
  }

  const decoded = verifyChallenge(String(signed));
  if (!decoded) {
    req.session.flash = { error: 'Challenge token expired or invalid.' };
    return res.redirect('/license');
  }

  try {
    await challenge.markPendingAd(String(signed));
  } catch {
    // Non-fatal — may already be in pending_ad state
  }

  const callbackUrl = encodeURIComponent(`${PUBLIC_URL}/unlock/linkvertise/done?challenge=${encodeURIComponent(signed)}`);
  const publisherId = process.env.LINKVERTISE_PUBLISHER_ID || '5914830';

  res.render('unlock_linkvertise', {
    title:        'Unlock – DENG Tool',
    signed,
    callbackUrl,
    publisherId,
  });
});

// ---------------------------------------------------------------
// Unlock: Linkvertise done callback
// ---------------------------------------------------------------
router.get('/unlock/linkvertise/done', requireLogin, async (req, res) => {
  const { challenge: signed } = req.query;
  if (!signed) {
    req.session.flash = { error: 'Missing challenge token.' };
    return res.redirect('/license');
  }

  const decoded = verifyChallenge(String(signed));
  if (!decoded) {
    req.session.flash = { error: 'Challenge expired. Please start again.' };
    return res.redirect('/license');
  }

  try {
    const row = await challenge.getChallengeByToken(String(signed));
    if (!row) {
      req.session.flash = { error: 'Challenge not found or expired.' };
      return res.redirect('/license');
    }

    if (row.site_user_id !== req.session.user.id) {
      req.session.flash = { error: 'Challenge does not belong to your account.' };
      return res.redirect('/license');
    }

    const { key, alreadyDone } = await challenge.completeAdAndGenerateKey(row);

    if (alreadyDone) {
      req.session.flash = { error: 'This challenge was already used.' };
      return res.redirect('/license');
    }

    // Store key in session for one-time display, then clear pending state
    req.session.generatedKey = key;
    delete req.session.pendingChallenge;
    res.redirect('/key/result');
  } catch (err) {
    console.error('[unlock/linkvertise/done]', err);
    req.session.flash = { error: 'Failed to complete key generation.' };
    res.redirect('/license');
  }
});

// ---------------------------------------------------------------
// Unlock: LootLabs callback landing
// ---------------------------------------------------------------
router.get('/unlock/lootlabs', requireLogin, async (req, res) => {
  const signed = req.session.pendingLootlabs;
  if (!signed) {
    req.session.flash = { error: 'No pending LootLabs challenge found. Please start again.' };
    return res.redirect('/license');
  }

  const decoded = verifyChallenge(signed);
  if (!decoded) {
    req.session.flash = { error: 'Challenge expired. Please start again.' };
    delete req.session.pendingLootlabs;
    return res.redirect('/license');
  }

  try {
    const row = await challenge.getChallengeByToken(signed);
    if (!row) {
      req.session.flash = { error: 'Challenge not found or expired.' };
      delete req.session.pendingLootlabs;
      return res.redirect('/license');
    }

    if (row.site_user_id !== req.session.user.id) {
      req.session.flash = { error: 'Challenge mismatch.' };
      delete req.session.pendingLootlabs;
      return res.redirect('/license');
    }

    // May still be in provider_selected; advance to pending_ad first if needed
    if (row.status === 'provider_selected') {
      try { await challenge.markPendingAd(signed); } catch { /* ok */ }
    }

    const freshRow = await challenge.getChallengeByToken(signed);
    const { key, alreadyDone } = await challenge.completeAdAndGenerateKey(freshRow || row);

    delete req.session.pendingLootlabs;

    if (alreadyDone) {
      req.session.flash = { error: 'This challenge was already used.' };
      return res.redirect('/license');
    }

    req.session.generatedKey = key;
    delete req.session.pendingChallenge;
    res.redirect('/key/result');
  } catch (err) {
    console.error('[unlock/lootlabs]', err);
    req.session.flash = { error: 'Key generation failed. Please try again.' };
    res.redirect('/license');
  }
});

// ---------------------------------------------------------------
// Key result (shown once, then cleared from session)
// ---------------------------------------------------------------
router.get('/key/result', requireLogin, (req, res) => {
  const key = req.session.generatedKey;
  if (!key) {
    req.session.flash = { error: 'No key available. Please generate a new one.' };
    return res.redirect('/license');
  }
  // Consume immediately — shown exactly once
  delete req.session.generatedKey;
  res.render('key_result', { title: 'Your Key – DENG Tool', key });
});

module.exports = router;
