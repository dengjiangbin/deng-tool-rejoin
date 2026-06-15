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
const { describeSessionCookieConfig } = require('./sessionCookieConfig');
const oauthStateStore = require('./oauthStateStore');
const aioSessionStore = require('./aioSessionStore');

/** APK handoff page — intent:// only for auto-open (avoid double custom-scheme + intent race). */
function renderApkOpenHandoffHtml(loginCode, mobileState) {
  const code = String(loginCode || '').trim();
  const stateNonce = String(mobileState || '').trim();
  const scheme = (process.env.DENG_AIO_APP_SCHEME || 'deng-aio').trim();
  const pkg = String(process.env.APK_ANDROID_PACKAGE || 'my.id.deng.monitor').trim();
  const publicBase = canonicalPublicUrl() || 'https://aio.deng.my.id';
  const stateQuery = stateNonce ? `&state=${encodeURIComponent(stateNonce)}` : '';
  const query = `?code=${encodeURIComponent(code)}${stateQuery}`;
  const deepLink = `${scheme}://auth/callback${query}`;
  const manualPage = `${publicBase}/auth/apk-open${query}&manual=1`;
  const intentUrl = `intent://auth/callback${query}#Intent;scheme=${scheme};package=${pkg};S.browser_fallback_url=${encodeURIComponent(manualPage)};end`;
  const safeDeep = deepLink.replace(/"/g, '&quot;');
  const safeIntent = intentUrl.replace(/"/g, '&quot;');
  const jsIntent = intentUrl.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const jsDeep = deepLink.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Returning to DENG All In One…</title></head>
<body><p>Opening DENG All In One…</p>
<p><a id="open-app" href="${safeIntent}">Tap here if the app does not open</a></p>
<p><a href="${safeDeep}">Alternate open link</a></p>
<script>
(function(){
  var intent="${jsIntent}";
  var deep="${jsDeep}";
  try { fetch("/api/aio/auth/apk-open-attempt",{method:"POST",credentials:"omit"}).catch(function(){}); } catch (e0) {}
  try { location.replace(intent); } catch (e1) {
    try { document.getElementById("open-app").click(); } catch (e2) {}
  }
  setTimeout(function(){
    try { if (!document.hidden) location.replace(deep); } catch (e3) {}
  }, 1200);
})();
</script></body></html>`;
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

function requestTransportProof(req) {
  return {
    host: requestHost(req),
    secure: !!req.secure,
    protocol: req.protocol,
    xForwardedProto: req.headers['x-forwarded-proto'] || null,
    xForwardedHost: req.headers['x-forwarded-host'] || null,
    cookieConfig: describeSessionCookieConfig(),
  };
}

function completeWebSession(req, res, { sessionUser, siteUser, authReturnTo, started, routePath }) {
  return new Promise((resolve) => {
    req.session.regenerate((regenErr) => {
      if (regenErr) {
        console.error('[discord-oauth-callback] category=session_regenerate_failed route=%s error=%s', routePath, regenErr.message);
        safeFlash(req, 'error', 'Session error. Please try again.');
        res.redirect(loginRedirect(req));
        return resolve({ ok: false, reason: 'session_regenerate_failed' });
      }

      req.session.user = sessionUser;
      req.session.site_user_id = siteUser && siteUser.id ? siteUser.id : null;
      req.session.discord_user_id = sessionUser.discord_user_id
        ? String(sessionUser.discord_user_id)
        : null;
      req.session.csrfToken = crypto.randomBytes(32).toString('hex');
      req.session.flash = { success: `Welcome, ${sessionUser.username}!` };
      if (req.session.authReturnTo) delete req.session.authReturnTo;

      const sessionPreview = {
        userId: sessionUser.id || null,
        discordUserId: sessionUser.discord_user_id || null,
        username: sessionUser.username || null,
        siteUserId: req.session.site_user_id,
      };

      req.session.save((saveErr) => {
        if (saveErr) {
          console.error(
            '[discord-oauth-callback] category=session_save_failed route=%s error=%s session=%j transport=%j',
            routePath,
            saveErr.message,
            sessionPreview,
            requestTransportProof(req),
          );
          safeFlash(req, 'error', 'Could not save your session. Please try again.');
          res.redirect(loginRedirect(req));
          return resolve({ ok: false, reason: 'session_save_failed' });
        }

        console.log(
          '[discord-oauth-callback] category=web_session_ok route=%s ms=%d return=%s session=%j transport=%j',
          routePath,
          Date.now() - started,
          authReturnTo,
          sessionPreview,
          requestTransportProof(req),
        );

        res.redirect(authReturnTo);
        resolve({ ok: true, authReturnTo });
      });
    });
  });
}

async function handleDiscordOAuthCallback(req, res) {
  const started = Date.now();
  const routePath = req.path || '/auth/discord/callback';
  const { code, state, error: oauthError } = req.query;

  console.log(
    '[discord-oauth-callback] category=callback_hit route=%s code=%s state=%s oauthError=%s transport=%j',
    routePath,
    code ? 'present' : 'missing',
    state ? 'present' : 'missing',
    oauthError ? String(oauthError).slice(0, 64) : null,
    requestTransportProof(req),
  );

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
    console.warn('[discord-oauth-callback] APK_AUTH_FAIL reason=state_invalid route=%s', routePath);
    safeFlash(req, 'error', 'Login session expired. Please try again.');
    return res.redirect(loginRedirect(req));
  }
  console.log('[discord-oauth-callback] APK_AUTH_STATE_VALID apk=%s return=%s', stored.oauthApkReturn === true, stored.authReturnTo || '/dashboard');

  let tokens;
  try {
    tokens = await exchangeDiscordCode(String(code), stored.redirectUri);
    console.log('[discord-oauth-callback] category=token_exchange_ok route=%s redirect_uri=%s', routePath, stored.redirectUri);
  } catch (err) {
    console.error('[discord-oauth-callback] category=token_exchange_failed route=%s redirect_uri=%s error=%s', routePath, stored.redirectUri, err.message);
    safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
    return res.redirect(loginRedirect(req));
  }

  let discordUser;
  try {
    discordUser = await fetchDiscordUser(tokens.access_token);
    console.log(
      '[discord-oauth-callback] category=profile_ok route=%s discord_id=%s username=%s',
      routePath,
      discordUser.id,
      discordUser.username || discordUser.global_name || 'unknown',
    );
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
  const authReturnTo = safeReturnPath(stored.authReturnTo) || '/tracker';
  const sessionUser = toSessionUser(siteUser);

  if (oauthApkReturn) {
    try {
      const mobileTransactionId = stored.mobileTransactionId || null;
      const userForCode = {
        discordUserId: discordUser.id,
        siteUserId: siteUser && siteUser.id ? siteUser.id : null,
        username: sessionUser.username || discordUser.username || null,
        avatar: discordUser.avatar || null,
      };
      const scheme = (process.env.DENG_AIO_APP_SCHEME || 'deng-aio').trim();
      const publicBase = canonicalPublicUrl() || oauthLoginRedirectHost(req) || '';

      // New first-party WebView bootstrap lane: bind the resolved user to the
      // mobile-auth transaction, mint a single-use consume code + state nonce,
      // and hand them to the APK (deep link now, polling as fallback). The APK
      // loads /mobile-auth/consume?code&state INSIDE the WebView so the real
      // session cookie is set by a first-party aio.deng.my.id response.
      if (mobileTransactionId) {
        const bound = aioSessionStore.authenticateMobileAuthTransaction(mobileTransactionId, userForCode);
        if (!bound || !bound.code) {
          console.warn('[discord-oauth-callback] APK_AUTH_FAIL reason=txn_expired route=%s', routePath);
          safeFlash(req, 'error', 'Sign-in link expired. Please try Discord login again.');
          return res.redirect(loginRedirect(req));
        }
        const handoffPath = `/auth/apk-open?code=${encodeURIComponent(bound.code)}&state=${encodeURIComponent(bound.state)}`;
        const handoffUrl = publicBase ? `${publicBase}${handoffPath}` : handoffPath;
        console.log('[discord-oauth-callback] APK_AUTH_MOBILE_TXN_BOUND route=%s txn=%s ms=%d', routePath, String(mobileTransactionId).slice(0, 8), Date.now() - started);
        console.log('[discord-oauth-callback] category=mobile_consume_handoff_created route=%s', routePath);
        res.set('Cache-Control', 'no-store');
        return res.redirect(302, handoffUrl);
      }

      // Legacy lane (older APK builds with no transaction): mint a login code
      // for the native exchange + web-bridge handoff.
      const { code: loginCode } = aioSessionStore.createLoginCode(userForCode);
      const handoffPath = `/auth/apk-open?code=${encodeURIComponent(loginCode)}`;
      const handoffUrl = publicBase ? `${publicBase}${handoffPath}` : handoffPath;
      console.log('[discord-oauth-callback] APK_AUTH_HANDOFF_CREATED route=%s scheme=%s ms=%d', routePath, scheme, Date.now() - started);
      console.log('[discord-oauth-callback] APK_AUTH_RETURN_TARGET route=%s handoff=%s', routePath, handoffPath);
      console.log('[discord-oauth-callback] category=mobile_handoff_created route=%s ms=%d', routePath, Date.now() - started);
      res.set('Cache-Control', 'no-store');
      return res.redirect(302, handoffUrl);
    } catch (bridgeErr) {
      console.error('[discord-oauth-callback] category=apk_bridge_failed route=%s error=%s', routePath, bridgeErr.message);
      safeFlash(req, 'error', 'Discord sign-in failed. Please try again.');
      return res.redirect(loginRedirect(req));
    }
  }

  return completeWebSession(req, res, { sessionUser, siteUser, authReturnTo, started, routePath });
}

module.exports = {
  handleDiscordOAuthCallback,
  requestTransportProof,
  renderApkOpenHandoffHtml,
};
