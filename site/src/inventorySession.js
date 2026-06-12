'use strict';

const { verifyCsrf, ensureRealSiteUser } = require('./auth');
const inventoryTrackedAccounts = require('./inventoryTrackedAccounts');

function getSessionUser(req) {
  return req.session && req.session.user ? req.session.user : null;
}

function normalizeDiscordUserId(value) {
  return inventoryTrackedAccounts.normalizeDiscordUserId(value);
}

/**
 * Resolve Discord owner id for inventory APIs from the unified session contract.
 * Supports current and legacy session shapes after Discord OAuth updates.
 */
function getInventoryDiscordUserId(req) {
  const user = getSessionUser(req);
  if (user && user.discord_user_id != null) {
    const id = normalizeDiscordUserId(user.discord_user_id);
    if (id) return id;
  }
  if (req.session && req.session.discord_user_id != null) {
    const id = normalizeDiscordUserId(req.session.discord_user_id);
    if (id) return id;
  }
  return null;
}

function getInventorySiteUserId(req) {
  if (req.session && req.session.site_user_id) {
    return String(req.session.site_user_id);
  }
  const user = getSessionUser(req);
  if (user && user.site_user_id) return String(user.site_user_id);
  if (user && user.id && String(user.id).includes('-')) return String(user.id);
  return null;
}

function getInventoryCsrfToken(req) {
  return req.session && req.session.csrfToken ? String(req.session.csrfToken) : '';
}

function hasInventorySession(req) {
  return !!getInventoryDiscordUserId(req);
}

async function repairInventorySession(req) {
  if (process.env.NODE_ENV === 'test') return;
  const user = getSessionUser(req);
  if (!user) return;
  if (user.discord_user_id) {
    if (!req.session.site_user_id && user.id && String(user.id).includes('-')) {
      req.session.site_user_id = String(user.id);
    }
    return;
  }
  try {
    await ensureRealSiteUser(req);
  } catch (err) {
    console.warn(
      '[inventory-session] repair skipped:',
      err && err.message ? err.message : err,
    );
  }
}

function logInventoryAccountsAction(req, action, meta) {
  const user = getSessionUser(req);
  console.log(
    '[inventory-accounts] action=%s hasSession=%s sessionID=%s discordUserId=%s siteUserId=%s csrf=%s %s',
    action,
    !!(req.session),
    req.session && req.session.id ? req.session.id : 'n/a',
    getInventoryDiscordUserId(req) || 'missing',
    getInventorySiteUserId(req) || 'n/a',
    getInventoryCsrfToken(req) ? 'present' : 'missing',
    meta ? JSON.stringify(meta) : '',
  );
}

module.exports = {
  getSessionUser,
  getInventoryDiscordUserId,
  getInventorySiteUserId,
  getInventoryCsrfToken,
  hasInventorySession,
  repairInventorySession,
  logInventoryAccountsAction,
  verifyInventoryCsrf: verifyCsrf,
};
