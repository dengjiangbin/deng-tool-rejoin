'use strict';
/**
 * Fish It Backpack Tracker – API routes + dashboard page.
 *
 * Public routes (no authentication required):
 *   GET  /tracker                        – serve the live dashboard UI
 *   POST /api/tracker/update-backpack    – receive payload from the Lua client
 *   GET  /api/tracker/get-backpack/:user – query live data for a username
 *
 * Security notes:
 *   - All data lives only in process memory (liveTrackDB). Nothing is
 *     persisted to disk or database.
 *   - Input is strictly validated and sanitised before storage.
 *   - Dedicated rate-limiters protect both endpoints independently so the
 *     global site limiter is not exhausted by the 2500 ms frontend polling.
 *   - Username keys are always lowercased; original casing is preserved
 *     inside the stored payload for display purposes only.
 */

const express   = require('express');
const rateLimit = require('express-rate-limit');

const router = express.Router();

// ── In-memory live-data store ─────────────────────────────────────
// Key: lowercased Roblox username  |  Value: last received payload + server ts
const liveTrackDB = {};

// ── Rate limiters ─────────────────────────────────────────────────
// POST: Lua scripts fire every 3 s but only when data changes, so 5/10 s
// gives generous headroom while preventing abuse.
const postLimiter = rateLimit({
  windowMs: 10 * 1000,   // 10 seconds
  max: 5,
  skip: () => process.env.NODE_ENV === 'test',
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'too_many_requests', message: 'Slow down.' },
});

// GET: frontend polls every 2500 ms = ~24 req/min, so 60/min is comfortable.
const getLimiter = rateLimit({
  windowMs: 60 * 1000,   // 1 minute
  max: 60,
  skip: () => process.env.NODE_ENV === 'test',
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'too_many_requests', message: 'Slow down.' },
});

// ── Input validation ──────────────────────────────────────────────
// Roblox usernames: 3–20 chars, alphanumeric + underscore.
const USERNAME_RE = /^[A-Za-z0-9_]{3,20}$/;

function sanitiseUsername(raw) {
  if (typeof raw !== 'string') return null;
  const s = raw.trim();
  return USERNAME_RE.test(s) ? s : null;
}

function sanitiseItems(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.slice(0, 300).map((item) => {
    const name = typeof item.name === 'string'
      ? item.name
      : (typeof item.Name === 'string' ? item.Name : '');
    const rawWeight = item.weight ?? item.totalWeight ?? item.Weight;
    const rawAmount = item.amount ?? item.Amount ?? 1;
    const weight = Number(rawWeight);
    const amount = Number(rawAmount);

    return {
      name:     name.slice(0, 100),
      weight:   Number.isFinite(weight) ? weight : null,
      amount:   Number.isFinite(amount) && amount > 0 ? Math.floor(amount) : 1,
      category: typeof item.category === 'string' ? item.category.slice(0, 50) : null,
      tab:      typeof item.tab === 'string'      ? item.tab.slice(0, 50)      : null,
      rarity:   typeof item.rarity === 'string'   ? item.rarity.slice(0, 50)   : null,
      shiny:    item.shiny === true               ? true                      : false,
    };
  });
}

// ── GET /tracker – serve the dashboard page ───────────────────────
router.get('/tracker', (_req, res) => {
  res.render('fishit_tracker', {
    layout: false,
    title: '🎣 Fish It Live Inventory Tracker',
  });
});

// ── POST /api/tracker/update-backpack ────────────────────────────
router.post(
  '/api/tracker/update-backpack',
  postLimiter,
  express.json({ limit: '64kb' }),
  (req, res) => {
    const { username, userId, items } = req.body || {};

    const cleanUser = sanitiseUsername(username);
    if (!cleanUser) {
      return res.status(400).json({ error: 'Invalid or missing username.' });
    }

    const key = cleanUser.toLowerCase();
    liveTrackDB[key] = {
      username:  cleanUser,
      userId:    Number.isFinite(Number(userId)) ? Number(userId) : 0,
      items:     sanitiseItems(items),
      updatedAt: new Date().toISOString(),
    };

    return res.status(200).json({ status: 'success' });
  },
);

// ── GET /api/tracker/get-backpack/:username ───────────────────────
router.get(
  '/api/tracker/get-backpack/:username',
  getLimiter,
  (req, res) => {
    const cleanUser = sanitiseUsername(req.params.username);
    if (!cleanUser) {
      return res.status(400).json({ error: 'Invalid username.' });
    }

    const key   = cleanUser.toLowerCase();
    const data  = liveTrackDB[key];

    if (!data) {
      return res.status(404).json({ error: 'No tracking session active for this user.' });
    }

    return res.status(200).json(data);
  },
);

module.exports = router;
