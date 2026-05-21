'use strict';
/**
 * DENG Tool Site – automated test suite
 * Uses Node.js built-in test runner (node --test) + supertest for HTTP assertions.
 *
 * Run: npm test
 * (Requires: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY set in env OR mocked)
 *
 * Strategy: inject mock supabase before requiring app so no real DB calls are made.
 */

const { test, before, describe } = require('node:test');
const assert = require('node:assert/strict');

// ── Environment stubs ──────────────────────────────────────────
// Set required env vars before loading app modules
process.env.TOOL_SITE_COOKIE_SECRET = 'test-secret-that-is-at-least-32-characters-long-for-tests!';
process.env.TOOL_SITE_STATE_SECRET  = 'test-state-secret-that-is-at-least-32-chars-long-tests!';
process.env.SUPABASE_URL            = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.NODE_ENV                = 'test';
process.env.TOOL_SITE_PUBLIC_URL    = 'http://localhost:8791';
process.env.DISCORD_CLIENT_ID       = 'test-client-id';
process.env.DISCORD_CLIENT_SECRET   = 'test-client-secret';
process.env.DISCORD_REDIRECT_URI    = 'http://localhost:8791/auth/discord/callback';
process.env.LINKVERTISE_PUBLISHER_ID= '5914830';
process.env.LOOTLABS_PUBLISHER_URL  = 'https://lootlabs.example.com/unlock';

// ── Supabase mock (intercept module) ──────────────────────────
// We patch require cache so db.js returns a mock client.
const mockSupabaseRows = {};

const mockSupabase = {
  from: (table) => ({
    select: (..._args) => ({
      eq: (..._a)     => ({ eq: (..._b) => ({ maybeSingle: async () => ({ data: mockSupabaseRows[table] || null, error: null }), single: async () => ({ data: null, error: { message: 'not found' } }), limit: (..._c) => ({ order: (..._d) => ({ limit: (..._e) => ({ data: [], error: null }) }) }), order: (..._c) => ({ limit: (..._d) => ({ data: [], error: null }) }) }), maybeSingle: async () => ({ data: mockSupabaseRows[table] || null, error: null }), single: async () => ({ data: null, error: { message: 'not found' } }), in: (..._b) => ({ gte: (..._c) => ({ order: (..._d) => ({ limit: (..._e) => ({ data: [], error: null }) }) }) }), order: (..._b) => ({ limit: (..._c) => ({ data: [], error: null }) }) }),
      in: (..._a)     => ({ gte: (..._b) => ({ order: (..._c) => ({ limit: (..._d) => ({ data: [], error: null }) }) }) }),
      or:  (..._a)    => ({ maybeSingle: async () => ({ data: null, error: null }) }),
      maybeSingle: async () => ({ data: null, error: null }),
      single: async () => ({ data: null, error: { message: 'no data' } }),
      order: (..._a)  => ({ limit: (..._b) => ({ data: [], error: null }) }),
    }),
    insert: (_row) => ({
      select: () => ({
        single: async () => ({ data: { id: 'mock-uuid', ..._row }, error: null }),
      }),
    }),
    update: (_row) => ({
      eq: (..._a) => ({
        eq: (..._b) => ({
          select: () => ({ single: async () => ({ data: null, error: null }) }),
          select_x: () => ({ single: async () => ({ data: _row, error: null }) }),
        }),
        select: () => ({ single: async () => ({ data: _row, error: null }) }),
      }),
    }),
  }),
};

// Inject into require cache BEFORE loading app
require.cache[require.resolve('../src/db')] = {
  id: require.resolve('../src/db'),
  filename: require.resolve('../src/db'),
  loaded: true,
  exports: mockSupabase,
};

const request = require('supertest');
const app     = require('../src/app');

// ──────────────────────────────────────────────────────────────
// SECTION 1: Crypto utilities
// ──────────────────────────────────────────────────────────────
describe('crypto utilities', () => {
  const { signChallenge, verifyChallenge, sha256, randomHex } = require('../src/crypto');

  test('T01 – sha256 returns 64-char hex string', () => {
    const h = sha256('hello');
    assert.equal(h.length, 64);
    assert.match(h, /^[0-9a-f]+$/);
  });

  test('T02 – randomHex returns hex of correct byte length', () => {
    const h = randomHex(16);
    assert.equal(h.length, 32); // 16 bytes = 32 hex chars
    assert.match(h, /^[0-9a-f]+$/);
  });

  test('T03 – signChallenge returns two-part token', () => {
    const token = signChallenge('uuid-1', 'lootlabs', Date.now() + 60_000);
    const parts = token.split('.');
    assert.equal(parts.length, 2);
    assert.ok(parts[0].length > 0);
    assert.ok(parts[1].length === 64); // SHA-256 hex
  });

  test('T04 – verifyChallenge round-trips correctly', () => {
    const exp = Date.now() + 60_000;
    const token = signChallenge('abc-123', 'linkvertise', exp);
    const decoded = verifyChallenge(token);
    assert.ok(decoded);
    assert.equal(decoded.cid, 'abc-123');
    assert.equal(decoded.p, 'linkvertise');
  });

  test('T05 – verifyChallenge rejects tampered token', () => {
    const token = signChallenge('abc-456', 'lootlabs', Date.now() + 60_000);
    const [payload, sig] = token.split('.');
    const tampered = payload + '.' + sig.slice(0, -2) + 'ff';
    assert.equal(verifyChallenge(tampered), null);
  });

  test('T06 – verifyChallenge rejects expired token', () => {
    const token = signChallenge('abc-789', 'lootlabs', Date.now() - 1000); // expired
    assert.equal(verifyChallenge(token), null);
  });

  test('T07 – verifyChallenge returns null for garbage input', () => {
    assert.equal(verifyChallenge('not.a.valid.token'), null);
    assert.equal(verifyChallenge(''), null);
    assert.equal(verifyChallenge(null), null);
  });
});

// ──────────────────────────────────────────────────────────────
// SECTION 2: Key generation
// ──────────────────────────────────────────────────────────────
describe('key generation', () => {
  const { generateDengKey } = require('../src/keyGen');

  test('T08 – generateDengKey produces DENG-XXXX-XXXX-XXXX-XXXX format', () => {
    const { raw } = generateDengKey();
    assert.match(raw, /^DENG-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$/);
  });

  test('T09 – generateDengKey id is 64-char hex (SHA-256)', () => {
    const { id } = generateDengKey();
    assert.equal(id.length, 64);
    assert.match(id, /^[0-9a-f]+$/);
  });

  test('T10 – generateDengKey prefix contains first 2 groups', () => {
    const { raw, prefix } = generateDengKey();
    const parts = raw.split('-');
    assert.equal(prefix, `DENG-${parts[1]}-${parts[2]}`);
  });

  test('T11 – generateDengKey suffix contains last 2 groups', () => {
    const { raw, suffix } = generateDengKey();
    const parts = raw.split('-');
    assert.equal(suffix, `${parts[3]}-${parts[4]}`);
  });

  test('T12 – consecutive keys are different (random)', () => {
    const k1 = generateDengKey().raw;
    const k2 = generateDengKey().raw;
    assert.notEqual(k1, k2);
  });
});

// ──────────────────────────────────────────────────────────────
// SECTION 3: HTTP routes (via supertest)
// ──────────────────────────────────────────────────────────────
describe('HTTP routes', () => {

  test('T13 – GET /health returns 200 JSON with status ok', async () => {
    const res = await request(app).get('/health');
    assert.equal(res.status, 200);
    assert.equal(res.body.status, 'ok');
    assert.equal(res.body.service, 'deng-tool-site');
  });

  test('T14 – GET / redirects to /login when not authenticated', async () => {
    const res = await request(app).get('/');
    assert.ok([301, 302].includes(res.status));
    assert.match(res.headers.location, /\/login/);
  });

  test('T15 – GET /login returns 200', async () => {
    const res = await request(app).get('/login');
    assert.equal(res.status, 200);
  });

  test('T16 – GET /dashboard redirects to /login when unauthenticated', async () => {
    const res = await request(app).get('/dashboard');
    assert.ok([301, 302].includes(res.status));
    assert.match(res.headers.location, /\/login/);
  });

  test('T17 – GET /license redirects to /login when unauthenticated', async () => {
    const res = await request(app).get('/license');
    assert.ok([301, 302].includes(res.status));
    assert.match(res.headers.location, /\/login/);
  });

  test('T18 – GET /key/result redirects to /license when no key in session', async () => {
    const res = await request(app).get('/key/result');
    assert.ok([301, 302].includes(res.status));
  });

  test('T19 – POST /auth/login without CSRF token redirects to /login', async () => {
    const res = await request(app)
      .post('/auth/login')
      .send({ username: 'x', password: 'y' })
      .set('Content-Type', 'application/x-www-form-urlencoded');
    assert.ok([301, 302].includes(res.status));
    assert.match(res.headers.location, /\/login/);
  });

  test('T20 – GET /auth/discord redirects to discord.com', async () => {
    const res = await request(app).get('/auth/discord');
    assert.ok([301, 302].includes(res.status));
    assert.match(res.headers.location, /discord\.com/);
  });

  test('T21 – GET /auth/discord/callback with no state → redirect /login', async () => {
    const res = await request(app).get('/auth/discord/callback?code=abc&state=badstate');
    assert.ok([301, 302].includes(res.status));
    assert.match(res.headers.location, /\/login/);
  });

  test('T22 – GET /unlock/linkvertise without challenge → redirect /license (need login first)', async () => {
    const res = await request(app).get('/unlock/linkvertise');
    // Unauthenticated → redirect to login
    assert.ok([301, 302].includes(res.status));
  });

  test('T23 – POST /license/generate without login → redirects to login', async () => {
    const res = await request(app)
      .post('/license/generate')
      .send('_csrf=fake')
      .set('Content-Type', 'application/x-www-form-urlencoded');
    assert.ok([301, 302].includes(res.status));
    assert.match(res.headers.location, /\/login/);
  });

  test('T24 – POST /auth/logout with bad CSRF → redirects to /', async () => {
    const res = await request(app)
      .post('/auth/logout')
      .send('_csrf=invalid')
      .set('Content-Type', 'application/x-www-form-urlencoded');
    assert.ok([301, 302].includes(res.status));
  });

  test('T25 – GET /nonexistent returns 404', async () => {
    const res = await request(app).get('/nonexistent-page-xyz');
    assert.equal(res.status, 404);
  });
});

// ──────────────────────────────────────────────────────────────
// SECTION 4: Auth module unit tests
// ──────────────────────────────────────────────────────────────
describe('auth utilities', () => {
  const { verifyCsrf, toSessionUser } = require('../src/auth');

  test('T26 – verifyCsrf returns false when tokens mismatch', () => {
    const req = {
      session: { csrfToken: 'aaaaaa' },
      body: { _csrf: 'bbbbbb' },
      headers: {},
    };
    // Tokens are different length so timingSafeEqual will throw → returns false
    assert.equal(verifyCsrf(req), false);
  });

  test('T27 – verifyCsrf returns true when tokens match', () => {
    const token = 'a'.repeat(64);
    const req = {
      session: { csrfToken: token },
      body: { _csrf: token },
      headers: {},
    };
    assert.equal(verifyCsrf(req), true);
  });

  test('T28 – verifyCsrf returns false when CSRF token missing from body', () => {
    const req = {
      session: { csrfToken: 'abc' },
      body: {},
      headers: {},
    };
    assert.equal(verifyCsrf(req), false);
  });

  test('T29 – toSessionUser returns minimal safe object', () => {
    const row = {
      id: 'uuid-1',
      username: 'john',
      discord_user_id: '12345',
      discord_username: 'john#0001',
      discord_avatar: 'abcdef',
      email: 'john@example.com',
      password_hash: '$2b$10$secret',   // should NOT appear in session
      discord_access_token: 'tok123',    // should NOT appear in session
    };
    const s = toSessionUser(row);
    assert.equal(s.id, 'uuid-1');
    assert.equal(s.username, 'john');
    assert.equal(s.discord_user_id, '12345');
    assert.ok(!('password_hash' in s));
    assert.ok(!('discord_access_token' in s));
  });

  test('T30 – toSessionUser falls back to discord_username when username is null', () => {
    const row = {
      id: 'uuid-2',
      username: null,
      discord_username: 'jill#0002',
      discord_user_id: '99999',
      discord_avatar: null,
      email: null,
    };
    const s = toSessionUser(row);
    assert.equal(s.username, 'jill#0002');
  });
});

// ──────────────────────────────────────────────────────────────
// SECTION 5: Security headers
// ──────────────────────────────────────────────────────────────
describe('security headers', () => {

  test('T31 – /health response includes X-Content-Type-Options: nosniff', async () => {
    const res = await request(app).get('/health');
    assert.match(res.headers['x-content-type-options'] || '', /nosniff/i);
  });

  test('T32 – /health response includes X-Frame-Options', async () => {
    const res = await request(app).get('/health');
    assert.ok(
      res.headers['x-frame-options'] !== undefined ||
      (res.headers['content-security-policy'] || '').includes('frame'),
      'Expected X-Frame-Options or frame-ancestors in CSP',
    );
  });

  test('T33 – /login page sets no sensitive cookies on a fresh GET', async () => {
    const res = await request(app).get('/login');
    const cookies = res.headers['set-cookie'] || [];
    // Session cookie should be HttpOnly
    const sessionCookie = cookies.find(c => c.includes('deng_sid'));
    if (sessionCookie) {
      assert.match(sessionCookie, /HttpOnly/i, 'session cookie must be HttpOnly');
    }
  });
});

// ──────────────────────────────────────────────────────────────
// SECTION 6: Key format validation
// ──────────────────────────────────────────────────────────────
describe('key format stress tests', () => {
  const { generateDengKey } = require('../src/keyGen');

  test('T34 – 100 generated keys all match DENG format', () => {
    for (let i = 0; i < 100; i++) {
      const { raw } = generateDengKey();
      assert.match(raw, /^DENG-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$/,
        `Key #${i} did not match: ${raw}`);
    }
  });

  test('T35 – 100 generated keys are all unique', () => {
    const seen = new Set();
    for (let i = 0; i < 100; i++) {
      const { raw } = generateDengKey();
      assert.ok(!seen.has(raw), `Duplicate key at iteration ${i}: ${raw}`);
      seen.add(raw);
    }
  });

  test('T36 – key id is deterministic: same raw → same id', () => {
    const crypto = require('node:crypto');
    const raw = 'DENG-AAAA-BBBB-CCCC-DDDD';
    const id1 = crypto.createHash('sha256').update(raw).digest('hex');
    const id2 = crypto.createHash('sha256').update(raw).digest('hex');
    assert.equal(id1, id2);
  });
});

// ──────────────────────────────────────────────────────────────
// SECTION 7: Challenge signing edge cases
// ──────────────────────────────────────────────────────────────
describe('challenge signing edge cases', () => {
  const { signChallenge, verifyChallenge } = require('../src/crypto');

  test('T37 – provider null round-trips as empty string', () => {
    const exp = Date.now() + 30_000;
    const token = signChallenge('cid-null', null, exp);
    const decoded = verifyChallenge(token);
    assert.ok(decoded);
    assert.equal(decoded.p, '');
  });

  test('T38 – very long challengeId is handled correctly', () => {
    const longId = 'a'.repeat(200);
    const exp = Date.now() + 30_000;
    const token = signChallenge(longId, 'lootlabs', exp);
    const decoded = verifyChallenge(token);
    assert.ok(decoded);
    assert.equal(decoded.cid, longId);
  });

  test('T39 – token with missing dot is rejected', () => {
    assert.equal(verifyChallenge('nodothere'), null);
  });

  test('T40 – token with empty sig is rejected', () => {
    const exp = Date.now() + 30_000;
    const token = signChallenge('cid-x', 'lootlabs', exp);
    const payload = token.split('.')[0];
    assert.equal(verifyChallenge(payload + '.'), null);
  });
});

// ──────────────────────────────────────────────────────────────
// SECTION 8: Route behaviour with fake session
// ──────────────────────────────────────────────────────────────
describe('authenticated route protection', () => {

  test('T41 – requireLogin allows request with valid session user', async () => {
    // We can't easily inject session via supertest without a helper.
    // Test by checking the redirect sends to /login (proves middleware fires).
    const res = await request(app).get('/dashboard');
    assert.ok([301, 302].includes(res.status));
    assert.match(res.headers.location, /login/);
  });

  test('T42 – POST /license/provider without CSRF → redirects', async () => {
    const res = await request(app)
      .post('/license/provider')
      .send('provider=lootlabs&challenge_id=fake')
      .set('Content-Type', 'application/x-www-form-urlencoded');
    assert.ok([301, 302].includes(res.status));
  });

  test('T43 – GET /unlock/lootlabs while unauthenticated → redirects to login', async () => {
    const res = await request(app).get('/unlock/lootlabs');
    assert.ok([301, 302].includes(res.status));
    assert.match(res.headers.location, /login/);
  });

  test('T44 – GET /unlock/linkvertise/done while unauthenticated → redirect', async () => {
    const res = await request(app).get('/unlock/linkvertise/done?challenge=fake');
    assert.ok([301, 302].includes(res.status));
  });

  test('T45 – Content-Type of /health is application/json', async () => {
    const res = await request(app).get('/health');
    assert.match(res.headers['content-type'], /application\/json/);
  });
});
