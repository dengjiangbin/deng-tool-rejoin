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
// Middleware
// ---------------------------------------------------------------

/**
 * Redirect unauthenticated users to /login.
 */
function requireLogin(req, res, next) {
  if (req.session && req.session.user) return next();
  req.session.flash = { error: 'Please sign in to continue.' };
  res.redirect('/login');
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

  const { data } = await axios.post(
    `${DISCORD_API}/oauth2/token`,
    params.toString(),
    { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } },
  );
  return data; // { access_token, refresh_token, token_type, scope, expires_in }
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
async function upsertDiscordUser(discordUser, _tokens) {
  const now = new Date().toISOString();

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

  // New user
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
}



/**
 * Create a minimal session user object (avoid storing full token in session).
 */
function toSessionUser(row) {
  return {
    id:               row.id,
    username:         row.username || row.discord_username || `user_${row.id.slice(0, 8)}`,
    discord_user_id:  row.discord_user_id || null,
    discord_username: row.discord_username || null,
    discord_avatar:   row.discord_avatar || null,
    email:            row.email || null,
  };
}

module.exports = {
  requireLogin,
  verifyCsrf,
  buildDiscordAuthUrl,
  exchangeDiscordCode,
  fetchDiscordUser,
  upsertDiscordUser,
  toSessionUser,
};
