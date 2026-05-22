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
process.env.LINKVERTISE_COMPLETE_URL = 'http://localhost:8791/unlock/linkvertise/complete';
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

// fakeAxios is declared as a plain object so individual tests can temporarily
// override .get() to exercise different Discord identity responses.
const fakeAxios = {
  async post() {
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
  const locationUrl = new URL(location, basePublicUrl);

  let returnToken;
  let returnUrl;

  if (provider === 'linkvertise' && location.includes('/unlock/linkvertise/start')) {
    // Full Script approach: token is directly in the internal start URL
    returnToken = locationUrl.searchParams.get('t');
    assert.ok(returnToken, 'Linkvertise start URL must include return token directly');
    returnUrl = `${basePublicUrl}/unlock/linkvertise/complete?t=${encodeURIComponent(returnToken)}`;
  } else {
    // LootLabs template or generic: token nested in destination/return_url param
    const destParam =
      locationUrl.searchParams.get('return_url') ||
      locationUrl.searchParams.get('deng_return') ||
      locationUrl.searchParams.get('destination') ||
      locationUrl.searchParams.get('target') ||
      locationUrl.searchParams.get('url');
    assert.ok(destParam, 'provider redirect must include signed return URL');
    returnUrl = destParam;
    returnToken = new URL(returnUrl).searchParams.get('t');
  }

  assert.ok(returnToken, 'signed return token must be present');
  assert.ok(returnToken.length > 80, 'return token must be long enough to be a valid HMAC token');
  return { started, res, location, returnUrl, returnToken };
}

function ageProviderStart(seconds = AD_MIN_COMPLETION_SECONDS + 1, index = 0) {
  const row = memoryDb.license_ad_challenges[index];
  assert.ok(row, 'challenge row must exist before aging provider start');
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
  const suffix = returnToken ? `?t=${encodeURIComponent(returnToken)}` : '';
  return agent.get(`/unlock/${provider}/complete${suffix}`).set('Referer', referer);
}

function tamperToken(token) {
  return `${token.slice(0, -1)}${token.endsWith('a') ? 'b' : 'a'}`;
}

beforeEach(resetDb);

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

  test('expired active provider challenge fails safely', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'linkvertise');
    memoryDb.license_ad_challenges[0].expires_at = new Date(Date.now() - 1000).toISOString();
    ageProviderStart();
    const res = await completeProvider(agent, 'linkvertise', returnToken);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('wrong user challenge ownership fails safely', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'linkvertise');
    memoryDb.license_ad_challenges[0].site_user_id = randomUUID();
    ageProviderStart();
    const res = await completeProvider(agent, 'linkvertise', returnToken);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('Linkvertise Full Script provider redirects to internal start page with signed token', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { res, location, returnToken } = await chooseProvider(agent, 'linkvertise');
    assert.equal(res.status, 303);
    // Linkvertise Full Script: must redirect to internal /unlock/linkvertise/start, NOT to link-hub.net
    assert.match(location, /\/unlock\/linkvertise\/start\?t=/);
    assert.ok(!location.includes('link-hub.net'), 'must NOT redirect directly to static link-hub.net campaign URL');
    assert.ok(returnToken.length > 80);
    assert.doesNotMatch(res.headers['content-type'] || '', /json/i);
    assert.equal(memoryDb.license_ad_challenges[0].provider, 'linkvertise');
    assert.equal(memoryDb.license_ad_challenges[0].status, 'pending_ad');
    assert.equal(memoryDb.license_ad_challenges[0].provider_payload.redirect_started, true);
    assert.ok(memoryDb.license_ad_challenges[0].provider_payload.provider_started_at);
    assert.equal(memoryDb.license_ad_challenges[0].provider_payload.return_token_hash.length, 64);
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
    // Linkvertise Full Script: first click goes to internal start page, not static link-hub.net
    assert.match(first.location, /\/unlock\/linkvertise\/start\?t=/);
    assert.notEqual(first.location, '/license');

    // Repeated same-provider click: must still 303 (safe reissue)
    const second = await agent.post('/key/provider/linkvertise').type('form').send({
      _csrf: first.started.csrf,
      challenge_id: first.started.challengeId,
      provider: 'linkvertise',
    });
    assert.equal(second.status, 303);
    assert.match(second.headers.location, /\/unlock\/linkvertise\/start\?t=/);
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

  test('missing AD_RETURN_SIGNING_SECRET fails provider redirect closed', async () => {
    const agent = request.agent(app);
    await login(agent);
    const started = await startChallenge(agent);
    const originalSecret = process.env.AD_RETURN_SIGNING_SECRET;
    delete process.env.AD_RETURN_SIGNING_SECRET;
    try {
      const res = await agent.post('/key/provider/linkvertise').type('form').send({
        _csrf: started.csrf,
        challenge_id: started.challengeId,
        provider: 'linkvertise',
      });
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
      assert.equal(memoryDb.license_keys.length, 0);
    } finally {
      process.env.AD_RETURN_SIGNING_SECRET = originalSecret;
    }
  });

  test('Linkvertise Full Script start page renders publisher JS and signed completion link', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { location, returnToken } = await chooseProvider(agent, 'linkvertise');
    // Follow the internal redirect to the start page using a path-only URL
    // (location is absolute http://localhost:8791/... but test server is on a random port)
    const startPath = new URL(location).pathname + new URL(location).search;
    const startPage = await agent.get(startPath);
    assert.equal(startPage.status, 200);
    assert.match(startPage.text, /publisher\.linkvertise\.com\/cdn\/linkvertise\.js/);
    assert.match(startPage.text, /linkvertise_publisher_id\s*=\s*5914830/);
    // The link on the start page must contain the signed token
    assert.match(startPage.text, /unlock\/linkvertise\/complete\?t=/);
    assert.ok(startPage.text.includes(encodeURIComponent(returnToken)) || startPage.text.includes(returnToken),
      'start page must contain the return token in the link href');
    assert.doesNotMatch(startPage.text, /DENG-[0-9A-F]{4}/);
  });

  test('Linkvertise Full Script start page with invalid or missing token redirects to license', async () => {
    const agent = request.agent(app);
    await login(agent);
    for (const bad of ['', 'fake.token', 'abc123']) {
      const suffix = bad ? `?t=${encodeURIComponent(bad)}` : '';
      const res = await agent.get(`/unlock/linkvertise/start${suffix}`);
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
    }
  });

  test('LootLabs template URL fallback uses static URL with return params when template not set', async () => {
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
      assert.equal(res.status, 303);
      // Fallback: static URL with return_url/deng_return appended using string concat
      assert.ok(res.headers.location.includes('lootdest.org'), `expected fallback to lootdest.org, got: ${res.headers.location}`);
      assert.ok(res.headers.location.includes('return_url=') || res.headers.location.includes('deng_return='));
      // Critical: the shortlink hash must NOT be corrupted by URL searchParams normalization.
      // URL API would turn "?TqZQAW38" into "?TqZQAW38=" which breaks LootDest lookup.
      assert.ok(
        !res.headers.location.includes('TqZQAW38='),
        `shortlink hash must not have '=' appended by URL API normalization, got: ${res.headers.location}`,
      );
    } finally {
      process.env.LOOTLABS_TEMPLATE_URL = originalTmpl;
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

  test('Linkvertise completion succeeds without provider referer (Linkvertise does not forward Referer)', async () => {
    // Linkvertise Full Script does not forward the Referer header when it
    // redirects back to the completion URL. The signed HMAC token + session
    // binding + time check provide equivalent protection.
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'linkvertise');
    ageProviderStart();

    // No Referer header — simulates Linkvertise behaviour
    const res = await agent
      .get(`/unlock/linkvertise/complete?t=${encodeURIComponent(returnToken)}`)
      .set('Accept', 'text/html');
    assert.equal(res.status, 302, 'Linkvertise completion without referer should succeed');
    assert.equal(res.headers.location, '/key/result');
    assert.equal(memoryDb.license_keys.length, 1);
  });

  test('Linkvertise completion with wrong referer is still rejected', async () => {
    // A non-empty, non-Linkvertise referer is not exempt — it indicates spoofing.
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'linkvertise');
    ageProviderStart();

    const res = await completeProvider(agent, 'linkvertise', returnToken, 'https://evil.example.com/');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('manual complete URLs without signed token are blocked even with a pending challenge', async () => {
    const agent = request.agent(app);
    await login(agent);
    await chooseProvider(agent, 'linkvertise');
    ageProviderStart();

    const res = await completeProvider(agent, 'linkvertise', '');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('fake and tampered signed return tokens are blocked', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'linkvertise');
    ageProviderStart();

    const fake = await completeProvider(agent, 'linkvertise', 'fake.token');
    assert.equal(fake.status, 302);
    assert.equal(memoryDb.license_keys.length, 0);

    const tampered = await completeProvider(agent, 'linkvertise', tamperToken(returnToken));
    assert.equal(tampered.status, 302);
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('expired signed return token is blocked', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'linkvertise');
    ageProviderStart();
    memoryDb.license_ad_challenges[0].provider_payload.return_token_expires_at =
      new Date(Date.now() - 1000).toISOString();

    const res = await completeProvider(agent, 'linkvertise', returnToken);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('return token for a different challenge or user is blocked', async () => {
    const agent = request.agent(app);
    await login(agent);
    const first = await chooseProvider(agent, 'linkvertise');
    ageProviderStart();
    const second = await chooseProvider(agent, 'linkvertise');
    ageProviderStart(AD_MIN_COMPLETION_SECONDS + 1, 1);

    const wrongChallenge = await completeProvider(agent, 'linkvertise', first.returnToken);
    assert.equal(wrongChallenge.status, 302);
    assert.equal(memoryDb.license_keys.length, 0);

    const other = request.agent(app);
    const originalGet = fakeAxios.get;
    fakeAxios.get = async () => ({
      data: { id: 'discord-user-2', username: 'OtherUser', avatar: null, email: null },
    });
    try {
      await login(other);
      const otherStart = await chooseProvider(other, 'linkvertise');
      ageProviderStart(AD_MIN_COMPLETION_SECONDS + 1, 2);
      const wrongUser = await completeProvider(other, 'linkvertise', second.returnToken);
      assert.equal(wrongUser.status, 302);
      assert.equal(memoryDb.license_keys.length, 0);

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

  test('wrong provider complete route is blocked even with allowed referer', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'linkvertise');
    ageProviderStart();

    const res = await completeProvider(agent, 'lootlabs', returnToken);
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
    const rendered = await agent.get('/license');
    assert.match(rendered.text, /Invalid or expired key generation session\. Please start again\./);
  });

  test('provider complete URL with wrong referer host is blocked', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'linkvertise');
    ageProviderStart();

    const res = await completeProvider(agent, 'linkvertise', returnToken, 'https://tool.deng.my.id/license');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('valid unlock generates one key, keeps it out of the URL, and shows redeem instructions', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'linkvertise');
    ageProviderStart();

    const unlock = await completeProvider(agent, 'linkvertise', returnToken);
    assert.equal(unlock.status, 302);
    assert.equal(unlock.headers.location, '/key/result');
    assert.equal(memoryDb.license_keys.length, 1);

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

  test('tampered and expired challenges are rejected', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'linkvertise');
    ageProviderStart();

    const tampered = await completeProvider(agent, 'lootlabs', returnToken);
    assert.equal(tampered.status, 302);
    assert.equal(memoryDb.license_keys.length, 0);

    const expiredToken = signChallenge('missing', 'linkvertise', Date.now() - 1000);
    const expired = await agent.get(`/unlock/linkvertise?challenge=${encodeURIComponent(expiredToken)}`);
    assert.equal(expired.status, 302);
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('callback replay and double-submit do not create duplicate keys', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'linkvertise');
    ageProviderStart();

    await completeProvider(agent, 'linkvertise', returnToken);
    await completeProvider(agent, 'linkvertise', returnToken);
    assert.equal(memoryDb.license_keys.length, 1);
  });

  test('server-side cooldown is enforced after a generated key', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'linkvertise');
    ageProviderStart();
    await completeProvider(agent, 'linkvertise', returnToken);

    const page = await agent.get('/license');
    const csrf = csrfFrom(page.text);
    const blocked = await agent.post('/api/key/start').type('form').send({ _csrf: csrf });
    assert.equal(blocked.status, 302);
    assert.equal(blocked.headers.location, '/license');
  });

  test('history masks older keys but result page can show the freshly generated key', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken } = await chooseProvider(agent, 'linkvertise');
    ageProviderStart();
    await completeProvider(agent, 'linkvertise', returnToken);
    const license = await agent.get('/license');
    assert.match(license.text, /\*\*\*\*/);
    assert.match(license.text, /Linkvertise/);
    assert.match(license.text, /Generated/);
    assert.doesNotMatch(license.text, />linkvertise</);
    assert.doesNotMatch(license.text, /pending_ad/);
    assert.doesNotMatch(license.text, /DENG-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}/);
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
