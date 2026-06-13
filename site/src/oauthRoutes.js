'use strict';
/**
 * Discord OAuth routes — mounted early in app.js (before tracker routers)
 * so login is not blocked by heavy inventory/tracker middleware or session I/O.
 */

const express = require('express');
const crypto = require('crypto');
const { createUserRateLimit } = require('./rateLimitUtils');
const {
  LOGIN_HOME,
  safeReturnPath,
  buildDiscordAuthUrl,
  exchangeDiscordCode,
  fetchDiscordUser,
  upsertDiscordUser,
  toSessionUser,
} = require('./auth');
const {
  canonicalPublicUrl,
  requestHost,
  isCanonicalPublicHost,
} = require('./publicDomain');
const oauthStateStore = require('./oauthStateStore');
const aioSessionStore = require('./aioSessionStore');

const router = express.Router();

function renderOAuthDeepLinkHtml(deepLink) {
  const safe = String(deepLink).replace(/"/g, '&quot;');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="0; url=${safe}">
<title>Returning to DENG AIO…</title></head>
<body><p>Signing you in… <a href="${safe}">Tap here if the app does not open</a>.</p>
<script>location.replace(${JSON.stringify(deepLink)});</script></body></html>`;
}

function safeFlash(req, key, value) {
  req.session.flash = { ...(req.session.flash || {}), [key]: value };
}

const authLimiter = createUserRateLimit({
  keyPrefix: 'auth-callback:',
  windowMs: 15 * 60 * 1000,
  max: 40,
  handlerOptions: {
    jsonError: 'too_many_login_attempts',
    jsonMessage: 'Too many login attempts. Please wait before trying again.',
    htmlMessage: 'Too many login attempts. Please wait before trying again.',
    redirectTo: '/login',
  },
});

function oauthLoginRedirectHost(req) {
  return isCanonicalPublicHost(requestHost(req)) ? canonicalPublicUrl() : '';
}

router.get('/auth/discord', (req, res) => {
  const started = Date.now();
  const ret = safeReturnPath(req.query.return || req.query.next);
  const oauthApkReturn = req.query.apk === '1' || req.query.apk === 'true';
  const returnPublicUrl = isCanonicalPublicHost(requestHost(req))
    || req.query.public_return === '1'
    || req.query.public_return === 'true'
    ? canonicalPublicUrl()
    : '';

  let authUrl;
  try {
    authUrl = buildDiscordAuthUrl(req, {
      authReturnTo: ret || '/dashboard',
      returnPublicUrl,
      oauthApkReturn,
    });
  } catch (err) {
    console.error('[auth/discord]', err.message || err);
    safeFlash(req, 'error', 'Discord login is not configured.');
    const publicBase = oauthLoginRedirectHost(req);
    return res.redirect(publicBase ? `${publicBase}${LOGIN_HOME}` : LOGIN_HOME);
  }

  // State lives in oauthStateStore — no blocking session file write before Discord.
  res.redirect(authUrl);
  if (process.env.NODE_ENV === 'production') {
    console.log('[auth/discord] redirect_ms=%d host=%s apk=%s', Date.now() - started, requestHost(req), oauthApkReturn);
  }
});

router.get('/auth/discord/callback', authLimiter, async (req, res) => {
  const started = Date.now();
  const { code, state, error: oauthError } = req.query;

  if (oauthError) {
    console.warn('[auth/discord/callback] category=oauth_denied discord_error=%s', String(oauthError).slice(0, 64));
    safeFlash(req, 'error', `Discord denied access: ${oauthError}`);
    const publicBase = oauthLoginRedirectHost(req);
    return res.redirect(publicBase ? `${publicBase}${LOGIN_HOME}` : LOGIN_HOME);
  }

  const stored = oauthStateStore.consumeOAuthState(state);
  if (!code) {
    console.warn('[auth/discord/callback] category=code_missing state_present=%s', !!stored);
    safeFlash(req, 'error', 'Invalid OAuth response. Please try again.');
    const publicBase = oauthLoginRedirectHost(req);
    return res.redirect(publicBase ? `${publicBase}${LOGIN_HOME}` : LOGIN_HOME);
  }
  if (!stored) {
    console.warn('[auth/discord/callback] category=state_missing_or_expired code_present=true');
    safeFlash(req, 'error', 'Login session expired. Please try again.');
    const publicBase = oauthLoginRedirectHost(req);
    return res.redirect(publicBase ? `${publicBase}${LOGIN_HOME}` : LOGIN_HOME);
  }

  let tokens;
  try {
    tokens = await exchangeDiscordCode(String(code), stored.redirectUri);
  } catch (_err) {
    safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
    const publicBase = oauthLoginRedirectHost(req);
    return res.redirect(publicBase ? `${publicBase}${LOGIN_HOME}` : LOGIN_HOME);
  }

  let discordUser;
  try {
    discordUser = await fetchDiscordUser(tokens.access_token);
  } catch (err) {
    const status = (err.response && err.response.status) || 'unknown';
    console.error('[auth/discord/callback] category=user_fetch_failed http_status=%s', status);
    safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
    const publicBase = oauthLoginRedirectHost(req);
    return res.redirect(publicBase ? `${publicBase}${LOGIN_HOME}` : LOGIN_HOME);
  }

  let siteUser;
  try {
    siteUser = await upsertDiscordUser(discordUser, tokens);
  } catch (err) {
    console.error('[auth/discord/callback] category=site_user_upsert_failed error=%s', err.message);
    safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
    const publicBase = oauthLoginRedirectHost(req);
    return res.redirect(publicBase ? `${publicBase}${LOGIN_HOME}` : LOGIN_HOME);
  }

  const returnPublicUrl = String(stored.returnPublicUrl || '').replace(/\/+$/, '');
  const oauthApkReturn = stored.oauthApkReturn === true;
  const authReturnTo = safeReturnPath(stored.authReturnTo) || '/dashboard';

  const sessionUser = toSessionUser(siteUser);
  const canonicalBase = canonicalPublicUrl();
  const needsPublicBridge = Boolean(
    returnPublicUrl
    && returnPublicUrl.startsWith(canonicalBase)
    && !isCanonicalPublicHost(requestHost(req)),
  );

  if (oauthApkReturn) {
    try {
      const { code: loginCode } = aioSessionStore.createLoginCode({
        discordUserId: discordUser.id,
        siteUserId: siteUser && siteUser.id ? siteUser.id : null,
        username: sessionUser.username || discordUser.username || null,
        avatar: discordUser.avatar || null,
      });
      const scheme = (process.env.DENG_AIO_APP_SCHEME || 'deng-aio').trim();
      const deepLink = `${scheme}://auth/callback?code=${encodeURIComponent(loginCode)}`;
      res.set('Content-Type', 'text/html; charset=utf-8');
      console.log('[auth/discord/callback] ok apk_bridge_ms=%d', Date.now() - started);
      return res.status(200).send(renderOAuthDeepLinkHtml(deepLink));
    } catch (bridgeErr) {
      console.error('[auth/discord/callback] category=apk_bridge_failed error=%s', bridgeErr.message);
      safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
      const publicBase = oauthLoginRedirectHost(req);
      return res.redirect(publicBase ? `${publicBase}${LOGIN_HOME}` : LOGIN_HOME);
    }
  }

  if (needsPublicBridge) {
    try {
      const { code: bridgeCode } = aioSessionStore.createLoginCode({
        discordUserId: discordUser.id,
        siteUserId: siteUser && siteUser.id ? siteUser.id : null,
        username: sessionUser.username || discordUser.username || null,
        avatar: discordUser.avatar || null,
      });
      const dest = `${returnPublicUrl}/auth/web-bridge?code=${encodeURIComponent(bridgeCode)}&return=${encodeURIComponent(authReturnTo)}`;
      console.log('[auth/discord/callback] ok web_bridge_ms=%d', Date.now() - started);
      return res.redirect(dest);
    } catch (bridgeErr) {
      console.error('[auth/discord/callback] category=web_bridge_failed error=%s', bridgeErr.message);
      safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
      const publicBase = oauthLoginRedirectHost(req);
      return res.redirect(publicBase ? `${publicBase}${LOGIN_HOME}` : LOGIN_HOME);
    }
  }

  return new Promise((resolve) => {
    req.session.regenerate((regenErr) => {
      if (regenErr) {
        console.error('[auth/discord/callback] category=session_regenerate_failed error=%s', regenErr.message);
        safeFlash(req, 'error', 'Session error. Please try again.');
        res.redirect(LOGIN_HOME);
        return resolve();
      }
      req.session.user = sessionUser;
      req.session.site_user_id = siteUser && siteUser.id ? siteUser.id : null;
      req.session.discord_user_id = req.session.user && req.session.user.discord_user_id
        ? String(req.session.user.discord_user_id)
        : null;
      req.session.csrfToken = crypto.randomBytes(32).toString('hex');
      req.session.flash = { success: `Welcome, ${req.session.user.username}!` };
      req.session.save((saveErr) => {
        if (saveErr) {
          console.error('[auth/discord/callback] category=session_save_failed error=%s', saveErr.message);
        }
        console.log('[auth/discord/callback] ok same_host_ms=%d', Date.now() - started);
        res.redirect(authReturnTo);
        resolve();
      });
    });
  });
});

router.get('/auth/web-bridge', authLimiter, async (req, res) => {
  const bridgeCode = typeof req.query.code === 'string' ? req.query.code.trim() : '';
  const authReturnTo = safeReturnPath(req.query.return) || '/dashboard';
  if (!bridgeCode) {
    safeFlash(req, 'error', 'Invalid sign-in link. Please try again.');
    return res.redirect(LOGIN_HOME);
  }
  const bridged = aioSessionStore.consumeLoginCode(bridgeCode);
  if (!bridged || !bridged.discordUserId) {
    safeFlash(req, 'error', 'Sign-in link expired. Please try Discord login again.');
    return res.redirect(LOGIN_HOME);
  }
  let siteUser = null;
  try {
    const discordUser = {
      id: bridged.discordUserId,
      username: bridged.username || `user_${String(bridged.discordUserId).slice(-4)}`,
      global_name: bridged.username || null,
      avatar: bridged.avatar || null,
    };
    siteUser = await upsertDiscordUser(discordUser, {}, { allowFallback: false });
  } catch (err) {
    console.error('[auth/web-bridge] category=site_user_resolve_failed error=%s', err.message);
    safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
    return res.redirect(LOGIN_HOME);
  }
  const sessionUser = toSessionUser(siteUser || {
    id: bridged.siteUserId,
    discord_user_id: bridged.discordUserId,
    discord_username: bridged.username,
    discord_avatar: bridged.avatar,
    username: bridged.username,
  });
  return new Promise((resolve) => {
    req.session.regenerate((regenErr) => {
      if (regenErr) {
        console.error('[auth/web-bridge] category=session_regenerate_failed error=%s', regenErr.message);
        safeFlash(req, 'error', 'Session error. Please try again.');
        res.redirect(LOGIN_HOME);
        return resolve();
      }
      req.session.user = sessionUser;
      req.session.site_user_id = siteUser && siteUser.id ? siteUser.id : (bridged.siteUserId || null);
      req.session.discord_user_id = bridged.discordUserId;
      req.session.csrfToken = crypto.randomBytes(32).toString('hex');
      req.session.flash = { success: `Welcome, ${sessionUser.username}!` };
      req.session.save((saveErr) => {
        if (saveErr) {
          console.error('[auth/web-bridge] category=session_save_failed error=%s', saveErr.message);
        }
        res.redirect(authReturnTo);
        resolve();
      });
    });
  });
});

module.exports = router;
