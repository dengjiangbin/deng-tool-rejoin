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
const { describeSessionCookieConfig } = require('./sessionCookieConfig');
const { handleDiscordOAuthCallback, requestTransportProof, renderApkOpenHandoffHtml } = require('./discordOAuthCallback');

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
  const oauthApkReturn = req.query.apk === '1' || req.query.apk === 'true'
    || req.query.client === 'apk';
  const returnPublicUrl = isCanonicalPublicHost(requestHost(req))
    || req.query.public_return === '1'
    || req.query.public_return === 'true'
    || oauthApkReturn
    ? canonicalPublicUrl()
    : '';

  let authUrl;
  try {
    authUrl = buildDiscordAuthUrl(req, {
      authReturnTo: ret || (oauthApkReturn ? '/tracker?apk=1' : '/dashboard'),
      returnPublicUrl,
      oauthApkReturn,
    });
  } catch (err) {
    console.error('[auth/discord]', err.message || err);
    req.session.flash = { ...(req.session.flash || {}), error: 'Discord login is not configured.' };
    const publicBase = oauthLoginRedirectHost(req);
    return res.redirect(publicBase ? `${publicBase}${LOGIN_HOME}` : LOGIN_HOME);
  }

  if (oauthApkReturn) {
    console.log(
      '[auth/discord] APK_AUTH_START return=%s host=%s',
      ret || '/tracker?apk=1',
      requestHost(req),
    );
  }

  res.redirect(authUrl);
  if (process.env.NODE_ENV === 'production') {
    console.log('[auth/discord] redirect_ms=%d host=%s apk=%s', Date.now() - started, requestHost(req), oauthApkReturn);
  }
});

router.get('/auth/discord/callback', authLimiter, (req, res) => handleDiscordOAuthCallback(req, res));
router.get('/api/aio/auth/callback', authLimiter, (req, res) => handleDiscordOAuthCallback(req, res));

/** APK OAuth handoff page — intent:// opens the installed app (Custom Tabs safe). */
router.get('/auth/apk-open', authLimiter, (req, res) => {
  const code = typeof req.query.code === 'string' ? req.query.code.trim() : '';
  const manual = req.query.manual === '1' || req.query.manual === 'true';
  if (!code) {
    console.warn('[auth/apk-open] APK_AUTH_FAIL_STAGE=handoff_missing');
    return res.redirect(`${LOGIN_HOME}?apk=1&auth_error=handoff_missing`);
  }
  console.log(
    '[auth/apk-open] APK_AUTH_DEEPLINK_RENDERED codeLen=%d manual=%s',
    code.length,
    manual,
  );
  res.set('Cache-Control', 'no-store');
  res.set('Content-Type', 'text/html; charset=utf-8');
  return res.status(200).send(renderApkOpenHandoffHtml(code));
});

/** Legacy cross-host session bridge — kept for backward compatibility only. */
router.get('/auth/web-bridge', authLimiter, async (req, res) => {
  const crypto = require('crypto');
  const aioSessionStore = require('./aioSessionStore');
  const { upsertDiscordUser, toSessionUser, LOGIN_HOME: loginHome } = require('./auth');

  const bridgeCode = typeof req.query.code === 'string' ? req.query.code.trim() : '';
  const authReturnTo = safeReturnPath(req.query.return) || '/dashboard';
  const apkFlow = req.query.apk === '1' || req.query.apk === 'true';
  if (!bridgeCode) {
    console.warn('[auth/web-bridge] APK_AUTH_FAIL_STAGE=handoff_missing apk=%s', apkFlow);
    req.session.flash = { ...(req.session.flash || {}), error: 'Invalid sign-in link. Please try again.' };
    const dest = apkFlow ? `${loginHome}?apk=1&auth_error=handoff_missing` : loginHome;
    return res.redirect(dest);
  }
  const bridged = aioSessionStore.consumeLoginCode(bridgeCode);
  if (!bridged || !bridged.discordUserId) {
    console.warn('[auth/web-bridge] APK_AUTH_FAIL_STAGE=handoff_expired apk=%s', apkFlow);
    req.session.flash = { ...(req.session.flash || {}), error: 'Sign-in link expired. Please try Discord login again.' };
    const dest = apkFlow ? `${loginHome}?apk=1&auth_error=handoff_expired` : loginHome;
    return res.redirect(dest);
  }
  console.log('[auth/web-bridge] APK_AUTH_WEB_BRIDGE_LOADED discordUserId=%s apk=%s', bridged.discordUserId, apkFlow);
  let siteUser = null;
  try {
    const discordUser = {
      id: bridged.discordUserId,
      username: bridged.username || `user_${String(bridged.discordUserId).slice(-4)}`,
      global_name: bridged.username || null,
      avatar: bridged.avatar || null,
    };
    siteUser = await upsertDiscordUser(discordUser, {}, { allowFallback: true });
  } catch (err) {
    console.warn(
      '[auth/web-bridge] category=site_user_resolve_fallback discordUserId=%s error=%s',
      bridged.discordUserId,
      err.message,
    );
    siteUser = {
      id: bridged.siteUserId || null,
      discord_user_id: bridged.discordUserId,
      discord_username: bridged.username || null,
      discord_avatar: bridged.avatar || null,
      username: bridged.username || `user_${String(bridged.discordUserId).slice(-4)}`,
    };
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
          req.session.flash = { ...(req.session.flash || {}), error: 'Could not save your session. Please try again.' };
          return res.redirect(loginHome);
        }
        console.log(
          '[auth/web-bridge] APK_AUTH_COOKIE_SET return=%s discordUserId=%s cookie=%j',
          authReturnTo,
          bridged.discordUserId,
          describeSessionCookieConfig(),
        );
        console.log(
          '[auth/web-bridge] APK_AUTH_SESSION_CREATED return=%s discordUserId=%s',
          authReturnTo,
          bridged.discordUserId,
        );
        res.redirect(authReturnTo);
        resolve();
      });
    });
  });
});

/** Auth/session transport probe for OAuth callback debugging behind Cloudflare. */
router.get('/api/internal/auth-probe', (req, res) => {
  return sendAuthDebugPayload(req, res, { probe: true });
});

/** Guarded auth debug — session file path, cookie receipt, failure reasons. */
router.get('/api/internal/auth-debug', (req, res) => sendAuthDebugPayload(req, res));

function sendAuthDebugPayload(req, res, extra = {}) {
  const token = process.env.STABILITY_STATUS_TOKEN || process.env.AUTH_DEBUG_TOKEN || '';
  if (token) {
    const provided = String(req.headers['x-stability-token'] || req.headers['x-auth-debug-token'] || req.query.token || '');
    if (provided !== token) {
      return res.status(403).json({ ok: false, error: 'forbidden' });
    }
  }
  const { getSessionStoreMetrics } = require('./sessionStore');
  const sessionMetrics = getSessionStoreMetrics(process.env.TOOL_SITE_SESSION_DIR);
  const cookieHeader = String(req.headers.cookie || '');
  const hasDengSid = /(?:^|;\s*)deng_sid=/.test(cookieHeader);
  const sessionUser = req.session && req.session.user ? req.session.user : null;
  let authFailureReason = null;
  if (!req.session) authFailureReason = 'session_middleware_skipped_or_no_cookie';
  else if (!sessionUser) authFailureReason = hasDengSid ? 'cookie_present_but_session_empty' : 'missing_deng_sid_cookie';
  res.set('Cache-Control', 'no-store');
  return res.json({
    ok: true,
    ...extra,
    ...requestTransportProof(req),
    ip: req.ip,
    headers: {
      'x-forwarded-proto': req.headers['x-forwarded-proto'] || null,
      'x-forwarded-host': req.headers['x-forwarded-host'] || null,
      'x-forwarded-for': req.headers['x-forwarded-for'] || null,
      'cf-connecting-ip': req.headers['cf-connecting-ip'] || null,
    },
    cookie: {
      received: hasDengSid,
      headerPresent: !!cookieHeader,
    },
    session: {
      hasSession: !!req.session,
      sessionIdPresent: !!(req.session && req.sessionID),
      sessionIdPrefix: req.session && req.sessionID ? String(req.sessionID).slice(0, 8) : null,
      authenticated: !!sessionUser,
      discordUserId: sessionUser?.discord_user_id || req.session?.discord_user_id || null,
      siteUserId: req.session?.site_user_id || sessionUser?.id || null,
      sessionKeys: req.session ? Object.keys(req.session).filter((k) => !k.startsWith('cookie')) : [],
      authFailureReason,
    },
    sessionStore: sessionMetrics,
    trustProxy: req.app?.get('trust proxy'),
  });
}

module.exports = router;
