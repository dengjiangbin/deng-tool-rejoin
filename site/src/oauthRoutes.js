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

/**
 * APK WebView cookie-priming interstitial.
 *
 * Android WebView frequently fails to persist/send a Set-Cookie that arrives on
 * a 302 redirect response before it follows the redirect to an auth-guarded
 * page — the request to /tracker then arrives without `deng_sid` and bounces to
 * /login ("web_bridge_cookie_missing"). Returning the cookie on a 200 HTML page
 * and navigating via JS after a short tick guarantees the WebView commits the
 * session cookie first, so the subsequent /tracker load is authenticated.
 */
function renderWebBridgeRedirectHtml(returnTo) {
  const safe = String(returnTo || '/tracker');
  const jsTarget = JSON.stringify(safe);
  const attr = safe
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="1;url=${attr}">
<title>Signing you in…</title>
<style>html,body{margin:0;height:100%;background:#0D0F14;color:#e8eef9;font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif}
.wrap{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px}
.spinner{width:36px;height:36px;border-radius:50%;border:3px solid rgba(255,255,255,.18);border-top-color:#4f8cff;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}</style></head>
<body><div class="wrap"><div class="spinner" aria-hidden="true"></div><div>Signing you in…</div></div>
<script>(function(){var t=${jsTarget};function go(){try{window.location.replace(t);}catch(e){window.location.href=t;}}setTimeout(go,120);})();</script></body></html>`;
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
      authReturnTo: ret || (oauthApkReturn ? '/tracker?apk=1' : '/tracker'),
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
  const authReturnTo = safeReturnPath(req.query.return) || '/tracker';
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
        // Prime the session cookie on a 200 HTML page (not a 302) so the WebView
        // reliably stores `deng_sid` before navigating to the guarded page.
        res.set('Cache-Control', 'no-store');
        res.status(200).type('html').send(renderWebBridgeRedirectHtml(authReturnTo));
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
