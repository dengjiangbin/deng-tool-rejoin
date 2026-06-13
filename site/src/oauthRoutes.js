'use strict';
/**
 * Discord OAuth routes — mounted early in app.js (before tracker routers)
 * so login is not blocked by heavy inventory/tracker middleware or session I/O.
 */

const express = require('express');
const { createUserRateLimit } = require('./rateLimitUtils');
const {
  LOGIN_HOME,
  safeReturnPath,
  buildDiscordAuthUrl,
} = require('./auth');
const {
  canonicalPublicUrl,
  requestHost,
  isCanonicalPublicHost,
} = require('./publicDomain');
const { handleDiscordOAuthCallback } = require('./discordOAuthCallback');

const router = express.Router();

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
  const ret = safeReturnPath(req.query.return || req.query.next)
    || safeReturnPath(req.session && req.session.authReturnTo);
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
    req.session.flash = { ...(req.session.flash || {}), error: 'Discord login is not configured.' };
    const publicBase = oauthLoginRedirectHost(req);
    return res.redirect(publicBase ? `${publicBase}${LOGIN_HOME}` : LOGIN_HOME);
  }

  res.redirect(authUrl);
  if (process.env.NODE_ENV === 'production') {
    console.log('[auth/discord] redirect_ms=%d host=%s apk=%s', Date.now() - started, requestHost(req), oauthApkReturn);
  }
});

router.get('/auth/discord/callback', authLimiter, (req, res) => handleDiscordOAuthCallback(req, res));
router.get('/api/aio/auth/callback', authLimiter, (req, res) => handleDiscordOAuthCallback(req, res));

/** Legacy cross-host session bridge — kept for backward compatibility only. */
router.get('/auth/web-bridge', authLimiter, async (req, res) => {
  const crypto = require('crypto');
  const aioSessionStore = require('./aioSessionStore');
  const { upsertDiscordUser, toSessionUser, LOGIN_HOME: loginHome } = require('./auth');

  const bridgeCode = typeof req.query.code === 'string' ? req.query.code.trim() : '';
  const authReturnTo = safeReturnPath(req.query.return) || '/dashboard';
  if (!bridgeCode) {
    req.session.flash = { ...(req.session.flash || {}), error: 'Invalid sign-in link. Please try again.' };
    return res.redirect(loginHome);
  }
  const bridged = aioSessionStore.consumeLoginCode(bridgeCode);
  if (!bridged || !bridged.discordUserId) {
    req.session.flash = { ...(req.session.flash || {}), error: 'Sign-in link expired. Please try Discord login again.' };
    return res.redirect(loginHome);
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
    req.session.flash = { ...(req.session.flash || {}), error: 'Discord sign-in failed. Please try again.' };
    return res.redirect(loginHome);
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
        req.session.flash = { ...(req.session.flash || {}), error: 'Session error. Please try again.' };
        res.redirect(loginHome);
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
