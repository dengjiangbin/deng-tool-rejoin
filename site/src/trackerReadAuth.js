'use strict';

const inventorySession = require('./inventorySession');
const aioSessionStore = require('./aioSessionStore');

async function resolveInventoryOwnerId(req) {
  const fromSession = inventorySession.getInventoryDiscordUserId(req);
  if (fromSession) return fromSession;
  const header = req.headers.authorization || '';
  const match = /^Bearer\s+(.+)$/i.exec(header);
  if (!match) return null;
  const token = String(match[1]).trim();
  try {
    const aio = aioSessionStore.resolveSession(token);
    if (aio && aio.discordUserId) return String(aio.discordUserId);
  } catch (_) { /* fall through */ }
  return null;
}

async function requireTrackerReadAuth(req, res, next) {
  let ownerId = null;
  try {
    ownerId = await resolveInventoryOwnerId(req);
  } catch (_) { ownerId = null; }
  if (!ownerId) {
    return res.status(401).json({
      ok: false,
      error: 'auth_required',
      message: 'Login with Discord first.',
    });
  }
  req.inventoryOwnerDiscordId = ownerId;
  return next();
}

module.exports = {
  resolveInventoryOwnerId,
  requireTrackerReadAuth,
};
