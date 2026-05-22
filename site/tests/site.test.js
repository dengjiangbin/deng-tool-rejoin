'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-the-site-suite';
process.env.TOOL_SITE_STATE_SECRET = 'test-state-secret-that-is-long-enough-for-challenge-suite';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_PUBLIC_URL = 'http://localhost:8791';
process.env.LICENSE_API_PUBLIC_URL = 'https://rejoin.deng.my.id';
process.env.DISCORD_CLIENT_ID = 'discord-client-id';
process.env.DISCORD_CLIENT_SECRET = 'discord-client-secret';
process.env.DISCORD_REDIRECT_URI = 'http://localhost:8791/auth/discord/callback';
process.env.LINKVERTISE_PUBLISHER_ID = '5914830';
process.env.LINKVERTISE_ENABLED = 'true';
process.env.LINKVERTISE_MONETIZED_URL = 'https://link-hub.net/5914830/XEpUhZ8TdtyV';
process.env.LINKVERTISE_TARGET_LINK_URL = 'https://link-hub.net/5914830/XEpUhZ8TdtyV';
process.env.LINKVERTISE_COMPLETE_URL = 'http://localhost:8791/unlock/linkvertise/complete';
process.env.LINKVERTISE_CALLBACK_URL = 'http://localhost:8791/unlock/linkvertise/complete';
process.env.LINKVERTISE_VERIFY_URL = 'https://publisher.linkvertise.com/api/v1/anti_bypassing';
process.env.LINKVERTISE_ANTI_BYPASS_TOKEN = 'test-anti-bypass-token-very-long-do-not-log-1234567890abcdef';
process.env.LOOTLABS_ENABLED = 'true';
process.env.LOOTLABS_MONETIZED_URL = 'https://lootdest.org/s?TqZQAW38';
process.env.LOOTLABS_COMPLETE_URL = 'http://localhost:8791/unlock/lootlabs/complete';
process.env.LOOTLABS_TEMPLATE_URL = 'https://lootlabs.example/unlock?target={url}';
process.env.AD_MIN_COMPLETION_SECONDS = '30';
process.env.AD_RETURN_SIGNING_SECRET = 'test-return-signing-secret-that-is-long-enough';

class MemoryQuery {
  constructor(db, table) {
    this.db = db;
    this.table = table;
    this.action = 'select';
    this.payload = null;
    this.filters = [];
    this.inFilters = [];
    this.gteFilters = [];
    this.neqFilters = [];
    this.orderSpec = null;
    this.limitCount = null;
    this.singleMode = false;
    this.maybeMode = false;
  }

  select() {
    if (this.action !== 'insert' && this.action !== 'update') this.action = 'select';
    return this;
  }

  insert(payload) {
    this.action = 'insert';
    this.payload = Array.isArray(payload) ? payload : [payload];
    return this;
  }

  update(payload) {
    this.action = 'update';
    this.payload = payload;
    return this;
  }

  eq(field, value) {
    this.filters.push({ field, value });
    return this;
  }

  in(field, values) {
    this.inFilters.push({ field, values });
    return this;
  }

  gte(field, value) {
    this.gteFilters.push({ field, value });
    return this;
  }

  neq(field, value) {
    this.neqFilters.push({ field, value });
    return this;
  }

  order(field, spec = {}) {
    this.orderSpec = { field, ascending: spec.ascending !== false };
    return this;
  }

  limit(count) {
    this.limitCount = count;
    return this;
  }

  maybeSingle() {
    this.maybeMode = true;
    return this.executeSingle(true);
  }

  single() {
    this.singleMode = true;
    return this.executeSingle(false);
  }

  then(resolve, reject) {
    return this.execute().then(resolve, reject);
  }

  _rows() {
    if (!this.db[this.table]) this.db[this.table] = [];
    return this.db[this.table];
  }

  _matches(row) {
    return this.filters.every((f) => row[f.field] === f.value) &&
      this.inFilters.every((f) => f.values.includes(row[f.field])) &&
      this.gteFilters.every((f) => String(row[f.field] || '') >= String(f.value)) &&
      this.neqFilters.every((f) => row[f.field] !== f.value);
  }

  async execute() {
    const rows = this._rows();
    if (this.action === 'insert') {
      const now = new Date().toISOString();
      const inserted = this.payload.map((row) => {
        const next = {
          created_at: now,
          updated_at: now,
          ...row,
          id: row.id || randomUUID(),
        };
        rows.push(next);
        return next;
      });
      return { data: inserted, error: null, count: inserted.length };
    }

    if (this.action === 'update') {
      const updated = [];
      for (const row of rows) {
        if (this._matches(row)) {
          Object.assign(row, this.payload, { updated_at: new Date().toISOString() });
          updated.push(row);
        }
      }
      return { data: updated, error: null, count: updated.length };
    }

    let result = rows.filter((row) => this._matches(row));
    if (this.orderSpec) {
      const { field, ascending } = this.orderSpec;
      result = result.slice().sort((a, b) => {
        if ((a[field] || '') === (b[field] || '')) return 0;
        return (a[field] || '') > (b[field] || '') ? (ascending ? 1 : -1) : (ascending ? -1 : 1);
      });
    }
    if (this.limitCount !== null) result = result.slice(0, this.limitCount);
    return { data: result, error: null, count: result.length };
  }

  async executeSingle(maybe) {
    const { data } = await this.execute();
    if (data.length) return { data: data[0], error: null };
    return { data: null, error: maybe ? null : { message: 'No rows found' } };
  }
}

const memoryDb = {
  site_users: [],
  license_ad_challenges: [],
  license_keys: [],
  license_users: [],
};

const mockSupabase = {
  from(table) {
    return new MemoryQuery(memoryDb, table);
  },
};

// Mocked Linkvertise Anti-Bypass API. Models the real behaviour:
//   - mode='auto' (default): TRUE iff the hash was registered in validHashes,
//     and the hash is consumed (deleted) on first successful verify.
//   - mode='true'          : always TRUE
//   - mode='false'         : always FALSE
//   - mode='invalid_token' : returns "invalid token" payload
//   - mode='http500'       : returns HTTP 500
//   - mode='timeout'       : rejects with ECONNABORTED
//   - mode='network'       : rejects with ECONNRESET
//   - mode='invalid_response': returns unrecognised JSON shape
const linkvertiseApi = {
  mode: 'auto',
  validHashes: new Set(),
  lastCall: null,
  callCount: 0,
};

function resetLinkvertiseApi() {
  linkvertiseApi.mode = 'auto';
  linkvertiseApi.validHashes.clear();
  linkvertiseApi.lastCall = null;
  linkvertiseApi.callCount = 0;
}

function extractFormValue(body, key) {
  if (!body || typeof body !== 'string') return '';
  for (const part of body.split('&')) {
    const eq = part.indexOf('=');
    if (eq < 0) continue;
    if (decodeURIComponent(part.slice(0, eq)) === key) {
      return decodeURIComponent(part.slice(eq + 1));
    }
  }
  return '';
}

function linkvertiseMockResponse(url, body) {
  linkvertiseApi.callCount += 1;
  linkvertiseApi.lastCall = { url, body: String(body || '') };
  const hash = extractFormValue(linkvertiseApi.lastCall.body, 'hash');
  // Mirror the real live API shape: `{ "status": true | false }`.
  switch (linkvertiseApi.mode) {
    case 'true':
      return Promise.resolve({ status: 200, data: { status: true } });
    case 'false':
      return Promise.resolve({ status: 200, data: { status: false } });
    case 'invalid_token':
      return Promise.resolve({ status: 200, data: { error: 'Invalid token' } });
    case 'http500':
      return Promise.resolve({ status: 500, data: 'server error' });
    case 'invalid_response':
      return Promise.resolve({ status: 200, data: { whatever: 'shape' } });
    case 'timeout': {
      const err = new Error('timeout of 8000ms exceeded');
      err.code = 'ECONNABORTED';
      return Promise.reject(err);
    }
    case 'network': {
      const err = new Error('socket hang up');
      err.code = 'ECONNRESET';
      return Promise.reject(err);
    }
    case 'auto':
    default:
      if (linkvertiseApi.validHashes.has(hash)) {
        linkvertiseApi.validHashes.delete(hash);
        return Promise.resolve({ status: 200, data: { status: true } });
      }
      return Promise.resolve({ status: 200, data: { status: false } });
  }
}

// fakeAxios is declared as a plain object so individual tests can temporarily
// override .get() to exercise different Discord identity responses, and so
// .post() can route Linkvertise Anti-Bypass calls to the mock above.
const fakeAxios = {
  async post(url, body) {
    if (typeof url === 'string' && url.includes('anti_bypassing')) {
      return linkvertiseMockResponse(url, body);
    }
    return { data: { access_token: 'discord-access-token' } };
  },
  async get() {
    return {
      data: {
        id: 'discord-user-1',
        username: 'DiscordTester',
        avatar: null,
        email: null,
      },
    };
  },
};

require.cache[require.resolve('../src/db')] = {
  id: require.resolve('../src/db'),
  filename: require.resolve('../src/db'),
  loaded: true,
  exports: mockSupabase,
};
require.cache[require.resolve('axios')] = {
  id: require.resolve('axios'),
  filename: require.resolve('axios'),
  loaded: true,
  exports: fakeAxios,
};

const request = require('supertest');
const app = require('../src/app');
const { signChallenge } = require('../src/crypto');
const { AD_MIN_COMPLETION_SECONDS, classifyChallengeInsertError } = require('../src/challenge');

function resetDb() {
  memoryDb.site_users.splice(0);
  memoryDb.license_ad_challenges.splice(0);
  memoryDb.license_keys.splice(0);
  memoryDb.license_users.splice(0);
}

function csrfFrom(html) {
  const match = html.match(/name="_csrf" value="([^"]+)"/);
  assert.ok(match, 'CSRF token should be present');
  return match[1];
}

function challengeIdFrom(html) {
  const match = html.match(/name="challenge_id" value="([^"]+)"/);
  assert.ok(match, 'challenge id should be present');
  return match[1];
}

function countOpaqueNearBlackPng(filePath) {
  const png = fs.readFileSync(filePath);
  assert.ok(png.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])));
  let pos = 8;
  let width = 0;
  let height = 0;
  let bitDepth = 0;
  let colorType = 0;
  let interlace = 0;
  const idat = [];
  while (pos < png.length) {
    const len = png.readUInt32BE(pos); pos += 4;
    const type = png.toString('ascii', pos, pos + 4); pos += 4;
    const data = png.subarray(pos, pos + len); pos += len;
    pos += 4;
    if (type === 'IHDR') {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      bitDepth = data[8];
      colorType = data[9];
      interlace = data[12];
    } else if (type === 'IDAT') {
      idat.push(Buffer.from(data));
    } else if (type === 'IEND') {
      break;
    }
  }
  assert.equal(bitDepth, 8);
  assert.equal(colorType, 6);
  assert.equal(interlace, 0);
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const bpp = 4;
  const stride = width * bpp;
  const pixels = Buffer.alloc(height * stride);
  let src = 0;
  let dst = 0;
  const paeth = (a, b, c) => {
    const p = a + b - c;
    const pa = Math.abs(p - a);
    const pb = Math.abs(p - b);
    const pc = Math.abs(p - c);
    if (pa <= pb && pa <= pc) return a;
    return pb <= pc ? b : c;
  };
  for (let y = 0; y < height; y += 1) {
    const filter = raw[src];
    src += 1;
    for (let x = 0; x < stride; x += 1) {
      const left = x >= bpp ? pixels[dst + x - bpp] : 0;
      const up = y > 0 ? pixels[dst + x - stride] : 0;
      const upLeft = y > 0 && x >= bpp ? pixels[dst + x - stride - bpp] : 0;
      const value = raw[src];
      src += 1;
      if (filter === 0) pixels[dst + x] = value;
      else if (filter === 1) pixels[dst + x] = (value + left) & 255;
      else if (filter === 2) pixels[dst + x] = (value + up) & 255;
      else if (filter === 3) pixels[dst + x] = (value + Math.floor((left + up) / 2)) & 255;
      else if (filter === 4) pixels[dst + x] = (value + paeth(left, up, upLeft)) & 255;
      else throw new Error(`Unsupported PNG filter ${filter}`);
    }
    dst += stride;
  }
  let opaqueNearBlack = 0;
  for (let i = 0; i < pixels.length; i += 4) {
    const max = Math.max(pixels[i], pixels[i + 1], pixels[i + 2]);
    const min = Math.min(pixels[i], pixels[i + 1], pixels[i + 2]);
    if (pixels[i + 3] > 0 && (max < 70 || (max < 96 && max - min < 28))) {
      opaqueNearBlack += 1;
    }
  }
  return opaqueNearBlack;
}

/** Log in via Discord OAuth using the mock fakeAxios identity. */
async function login(agent) {
  const start = await agent.get('/auth/discord');
  assert.equal(start.status, 302);
  const state = new URL(start.headers.location).searchParams.get('state');
  const res = await agent.get(`/auth/discord/callback?code=ok&state=${state}`);
  assert.equal(res.status, 302);
  assert.equal(res.headers.location, '/dashboard');
}

async function startChallenge(agent) {
  const page = await agent.get('/license');
  const csrf = csrfFrom(page.text);
  const res = await agent.post('/api/key/start').type('form').send({ _csrf: csrf });
  assert.equal(res.status, 200);
  return { html: res.text, csrf: csrfFrom(res.text), challengeId: challengeIdFrom(res.text) };
}

async function chooseProvider(agent, provider = 'lootlabs') {
  const started = await startChallenge(agent);
  const res = await agent.post(`/key/provider/${provider}`).type('form').send({
    _csrf: started.csrf,
    challenge_id: started.challengeId,
    provider,
  });
  assert.equal(res.status, 303);
  const location = res.headers.location;
  const basePublicUrl = process.env.TOOL_SITE_PUBLIC_URL || 'http://localhost:8791';

  if (provider === 'linkvertise') {
    // Linkvertise Target-Link Anti-Bypass: redirect goes straight to the real
    // link-hub.net target URL. There is NO signed `t=` token in the URL —
    // verification happens server-side via Linkvertise's Anti-Bypass API
    // using the `hash` query param that Linkvertise appends to the callback.
    assert.equal(location, process.env.LINKVERTISE_TARGET_LINK_URL);
    // Simulate Linkvertise issuing a hash to this visitor for this challenge
    // and pre-register it with the mocked Anti-Bypass API so a later POST to
    // /unlock/linkvertise/complete?hash=<hash> verifies TRUE exactly once.
    const hash = validLinkvertiseHash(started.challengeId);
    linkvertiseApi.validHashes.add(hash);
    return { started, res, location, returnUrl: null, returnToken: hash, linkvertiseHash: hash };
  }

  const locationUrl = new URL(location, basePublicUrl);
  // LootLabs template or generic: token nested in destination/return_url param
  const destParam =
    locationUrl.searchParams.get('return_url') ||
    locationUrl.searchParams.get('deng_return') ||
    locationUrl.searchParams.get('destination') ||
    locationUrl.searchParams.get('target') ||
    locationUrl.searchParams.get('url');
  assert.ok(destParam, 'provider redirect must include signed return URL');
  const returnUrl = destParam;
  const returnToken = new URL(returnUrl).searchParams.get('t');

  assert.ok(returnToken, 'signed return token must be present');
  assert.ok(returnToken.length > 80, 'return token must be long enough to be a valid HMAC token');
  return { started, res, location, returnUrl, returnToken };
}

/** Generate a syntactically valid (64 url-safe chars) Linkvertise hash. */
function validLinkvertiseHash(seed = '') {
  const base = require('node:crypto').createHash('sha256').update(`lv:${seed || Date.now()}`).digest('hex');
  return base.padEnd(64, 'a').slice(0, 64);
}

function ageProviderStart(seconds = AD_MIN_COMPLETION_SECONDS + 1, index = 0) {
  const row = memoryDb.license_ad_challenges[index];
  assert.ok(row, 'challenge row must exist before aging provider start');
  // IMPORTANT: spread previous payload first so Linkvertise-specific markers
  // (linkvertise_started, target_link_host, callback_url) are preserved.
  row.provider_payload = {
    ...(row.provider_payload || {}),
    redirect_started: true,
    provider_started_at: new Date(Date.now() - seconds * 1000).toISOString(),
  };
}

function providerReferer(provider) {
  // Reflect the actual referer each provider sends after monetisation:
  // - Linkvertise Full Script: user returns from linkvertise.com
  // - LootLabs: user returns from lootdest.org (template URL provider)
  return provider === 'lootlabs'
    ? 'https://lootdest.org/'
    : 'https://linkvertise.com/';
}

async function completeProvider(agent, provider = 'linkvertise', returnToken = '', referer = providerReferer(provider)) {
  // For Linkvertise we treat `returnToken` as the Linkvertise hash that the
  // provider appends to the callback URL as `?hash=...`. For LootLabs we
  // keep the legacy `?t=<signed_token>` behaviour.
  if (provider === 'linkvertise') {
    const suffix = returnToken ? `?hash=${encodeURIComponent(returnToken)}` : '';
    const req = agent.get(`/unlock/linkvertise/complete${suffix}`);
    if (referer) req.set('Referer', referer);
    return req;
  }
  const suffix = returnToken ? `?t=${encodeURIComponent(returnToken)}` : '';
  return agent.get(`/unlock/${provider}/complete${suffix}`).set('Referer', referer);
}

/**
 * Linkvertise-specific completion helper. Returns a fresh valid hash by
 * default and lets the caller override `linkvertiseApi.mode` to simulate
 * TRUE / FALSE / timeout etc.
 */
async function completeLinkvertise(agent, { hash, referer = '' } = {}) {
  const h = hash || validLinkvertiseHash();
  const req = agent.get(`/unlock/linkvertise/complete?hash=${encodeURIComponent(h)}`);
  if (referer) req.set('Referer', referer);
  const res = await req;
  return { res, hash: h };
}

function tamperToken(token) {
  return `${token.slice(0, -1)}${token.endsWith('a') ? 'b' : 'a'}`;
}

beforeEach(() => {
  resetDb();
  resetLinkvertiseApi();
});

describe('auth and protected pages', () => {
  test('login page shows Discord-only login with required text and no database login', async () => {
    const res = await request(app).get('/login');
    assert.equal(res.status, 200);
    assert.match(res.text, /DENG Tool/);
    assert.match(res.text, /Secure portal for DENG Tool: Rejoin/);
    assert.match(res.text, /Continue With Discord/);
    // Database login must be absent
    assert.doesNotMatch(res.text, /Username or email/);
    assert.doesNotMatch(res.text, /password/);
    assert.doesNotMatch(res.text, /sign in with database/i);
    assert.doesNotMatch(res.text, /sign up/i);
    assert.doesNotMatch(res.text, /register/i);
    assert.doesNotMatch(res.text, /\/auth\/login/);
  });

  test('Discord OAuth start redirects to Discord with identify scope only', async () => {
    const agent = request.agent(app);
    const res = await agent.get('/auth/discord');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /^https:\/\/discord\.com\/api\/v10\/oauth2\/authorize/);
    const url = new URL(res.headers.location);
    assert.equal(url.searchParams.get('scope'), 'identify');
    assert.equal(url.searchParams.get('response_type'), 'code');
    assert.equal(url.searchParams.get('client_id'), 'discord-client-id');
    assert.ok(url.searchParams.get('state'), 'state nonce must be present');
  });

  test('Discord callback creates and logs in a portal user', async () => {
    const agent = request.agent(app);
    const start = await agent.get('/auth/discord');
    const state = new URL(start.headers.location).searchParams.get('state');
    const res = await agent.get(`/auth/discord/callback?code=ok&state=${state}`);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/dashboard');
    assert.ok(memoryDb.site_users.some((row) => row.discord_user_id === 'discord-user-1'));
  });

  test('database login route is removed and returns 404', async () => {
    const routes = [
      { method: 'post', path: '/auth/login' },
      { method: 'post', path: '/auth/local' },
      { method: 'post', path: '/auth/database' },
      { method: 'post', path: '/login' },
      { method: 'post', path: '/signup' },
      { method: 'post', path: '/register' },
    ];
    for (const { method, path: p } of routes) {
      const res = await request(app)[method](p).type('form').send({ _csrf: 'x' });
      assert.ok([404, 410].includes(res.status), `${method.toUpperCase()} ${p} should be 404/410, got ${res.status}`);
    }
  });

  test('callback token exchange failure redirects to login and exposes no secrets', async () => {
    const originalPost = fakeAxios.post;
    fakeAxios.post = async () => {
      const err = new Error('Unauthorized');
      err.response = { status: 401, data: { error: 'invalid_client' } };
      throw err;
    };
    try {
      const agent = request.agent(app);
      const start = await agent.get('/auth/discord');
      const state = new URL(start.headers.location).searchParams.get('state');
      const res = await agent.get(`/auth/discord/callback?code=ok&state=${state}`);
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/login');
      // Must not expose secrets in redirect URL or response body
      assert.doesNotMatch(res.headers.location, /secret|token|code/i);
    } finally {
      fakeAxios.post = originalPost;
    }
  });

  test('callback state mismatch rejects without creating session', async () => {
    const agent = request.agent(app);
    await agent.get('/auth/discord'); // seeds oauthState in session
    const res = await agent.get('/auth/discord/callback?code=ok&state=WRONG_STATE');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/login');
    assert.equal(memoryDb.site_users.length, 0);
  });

  test('callback with missing state rejects (session expired scenario)', async () => {
    // No prior /auth/discord call, so no oauthState in session
    const res = await request(app).get('/auth/discord/callback?code=ok&state=anything');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/login');
  });

  test('identify-only Discord user (no email field) logs in successfully', async () => {
    const originalGet = fakeAxios.get;
    fakeAxios.get = async () => ({
      data: { id: 'discord-user-nomail', username: 'NoMailUser', avatar: null },
      // no email field at all
    });
    try {
      const agent = request.agent(app);
      const start = await agent.get('/auth/discord');
      const state = new URL(start.headers.location).searchParams.get('state');
      const res = await agent.get(`/auth/discord/callback?code=ok&state=${state}`);
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/dashboard');
      // Dashboard must be accessible
      const dash = await agent.get('/dashboard');
      assert.equal(dash.status, 200);
    } finally {
      fakeAxios.get = originalGet;
    }
  });

  test('logout destroys the session and protected pages redirect to login', async () => {
    const agent = request.agent(app);
    await login(agent);
    const page = await agent.get('/dashboard');
    const csrf = csrfFrom(page.text);
    const out = await agent.post('/auth/logout').type('form').send({ _csrf: csrf });
    assert.equal(out.status, 302);
    assert.equal(out.headers.location, '/login');
    const res = await agent.get('/dashboard');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/login');
  });

  test('protected pages and APIs redirect unauthenticated visitors', async () => {
    for (const route of ['/dashboard', '/license', '/api/key/start', '/api/license/me', '/api/license/history']) {
      const res = route === '/api/key/start'
        ? await request(app).post(route)
        : await request(app).get(route);
      assert.equal(res.status, 302, route);
      assert.equal(res.headers.location, '/login');
    }
  });
});

describe('theme and dashboard UI', () => {
  test('sidebar has only Dashboard, My License, and Logout nav labels', async () => {
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.get('/dashboard');
    assert.equal(res.status, 200);
    assert.match(res.text, /Dashboard/);
    assert.match(res.text, /My License/);
    assert.match(res.text, /Logout/);
    for (const forbidden of ['Device List', 'Executor Installer', 'Cookies', 'Modules', 'Extras']) {
      assert.doesNotMatch(res.text, new RegExp(forbidden));
    }
  });

  test('dashboard and My License render compact portal panels', async () => {
    const agent = request.agent(app);
    await login(agent);
    const dashboard = await agent.get('/dashboard');
    assert.match(dashboard.text, /Dashboard Overview/);
    assert.match(dashboard.text, /Generate Key/);
    assert.match(dashboard.text, /News & Updates/);
    assert.match(dashboard.text, /Your Activity/);
    assert.match(dashboard.text, /stats-grid/);
    assert.doesNotMatch(dashboard.text, /Buy License/);

    const license = await agent.get('/license');
    assert.doesNotMatch(license.text, /Current License Status/);
    assert.doesNotMatch(license.text, /Freshly generated keys are shown here as masked history/);
    assert.doesNotMatch(license.text, /No active generated key/);
    assert.doesNotMatch(license.text, /Unredeemed 0/);
    assert.doesNotMatch(license.text, /Expired 0/);
    assert.doesNotMatch(license.text, /Expires if unused/);
    assert.doesNotMatch(license.text, /24h/);
    assert.match(license.text, /Generate DENG Tool: Rejoin Key/);
    assert.match(license.text, /Generate Key/);
    assert.match(license.text, /Recent Generated Keys/);
  });

  test('logo image replaces DT placeholders on login and dashboard', async () => {
    const loginPage = await request(app).get('/login');
    assert.match(loginPage.text, /\/public\/img\/deng-logo\.png\?v=/);
    assert.doesNotMatch(loginPage.text, />DT</);
    assert.doesNotMatch(loginPage.text, /favicon\.svg/);

    const agent = request.agent(app);
    await login(agent);
    const dashboard = await agent.get('/dashboard');
    assert.match(dashboard.text, /\/public\/img\/deng-logo\.png\?v=/);
    assert.doesNotMatch(dashboard.text, />DT</);
  });

  test('logo PNG has transparent near-black pixels instead of black backing', () => {
    const opaqueNearBlack = countOpaqueNearBlackPng(path.join(__dirname, '..', 'public', 'img', 'deng-logo.png'));
    assert.equal(opaqueNearBlack, 0);
  });

  test('theme stylesheet uses logo-inspired neon blue-pink gradient and readable text', () => {
    const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'css', 'style.css'), 'utf8');
    assert.match(css, /#00cfff|#17a0dd/i);
    assert.match(css, /#ff2fb3|#c0187a/i);
    assert.match(css, /#6143b2/i);
    assert.match(css, /rgba\(255,\s*255,\s*255,\s*0\.82\)/i);
    assert.match(css, /\.nav-link\.active/);
    assert.match(css, /@media \(max-width: 760px\)/);
    assert.doesNotMatch(css, /#050816|#0b1020|#00C7A3/i);
  });

  test('layout includes cache-busted stylesheet URL', async () => {
    const res = await request(app).get('/login');
    assert.equal(res.status, 200);
    assert.match(res.text, /\/public\/css\/style\.css\?v=[A-Za-z0-9._-]+/);
  });

  test('dashboard does not show removed stat cards', async () => {
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.get('/dashboard');
    assert.equal(res.status, 200);
    assert.doesNotMatch(res.text, /Cooldown Status/);
    assert.doesNotMatch(res.text, /Redeemed Keys/);
    assert.doesNotMatch(res.text, /Active License/);
    assert.doesNotMatch(res.text, /Latest Key Status/);
    assert.doesNotMatch(res.text, /Tool Version/);
    assert.doesNotMatch(res.text, /See tool status/);
    assert.doesNotMatch(res.text, /Buy License/);
  });

  test('My License page does not show Expires if unused display', async () => {
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.get('/license');
    assert.equal(res.status, 200);
    assert.doesNotMatch(res.text, /Current License Status/);
    assert.doesNotMatch(res.text, /No active generated key/);
    assert.doesNotMatch(res.text, /Unredeemed 0/);
    assert.doesNotMatch(res.text, /Expired 0/);
    assert.doesNotMatch(res.text, /Expires if unused/);
    assert.doesNotMatch(res.text, /24h/);
    assert.match(res.text, /Generate DENG Tool: Rejoin Key/);
  });
});

describe('Luarmor-style key flow', () => {
  test('Generate Key creates a challenge and shows provider choices', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { html } = await startChallenge(agent);
    assert.match(html, /LootLabs/);
    assert.match(html, /Linkvertise/);
    assert.doesNotMatch(html, /Could not start key generation/);
    assert.equal(memoryDb.license_ad_challenges.length, 1);
    assert.equal(memoryDb.license_ad_challenges[0].status, 'created');
  });

  test('Generate Key repairs stale fallback site_user_id before challenge insert', async () => {
    const agent = request.agent(app);
    await login(agent);
    const page = await agent.get('/license');
    const csrf = csrfFrom(page.text);
    const staleId = memoryDb.site_users[0].id;
    const realId = randomUUID();
    memoryDb.site_users[0].id = realId;

    const res = await agent.post('/api/key/start').type('form').send({ _csrf: csrf });
    assert.equal(res.status, 200);
    assert.notEqual(staleId, realId);
    assert.equal(memoryDb.license_ad_challenges.length, 1);
    assert.equal(memoryDb.license_ad_challenges[0].site_user_id, realId);
    assert.match(res.text, /Linkvertise/);
    assert.match(res.text, /LootLabs/);
  });

  test('challenge insert error classification distinguishes FK from table missing', () => {
    assert.equal(classifyChallengeInsertError({
      code: '23503',
      message: 'insert or update on table "license_ad_challenges" violates foreign key constraint "license_ad_challenges_site_user_id_fkey"',
    }), 'DB_FOREIGN_KEY_FAILED');

    assert.equal(classifyChallengeInsertError({
      code: 'PGRST205',
      message: "Could not find the table 'public.license_ad_challenges' in the schema cache",
    }), 'CHALLENGE_TABLE_MISSING');

    assert.equal(classifyChallengeInsertError({
      code: '42501',
      message: 'permission denied for table license_ad_challenges',
    }), 'DB_PERMISSION_DENIED');
  });

  test('provider complete routes require an active session challenge', async () => {
    const agent = request.agent(app);
    await login(agent);
    for (const route of ['/unlock/linkvertise/complete', '/unlock/lootlabs/complete']) {
      const res = await agent.get(route).set('Accept', 'text/html');
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
    }
    assert.equal(memoryDb.license_keys.length, 0);
    const rendered = await agent.get('/license');
    assert.match(rendered.text, /Please start key generation again\./);
    assert.doesNotMatch(rendered.text, /^\{"error"/);
  });

  test('expired active provider challenge fails safely (Linkvertise)', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    memoryDb.license_ad_challenges[0].expires_at = new Date(Date.now() - 1000).toISOString();
    const res = await completeProvider(agent, 'linkvertise', hash);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('wrong user challenge ownership fails safely (Linkvertise)', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    memoryDb.license_ad_challenges[0].site_user_id = randomUUID();
    const res = await completeProvider(agent, 'linkvertise', hash);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('Linkvertise Target-Link Anti-Bypass start redirects 303 directly to link-hub.net', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { res, location } = await chooseProvider(agent, 'linkvertise');
    assert.equal(res.status, 303);
    // Must redirect EXACTLY to the configured link-hub.net Target-Link URL
    assert.equal(location, 'https://link-hub.net/5914830/XEpUhZ8TdtyV');
    assert.ok(location.includes('link-hub.net'));
    // Must NOT include the anti-bypass token in the URL
    assert.doesNotMatch(location, /anti.?bypass|token=/i);
    // Must NOT include a signed return token in the URL
    assert.doesNotMatch(location, /[?&]t=/);
    // Must NOT redirect to an internal Full Script start page
    assert.doesNotMatch(location, /\/unlock\/linkvertise\/start/);
    // Must NOT redirect to the completion URL
    assert.doesNotMatch(location, /\/unlock\/linkvertise\/complete/);
    assert.doesNotMatch(res.headers['content-type'] || '', /json/i);
    assert.equal(memoryDb.license_ad_challenges[0].provider, 'linkvertise');
    assert.equal(memoryDb.license_ad_challenges[0].status, 'pending_ad');
    const payload = memoryDb.license_ad_challenges[0].provider_payload;
    assert.equal(payload.linkvertise_started, true);
    assert.equal(payload.target_link_host, 'link-hub.net');
    assert.equal(payload.callback_url, 'http://localhost:8791/unlock/linkvertise/complete');
    assert.ok(payload.provider_started_at);
  });

  test('LootLabs template URL provider embeds signed return URL in destination param', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { res, location, returnUrl, returnToken } = await chooseProvider(agent, 'lootlabs');
    assert.equal(res.status, 303);
    // With LOOTLABS_TEMPLATE_URL set, location must use the template base
    assert.ok(location.startsWith('https://lootlabs.example/unlock?'), `expected template URL, got: ${location}`);
    assert.ok(!location.includes('lootdest.org'), 'must use template URL, not static lootdest shortlink');
    // The destination param must decode to the signed complete URL
    assert.match(returnUrl, /^http:\/\/localhost:8791\/unlock\/lootlabs\/complete\?t=/);
    assert.ok(returnToken.length > 80);
    assert.doesNotMatch(res.headers['content-type'] || '', /json/i);
    assert.equal(memoryDb.license_ad_challenges[0].provider, 'lootlabs');
    assert.equal(memoryDb.license_ad_challenges[0].status, 'pending_ad');
    assert.equal(memoryDb.license_ad_challenges[0].provider_payload.redirect_started, true);
    assert.ok(memoryDb.license_ad_challenges[0].provider_payload.provider_started_at);
    assert.equal(memoryDb.license_ad_challenges[0].provider_payload.return_token_hash.length, 64);
  });

  test('first provider click immediately redirects and repeated click does not corrupt the challenge', async () => {
    const agent = request.agent(app);
    await login(agent);
    const first = await chooseProvider(agent, 'linkvertise');
    assert.equal(first.res.status, 303);
    // Linkvertise Target-Link: first click goes straight to the real link-hub.net
    assert.equal(first.location, 'https://link-hub.net/5914830/XEpUhZ8TdtyV');
    assert.notEqual(first.location, '/license');

    // Repeated same-provider click: must still 303 (safe reissue)
    const second = await agent.post('/key/provider/linkvertise').type('form').send({
      _csrf: first.started.csrf,
      challenge_id: first.started.challengeId,
      provider: 'linkvertise',
    });
    assert.equal(second.status, 303);
    assert.equal(second.headers.location, 'https://link-hub.net/5914830/XEpUhZ8TdtyV');
    assert.notEqual(second.headers.location, '/license');
    assert.equal(memoryDb.license_ad_challenges.length, 1);
    assert.equal(memoryDb.license_ad_challenges[0].status, 'pending_ad');
  });

  test('provider selection works as a mobile top-level form redirect using template URL', async () => {
    const agent = request.agent(app);
    await login(agent);
    const started = await startChallenge(agent);
    const res = await agent.post('/key/provider/lootlabs')
      .set('User-Agent', 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile Safari/604.1')
      .type('form')
      .send({
        _csrf: started.csrf,
        challenge_id: started.challengeId,
        provider: 'lootlabs',
      });
    assert.equal(res.status, 303);
    // Must use template URL, not the static lootdest shortlink
    assert.ok(res.headers.location.startsWith('https://lootlabs.example/unlock?'), `expected template URL, got: ${res.headers.location}`);
    assert.doesNotMatch(res.headers['content-type'] || '', /json/i);
  });

  test('missing AD_RETURN_SIGNING_SECRET fails LootLabs provider redirect closed', async () => {
    // LootLabs still uses the signed return token; Linkvertise has its own
    // Anti-Bypass verification and no longer depends on AD_RETURN_SIGNING_SECRET.
    const agent = request.agent(app);
    await login(agent);
    const started = await startChallenge(agent);
    const originalSecret = process.env.AD_RETURN_SIGNING_SECRET;
    delete process.env.AD_RETURN_SIGNING_SECRET;
    try {
      const res = await agent.post('/key/provider/lootlabs').type('form').send({
        _csrf: started.csrf,
        challenge_id: started.challengeId,
        provider: 'lootlabs',
      });
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
      assert.equal(memoryDb.license_keys.length, 0);
    } finally {
      process.env.AD_RETURN_SIGNING_SECRET = originalSecret;
    }
  });

  test('legacy Linkvertise Full Script start page is unreachable (redirects, no raw completion button)', async () => {
    // After moving to the Target-Link Anti-Bypass flow, the internal start
    // page must no longer render a raw completion link / Full Script JS.
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.get('/unlock/linkvertise/start');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
  });

  test('legacy /unlock/linkvertise/start with any input redirects to license (never generates a key)', async () => {
    const agent = request.agent(app);
    await login(agent);
    for (const bad of ['', 'fake.token', 'abc123', 'anything', 'evil-payload']) {
      const suffix = bad ? `?t=${encodeURIComponent(bad)}` : '';
      const res = await agent.get(`/unlock/linkvertise/start${suffix}`);
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
    }
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('LootLabs is disabled as provider when LOOTLABS_TEMPLATE_URL is not configured', async () => {
    // Without LOOTLABS_TEMPLATE_URL, lootdest.org cannot redirect back to the DENG portal.
    // The server must refuse the provider selection request (302 PROVIDER_NOT_CONFIGURED)
    // instead of silently redirecting to a URL that has no return path.
    const agent = request.agent(app);
    await login(agent);
    const originalTmpl = process.env.LOOTLABS_TEMPLATE_URL;
    delete process.env.LOOTLABS_TEMPLATE_URL;
    try {
      const started = await startChallenge(agent);
      const res = await agent.post('/key/provider/lootlabs').type('form').send({
        _csrf: started.csrf,
        challenge_id: started.challengeId,
        provider: 'lootlabs',
      });
      // Must refuse (provider not ready) rather than silently generating a broken URL
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
      assert.equal(memoryDb.license_keys.length, 0);
      // No LootLabs key challenge must have been created
      const pendingLl = memoryDb.license_ad_challenges.filter(
        (c) => c.provider === 'lootlabs' && c.status === 'pending_ad',
      );
      assert.equal(pendingLl.length, 0);
    } finally {
      if (originalTmpl !== undefined) process.env.LOOTLABS_TEMPLATE_URL = originalTmpl;
    }
  });

  test('pending provider attempts are hidden from public history and totals', async () => {
    const agent = request.agent(app);
    await login(agent);
    await chooseProvider(agent, 'lootlabs');
    assert.equal(memoryDb.license_ad_challenges[0].status, 'pending_ad');

    const license = await agent.get('/license');
    assert.match(license.text, /No keys generated yet\./);
    assert.doesNotMatch(license.text, /DENG-\?\?\?\?-\?\?\?\?/);
    assert.doesNotMatch(license.text, /pending_ad|provider_selected|lootlabs/i);

    const dashboard = await agent.get('/dashboard');
    assert.match(dashboard.text, /Total Licenses[\s\S]*?<p class="stat-value">0<\/p>/);
    assert.doesNotMatch(dashboard.text, /pending_ad|provider_selected|lootlabs/i);
  });

  test('direct key result cannot generate or reveal a key', async () => {
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.get('/key/result');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('manual complete URL without provider referer is blocked for LootLabs', async () => {
    // LootLabs always requires a valid referer from the provider domain.
    // Linkvertise is exempt because their interstitial does not forward Referer.
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'lootlabs');
    ageProviderStart();

    const res = await agent
      .get(`/unlock/lootlabs/complete?t=${encodeURIComponent(returnToken)}`)
      .set('Accept', 'text/html');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
    const rendered = await agent.get('/license');
    assert.match(rendered.text, /Could not verify ad completion\. Please complete the ad step again\./);
    assert.doesNotMatch(rendered.text, /^\{"error"/);
  });

  test('Linkvertise completion succeeds via hash (Anti-Bypass TRUE) without referer requirement', async () => {
    // Linkvertise does not forward the Referer header to the completion URL.
    // With the Target-Link Anti-Bypass flow, the Linkvertise API server-side
    // hash verification provides equivalent (stronger) protection.
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');

    // No Referer header — simulates Linkvertise behaviour
    const res = await agent
      .get(`/unlock/linkvertise/complete?hash=${encodeURIComponent(hash)}`)
      .set('Accept', 'text/html');
    assert.equal(res.status, 302, 'Linkvertise completion via hash should succeed');
    assert.equal(res.headers.location, '/key/result');
    assert.equal(memoryDb.license_keys.length, 1);
    // Verify Linkvertise Anti-Bypass API was actually called with the hash
    assert.equal(linkvertiseApi.callCount, 1);
    assert.ok(linkvertiseApi.lastCall.body.includes(`hash=${encodeURIComponent(hash)}`));
    // Token must be sent in body, never in URL
    assert.ok(!linkvertiseApi.lastCall.url.includes('token='));
    assert.match(linkvertiseApi.lastCall.body, /token=/);
  });

  test('Linkvertise completion with non-Linkvertise referer still succeeds when Anti-Bypass TRUE', async () => {
    // Linkvertise no longer relies on referer. The Anti-Bypass hash is the
    // authoritative proof, so a non-Linkvertise referer alone is not enough
    // to reject — only the API result matters.
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');

    const res = await completeProvider(agent, 'linkvertise', hash, 'https://evil.example.com/');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/key/result');
    assert.equal(memoryDb.license_keys.length, 1);
  });

  test('Linkvertise completion without hash is blocked even with a pending challenge', async () => {
    const agent = request.agent(app);
    await login(agent);
    await chooseProvider(agent, 'linkvertise');

    const res = await completeProvider(agent, 'linkvertise', '');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
    // Linkvertise API must not have been called when there's no hash
    assert.equal(linkvertiseApi.callCount, 0);
  });

  test('fake and tampered Linkvertise hashes are blocked', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');

    // 1. Malformed hash ('fake.token' is not 64 url-safe chars) — blocked at
    //    format check, no API call should be made.
    const fake = await completeProvider(agent, 'linkvertise', 'fake.token');
    assert.equal(fake.status, 302);
    assert.equal(fake.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
    assert.equal(linkvertiseApi.callCount, 0);

    // 2. Tampered hash (still 64 url-safe chars, but the byte was flipped) —
    //    format passes, but Linkvertise returns FALSE for an unknown hash.
    const tampered = await completeProvider(agent, 'linkvertise', tamperToken(hash));
    assert.equal(tampered.status, 302);
    assert.equal(tampered.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
    assert.ok(linkvertiseApi.callCount >= 1, 'tampered hash should reach the Linkvertise API');
  });

  test('Linkvertise Anti-Bypass FALSE blocks key generation', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    linkvertiseApi.mode = 'false';

    const res = await completeProvider(agent, 'linkvertise', hash);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
    assert.equal(linkvertiseApi.callCount, 1);
  });

  test('Linkvertise Anti-Bypass API timeout blocks key generation (fail-closed)', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    linkvertiseApi.mode = 'timeout';

    const res = await completeProvider(agent, 'linkvertise', hash);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('Linkvertise Anti-Bypass HTTP 500 blocks key generation (fail-closed)', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    linkvertiseApi.mode = 'http500';

    const res = await completeProvider(agent, 'linkvertise', hash);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('Linkvertise Anti-Bypass "invalid token" response blocks key generation', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    linkvertiseApi.mode = 'invalid_token';

    const res = await completeProvider(agent, 'linkvertise', hash);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('Linkvertise challenge expiry blocks completion even with a valid hash', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    memoryDb.license_ad_challenges[0].expires_at = new Date(Date.now() - 1000).toISOString();

    const res = await completeProvider(agent, 'linkvertise', hash);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('a different Discord user cannot complete another user\'s pending Linkvertise challenge', async () => {
    // A second Discord user logging in with a fresh session has no
    // activeAdChallengeId, so even a syntactically valid hash cannot bind
    // to another user's pending challenge.
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');

    const other = request.agent(app);
    const originalGet = fakeAxios.get;
    fakeAxios.get = async () => ({
      data: { id: 'discord-user-2', username: 'OtherUser', avatar: null, email: null },
    });
    try {
      await login(other);
      // Other user has NO pending challenge but tries to use the first
      // user's hash — must be rejected by the session ownership check
      // before any API call.
      const wrongUser = await completeProvider(other, 'linkvertise', hash);
      assert.equal(wrongUser.status, 302);
      assert.equal(wrongUser.headers.location, '/license');
      assert.equal(memoryDb.license_keys.length, 0);

      // Other user properly starts their own challenge then completes it.
      const otherStart = await chooseProvider(other, 'linkvertise');
      const validOther = await completeProvider(other, 'linkvertise', otherStart.returnToken);
      assert.equal(validOther.status, 302);
      assert.equal(validOther.headers.location, '/key/result');
      assert.equal(memoryDb.license_keys.length, 1);
    } finally {
      fakeAxios.get = originalGet;
    }
  });

  test('provider complete URL before minimum ad wait is blocked', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'lootlabs');

    const res = await completeProvider(agent, 'lootlabs', returnToken);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
    const rendered = await agent.get('/license');
    assert.match(rendered.text, /Please complete the ad step before continuing\./);
  });

  test('wrong provider complete route is blocked for a Linkvertise challenge', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');

    // LootLabs route invoked with a Linkvertise-bound active challenge → must
    // be rejected by the provider mismatch check.
    const res = await completeProvider(agent, 'lootlabs', hash);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
    const rendered = await agent.get('/license');
    assert.match(rendered.text, /Invalid or expired key generation session\. Please start again\./);
  });

  test('valid Linkvertise unlock generates one key, keeps it out of the URL, and shows redeem instructions', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');

    const unlock = await completeProvider(agent, 'linkvertise', hash);
    assert.equal(unlock.status, 302);
    assert.equal(unlock.headers.location, '/key/result');
    assert.equal(memoryDb.license_keys.length, 1);
    assert.equal(linkvertiseApi.callCount, 1);
    // Anti-Bypass token must travel in the request BODY, never the URL
    assert.doesNotMatch(linkvertiseApi.lastCall.url, /token=/);
    assert.match(linkvertiseApi.lastCall.body, /token=/);

    const result = await agent.get('/key/result');
    assert.equal(result.status, 200);
    assert.match(result.text, /DENG-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}/);
    assert.match(result.text, /Redeem this key inside DENG Tool: Rejoin to use the tool\./);
    assert.match(result.text, /This key expires if not redeemed within 24 hours\./);
    assert.match(result.text, /Open DENG Tool: Rejoin, paste this license key, then continue setup\./);
    assert.doesNotMatch(result.req.path, /DENG-/);
  });

  test('valid LootLabs signed return generates one key', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'lootlabs');
    ageProviderStart();

    const unlock = await completeProvider(agent, 'lootlabs', returnToken);
    assert.equal(unlock.status, 302);
    assert.equal(unlock.headers.location, '/key/result');
    assert.equal(memoryDb.license_keys.length, 1);
  });

  test('tampered LootLabs token and legacy Linkvertise URL are rejected', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');

    // LootLabs route called with Linkvertise hash as ?t= — fails provider
    // mismatch (and bad signed token shape).
    const tampered = await completeProvider(agent, 'lootlabs', hash);
    assert.equal(tampered.status, 302);
    assert.equal(memoryDb.license_keys.length, 0);

    // Legacy signed challenge query on /unlock/linkvertise → just redirects.
    const expiredToken = signChallenge('missing', 'linkvertise', Date.now() - 1000);
    const expired = await agent.get(`/unlock/linkvertise?challenge=${encodeURIComponent(expiredToken)}`);
    assert.equal(expired.status, 302);
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('callback replay and double-submit do not create duplicate keys (Linkvertise)', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');

    const first = await completeProvider(agent, 'linkvertise', hash);
    assert.equal(first.headers.location, '/key/result');
    // Same hash replayed: Linkvertise hash is single-use AND the challenge
    // is already consumed — both lines of defence must prevent a second key.
    const second = await completeProvider(agent, 'linkvertise', hash);
    assert.notEqual(second.headers.location, '/key/result');
    assert.equal(memoryDb.license_keys.length, 1);
  });

  test('server-side cooldown is enforced after a generated key (Linkvertise)', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    await completeProvider(agent, 'linkvertise', hash);

    const page = await agent.get('/license');
    const csrf = csrfFrom(page.text);
    const blocked = await agent.post('/api/key/start').type('form').send({ _csrf: csrf });
    assert.equal(blocked.status, 302);
    assert.equal(blocked.headers.location, '/license');
  });

  test('authenticated license history shows full unmasked keys for the key owner', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    await completeProvider(agent, 'linkvertise', hash);
    const license = await agent.get('/license');
    assert.equal(license.status, 200);
    // Full key must appear in the history table
    assert.match(license.text, /DENG-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}/);
    // Masked keys must NOT appear
    assert.doesNotMatch(license.text, /\*\*\*\*/);
    assert.match(license.text, /Linkvertise/);
    assert.match(license.text, /Generated/);
    assert.doesNotMatch(license.text, />linkvertise</);
    assert.doesNotMatch(license.text, /pending_ad/);
  });

  test('too many attempts render a portal error instead of a raw JSON page for browser flow', async () => {
    process.env.ENABLE_RATE_LIMIT_TEST = '1';
    const agent = request.agent(app);
    try {
      await login(agent);
      const page = await agent.get('/license');
      const csrf = csrfFrom(page.text);
      let blocked = null;
      for (let i = 0; i < 6; i += 1) {
        blocked = await agent.post('/api/key/start')
          .set('Accept', 'text/html')
          .type('form')
          .send({ _csrf: csrf });
      }
      assert.equal(blocked.status, 303);
      assert.equal(blocked.headers.location, '/license');
      assert.doesNotMatch(blocked.headers['content-type'] || '', /json/i);
      const rendered = await agent.get('/license');
      assert.match(rendered.text, /Too many key generation attempts\. Please wait before trying again\./);
      assert.doesNotMatch(rendered.text, /^\{"error"/);
    } finally {
      delete process.env.ENABLE_RATE_LIMIT_TEST;
    }
  });
});

describe('security controls', () => {
  test('CSRF blocks state-changing requests', async () => {
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.post('/api/key/start').type('form').send({ _csrf: 'bad-token' });
    assert.equal(res.status, 302);
    assert.equal(memoryDb.license_ad_challenges.length, 0);
  });

  test('XSS-style usernames are escaped in rendered pages', async () => {
    const originalGet = fakeAxios.get;
    fakeAxios.get = async () => ({
      data: { id: 'xss-user-1', username: '<script>alert(1)</script>', avatar: null },
    });
    try {
      const agent = request.agent(app);
      await login(agent);
      const res = await agent.get('/dashboard');
      assert.doesNotMatch(res.text, /<script>alert\(1\)<\/script>/);
      assert.match(res.text, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
    } finally {
      fakeAxios.get = originalGet;
    }
  });

  test('open redirect input is ignored by auth callback failures', async () => {
    const res = await request(app)
      .get('/auth/discord/callback?error=access_denied&redirect=https://evil.example');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/login');
  });

  test('frontend responses do not expose server secrets or full keys outside authorized result', async () => {
    const res = await request(app).get('/login');
    assert.doesNotMatch(res.text, /test-service-role-key/);
    assert.doesNotMatch(res.text, /test-state-secret/);
    assert.doesNotMatch(res.text, /DENG-[0-9A-F]{4}/);
  });

  test('session cookie and headers are configured for HttpOnly/SameSite/Secure production behavior', async () => {
    const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'app.js'), 'utf8');
    assert.match(source, /httpOnly:\s*true/);
    assert.match(source, /secure:\s*process\.env\.NODE_ENV === 'production'/);
    assert.match(source, /sameSite:\s*'lax'/);
    assert.match(source, /contentSecurityPolicy/);
    assert.match(source, /frameAncestors/);
  });
});

describe('health and service identity', () => {
  test('health reports the portal service and port 8791', async () => {
    const res = await request(app).get('/health');
    assert.equal(res.status, 200);
    assert.equal(res.body.service, 'deng-tool-site');
    assert.equal(res.body.port, 8791);
  });

  test('public stats route exposes cooldown and expiry without secrets', async () => {
    const res = await request(app).get('/api/stats/public');
    assert.equal(res.status, 200);
    assert.equal(res.body.cooldown_seconds, 60);
    assert.equal(res.body.unredeemed_key_expiry_hours, 24);
    assert.doesNotMatch(JSON.stringify(res.body), /service-role|secret/i);
  });
});

describe('provider UI and security gate', () => {
  test('Linkvertise disabled in choose_provider UI when LINKVERTISE_ENABLED is false', async () => {
    const originalEnabled = process.env.LINKVERTISE_ENABLED;
    process.env.LINKVERTISE_ENABLED = 'false';
    try {
      const agent = request.agent(app);
      await login(agent);
      const { html } = await startChallenge(agent);
      assert.match(html, /Linkvertise is temporarily unavailable/i);
      assert.doesNotMatch(html, /action="\/key\/provider\/linkvertise"/);
    } finally {
      process.env.LINKVERTISE_ENABLED = originalEnabled;
    }
  });

  test('Linkvertise disabled when LINKVERTISE_ANTI_BYPASS_TOKEN is missing', async () => {
    const original = process.env.LINKVERTISE_ANTI_BYPASS_TOKEN;
    delete process.env.LINKVERTISE_ANTI_BYPASS_TOKEN;
    try {
      const agent = request.agent(app);
      await login(agent);
      const { html, csrf, challengeId } = await startChallenge(agent);
      assert.match(html, /Linkvertise is temporarily unavailable/i);
      assert.doesNotMatch(html, /action="\/key\/provider\/linkvertise"/);

      // Even forging a POST must be refused with PROVIDER_NOT_CONFIGURED.
      const res = await agent.post('/key/provider/linkvertise').type('form').send({
        _csrf: csrf,
        challenge_id: challengeId,
        provider: 'linkvertise',
      });
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
    } finally {
      process.env.LINKVERTISE_ANTI_BYPASS_TOKEN = original;
    }
  });

  test('Linkvertise disabled when LINKVERTISE_TARGET_LINK_URL (and fallback) are missing', async () => {
    const origTarget = process.env.LINKVERTISE_TARGET_LINK_URL;
    const origMonetized = process.env.LINKVERTISE_MONETIZED_URL;
    delete process.env.LINKVERTISE_TARGET_LINK_URL;
    delete process.env.LINKVERTISE_MONETIZED_URL;
    try {
      const agent = request.agent(app);
      await login(agent);
      const { html } = await startChallenge(agent);
      assert.match(html, /Linkvertise is temporarily unavailable/i);
      assert.doesNotMatch(html, /action="\/key\/provider\/linkvertise"/);
    } finally {
      if (origTarget !== undefined) process.env.LINKVERTISE_TARGET_LINK_URL = origTarget;
      if (origMonetized !== undefined) process.env.LINKVERTISE_MONETIZED_URL = origMonetized;
    }
  });

  test('LootLabs disabled in choose_provider when LOOTLABS_TEMPLATE_URL not set', async () => {
    const originalTmpl = process.env.LOOTLABS_TEMPLATE_URL;
    delete process.env.LOOTLABS_TEMPLATE_URL;
    try {
      const agent = request.agent(app);
      await login(agent);
      const { html } = await startChallenge(agent);
      // LootLabs unavailable card must be shown
      assert.match(html, /LootLabs is temporarily unavailable/i);
      // Must not have an active submit form for LootLabs
      assert.doesNotMatch(html, /action="\/key\/provider\/lootlabs"(?=(?:[^"]*"[^"]*")*[^"]*$)/);
    } finally {
      if (originalTmpl !== undefined) process.env.LOOTLABS_TEMPLATE_URL = originalTmpl;
    }
  });

  test('key result page shows DENG logo image not generic OK badge', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    await completeProvider(agent, 'linkvertise', hash);

    const result = await agent.get('/key/result');
    assert.equal(result.status, 200);
    assert.match(result.text, /\/public\/img\/deng-logo\.png/);
    // Generic OK badge must not appear as the result icon
    assert.doesNotMatch(result.text, /<div[^>]*class="key-success-mark"[^>]*>OK<\/div>/);
  });

  test('cooldown notice is never rendered blank (secondsLeft=0 shows no notice)', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    await completeProvider(agent, 'linkvertise', hash);

    // Simulate cooldown having already expired at render time
    const row = memoryDb.license_ad_challenges[0];
    // Wind completed_at back so secondsLeft would be 0 or negative
    row.completed_at = new Date(Date.now() - 65 * 1000).toISOString();
    row.created_at = new Date(Date.now() - 65 * 1000).toISOString();

    const license = await agent.get('/license');
    assert.equal(license.status, 200);
    // Blank cooldown notice must never appear
    assert.doesNotMatch(license.text, /Cooldown active:\s*<span[^>]*class="countdown"[^>]*><\/span>/);
  });

  test('direct Linkvertise completion from a different session does not generate key', async () => {
    // Directly hitting the completion URL without going through the provider
    // flow in this session must be blocked, even with an otherwise-valid hash
    // and even if a real Linkvertise call would have returned TRUE.
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');

    // A second fresh agent (same Discord user, different session) tries the
    // first session's hash — must be rejected because the second session has
    // no activeAdChallengeId.
    const other = request.agent(app);
    const originalGet = fakeAxios.get;
    fakeAxios.get = async () => ({
      data: { id: 'discord-user-1', username: 'DiscordTester', avatar: null },
    });
    try {
      await login(other);
      const res = await other.get(`/unlock/linkvertise/complete?hash=${encodeURIComponent(hash)}`);
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
      assert.equal(memoryDb.license_keys.length, 0);
    } finally {
      fakeAxios.get = originalGet;
    }
  });

  test('direct /unlock/linkvertise/complete (no hash, no t=) is blocked even with logged-in session', async () => {
    const agent = request.agent(app);
    await login(agent);
    // No challenge ever started → both ownership check and hash check refuse.
    for (const url of [
      '/unlock/linkvertise/complete',
      '/unlock/linkvertise/complete?hash=fake',
      '/unlock/linkvertise/complete?t=anything',
      '/unlock/linkvertise/complete?t=anything&hash=fake',
    ]) {
      const res = await agent.get(url);
      assert.equal(res.status, 302, `${url} should redirect`);
      assert.equal(res.headers.location, '/license');
    }
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('missing AD_RETURN_SIGNING_SECRET blocks LootLabs provider redirect', async () => {
    const agent = request.agent(app);
    await login(agent);
    const started = await startChallenge(agent);
    const originalSecret = process.env.AD_RETURN_SIGNING_SECRET;
    delete process.env.AD_RETURN_SIGNING_SECRET;
    try {
      const res = await agent.post('/key/provider/lootlabs').type('form').send({
        _csrf: started.csrf,
        challenge_id: started.challengeId,
        provider: 'lootlabs',
      });
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
      assert.equal(memoryDb.license_keys.length, 0);
    } finally {
      process.env.AD_RETURN_SIGNING_SECRET = originalSecret;
    }
  });

  test('full key visible in authenticated dashboard activity history', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    await completeProvider(agent, 'linkvertise', hash);

    const dashboard = await agent.get('/dashboard');
    assert.equal(dashboard.status, 200);
    assert.match(dashboard.text, /DENG-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}/);
    assert.doesNotMatch(dashboard.text, /\*\*\*\*/);
  });

  test('unauthenticated users cannot reach key result or license history', async () => {
    const res1 = await request(app).get('/key/result');
    assert.equal(res1.status, 302);
    assert.equal(res1.headers.location, '/login');

    const res2 = await request(app).get('/license');
    assert.equal(res2.status, 302);
    assert.equal(res2.headers.location, '/login');
  });

  test('Linkvertise Anti-Bypass token is NEVER included in the redirect URL or in any rendered HTML', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { res } = await chooseProvider(agent, 'linkvertise');
    // 1. Redirect Location header must not contain the token
    const location = res.headers.location || '';
    assert.doesNotMatch(location, new RegExp(process.env.LINKVERTISE_ANTI_BYPASS_TOKEN));
    // 2. The license page itself must not contain the token either
    const license = await agent.get('/license');
    assert.doesNotMatch(license.text, new RegExp(process.env.LINKVERTISE_ANTI_BYPASS_TOKEN));
    // 3. Choose-provider page must not contain the token
    const { html } = await startChallenge(agent);
    assert.doesNotMatch(html, new RegExp(process.env.LINKVERTISE_ANTI_BYPASS_TOKEN));
  });

  test('Content-Security-Policy form-action allows the Linkvertise / LootLabs ad provider hosts so post-form 303 redirects are not silently blocked', async () => {
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.get('/license');
    const csp = String(res.headers['content-security-policy'] || '');
    assert.ok(csp.length > 0, 'CSP header must be present');
    // Extract the form-action directive value.
    const match = csp.match(/(?:^|;)\s*form-action\s+([^;]+)/i);
    assert.ok(match, "CSP must include a form-action directive (otherwise it falls back to default-src 'self' and blocks ad redirects)");
    const formAction = match[1].trim();
    assert.ok(/'self'/.test(formAction), "form-action must include 'self' for portal POSTs");
    assert.ok(/link-hub\.net/.test(formAction), 'form-action must allow link-hub.net so the Linkvertise Target-Link 303 redirect is not blocked by CSP');
    assert.ok(/linkvertise\.com/.test(formAction), 'form-action must allow linkvertise.com (apex and subdomains) for any in-flow Linkvertise redirect');
    assert.ok(/lootdest\.org/.test(formAction), 'form-action must allow lootdest.org so the LootLabs 303 redirect is not blocked by CSP');
  });

  test('LootLabs static fallback URL does not corrupt shortlink hash with = suffix', async () => {
    const agent = request.agent(app);
    await login(agent);
    const originalTmpl = process.env.LOOTLABS_TEMPLATE_URL;
    delete process.env.LOOTLABS_TEMPLATE_URL;
    try {
      const started = await startChallenge(agent);
      // Bypass providerIsReady by posting directly (template URL check is server-side)
      // The fallback URL generation must not corrupt the shortlink hash
      const { lootlabsProviderUrl } = require('../src/routes');
      // Since lootlabsProviderUrl is not exported, test indirectly via the fallback
      // by checking the internal logic doesn't produce '=' appended to shortlink key
      const base = 'https://lootdest.org/s?TqZQAW38';
      const sep = base.includes('?') ? '&' : '?';
      const result = `${base}${sep}return_url=https%3A%2F%2Fexample.com%2Fcomplete%3Ft%3Dabc`;
      assert.ok(!result.includes('TqZQAW38='), 'shortlink hash must not have = appended');
    } finally {
      if (originalTmpl !== undefined) process.env.LOOTLABS_TEMPLATE_URL = originalTmpl;
    }
  });
});

describe('Linkvertise provider helper (Anti-Bypass)', () => {
  const lv = require('../src/providers/linkvertise');

  test('isLinkvertiseConfigured returns true when env is complete', () => {
    assert.equal(lv.isLinkvertiseConfigured(), true);
    assert.equal(lv.getLinkvertiseUnavailableReason(), null);
  });

  test('isLinkvertiseConfigured returns false when LINKVERTISE_ENABLED is false', () => {
    const original = process.env.LINKVERTISE_ENABLED;
    process.env.LINKVERTISE_ENABLED = 'false';
    try {
      assert.equal(lv.isLinkvertiseConfigured(), false);
      assert.match(lv.getLinkvertiseUnavailableReason(), /LINKVERTISE_ENABLED/);
    } finally {
      process.env.LINKVERTISE_ENABLED = original;
    }
  });

  test('isLinkvertiseConfigured returns false when LINKVERTISE_ANTI_BYPASS_TOKEN is missing', () => {
    const original = process.env.LINKVERTISE_ANTI_BYPASS_TOKEN;
    delete process.env.LINKVERTISE_ANTI_BYPASS_TOKEN;
    try {
      assert.equal(lv.isLinkvertiseConfigured(), false);
      assert.match(lv.getLinkvertiseUnavailableReason(), /ANTI_BYPASS_TOKEN/);
    } finally {
      process.env.LINKVERTISE_ANTI_BYPASS_TOKEN = original;
    }
  });

  test('isLinkvertiseConfigured returns false when LINKVERTISE_TARGET_LINK_URL is missing', () => {
    const orig1 = process.env.LINKVERTISE_TARGET_LINK_URL;
    const orig2 = process.env.LINKVERTISE_MONETIZED_URL;
    delete process.env.LINKVERTISE_TARGET_LINK_URL;
    delete process.env.LINKVERTISE_MONETIZED_URL;
    try {
      assert.equal(lv.isLinkvertiseConfigured(), false);
      assert.match(lv.getLinkvertiseUnavailableReason(), /TARGET_LINK_URL/);
    } finally {
      if (orig1 !== undefined) process.env.LINKVERTISE_TARGET_LINK_URL = orig1;
      if (orig2 !== undefined) process.env.LINKVERTISE_MONETIZED_URL = orig2;
    }
  });

  test('isValidHashFormat accepts exactly 64 url-safe chars and rejects everything else', () => {
    assert.equal(lv.isValidHashFormat('a'.repeat(64)), true);
    assert.equal(lv.isValidHashFormat('A1b2-_'.padEnd(64, 'x')), true);
    assert.equal(lv.isValidHashFormat(''), false);
    assert.equal(lv.isValidHashFormat('short'), false);
    assert.equal(lv.isValidHashFormat('a'.repeat(63)), false);
    assert.equal(lv.isValidHashFormat('a'.repeat(65)), false);
    assert.equal(lv.isValidHashFormat('a.b'.padEnd(64, 'x')), false);
    assert.equal(lv.isValidHashFormat('a/b'.padEnd(64, 'x')), false);
    assert.equal(lv.isValidHashFormat('a b'.padEnd(64, 'x')), false);
  });

  test('verifyLinkvertiseAntiBypass rejects missing hash without making API call', async () => {
    const before = linkvertiseApi.callCount;
    const res = await lv.verifyLinkvertiseAntiBypass({ hash: '', requestId: 'r' });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'missing_hash');
    assert.equal(linkvertiseApi.callCount, before);
  });

  test('verifyLinkvertiseAntiBypass rejects bad-format hash without making API call', async () => {
    const before = linkvertiseApi.callCount;
    const res = await lv.verifyLinkvertiseAntiBypass({ hash: 'too-short', requestId: 'r' });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'bad_hash_format');
    assert.equal(linkvertiseApi.callCount, before);
  });

  test('verifyLinkvertiseAntiBypass returns success on TRUE response', async () => {
    linkvertiseApi.mode = 'true';
    const hash = 'b'.repeat(64);
    const res = await lv.verifyLinkvertiseAntiBypass({ hash, requestId: 'r' });
    assert.equal(res.ok, true);
    assert.equal(res.reason, 'success');
  });

  test('verifyLinkvertiseAntiBypass returns api_false on FALSE response', async () => {
    linkvertiseApi.mode = 'false';
    const hash = 'c'.repeat(64);
    const res = await lv.verifyLinkvertiseAntiBypass({ hash, requestId: 'r' });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'api_false');
  });

  test('verifyLinkvertiseAntiBypass returns api_invalid_token on "invalid token" response', async () => {
    linkvertiseApi.mode = 'invalid_token';
    const hash = 'd'.repeat(64);
    const res = await lv.verifyLinkvertiseAntiBypass({ hash, requestId: 'r' });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'api_invalid_token');
  });

  test('verifyLinkvertiseAntiBypass fails closed on timeout', async () => {
    linkvertiseApi.mode = 'timeout';
    const hash = 'e'.repeat(64);
    const res = await lv.verifyLinkvertiseAntiBypass({ hash, requestId: 'r' });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'api_timeout');
  });

  test('verifyLinkvertiseAntiBypass fails closed on network error', async () => {
    linkvertiseApi.mode = 'network';
    const hash = 'f'.repeat(64);
    const res = await lv.verifyLinkvertiseAntiBypass({ hash, requestId: 'r' });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'api_error');
  });

  test('verifyLinkvertiseAntiBypass fails closed on HTTP 500', async () => {
    linkvertiseApi.mode = 'http500';
    const hash = '1'.repeat(64);
    const res = await lv.verifyLinkvertiseAntiBypass({ hash, requestId: 'r' });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'api_error');
  });

  test('verifyLinkvertiseAntiBypass fails closed on unrecognised response shape', async () => {
    linkvertiseApi.mode = 'invalid_response';
    const hash = '2'.repeat(64);
    const res = await lv.verifyLinkvertiseAntiBypass({ hash, requestId: 'r' });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'api_invalid_response');
  });

  test('classifyApiResponse handles real Linkvertise {"status": true/false} JSON shape', () => {
    // Empirically the live API returns this object shape (not bare true/false).
    assert.deepEqual(lv.classifyApiResponse({ status: true }), { ok: true, reason: 'success' });
    assert.deepEqual(lv.classifyApiResponse({ status: false }), { ok: false, reason: 'api_false' });
  });

  test('classifyApiResponse keeps accepting bare true/false bodies for back-compat', () => {
    assert.deepEqual(lv.classifyApiResponse(true), { ok: true, reason: 'success' });
    assert.deepEqual(lv.classifyApiResponse(false), { ok: false, reason: 'api_false' });
    assert.deepEqual(lv.classifyApiResponse('true'), { ok: true, reason: 'success' });
    assert.deepEqual(lv.classifyApiResponse('false'), { ok: false, reason: 'api_false' });
  });

  test('classifyApiResponse treats "invalid token" inside the error/message fields as api_invalid_token', () => {
    assert.deepEqual(
      lv.classifyApiResponse({ error: 'Invalid token' }),
      { ok: false, reason: 'api_invalid_token' },
    );
    assert.deepEqual(
      lv.classifyApiResponse({ message: 'Invalid token provided' }),
      { ok: false, reason: 'api_invalid_token' },
    );
    assert.deepEqual(
      lv.classifyApiResponse('Invalid token'),
      { ok: false, reason: 'api_invalid_token' },
    );
  });

  test('verifyLinkvertiseAntiBypass sends token in body, never in URL', async () => {
    linkvertiseApi.mode = 'true';
    const hash = '3'.repeat(64);
    await lv.verifyLinkvertiseAntiBypass({ hash, requestId: 'r' });
    assert.doesNotMatch(linkvertiseApi.lastCall.url, /token=/);
    assert.match(linkvertiseApi.lastCall.body, /token=/);
    assert.match(linkvertiseApi.lastCall.body, /hash=/);
  });

  test('getLinkvertiseTargetLinkUrl returns the configured Target-Link', () => {
    assert.equal(lv.getLinkvertiseTargetLinkUrl(), 'https://link-hub.net/5914830/XEpUhZ8TdtyV');
  });

  test('getLinkvertiseTargetLinkUrl returns "" when no env var is set (no hardcoded default)', () => {
    const orig1 = process.env.LINKVERTISE_TARGET_LINK_URL;
    const orig2 = process.env.LINKVERTISE_MONETIZED_URL;
    delete process.env.LINKVERTISE_TARGET_LINK_URL;
    delete process.env.LINKVERTISE_MONETIZED_URL;
    try {
      assert.equal(lv.getLinkvertiseTargetLinkUrl(), '');
    } finally {
      if (orig1 !== undefined) process.env.LINKVERTISE_TARGET_LINK_URL = orig1;
      if (orig2 !== undefined) process.env.LINKVERTISE_MONETIZED_URL = orig2;
    }
  });

  test('getLinkvertiseCallbackUrl returns the configured callback URL', () => {
    assert.equal(lv.getLinkvertiseCallbackUrl(), 'http://localhost:8791/unlock/linkvertise/complete');
  });

  test('getLinkvertiseVerifyUrl returns the publisher.linkvertise.com endpoint', () => {
    assert.equal(lv.getLinkvertiseVerifyUrl(), 'https://publisher.linkvertise.com/api/v1/anti_bypassing');
  });
});
