'use strict';
/**
 * Shared Discord OAuth callback handler for web and APK flows.
 * Mounted at GET /auth/discord/callback and GET /api/aio/auth/callback.
 */

const crypto = require('crypto');
const {
  LOGIN_HOME,
  safeReturnPath,
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

function renderOAuthDeepLinkHtml(deepLink) {
  const safe = String(deepLink).replace(/"/g, '&quot;');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="0; url=${safe}">
<title>Returning to DENG All In One…</title></head>
<body><p>Signing you in… <a href="${safe}">Tap here if the app does not open</a>.</p>
<script>location.replace(${JSON.stringify(deepLink)});</script></body></html>`;
}

function safeFlash(req, key, value) {
  req.session.flash = { ...(req.session.flash || {}), [key]: value };
}

function oauthLoginRedirectHost(req) {
  return isCanonicalPublicHost(requestHost(req)) ? canonicalPublicUrl() : '';
}

function loginRedirect(req) {
  const publicBase = oauthLoginRedirectHost(req);
  return publicBase ? `${publicBase}${LOGIN_HOME}` : LOGIN_HOME;
}

function completeWebSession(req, res, { sessionUser, siteUser, authReturnTo, started }) {
  return new Promise((resolve) => {
    req.session.regenerate((regenErr) => {
      if (regenErr) {
        console.error('[discord-oauth-callback] category=session_regenerate_failed error=%s', regenErr.message);
        safeFlash(req, 'error', 'Session error. Please try again.');
        res.redirect(LOGIN_HOME);
        return resolve();
      }
      req.session.user = sessionUser;
      req.session.site_user_id = siteUser && siteUser.id ? siteUser.id : null;
      req.session.discord_user_id = sessionUser.discord_user_id
        ? String(sessionUser.discord_user_id)
        : null;
      req.session.csrfToken = crypto.randomBytes(32).toString('hex');
      req.session.flash = { success: `Welcome, ${sessionUser.username}!` };
      if (req.session.authReturnTo) delete req.session.authReturnTo;
      req.session.save((saveErr) => {
        if (saveErr) {
          console.error('[discord-oauth-callback] category=session_save_failed error=%s', saveErr.message);
        }
        console.log('[discord-oauth-callback] ok web_session_ms=%d return=%s', Date.now() - started, authReturnTo);
        res.redirect(authReturnTo);
        resolve();
      });
    });
  });
}

async function handleDiscordOAuthCallback(req, res) {
  const started = Date.now();
  const routePath = req.path || '/auth/discord/callback';
  const { code, state, error: oauthError } = req.query;

  if (oauthError) {
    console.warn('[discord-oauth-callback] category=oauth_denied route=%s discord_error=%s', routePath, String(oauthError).slice(0, 64));
    safeFlash(req, 'error', `Discord denied access: ${oauthError}`);
    return res.redirect(loginRedirect(req));
  }

  const stored = oauthStateStore.consumeOAuthState(state);
  if (!code) {
    console.warn('[discord-oauth-callback] category=code_missing route=%s state_present=%s', routePath, !!stored);
    safeFlash(req, 'error', 'Invalid OAuth response. Please try again.');
    return res.redirect(loginRedirect(req));
  }
  if (!stored) {
    console.warn('[discord-oauth-callback] category=state_missing_or_expired route=%s code_present=true', routePath);
    safeFlash(req, 'error', 'Login session expired. Please try again.');
    return res.redirect(loginRedirect(req));
  }

  let tokens;
  try {
    tokens = await exchangeDiscordCode(String(code), stored.redirectUri);
  } catch (_err) {
    safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
    return res.redirect(loginRedirect(req));
  }

  let discordUser;
  try {
    discordUser = await fetchDiscordUser(tokens.access_token);
  } catch (err) {
    const status = (err.response && err.response.status) || 'unknown';
    console.error('[discord-oauth-callback] category=user_fetch_failed route=%s http_status=%s', routePath, status);
    safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
    return res.redirect(loginRedirect(req));
  }

  let siteUser;
  try {
    siteUser = await upsertDiscordUser(discordUser, tokens);
  } catch (err) {
    if (stored.oauthApkReturn === true) {
      console.warn(
        '[discord-oauth-callback] apk upsert fallback discord_id=%s reason=%s',
        discordUser.id,
        err.message,
      );
      siteUser = {
        id: null,
        discord_user_id: discordUser.id,
        discord_username: discordUser.username || discordUser.global_name || null,
        discord_avatar: discordUser.avatar || null,
        username: discordUser.username || discordUser.global_name || `user_${String(discordUser.id).slice(-4)}`,
        email: discordUser.email || null,
      };
    } else {
      console.error('[discord-oauth-callback] category=site_user_upsert_failed route=%s error=%s', routePath, err.message);
      safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
      return res.redirect(loginRedirect(req));
    }
  }

  const oauthApkReturn = stored.oauthApkReturn === true;
  const authReturnTo = safeReturnPath(stored.authReturnTo) || '/dashboard';
  const sessionUser = toSessionUser(siteUser);

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
      console.log('[discord-oauth-callback] ok apk_bridge route=%s ms=%d', routePath, Date.now() - started);
      return res.status(200).send(renderOAuthDeepLinkHtml(deepLink));
    } catch (bridgeErr) {
      console.error('[discord-oauth-callback] category=apk_bridge_failed route=%s error=%s', routePath, bridgeErr.message);
      safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
      return res.redirect(loginRedirect(req));
    }
  }

  return completeWebSession(req, res, { sessionUser, siteUser, authReturnTo, started });
}

module.exports = {
  handleDiscordOAuthCallback,
};
