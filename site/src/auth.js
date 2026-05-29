'use strict';
/**
 * Authentication utilities:
 *  - requireLogin middleware
 *  - verifyCsrf helper
 *  - Discord OAuth2 helpers (manual, no Passport)
 */
const crypto  = require('crypto');
const axios   = require('axios');
const supabase = require('./db');

const DISCORD_CLIENT_ID     = process.env.DISCORD_CLIENT_ID     || '';
const DISCORD_CLIENT_SECRET = process.env.DISCORD_CLIENT_SECRET || '';
const DISCORD_REDIRECT_URI  = process.env.DISCORD_REDIRECT_URI  || '';
const DISCORD_API           = 'https://discord.com/api/v10';
const SCOPES                = 'identify';

// ---------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------

/**
 * Detect PostgREST/Supabase "table not in schema cache" errors.
 * These occur when migration 005_site_portal.sql has not been applied.
 */
function isSchemaMissingError(err) {
  const msg = `${(err && err.code) || ''} ${(err && err.message) || ''} ${(err && err.details) || ''} ${(err && err.hint) || ''}`.toLowerCase();
  return (
    msg.includes('schema cache') ||
    msg.includes('could not find the table') ||
    msg.includes('does not exist') ||
    msg.includes('relation') ||
    msg.includes('42p01') ||
    msg.includes('pgrst204') ||
    msg.includes('pgrst205')
  );
}

function codedError(code, message) {
  const err = new Error(message || code);
  err.code = code;
  return err;
}

function saveSession(req) {
  return new Promise((resolve, reject) => {
    req.session.save((err) => (err ? reject(err) : resolve()));
  });
}

/**
 * Derive a deterministic UUID-v4-formatted ID from a Discord user ID.
 * The same Discord ID always maps to the same portal ID so sessions are
 * stable across restarts even when the site_users table is not yet created.
 */
function discordFallbackId(discordId) {
  const h = crypto.createHash('sha256').update(`portal:${discordId}`).digest('hex');
  return [
    h.slice(0, 8),
    h.slice(8, 12),
    `4${h.slice(13, 16)}`,
    `${(parseInt(h[16], 16) & 0x3 | 0x8).toString(16)}${h.slice(17, 20)}`,
    h.slice(20, 32),
  ].join('-');
}

// ---------------------------------------------------------------
// Middleware
// ---------------------------------------------------------------

/** Public landing + sign-in page (Discord OAuth entry). */
const LOGIN_HOME = '/';

/**
 * Redirect unauthenticated users to the public home page.
 */
function requireLogin(req, res, next) {
  if (req.session && req.session.user) return next();
  req.session.flash = { error: 'Please login with Discord first.' };
  res.redirect(LOGIN_HOME);
}

/**
 * Verify CSRF token submitted via form or X-CSRF-Token header.
 * Returns true if valid, false otherwise.
 */
function verifyCsrf(req) {
  const sessionToken  = req.session.csrfToken;
  const submittedToken = req.body?._csrf || req.headers['x-csrf-token'];
  if (!sessionToken || !submittedToken) return false;
  try {
    return crypto.timingSafeEqual(
      Buffer.from(sessionToken),
      Buffer.from(submittedToken),
    );
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------
// Discord OAuth2
// ---------------------------------------------------------------

/**
 * Build the Discord authorization URL with a random state nonce.
 * Stores the state in session for later validation.
 */
function buildDiscordAuthUrl(req) {
  const missing = [];
  if (!DISCORD_CLIENT_ID) missing.push('DISCORD_CLIENT_ID');
  if (!DISCORD_CLIENT_SECRET) missing.push('DISCORD_CLIENT_SECRET');
  if (!DISCORD_REDIRECT_URI) missing.push('DISCORD_REDIRECT_URI');
  if (missing.length) {
    console.error('[auth] Discord OAuth not configured, missing env:', missing.join(', '));
    throw new Error('Discord OAuth is not configured');
  }
  const state = crypto.randomBytes(24).toString('hex');
  req.session.oauthState = state;

  const params = new URLSearchParams({
    client_id:     DISCORD_CLIENT_ID,
    redirect_uri:  DISCORD_REDIRECT_URI,
    response_type: 'code',
    scope:         SCOPES,
    state,
    prompt:        'consent',
  });
  return `${DISCORD_API}/oauth2/authorize?${params}`;
}

/**
 * Exchange authorization code for Discord access token.
 * Throws on failure.
 */
async function exchangeDiscordCode(code) {
  const params = new URLSearchParams({
    client_id:     DISCORD_CLIENT_ID,
    client_secret: DISCORD_CLIENT_SECRET,
    grant_type:    'authorization_code',
    code,
    redirect_uri:  DISCORD_REDIRECT_URI,
  });

  try {
    const { data } = await axios.post(
      `${DISCORD_API}/oauth2/token`,
      params.toString(),
      { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } },
    );
    return data; // { access_token, token_type, scope, expires_in }
  } catch (err) {
    const status   = (err.response && err.response.status) || 'network_error';
    const errName  = (err.response && err.response.data && err.response.data.error) || err.message;
    console.error(
      '[auth] category=token_exchange_failed http_status=%s discord_error=%s redirect_uri=%s client_id=%s client_secret_set=%s',
      status, errName, DISCORD_REDIRECT_URI, DISCORD_CLIENT_ID, !!DISCORD_CLIENT_SECRET,
    );
    throw err;
  }
}

/**
 * Fetch the authenticated Discord user's profile.
 */
async function fetchDiscordUser(accessToken) {
  const { data } = await axios.get(`${DISCORD_API}/users/@me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  return data; // { id, username, discriminator, avatar, email, ... }
}

/**
 * Upsert a site_user row from Discord OAuth data.
 * Returns the site_users row.
 */
async function upsertDiscordUser(discordUser, _tokens, options = {}) {
  const now = new Date().toISOString();

  try {
    // Check if user already exists by discord_user_id
    const { data: existing } = await supabase
      .from('site_users')
      .select('*')
      .eq('discord_user_id', discordUser.id)
      .maybeSingle();

    if (existing) {
      const { data, error } = await supabase
        .from('site_users')
        .update({
          discord_username:     discordUser.username,
          discord_avatar:       discordUser.avatar || null,
          discord_access_token: null,
          discord_refresh_token:null,
          last_login_at:        now,
        })
        .eq('id', existing.id)
        .select()
        .single();
      if (error) throw new Error(`DB update failed: ${error.message}`);
      return data;
    }

    // New user — email is optional (scope is identify only)
    const { data, error } = await supabase
      .from('site_users')
      .insert({
        discord_user_id:      discordUser.id,
        discord_username:     discordUser.username,
        discord_avatar:       discordUser.avatar || null,
        discord_access_token: null,
        discord_refresh_token:null,
        email:                discordUser.email || null,
        last_login_at:        now,
      })
      .select()
      .single();
    if (error) throw new Error(`DB insert failed: ${error.message}`);
    return data;
  } catch (err) {
    if (isSchemaMissingError(err) && options.allowFallback !== false) {
      console.warn(
        '[auth] category=site_users_schema_missing discord_id=%s – using Discord-only session.' +
        ' Apply migration: supabase/migrations/005_site_portal.sql',
        discordUser.id,
      );
      // Return a synthetic user so login works even without the DB table.
      // The ID is deterministic so the same Discord user always gets the same ID.
      return {
        id:               discordFallbackId(discordUser.id),
        discord_user_id:  discordUser.id,
        discord_username: discordUser.username || discordUser.global_name || `user_${discordUser.id.slice(-4)}`,
        discord_avatar:   discordUser.avatar || null,
        email:            null,
      };
    }
    throw err;
  }
}



/**
 * Create a minimal session user object (avoid storing full token in session).
 */
function toSessionUser(row) {
  return {
    id:               row.id,
    site_user_id:     row.id,
    username:         row.username || row.discord_username || `user_${row.id.slice(0, 8)}`,
    discord_user_id:  row.discord_user_id || null,
    discord_username: row.discord_username || null,
    discord_avatar:   row.discord_avatar || null,
    email:            row.email || null,
  };
}

async function ensureRealSiteUser(req) {
  const sessionUser = req.session && req.session.user;
  if (!sessionUser || !sessionUser.discord_user_id) {
    throw codedError('AUTH_REQUIRED', 'Discord session is required');
  }

  const discordUser = {
    id:          sessionUser.discord_user_id,
    username:    sessionUser.discord_username || sessionUser.username || `user_${String(sessionUser.discord_user_id).slice(-4)}`,
    global_name: sessionUser.username || sessionUser.discord_username || null,
    avatar:      sessionUser.discord_avatar || null,
    email:       sessionUser.email || null,
  };

  let siteUser;
  try {
    siteUser = await upsertDiscordUser(discordUser, {}, { allowFallback: false });
  } catch (err) {
    if (isSchemaMissingError(err)) {
      throw codedError('CHALLENGE_TABLE_MISSING', `site_users schema missing: ${err.message}`);
    }
    throw codedError('SITE_USER_UPSERT_FAILED', `site user upsert failed: ${err.message}`);
  }

  if (!siteUser || !siteUser.id) {
    throw codedError('SITE_USER_UPSERT_FAILED', 'site user upsert returned no id');
  }

  const repairedUser = {
    ...toSessionUser(siteUser),
    site_user_id: siteUser.id,
  };
  const changed = (
    sessionUser.id !== repairedUser.id ||
    sessionUser.site_user_id !== repairedUser.site_user_id ||
    sessionUser.username !== repairedUser.username
  );

  req.session.user = repairedUser;
  req.session.site_user_id = siteUser.id;
  if (changed) await saveSession(req);
  return siteUser;
}

module.exports = {
  LOGIN_HOME,
  requireLogin,
  verifyCsrf,
  buildDiscordAuthUrl,
  exchangeDiscordCode,
  fetchDiscordUser,
  upsertDiscordUser,
  ensureRealSiteUser,
  toSessionUser,
};
