'use strict';
/**
 * Tests for the Fish It stats API (fishitRoutes.js).
 *
 * We mock BOTH:
 *   - ./fishitDb  -> deterministic fake stats (no real SQLite file needed)
 *   - ./db        -> tiny Supabase mock for the Android bearer-token auth path
 *
 * Security focus: a client must never be able to read another user's private
 * stats, and a client-supplied discord_id must be ignored.
 */

const { describe, test, before, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const path = require('node:path');

process.env.TOOL_SITE_COOKIE_SECRET = 'fishit-test-cookie-secret-long-enough-yes';
process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.TOOL_SITE_PUBLIC_URL = 'http://localhost:8791';
process.env.DISCORD_CLIENT_ID = 'x';
process.env.DISCORD_CLIENT_SECRET = 'x';
process.env.DISCORD_REDIRECT_URI = 'http://localhost:8791/auth/discord/callback';

function sha256(s) { return crypto.createHash('sha256').update(String(s)).digest('hex'); }

// ── In-memory Supabase mock (only monitor_app_sessions matters here) ─────────
function makeMemoryDb() { return { monitor_app_sessions: [] }; }
let mem = makeMemoryDb();

class Q {
  constructor(table) { this.table = table; this.filters = []; }
  _rows() { return mem[this.table] || (mem[this.table] = []); }
  select() { return this; }
  eq(field, value) { this.filters.push({ field, value }); return this; }
  async maybeSingle() {
    const row = this._rows().find((r) => this.filters.every((f) => r[f.field] === f.value));
    return { data: row || null, error: null };
  }
}
const mockSupabase = { from(table) { return new Q(table); } };

// ── Fake Fish It DB ──────────────────────────────────────────────────────────
const OWNER = '915851106280681492';
const fishCards = [
  { name: 'King Crab', rarity: 'secret', amount: 1314, image: 'https://cdn/king.png', max_weight: 192306, mutation: 'Albino', last_caught: '2026-05-29T03:46:12Z', fallback: 'secret' },
  { name: 'Iridesca', rarity: 'forgotten', amount: 12, image: null, max_weight: 50000, mutation: null, last_caught: '2026-05-20T01:00:00Z', fallback: 'forgotten' },
  { name: 'Frostbite Leviathan', rarity: 'forgotten', amount: 357, image: 'https://cdn/frost.png', max_weight: 900000, mutation: 'Corrupt', last_caught: '2026-05-28T10:00:00Z', fallback: 'forgotten' },
];

const fakeState = { available: true };
const fakeFishit = {
  DB_PATH: '/fake/deng-fish-it.sqlite',
  isAvailable: () => fakeState.available,
  getGlobal: () => fakeState.available ? {
    available: true,
    last_updated: '2026-05-29T05:00:23.098Z',
    total_players: 90, total_fish: 53947, secret_fish: 53371, forgotten_fish: 576,
    thunderzilla: 88, sea_eater: 43, maxton: 82,
    top_forgotten: [{ name: 'Frostbite Leviathan', count: 357 }],
    rods: { ghostfinn: 79, element: 96, diamond: 91, total: 266, participants: 22 },
  } : { available: false },
  getForgottenSpecies: () => [{ name: 'Thunderzilla', emoji: '<:t:1>', imageUrl: null, maxtonWeight: 1100000 }],
  getUserProfile: (id) => id === OWNER ? {
    has_data: true, discord_user_id: OWNER, username: 'neptune_75',
    total_fish: 3061, secret_fish: 3061, forgotten_fish: 0,
    rank: { rank: 6, of: 87 }, rods: { ghostfinn: 0, element: 1, diamond: 0, total: 1 },
  } : { has_data: false },
  getUserStats: (id) => id === OWNER ? {
    has_data: true, discord_user_id: OWNER, username: 'neptune_75', total_fish: 3061,
    rank: { rank: 6, of: 87 },
    rarity_cards: [
      { key: 'secret', label: 'Secret', amount: 3061, image: 'https://cdn/king.png', fallback: 'secret' },
      { key: 'forgotten', label: 'Forgotten', amount: 0, image: null, fallback: 'forgotten' },
    ],
    rod_cards: [
      { key: 'ghostfinn', label: 'Ghostfinn Rod', amount: 0, image: null, fallback: 'rod' },
      { key: 'element', label: 'Element Rod', amount: 1, image: null, fallback: 'rod' },
      { key: 'diamond', label: 'Diamond Rod', amount: 0, image: null, fallback: 'rod' },
    ],
  } : { has_data: false },
  getUserFish: (id) => id === OWNER
    ? { has_data: true, total_species: fishCards.length, fish: fishCards.map((c) => ({ ...c })) }
    : { has_data: false, fish: [] },
  getUserDaily: (id, period) => id === OWNER ? {
    has_data: period === 'all', period, period_label: period, total: period === 'all' ? 3061 : 0,
    secret: period === 'all' ? 3061 : 0, forgotten: 0, best_catch: null,
    secret_breakdown: [], forgotten_breakdown: [], last_updated: '2026-05-29T05:00:23.098Z',
  } : { has_data: false, period, total: 0, secret: 0, forgotten: 0, last_updated: null },
  _resetCache: () => {},
};

// Inject mocks BEFORE app loads.
const dbPath = path.join(__dirname, '..', 'src', 'db.js');
require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true, exports: mockSupabase };
const fishitDbPath = path.join(__dirname, '..', 'src', 'fishitDb.js');
require.cache[fishitDbPath] = { id: fishitDbPath, filename: fishitDbPath, loaded: true, exports: fakeFishit };

const request = require('supertest');
let app;
before(() => { app = require('../src/app'); });
beforeEach(() => { mem = makeMemoryDb(); fakeState.available = true; });

function seedAppSession(owner = OWNER) {
  const token = 'app-token-' + crypto.randomBytes(8).toString('hex');
  mem.monitor_app_sessions.push({
    id: crypto.randomUUID(),
    owner_discord_user_id: owner,
    token_hash: sha256(token),
    revoked_at: null,
    expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  });
  return token;
}

// ── Tests ────────────────────────────────────────────────────────────────────
describe('Fish It global stats (public)', () => {
  test('returns real global numbers without auth', async () => {
    const res = await request(app).get('/api/fishit/global');
    assert.equal(res.status, 200);
    assert.equal(res.body.available, true);
    assert.equal(res.body.total_players, 90);
    assert.equal(res.body.total_fish, 53947);
    assert.ok(Array.isArray(res.body.top_forgotten));
    assert.equal(res.body.rods.element, 96);
  });

  test('reports unavailable cleanly when DB is down', async () => {
    fakeState.available = false;
    const res = await request(app).get('/api/fishit/global');
    assert.equal(res.status, 200);
    assert.equal(res.body.available, false);
  });

  test('assets route returns fallbacks + species', async () => {
    const res = await request(app).get('/api/fishit/assets');
    assert.equal(res.status, 200);
    assert.ok(res.body.fallbacks.fish);
    assert.ok(res.body.fallbacks.rod);
    assert.ok(Array.isArray(res.body.forgotten_species));
  });
});

describe('Fish It private routes require auth', () => {
  for (const route of ['/api/fishit/me', '/api/fishit/me/daily', '/api/fishit/me/stats', '/api/fishit/me/fish']) {
    test(`401 unauthenticated: ${route}`, async () => {
      const res = await request(app).get(route);
      assert.equal(res.status, 401);
      assert.equal(res.body.error, 'auth_required');
    });
  }

  test('invalid/expired bearer token is rejected', async () => {
    const res = await request(app).get('/api/fishit/me').set('Authorization', 'Bearer not-a-real-token');
    assert.equal(res.status, 401);
  });
});

describe('Fish It authenticated via Android bearer token', () => {
  test('returns the token owner profile', async () => {
    const token = seedAppSession(OWNER);
    const res = await request(app).get('/api/fishit/me').set('Authorization', `Bearer ${token}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.has_data, true);
    assert.equal(res.body.discord_user_id, OWNER);
    assert.equal(res.body.total_fish, 3061);
  });

  test('client CANNOT spoof identity via discord_id query param', async () => {
    const token = seedAppSession(OWNER);
    // Ask for a different user's data — must be ignored, returns OWNER's data.
    const res = await request(app)
      .get('/api/fishit/me?discord_id=000000000000000000&user=victim')
      .set('Authorization', `Bearer ${token}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.discord_user_id, OWNER);
  });

  test('token whose owner has no Fish It data returns clean empty state', async () => {
    const token = seedAppSession('111111111111111111');
    const res = await request(app).get('/api/fishit/me').set('Authorization', `Bearer ${token}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.has_data, false);
  });
});

describe('Fish It daily filter', () => {
  test('default period is today', async () => {
    const token = seedAppSession(OWNER);
    const res = await request(app).get('/api/fishit/me/daily').set('Authorization', `Bearer ${token}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.period, 'today');
  });

  test('all-time period returns data', async () => {
    const token = seedAppSession(OWNER);
    const res = await request(app).get('/api/fishit/me/daily?period=all').set('Authorization', `Bearer ${token}`);
    assert.equal(res.body.period, 'all');
    assert.equal(res.body.has_data, true);
    assert.equal(res.body.total, 3061);
  });

  test('invalid period falls back to today', async () => {
    const token = seedAppSession(OWNER);
    const res = await request(app).get('/api/fishit/me/daily?period=garbage').set('Authorization', `Bearer ${token}`);
    assert.equal(res.body.period, 'today');
  });
});

describe('Fish It stats cards', () => {
  test('returns rarity + rod cards with fallback URLs', async () => {
    const token = seedAppSession(OWNER);
    const res = await request(app).get('/api/fishit/me/stats').set('Authorization', `Bearer ${token}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.rarity_cards.length, 2);
    assert.equal(res.body.rod_cards.length, 3);
    // Rod cards have no DB image -> must carry a fallback URL.
    assert.ok(res.body.rod_cards.every((c) => typeof c.fallback_url === 'string'));
    assert.ok(res.body.rarity_cards.every((c) => typeof c.fallback_url === 'string'));
  });
});

describe('Fish It fish grid (server-side filter/sort/paginate)', () => {
  test('returns all cards by default sorted by amount', async () => {
    const token = seedAppSession(OWNER);
    const res = await request(app).get('/api/fishit/me/fish').set('Authorization', `Bearer ${token}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.total, 3);
    assert.equal(res.body.fish[0].name, 'King Crab'); // highest amount
    // Missing image must still carry a fallback URL (no crash).
    const iridesca = res.body.fish.find((f) => f.name === 'Iridesca');
    assert.equal(iridesca.image, null);
    assert.ok(typeof iridesca.fallback_url === 'string');
  });

  test('search filters by name', async () => {
    const token = seedAppSession(OWNER);
    const res = await request(app).get('/api/fishit/me/fish?search=frost').set('Authorization', `Bearer ${token}`);
    assert.equal(res.body.total, 1);
    assert.equal(res.body.fish[0].name, 'Frostbite Leviathan');
  });

  test('rarity filter works', async () => {
    const token = seedAppSession(OWNER);
    const res = await request(app).get('/api/fishit/me/fish?rarity=forgotten').set('Authorization', `Bearer ${token}`);
    assert.equal(res.body.total, 2);
    assert.ok(res.body.fish.every((f) => f.rarity === 'forgotten'));
  });

  test('sort by name works', async () => {
    const token = seedAppSession(OWNER);
    const res = await request(app).get('/api/fishit/me/fish?sort=name').set('Authorization', `Bearer ${token}`);
    assert.equal(res.body.fish[0].name, 'Frostbite Leviathan');
  });

  test('pagination limits results', async () => {
    const token = seedAppSession(OWNER);
    const res = await request(app).get('/api/fishit/me/fish?limit=1&page=2').set('Authorization', `Bearer ${token}`);
    assert.equal(res.body.fish.length, 1);
    assert.equal(res.body.page, 2);
    assert.equal(res.body.pages, 3);
  });
});
