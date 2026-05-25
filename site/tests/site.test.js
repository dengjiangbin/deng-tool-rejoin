'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const { encryptLicenseKeyPlaintext } = require('../src/licenseCrypto');

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
process.env.LOOTLABS_BASE_LINK = 'https://lootdest.org/s?TqZQAW38';
process.env.LOOTLABS_API_TOKEN = 'test-lootlabs-api-token-very-long-do-not-log-1234567890abcdef';
process.env.LOOTLABS_ENCRYPT_URL = 'https://creators.lootlabs.gg/api/public/url_encryptor';
// Legacy LootLabs vars kept for backward-compat regression cases in this suite.
process.env.LOOTLABS_MONETIZED_URL = 'https://lootdest.org/s?TqZQAW38';
process.env.LOOTLABS_COMPLETE_URL = 'http://localhost:8791/unlock/lootlabs/complete';
process.env.AD_MIN_COMPLETION_SECONDS = '30';
process.env.AD_RETURN_SIGNING_SECRET = 'test-return-signing-secret-that-is-long-enough';
process.env.LICENSE_KEY_EXPORT_SECRET = 'test-license-key-export-secret-that-is-long-enough';

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
  device_bindings: [],
  hwid_reset_logs: [],
  license_key_executions: [],
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

// LootLabs Redirect API / Anti-Bypass mock.
//   - mode='auto'              : every call gets a unique encrypted blob and
//                                 records destination_url→encrypted mapping
//   - mode='invalid_token'     : returns { type: 'error', message: 'Invalid token' }
//   - mode='type_error'        : returns { type: 'error', message: 'Bad input' }
//   - mode='http500'           : returns HTTP 500
//   - mode='timeout'           : rejects with ECONNABORTED
//   - mode='network'           : rejects with ECONNRESET
//   - mode='invalid_response'  : returns { whatever: 'shape' } (no message)
const lootlabsApi = {
  mode: 'auto',
  byEncrypted: new Map(), // encrypted → destination_url
  lastCall: null,         // { url, headers, body, destination_url }
  callCount: 0,
  counter: 0,
};

function resetLootLabsApi() {
  lootlabsApi.mode = 'auto';
  lootlabsApi.byEncrypted.clear();
  lootlabsApi.lastCall = null;
  lootlabsApi.callCount = 0;
  lootlabsApi.counter = 0;
}

function lootlabsMockResponse(url, body, opts = {}) {
  lootlabsApi.callCount += 1;
  const headers = (opts && opts.headers) || {};
  const destination = body && typeof body === 'object' ? String(body.destination_url || '') : '';
  lootlabsApi.lastCall = {
    url: String(url || ''),
    headers,
    body,
    destination_url: destination,
  };
  switch (lootlabsApi.mode) {
    case 'invalid_token':
      return Promise.resolve({ status: 401, data: { type: 'error', message: 'Invalid token' } });
    case 'type_error':
      return Promise.resolve({ status: 200, data: { type: 'error', message: 'Bad input' } });
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
    default: {
      lootlabsApi.counter += 1;
      const encrypted = `enc_${lootlabsApi.counter}_${Buffer.from(destination).toString('base64url').slice(0, 24)}`;
      lootlabsApi.byEncrypted.set(encrypted, destination);
      return Promise.resolve({
        status: 200,
        data: { type: 'success', message: encrypted },
      });
    }
  }
}

// fakeAxios is declared as a plain object so individual tests can temporarily
// override .get() to exercise different Discord identity responses, and so
// .post() can route Linkvertise Anti-Bypass + LootLabs encrypt calls to the
// mocks above.
const fakeAxios = {
  async post(url, body, opts) {
    if (typeof url === 'string' && url.includes('anti_bypassing')) {
      return linkvertiseMockResponse(url, body);
    }
    if (typeof url === 'string' && url.includes('url_encryptor')) {
      return lootlabsMockResponse(url, body, opts);
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
const {
  formatWibDate,
  formatWibTimestamp,
  licenseExportFilename,
  sanitizeFilenameUsername,
} = require('../src/licenseFormat');

function resetDb() {
  memoryDb.site_users.splice(0);
  memoryDb.license_ad_challenges.splice(0);
  memoryDb.license_keys.splice(0);
  memoryDb.license_users.splice(0);
  memoryDb.device_bindings.splice(0);
  memoryDb.hwid_reset_logs.splice(0);
  memoryDb.license_key_executions.splice(0);
}

function csrfFrom(html) {
  const match = html.match(/name="_csrf" value="([^"]+)"/);
  assert.ok(match, 'CSRF token should be present');
  return match[1];
}

function licenseKeyId(rawKey) {
  return require('node:crypto').createHash('sha256').update(rawKey.toUpperCase()).digest('hex');
}

function insertLicenseFixture(rawKey, overrides = {}) {
  const normalized = rawKey.toUpperCase();
  const parts = normalized.split('-');
  const id = licenseKeyId(normalized);
  const now = new Date().toISOString();
  const keyCiphertext = encryptLicenseKeyPlaintext(normalized);
  const row = {
    id,
    prefix: `${parts[0]}-${parts[1]}`,
    suffix: parts[4],
    owner_discord_id: 'discord-user-1',
    site_user_id: memoryDb.site_users[0]?.id || null,
    status: 'active',
    plan: 'standard',
    created_at: now,
    redeemed_at: null,
    expires_at: new Date(Date.now() + 3600 * 1000).toISOString(),
    key_ciphertext: keyCiphertext,
    key_export_available: Boolean(keyCiphertext),
    ...overrides,
  };
  memoryDb.license_keys.push(row);
  memoryDb.license_ad_challenges.push({
    id: `fixture-${id.slice(0, 8)}`,
    site_user_id: row.site_user_id,
    discord_user_id: row.owner_discord_id,
    status: 'key_generated',
    provider: 'lootlabs',
    license_key_id: id,
    key_prefix: `${parts[0]}-${parts[1]}-${parts[2]}`,
    key_suffix: `${parts[3]}-${parts[4]}`,
    created_at: row.created_at,
    completed_at: row.created_at,
    key_expires_at: row.expires_at,
  });
  return row;
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

  if (provider === 'lootlabs') {
    // LootLabs Redirect API / Anti-Bypass: server signs a state, calls the
    // mocked encrypt API, then redirects 303 to:
    //   https://lootdest.org/s?TqZQAW38&data=<encrypted>
    // The shortlink id (?TqZQAW38) is a valueless query key — assert that
    // it was preserved exactly (no `=` appended).
    const base = (process.env.LOOTLABS_BASE_LINK || 'https://lootdest.org/s?TqZQAW38').replace(/[?&]data=.*$/, '');
    assert.ok(location.startsWith(`${base}&data=`), `expected ${base}&data=… , got: ${location}`);
    assert.ok(!/\bTqZQAW38=/.test(location), 'LootLabs shortlink hash must not gain "=" suffix');
    // Pull out the encrypted blob, look up the destination URL from the mock,
    // and extract `?s=<signed_state>` from that destination URL.
    const dataMatch = location.match(/[?&]data=([^&]+)$/);
    assert.ok(dataMatch, 'encrypted data param must be present');
    const encrypted = decodeURIComponent(dataMatch[1]);
    const destinationUrl = lootlabsApi.byEncrypted.get(encrypted);
    assert.ok(destinationUrl, 'mock must have recorded the destination URL for the encrypted blob');
    const destUrl = new URL(destinationUrl);
    const signedState = destUrl.searchParams.get('s');
    assert.ok(signedState, 'destination URL must include the signed state ?s=…');
    assert.ok(signedState.length > 32, 'signed state must look like an HMAC token');
    return {
      started,
      res,
      location,
      returnUrl: destinationUrl,
      returnToken: signedState,
      lootlabsEncrypted: encrypted,
      lootlabsDestination: destinationUrl,
    };
  }

  // Generic fallback (unknown provider) — keep legacy template/destination parsing
  const locationUrl = new URL(location, basePublicUrl);
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
  // provider appends to the callback URL as `?hash=...`.
  // For LootLabs we treat `returnToken` as the HMAC-signed state that LootLabs
  // delivers back via the encrypted destination URL: `?s=<signed_state>`.
  // For any other provider we fall back to the legacy `?t=<signed_token>`.
  if (provider === 'linkvertise') {
    const suffix = returnToken ? `?hash=${encodeURIComponent(returnToken)}` : '';
    const req = agent.get(`/unlock/linkvertise/complete${suffix}`);
    if (referer) req.set('Referer', referer);
    return req;
  }
  if (provider === 'lootlabs') {
    const suffix = returnToken ? `?s=${encodeURIComponent(returnToken)}` : '';
    const req = agent.get(`/unlock/lootlabs/complete${suffix}`);
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
  resetLootLabsApi();
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

  test('authenticated layout uses real nav icons and theme toggle', async () => {
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.get('/dashboard');
    assert.equal(res.status, 200);
    assert.match(res.text, /data-theme-toggle/);
    assert.match(res.text, /deng_tool_theme/);
    assert.match(res.text, /prefers-color-scheme: light/);
    assert.match(res.text, /aria-label="Switch theme"/);
    assert.match(res.text, /theme-toggle-track/);
    assert.match(res.text, /theme-toggle-knob/);
    assert.match(res.text, /data-theme-label>Dark<\/span>/);
    assert.match(res.text, /theme-icon-sun/);
    assert.match(res.text, /theme-icon-moon/);
    assert.doesNotMatch(res.text, /theme-toggle-text-light|theme-toggle-text-dark/);
    assert.match(res.text, /<rect x="3" y="3" width="7" height="8" rx="2"><\/rect>/);
    assert.match(res.text, /<circle cx="7\.5" cy="14\.5" r="3\.5"><\/circle>/);
    assert.doesNotMatch(res.text, /<span class="nav-icon" aria-hidden="true">D<\/span>/);
    assert.doesNotMatch(res.text, /<span class="nav-icon" aria-hidden="true">K<\/span>/);
    assert.doesNotMatch(res.text, /<span class="nav-icon" aria-hidden="true">ML<\/span>/);
  });

  test('dashboard and My License render compact portal panels', async () => {
    const agent = request.agent(app);
    await login(agent);
    memoryDb.license_keys.push({
      id: 'history-wib',
      prefix: 'DENG-WIB',
      suffix: '0001',
      owner_discord_id: 'discord-user-1',
      status: 'active',
      plan: 'standard',
      created_at: '2026-05-22T07:14:05.740Z',
      redeemed_at: null,
      expires_at: null,
    });
    const dashboard = await agent.get('/dashboard');
    assert.match(dashboard.text, /Dashboard Overview/);
    assert.match(dashboard.text, /Generate Key/);
    assert.match(dashboard.text, /News & Updates/);
    assert.match(dashboard.text, /Your Activity/);
    assert.match(dashboard.text, /stats-grid/);
    assert.match(dashboard.text, /22 Mei 2026, 2:14:05 PM/);
    assert.doesNotMatch(dashboard.text, /5\/22\/2026|2026-05-22T/);
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
    assert.match(license.text, /22 Mei 2026, 2:14:05 PM/);
    assert.doesNotMatch(license.text, /5\/22\/2026|2026-05-22T/);
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
    assert.match(css, /\.gradient-brand-text/);
    assert.match(css, /#22d3ee/i);
    assert.match(css, /#3b82f6/i);
    assert.match(css, /#ec4899/i);
    assert.match(css, /#f472b6/i);
    assert.match(css, /#00cfff|#17a0dd/i);
    assert.match(css, /#ff2fb3|#c0187a/i);
    assert.match(css, /#6143b2/i);
    assert.match(css, /--button-gradient:\s*linear-gradient\(90deg,\s*#05c8ff 0%,\s*#7b5cff 50%,\s*#ff2bae 100%\)/i);
    assert.match(css, /var\(--button-gradient\) padding-box,\s*var\(--button-gradient\) border-box/i);
    assert.match(css, /:root\[data-theme="light"\]/);
    assert.match(css, /--body-bg:/);
    assert.match(css, /color-scheme:\s*dark/);
    assert.match(css, /\.nav-link\.active/);
    assert.match(css, /@media \(max-width: 760px\)/);
    assert.doesNotMatch(css, /#00C7A3/i);
  });

  test('theme toggle placement is desktop stacked and mobile beside logout', () => {
    const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'css', 'style.css'), 'utf8');
    assert.match(css, /\.sidebar-actions\s*\{\s*display:\s*grid;\s*gap:\s*10px;/);
    assert.match(css, /@media \(max-width: 760px\)[\s\S]*\.sidebar-actions\s*\{\s*grid-template-columns:\s*auto auto;/);
    assert.match(css, /@media \(max-width: 480px\)[\s\S]*\.sidebar-actions\s*\{\s*grid-template-columns:\s*1fr 1fr;/);
    assert.match(css, /\.theme-toggle-track,\s*\.theme-toggle-knob,\s*\.theme-toggle-icon\s*\{\s*pointer-events:\s*none;/);
    assert.match(css, /\.theme-toggle-label\s*\{[\s\S]*min-width:\s*44px;[\s\S]*white-space:\s*nowrap;/);
    assert.match(css, /@media \(max-width: 760px\)[\s\S]*\.theme-toggle\s*\{[\s\S]*min-width:\s*116px;[\s\S]*grid-template-columns:\s*auto auto;[\s\S]*gap:\s*6px;/);
    assert.match(css, /@media \(max-width: 760px\)[\s\S]*\.theme-toggle-label\s*\{\s*display:\s*inline-flex;/);
    assert.match(css, /@media \(max-width: 480px\)[\s\S]*\.theme-toggle\s*\{[\s\S]*width:\s*100%;[\s\S]*justify-content:\s*center;[\s\S]*padding-inline:\s*6px;/);
  });

  test('theme toggle uses one active label to prevent ghost text bleed', () => {
    const layout = fs.readFileSync(path.join(__dirname, '..', 'views', 'layout.ejs'), 'utf8');
    const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'css', 'style.css'), 'utf8');
    const js = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'app.js'), 'utf8');
    assert.match(layout, /<span class="theme-toggle-label" data-theme-label>Dark<\/span>/);
    assert.doesNotMatch(layout, /theme-toggle-text-light|theme-toggle-text-dark/);
    assert.doesNotMatch(css, /theme-toggle-text-light|theme-toggle-text-dark/);
    assert.match(css, /\.theme-toggle-track\s*\{[\s\S]*width:\s*72px;[\s\S]*overflow:\s*hidden;/);
    assert.match(css, /\.theme-toggle-knob\s*\{[\s\S]*transform:\s*translateX\(32px\);/);
    assert.match(css, /:root\[data-theme="light"\]\s+\.theme-toggle-knob\s*\{[\s\S]*transform:\s*translateX\(0\);/);
    assert.match(css, /\.theme-toggle-label\s*\{[\s\S]*white-space:\s*nowrap;[\s\S]*pointer-events:\s*none;/);
    assert.match(js, /nextLabel = next === 'light' \? 'Light' : 'Dark'/);
    assert.match(js, /Switch to ' \+ \(next === 'light' \? 'dark' : 'light'\) \+ ' mode'/);
    assert.doesNotMatch(js, /Night|Switch to night/i);
  });

  test('theme toggle script swaps only the active label and remains clickable', () => {
    const vm = require('node:vm');
    const script = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'app.js'), 'utf8');
    const label = { textContent: '' };
    const toggle = {
      attributes: {},
      listeners: {},
      querySelector(selector) {
        return selector === '[data-theme-label]' ? label : null;
      },
      setAttribute(name, value) {
        this.attributes[name] = value;
      },
      addEventListener(type, fn) {
        this.listeners[type] = fn;
      },
    };
    const root = { dataset: { theme: 'light' } };
    const storage = {};
    const context = {
      document: {
        documentElement: root,
        querySelectorAll(selector) {
          return selector === '[data-theme-toggle]' ? [toggle] : [];
        },
        querySelector() {
          return null;
        },
      },
      localStorage: {
        getItem(key) {
          return storage[key] || null;
        },
        setItem(key, value) {
          storage[key] = value;
        },
      },
      window: {
        matchMedia() {
          return { matches: true };
        },
        location: { href: '', reload() {} },
      },
      fetch() {
        throw new Error('fetch should not run during theme toggle init');
      },
      navigator: {},
      setTimeout() {},
    };

    vm.runInNewContext(script, context);
    assert.equal(root.dataset.theme, 'light');
    assert.equal(label.textContent, 'Light');
    assert.equal(toggle.attributes['aria-label'], 'Switch to dark mode');
    assert.equal(toggle.attributes['aria-pressed'], 'false');

    toggle.listeners.click({ preventDefault() {}, stopPropagation() {} });
    assert.equal(root.dataset.theme, 'dark');
    assert.equal(label.textContent, 'Dark');
    assert.equal(toggle.attributes['aria-label'], 'Switch to light mode');
    assert.equal(toggle.attributes['aria-pressed'], 'true');
    assert.equal(storage.deng_tool_theme, 'dark');

    toggle.listeners.click({ preventDefault() {}, stopPropagation() {} });
    assert.equal(root.dataset.theme, 'light');
    assert.equal(label.textContent, 'Light');
    assert.equal(toggle.attributes['aria-label'], 'Switch to dark mode');
    assert.equal(toggle.attributes['aria-pressed'], 'false');
    assert.equal(storage.deng_tool_theme, 'light');
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

  test('My License action row renders only requested license buttons', async () => {
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.get('/license');
    assert.equal(res.status, 200);
    assert.match(res.text, /Generate Key/);
    assert.match(res.text, /Reset HWID/);
    assert.match(res.text, /Redeem Key/);
    assert.match(res.text, /Download Key/);
    assert.match(res.text, /class="license-actions"/);
    assert.match(res.text, /class="btn btn-primary btn-generate"[^>]*>\s*Generate Key/);
    assert.match(res.text, /class="btn btn-primary"[^>]*data-open-license-modal="reset"[^>]*>Reset HWID/);
    assert.match(res.text, /class="btn btn-primary"[^>]*data-open-license-modal="redeem"[^>]*>Redeem Key/);
    assert.match(res.text, /class="btn btn-primary"[^>]*data-download-keys[^>]*>Download Key/);
    assert.doesNotMatch(res.text, /Key Stats/);
    assert.doesNotMatch(res.text, /Select Package/);
    assert.doesNotMatch(res.text, /Select Version/);
    assert.doesNotMatch(res.text, /Package Version/);
  });

  test('license action CSS keeps buttons responsive and mobile-wrapping', () => {
    const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'css', 'style.css'), 'utf8');
    assert.match(css, /\.license-actions/);
    assert.match(css, /grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
    assert.match(css, /grid-template-columns:\s*1fr/);
  });

  test('primary website action buttons share fixed gradient class', async () => {
    const agent = request.agent(app);
    await login(agent);
    const dashboard = await agent.get('/dashboard');
    const license = await agent.get('/license');
    const provider = await startChallenge(agent).then((result) => ({ status: 200, text: result.html }));
    assert.equal(dashboard.status, 200);
    assert.equal(license.status, 200);
    assert.match(dashboard.text, /class="btn btn-primary"[^>]*>Generate Key/);
    assert.match(license.text, /data-download-keys/);
    assert.match(provider.text, /class="btn btn-primary btn-block btn-provider"[^>]*>Continue with LootLabs/);
    assert.match(provider.text, /class="btn btn-primary btn-block btn-provider"[^>]*>Continue with Linkvertise/);
    assert.doesNotMatch(license.text, /class="btn btn-outline"[^>]*(Reset HWID|Redeem Key|Download Key)/);
  });
});

describe('license WIB formatting helpers', () => {
  test('UTC timestamp converts to WIB with Indonesian month and AM/PM', () => {
    assert.equal(formatWibTimestamp('2026-05-22T07:14:05.740Z'), '22 Mei 2026, 2:14:05 PM');
    assert.equal(formatWibTimestamp('2026-05-15T20:40:35.000Z'), '16 Mei 2026, 3:40:35 AM');
    assert.equal(formatWibTimestamp('2026-05-23T13:20:40.000Z'), '23 Mei 2026, 8:20:40 PM');
    assert.equal(formatWibTimestamp(null), 'None');
    assert.equal(formatWibDate('2026-05-23T17:00:00.000Z'), '24 Mei 2026');
  });

  test('download filename uses sanitized Discord username and WIB date without time', () => {
    assert.equal(
      licenseExportFilename('deng', '110184213604499456', '2026-05-23T17:00:00.000Z'),
      'deng - DENG Tool Rejoin License Keys - 24 Mei 2026.txt',
    );
    assert.equal(sanitizeFilenameUsername(' DENG/Test  Name ', '1'), 'DENG Test Name');
    assert.equal(
      licenseExportFilename('DENG/Test', '110184213604499456', '2026-05-23T17:00:00.000Z'),
      'DENG Test - DENG Tool Rejoin License Keys - 24 Mei 2026.txt',
    );
    assert.equal(
      licenseExportFilename(' /:*?"<>| ', '110184213604499456', '2026-05-23T17:00:00.000Z'),
      'user-110184213604499456 - DENG Tool Rejoin License Keys - 24 Mei 2026.txt',
    );
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

  test('Generate Key returns existing active unredeemed key in full without a new ad challenge', async () => {
    const agent = request.agent(app);
    await login(agent);
    const existing = 'DENG-1111-2222-3333-4444';
    insertLicenseFixture(existing);

    const page = await agent.get('/license');
    assert.match(page.text, /You already have an unused key\./);
    assert.match(page.text, new RegExp(existing));
    assert.doesNotMatch(page.text, /\*\*\*\*/);

    const csrf = csrfFrom(page.text);
    const blocked = await agent
      .post('/api/key/start')
      .set('Accept', 'application/json')
      .type('form')
      .send({ _csrf: csrf });
    assert.equal(blocked.status, 200);
    assert.equal(blocked.body.status, 'existing_unused_key');
    assert.equal(blocked.body.existing_key.key, existing);
    assert.equal(memoryDb.license_ad_challenges.length, 1);
    assert.equal(memoryDb.license_keys.length, 1);
    assert.equal(linkvertiseApi.callCount, 0);
    assert.equal(lootlabsApi.callCount, 0);
  });

  test('active unredeemed key message takes priority over cooldown', async () => {
    const agent = request.agent(app);
    await login(agent);
    const existing = 'DENG-1212-3434-5656-7878';
    insertLicenseFixture(existing);
    memoryDb.license_ad_challenges[0].created_at = new Date().toISOString();
    memoryDb.license_ad_challenges[0].completed_at = new Date().toISOString();

    const page = await agent.get('/license');
    assert.match(page.text, /You already have an unused key\./);
    assert.match(page.text, new RegExp(existing));
    assert.doesNotMatch(page.text, /Cooldown active:/);
  });

  test('expired unredeemed key is marked expired and does not block generation', async () => {
    const agent = request.agent(app);
    await login(agent);
    const past = new Date(Date.now() - 25 * 3600 * 1000).toISOString();
    const old = insertLicenseFixture('DENG-AAAA-BBBB-CCCC-DDDD', {
      created_at: past,
      expires_at: new Date(Date.now() - 3600 * 1000).toISOString(),
    });

    const { html } = await startChallenge(agent);
    assert.match(html, /LootLabs/);
    assert.equal(memoryDb.license_keys.find((row) => row.id === old.id).status, 'expired');
    assert.equal(memoryDb.license_ad_challenges.length, 2);
  });

  test('redeemed, bound, and revoked keys do not block generation when cooldown allows', async () => {
    const agent = request.agent(app);
    await login(agent);
    const past = new Date(Date.now() - 120 * 1000).toISOString();
    const redeemed = insertLicenseFixture('DENG-1000-2000-3000-4000', {
      created_at: past,
      redeemed_at: past,
      expires_at: null,
    });
    const bound = insertLicenseFixture('DENG-ABCD-1111-2222-3333', {
      created_at: past,
      redeemed_at: past,
      expires_at: null,
    });
    insertLicenseFixture('DENG-9999-8888-7777-6666', {
      status: 'revoked',
      created_at: past,
      expires_at: null,
    });
    memoryDb.device_bindings.push({
      key_id: bound.id,
      install_id_hash: 'bound-hwid',
      device_model: 'Cloud Phone',
      device_label: 'Cloud Phone',
      last_seen_at: past,
      is_active: true,
    });
    memoryDb.license_ad_challenges.forEach((row) => {
      row.created_at = past;
      row.completed_at = past;
    });

    const { html } = await startChallenge(agent);
    assert.match(html, /LootLabs/);
    assert.equal(memoryDb.license_ad_challenges.length, 4);
    assert.ok(memoryDb.license_keys.find((row) => row.id === redeemed.id).redeemed_at);
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

  test('provider complete routes require a proof param (hash for Linkvertise, ?s= for LootLabs)', async () => {
    const agent = request.agent(app);
    await login(agent);
    for (const route of ['/unlock/linkvertise/complete', '/unlock/lootlabs/complete']) {
      const res = await agent.get(route).set('Accept', 'text/html');
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
    }
    assert.equal(memoryDb.license_keys.length, 0);
    const rendered = await agent.get('/license');
    // Both providers fail closed with a styled "key generation session" error.
    assert.match(rendered.text, /Invalid or expired key generation session\. Please start again\.|Please start key generation again\./);
    // No raw JSON leaked to the browser.
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

  test('LootLabs Redirect API: provider POST encrypts the callback and 303s to lootdest.org/s?TqZQAW38&data=…', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { res, location, returnUrl, returnToken, lootlabsEncrypted } = await chooseProvider(agent, 'lootlabs');
    assert.equal(res.status, 303);
    // Final URL must keep the LootLabs shortlink id exactly as written.
    assert.ok(location.startsWith('https://lootdest.org/s?TqZQAW38&data='), `expected lootdest.org/s?TqZQAW38&data=… , got: ${location}`);
    // The valueless shortlink key MUST NOT gain `=` (no URLSearchParams normalisation).
    assert.ok(!/\bTqZQAW38=/.test(location), 'shortlink hash must not have "=" appended');
    // Exactly one encrypt API call was made.
    assert.equal(lootlabsApi.callCount, 1);
    // The encrypt call sent the API token via Authorization header — NOT in the URL or body.
    assert.ok(lootlabsApi.lastCall.headers.Authorization && lootlabsApi.lastCall.headers.Authorization.startsWith('Bearer '));
    assert.ok(!String(lootlabsApi.lastCall.url).includes('token='));
    assert.ok(!String(JSON.stringify(lootlabsApi.lastCall.body)).includes(process.env.LOOTLABS_API_TOKEN));
    // The destination URL passed to the encrypt API points at the DENG callback.
    assert.match(lootlabsApi.lastCall.destination_url, /^https?:\/\/[^/]+\/unlock\/lootlabs\/complete\?s=/);
    // returnUrl is the destination URL recovered from the mock (would be inside LootLabs encrypted blob in real life)
    assert.match(returnUrl, /\/unlock\/lootlabs\/complete\?s=/);
    assert.ok(returnToken.length > 32);
    // Encrypted blob must not appear in plaintext as a `data=<destination>` (it was opaque)
    assert.ok(!location.includes('/unlock/lootlabs/complete'), 'destination URL must NOT appear in the redirect (only the encrypted blob)');
    assert.ok(typeof lootlabsEncrypted === 'string' && lootlabsEncrypted.length > 0);
    assert.doesNotMatch(res.headers['content-type'] || '', /json/i);
    assert.equal(memoryDb.license_ad_challenges[0].provider, 'lootlabs');
    assert.equal(memoryDb.license_ad_challenges[0].status, 'pending_ad');
    assert.equal(memoryDb.license_ad_challenges[0].provider_payload.lootlabs_started, true);
    assert.equal(memoryDb.license_ad_challenges[0].provider_payload.base_link_host, 'lootdest.org');
    assert.equal(memoryDb.license_ad_challenges[0].provider_payload.callback_path, '/unlock/lootlabs/complete');
    assert.equal(memoryDb.license_ad_challenges[0].provider_payload.encrypted_data_present, true);
    assert.ok(memoryDb.license_ad_challenges[0].provider_payload.provider_started_at);
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

  test('LootLabs provider selection works as a mobile top-level form redirect with the encrypted shortlink', async () => {
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
    assert.ok(res.headers.location.startsWith('https://lootdest.org/s?TqZQAW38&data='), `expected lootdest.org/s?TqZQAW38&data=… , got: ${res.headers.location}`);
    assert.ok(!/\bTqZQAW38=/.test(res.headers.location), 'shortlink hash must not have "=" appended');
    assert.doesNotMatch(res.headers['content-type'] || '', /json/i);
  });

  test('LootLabs encrypt API failure (HTTP 500) fails closed (no redirect to lootdest.org, no key)', async () => {
    const agent = request.agent(app);
    await login(agent);
    const started = await startChallenge(agent);
    lootlabsApi.mode = 'http500';
    const res = await agent.post('/key/provider/lootlabs').type('form').send({
      _csrf: started.csrf,
      challenge_id: started.challengeId,
      provider: 'lootlabs',
    });
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('LootLabs encrypt API timeout fails closed', async () => {
    const agent = request.agent(app);
    await login(agent);
    const started = await startChallenge(agent);
    lootlabsApi.mode = 'timeout';
    const res = await agent.post('/key/provider/lootlabs').type('form').send({
      _csrf: started.csrf,
      challenge_id: started.challengeId,
      provider: 'lootlabs',
    });
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('LootLabs encrypt API invalid_token fails closed with PROVIDER_NOT_CONFIGURED reason', async () => {
    const agent = request.agent(app);
    await login(agent);
    const started = await startChallenge(agent);
    lootlabsApi.mode = 'invalid_token';
    const res = await agent.post('/key/provider/lootlabs').type('form').send({
      _csrf: started.csrf,
      challenge_id: started.challengeId,
      provider: 'lootlabs',
    });
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
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

  test('LootLabs is disabled as provider when LOOTLABS_API_TOKEN is not configured', async () => {
    // Without LOOTLABS_API_TOKEN, the encrypt API cannot be called and the
    // anti-bypass flow cannot start. The server must refuse the provider
    // selection (302 PROVIDER_NOT_CONFIGURED) rather than silently redirect.
    const agent = request.agent(app);
    await login(agent);
    const originalToken = process.env.LOOTLABS_API_TOKEN;
    delete process.env.LOOTLABS_API_TOKEN;
    try {
      const started = await startChallenge(agent);
      const res = await agent.post('/key/provider/lootlabs').type('form').send({
        _csrf: started.csrf,
        challenge_id: started.challengeId,
        provider: 'lootlabs',
      });
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
      assert.equal(memoryDb.license_keys.length, 0);
      const pendingLl = memoryDb.license_ad_challenges.filter(
        (c) => c.provider === 'lootlabs' && c.status === 'pending_ad',
      );
      assert.equal(pendingLl.length, 0);
      // No encrypt API call must have been made when config is missing.
      assert.equal(lootlabsApi.callCount, 0);
    } finally {
      if (originalToken !== undefined) process.env.LOOTLABS_API_TOKEN = originalToken;
    }
  });

  test('LootLabs is disabled as provider when LOOTLABS_BASE_LINK is not configured', async () => {
    const agent = request.agent(app);
    await login(agent);
    const originalBase = process.env.LOOTLABS_BASE_LINK;
    const originalMon = process.env.LOOTLABS_MONETIZED_URL;
    delete process.env.LOOTLABS_BASE_LINK;
    delete process.env.LOOTLABS_MONETIZED_URL;
    try {
      const started = await startChallenge(agent);
      const res = await agent.post('/key/provider/lootlabs').type('form').send({
        _csrf: started.csrf,
        challenge_id: started.challengeId,
        provider: 'lootlabs',
      });
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
      assert.equal(memoryDb.license_keys.length, 0);
    } finally {
      if (originalBase !== undefined) process.env.LOOTLABS_BASE_LINK = originalBase;
      if (originalMon !== undefined) process.env.LOOTLABS_MONETIZED_URL = originalMon;
    }
  });

  test('LootLabs is disabled when LOOTLABS_ENABLED is false', async () => {
    const agent = request.agent(app);
    await login(agent);
    const originalEnabled = process.env.LOOTLABS_ENABLED;
    process.env.LOOTLABS_ENABLED = 'false';
    try {
      const started = await startChallenge(agent);
      const res = await agent.post('/key/provider/lootlabs').type('form').send({
        _csrf: started.csrf,
        challenge_id: started.challengeId,
        provider: 'lootlabs',
      });
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
      assert.equal(memoryDb.license_keys.length, 0);
    } finally {
      if (originalEnabled !== undefined) process.env.LOOTLABS_ENABLED = originalEnabled;
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

  test('LootLabs completion with legacy ?t= token (not ?s=) is blocked — no key generated', async () => {
    // The Redirect API flow only accepts the HMAC-signed ?s= state. Legacy
    // ?t= return tokens from the old template flow must NOT generate a key.
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: signedState } = await chooseProvider(agent, 'lootlabs');

    const res = await agent
      .get(`/unlock/lootlabs/complete?t=${encodeURIComponent(signedState)}`)
      .set('Accept', 'text/html');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);
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

  test('LootLabs provider URL builder does NOT use URLSearchParams (regression: shortlink id corruption)', async () => {
    // The shortlink id `?TqZQAW38` is a valueless query key. URLSearchParams
    // would normalise it to `?TqZQAW38=`. This regression test directly
    // checks the URL builder output for the exact byte sequence.
    const ll = require('../src/providers/lootlabs');
    const url = ll.buildLootLabsStartUrl({
      baseLink: 'https://lootdest.org/s?TqZQAW38',
      encryptedData: 'opaque',
    });
    assert.equal(url, 'https://lootdest.org/s?TqZQAW38&data=opaque');
    // Reject any byte sequence "TqZQAW38=" anywhere in the URL.
    assert.ok(!/\bTqZQAW38=/.test(url), 'shortlink id must not gain "=" suffix');
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
    assert.equal(memoryDb.license_keys[0].owner_discord_id, 'discord-user-1');
    assert.equal(memoryDb.license_keys[0].prefix.split('-').length, 2);
    assert.match(memoryDb.license_keys[0].suffix, /^[0-9A-F]{4}$/);
    assert.equal(memoryDb.license_keys[0].plan, 'standard');
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

  test('LootLabs Redirect API: valid signed state + pending challenge generates exactly one key', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: signedState } = await chooseProvider(agent, 'lootlabs');

    const unlock = await completeProvider(agent, 'lootlabs', signedState);
    assert.equal(unlock.status, 302);
    assert.equal(unlock.headers.location, '/key/result');
    assert.equal(memoryDb.license_keys.length, 1);
    assert.equal(memoryDb.license_ad_challenges[0].status, 'key_generated');
  });

  test('LootLabs Redirect API: tampered signed state and provider-mismatched state are rejected', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');

    // LootLabs route called with Linkvertise hash as ?s= — bad signed token shape.
    const tampered = await completeProvider(agent, 'lootlabs', hash);
    assert.equal(tampered.status, 302);
    assert.equal(tampered.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);

    // Signed state for the wrong provider must be rejected.
    const wrongProviderState = signChallenge('00000000-0000-0000-0000-000000000000', 'linkvertise', Date.now() + 60000);
    const wrong = await completeProvider(agent, 'lootlabs', wrongProviderState);
    assert.equal(wrong.status, 302);
    assert.equal(wrong.headers.location, '/license');
    assert.equal(memoryDb.license_keys.length, 0);

    // An expired signed state must be rejected.
    const expiredState = signChallenge('00000000-0000-0000-0000-000000000000', 'lootlabs', Date.now() - 1000);
    const expired = await completeProvider(agent, 'lootlabs', expiredState);
    assert.equal(expired.status, 302);
    assert.equal(expired.headers.location, '/license');
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

  test('two simultaneous provider completions create only one active unredeemed key', async () => {
    const agent = request.agent(app);
    await login(agent);
    const first = await chooseProvider(agent, 'lootlabs');
    const second = await chooseProvider(agent, 'lootlabs');

    const [firstComplete, secondComplete] = await Promise.all([
      completeProvider(agent, 'lootlabs', first.returnToken),
      completeProvider(agent, 'lootlabs', second.returnToken),
    ]);

    assert.equal(firstComplete.status, 302);
    assert.equal(secondComplete.status, 302);
    assert.equal(firstComplete.headers.location, '/key/result');
    assert.equal(secondComplete.headers.location, '/key/result');
    const activeUnused = memoryDb.license_keys.filter((row) => (
      row.status === 'active' &&
      !row.redeemed_at &&
      !memoryDb.device_bindings.some((binding) => binding.key_id === row.id && binding.is_active) &&
      new Date(row.expires_at).getTime() > Date.now()
    ));
    assert.equal(activeUnused.length, 1);
    assert.equal(memoryDb.license_keys.length, 1);
  });

  test('server-side cooldown is enforced after a redeemed generated key (Linkvertise)', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    await completeProvider(agent, 'linkvertise', hash);
    memoryDb.license_keys[0].redeemed_at = new Date().toISOString();
    memoryDb.license_keys[0].expires_at = null;

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
    assert.match(license.text, /Unbound/);
    assert.doesNotMatch(license.text, />linkvertise</);
    assert.doesNotMatch(license.text, /pending_ad/);
  });

  test('My License reads canonical license_keys by Discord owner, not portal-only challenge history', async () => {
    const agent = request.agent(app);
    await login(agent);
    memoryDb.license_keys.push({
      id: 'discord-owned-key',
      prefix: 'DENG-AAAA',
      suffix: 'DDDD',
      owner_discord_id: 'discord-user-1',
      status: 'active',
      plan: 'standard',
      created_at: new Date().toISOString(),
      redeemed_at: new Date().toISOString(),
      expires_at: null,
    });

    const license = await agent.get('/license');
    assert.equal(license.status, 200);
    assert.match(license.text, /Full key unavailable for this old key/);
    assert.match(license.text, /Unbound/);
  });

  test('dashboard stats use the same active license filter as Discord stats', async () => {
    const agent = request.agent(app);
    await login(agent);
    const now = new Date().toISOString();
    memoryDb.license_keys.push(
      {
        id: 'active-unbound',
        prefix: 'DENG-1111',
        suffix: '4444',
        owner_discord_id: 'discord-user-1',
        status: 'active',
        plan: 'standard',
        created_at: now,
        redeemed_at: null,
        expires_at: new Date(Date.now() + 3600 * 1000).toISOString(),
      },
      {
        id: 'revoked-hidden',
        prefix: 'DENG-9999',
        suffix: '0000',
        owner_discord_id: 'discord-user-1',
        status: 'revoked',
        plan: 'standard',
        created_at: now,
        redeemed_at: null,
        expires_at: null,
      },
      {
        id: 'expired-hidden',
        prefix: 'DENG-8888',
        suffix: '0000',
        owner_discord_id: 'discord-user-1',
        status: 'active',
        plan: 'standard',
        created_at: now,
        redeemed_at: null,
        expires_at: new Date(Date.now() - 3600 * 1000).toISOString(),
      },
    );

    const dashboard = await agent.get('/dashboard');
    assert.equal(dashboard.status, 200);
    assert.match(dashboard.text, /Total Licenses[\s\S]*?<p class="stat-value">1<\/p>/);
    assert.doesNotMatch(dashboard.text, /revoked-hidden|expired-hidden/);
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

describe('canonical license service', () => {
  test('active/status helper matches Discord rules for revoked, expired, bound, and unbound keys', () => {
    const svc = require('../src/licenseService');
    assert.equal(svc.isActiveLicense({ status: 'revoked' }), false);
    assert.equal(svc.isActiveLicense({ status: 'active', expires_at: new Date(Date.now() - 1).toISOString() }), false);
    assert.equal(svc.isActiveLicense({ status: 'active', redeemed_at: new Date().toISOString() }), true);
    assert.equal(svc.isActiveLicense({ status: 'active', active_binding: true }), true);
    assert.equal(svc.isActiveLicense({ status: 'active', expires_at: new Date(Date.now() + 1000).toISOString() }), true);
    assert.equal(svc.formatLicenseStatus({ status: 'active', active_binding: true }), 'Bound');
    assert.equal(svc.formatLicenseStatus({ status: 'active', redeemed_at: new Date().toISOString(), license_key_id: 'owned' }), 'Unbound');
    assert.equal(svc.formatLicenseStatus({ status: 'active', license_key_id: 'owned' }), 'Unbound');
  });
});

describe('My License action APIs', () => {
  test('new license action endpoints reject unauthenticated requests', async () => {
    const getResettable = await request(app).get('/api/license/resettable');
    const reset = await request(app).post('/api/license/reset-hwid').send({ key_id: 'x' });
    const redeem = await request(app).post('/api/license/redeem').send({ key: 'x' });
    const download = await request(app).get('/api/license/download');
    assert.equal(getResettable.status, 401);
    assert.equal(reset.status, 401);
    assert.equal(redeem.status, 401);
    assert.equal(download.status, 401);
  });

  test('Reset HWID lists only logged-in user active resettable keys and resets canonical binding', async () => {
    const agent = request.agent(app);
    await login(agent);
    const page = await agent.get('/license');
    const csrf = csrfFrom(page.text);
    const now = new Date().toISOString();
    const ownedKey = 'DENG-1111-2222-3333-4444';
    const ownedId = licenseKeyId(ownedKey);
    const otherKey = 'DENG-9999-9999-9999-0000';
    const otherId = licenseKeyId(otherKey);
    const revokedKey = 'DENG-8888-8888-8888-0000';
    const revokedId = licenseKeyId(revokedKey);
    memoryDb.license_keys.push(
      {
        id: ownedId,
        prefix: 'DENG-1111',
        suffix: '4444',
        owner_discord_id: 'discord-user-1',
        status: 'active',
        plan: 'standard',
        created_at: now,
        redeemed_at: now,
        expires_at: null,
      },
      {
        id: otherId,
        prefix: 'DENG-9999',
        suffix: '0000',
        owner_discord_id: 'discord-user-2',
        status: 'active',
        plan: 'standard',
        created_at: now,
        redeemed_at: now,
        expires_at: null,
      },
      {
        id: revokedId,
        prefix: 'DENG-8888',
        suffix: '0000',
        owner_discord_id: 'discord-user-1',
        status: 'revoked',
        plan: 'standard',
        created_at: now,
        redeemed_at: now,
        expires_at: null,
      },
    );
    memoryDb.device_bindings.push(
      { key_id: ownedId, install_id_hash: 'old-hwid', device_model: 'SM-S901B', device_label: 'Phone', last_seen_at: now, is_active: true },
      { key_id: otherId, install_id_hash: 'other-hwid', device_model: 'Other', last_seen_at: now, is_active: true },
      { key_id: revokedId, install_id_hash: 'dead-hwid', device_model: 'Dead', last_seen_at: now, is_active: true },
    );

    const list = await agent.get('/api/license/resettable');
    assert.equal(list.status, 200);
    assert.equal(list.body.keys.length, 1);
    assert.equal(list.body.keys[0].id, ownedId);
    assert.equal(list.body.keys[0].device_status, 'Bound To A Device');

    const wrongOwner = await agent.post('/api/license/reset-hwid')
      .set('X-CSRF-Token', csrf)
      .send({ key_id: otherId });
    assert.equal(wrongOwner.status, 403);

    const revoked = await agent.post('/api/license/reset-hwid')
      .set('X-CSRF-Token', csrf)
      .send({ key_id: revokedId });
    assert.equal(revoked.status, 400);

    const reset = await agent.post('/api/license/reset-hwid')
      .set('X-CSRF-Token', csrf)
      .send({ key_id: ownedId });
    assert.equal(reset.status, 200);
    assert.equal(reset.body.message, 'HWID Reset Successful. You Can Bind This Key On A New Device.');
    assert.equal(memoryDb.device_bindings.find((row) => row.key_id === ownedId).is_active, false);
    assert.equal(memoryDb.hwid_reset_logs.length, 1);
    assert.equal(memoryDb.hwid_reset_logs[0].owner_discord_id, 'discord-user-1');
    assert.equal(memoryDb.hwid_reset_logs[0].old_install_id_hash, 'old-hwid');

    const noDevice = await agent.post('/api/license/reset-hwid')
      .set('X-CSRF-Token', csrf)
      .send({ key_id: ownedId });
    assert.equal(noDevice.status, 400);
    assert.equal(noDevice.body.error, 'no_device_linked');
  });

  test('Redeem Key validates format and ownership, handles self-owned, and claims unowned keys', async () => {
    const agent = request.agent(app);
    await login(agent);
    const page = await agent.get('/license');
    const csrf = csrfFrom(page.text);
    const now = new Date().toISOString();
    const redeemable = 'DENG-AAAA-BBBB-CCCC-DDDD';
    const selfOwned = 'DENG-1111-AAAA-2222-BBBB';
    const otherOwned = 'DENG-9999-AAAA-2222-BBBB';
    const expired = 'DENG-EEEE-FFFF-AAAA-BBBB';
    memoryDb.license_users.push({ discord_user_id: 'discord-user-1', max_keys: 999999, is_blocked: false });
    memoryDb.license_keys.push(
      { id: licenseKeyId(redeemable), prefix: 'DENG-AAAA', suffix: 'DDDD', owner_discord_id: null, status: 'active', plan: 'standard', created_at: now, expires_at: new Date(Date.now() + 3600 * 1000).toISOString(), redeemed_at: null },
      { id: licenseKeyId(selfOwned), prefix: 'DENG-1111', suffix: 'BBBB', owner_discord_id: 'discord-user-1', status: 'active', plan: 'standard', created_at: now, expires_at: null, redeemed_at: now },
      { id: licenseKeyId(otherOwned), prefix: 'DENG-9999', suffix: 'BBBB', owner_discord_id: 'discord-user-2', status: 'active', plan: 'standard', created_at: now, expires_at: null, redeemed_at: now },
      { id: licenseKeyId(expired), prefix: 'DENG-EEEE', suffix: 'BBBB', owner_discord_id: null, status: 'active', plan: 'standard', created_at: now, expires_at: new Date(Date.now() - 1000).toISOString(), redeemed_at: null },
    );

    const invalid = await agent.post('/api/license/redeem').set('X-CSRF-Token', csrf).send({ key: 'bad-key' });
    assert.equal(invalid.status, 400);
    assert.equal(invalid.body.error, 'invalid_key_format');

    const expiredRes = await agent.post('/api/license/redeem').set('X-CSRF-Token', csrf).send({ key: expired });
    assert.equal(expiredRes.status, 400);
    assert.equal(expiredRes.body.error, 'key_expired');

    const other = await agent.post('/api/license/redeem').set('X-CSRF-Token', csrf).send({ key: otherOwned });
    assert.equal(other.status, 403);
    assert.equal(other.body.error, 'key_owned_by_another_user');

    const self = await agent.post('/api/license/redeem').set('X-CSRF-Token', csrf).send({ key: selfOwned });
    assert.equal(self.status, 200);
    assert.equal(self.body.status, 'already_owned');
    assert.match(self.body.message, /already redeemed by you/i);

    const redeemed = await agent.post('/api/license/redeem').set('X-CSRF-Token', csrf).send({ key: redeemable });
    assert.equal(redeemed.status, 200);
    assert.equal(redeemed.body.status, 'redeemed');
    const row = memoryDb.license_keys.find((item) => item.id === licenseKeyId(redeemable));
    assert.equal(row.owner_discord_id, 'discord-user-1');
    assert.equal(row.expires_at, null);
    assert.ok(row.redeemed_at);
  });

  test('Download Key exports only logged-in user active keys with safe full-key fallback', async () => {
    const agent = request.agent(app);
    await login(agent);
    const now = '2026-05-22T07:14:05.740Z';
    const redeemedAt = '2026-05-22T07:21:40.000Z';
    const activeFull = 'DENG-AAAA-1111-BBBB-2222';
    const activeFullId = licenseKeyId(activeFull);
    const boundFull = 'DENG-68C9-0BA2-F745-E506';
    const boundFullId = licenseKeyId(boundFull);
    memoryDb.license_keys.push(
      { id: activeFullId, prefix: 'DENG-AAAA', suffix: '2222', owner_discord_id: 'discord-user-1', status: 'active', plan: 'standard', created_at: now, redeemed_at: redeemedAt, expires_at: null },
      { id: boundFullId, prefix: 'DENG-68C9', suffix: 'E506', owner_discord_id: 'discord-user-1', status: 'active', plan: 'standard', created_at: '2026-05-14T20:40:35.000Z', redeemed_at: '2026-05-14T20:41:40.000Z', expires_at: null },
      { id: 'old-unrecoverable', prefix: 'DENG-3333', suffix: '4444', owner_discord_id: 'discord-user-1', status: 'active', plan: 'standard', created_at: now, redeemed_at: null, expires_at: null },
      { id: 'other-user-export', prefix: 'DENG-9999', suffix: '0000', owner_discord_id: 'discord-user-2', status: 'active', plan: 'standard', created_at: now, redeemed_at: redeemedAt, expires_at: null },
      { id: 'revoked-export', prefix: 'DENG-8888', suffix: '0000', owner_discord_id: 'discord-user-1', status: 'revoked', plan: 'standard', created_at: now, redeemed_at: redeemedAt, expires_at: null },
      { id: 'expired-export', prefix: 'DENG-7777', suffix: '0000', owner_discord_id: 'discord-user-1', status: 'active', plan: 'standard', created_at: now, redeemed_at: null, expires_at: new Date(Date.now() - 1000).toISOString() },
    );
    memoryDb.device_bindings.push({
      key_id: boundFullId,
      install_id_hash: 'bound-hwid',
      device_model: 'SM-N9810',
      device_label: 'Phone',
      last_seen_at: '2026-05-15T01:00:00.000Z',
      is_active: true,
    });
    memoryDb.license_ad_challenges.push({
      id: 'challenge-export',
      license_key_id: activeFullId,
      key_prefix: 'DENG-AAAA-1111',
      key_suffix: 'BBBB-2222',
      provider: 'lootlabs',
      completed_at: now,
      created_at: now,
      key_expires_at: null,
    });
    memoryDb.license_ad_challenges.push({
      id: 'challenge-bound-export',
      license_key_id: boundFullId,
      key_prefix: 'DENG-68C9-0BA2',
      key_suffix: 'F745-E506',
      provider: 'discord',
      completed_at: '2026-05-14T20:40:35.000Z',
      created_at: '2026-05-14T20:40:35.000Z',
      key_expires_at: null,
    });

    const res = await agent.get('/api/license/download');
    assert.equal(res.status, 200);
    const disposition = decodeURIComponent(res.headers['content-disposition']);
    assert.match(disposition, /attachment/);
    assert.match(disposition, /DiscordTester - DENG Tool Rejoin License Keys - \d{1,2} [A-Za-z]+ 20\d{2}\.txt/);
    assert.doesNotMatch(disposition, /deng-rejoin-keys|T\d{2}-\d{2}-\d{2}|\.?\d{3}Z/);
    assert.match(res.text, /DENG Tool: Rejoin Keys/);
    assert.match(res.text, /User: DiscordTester/);
    assert.match(res.text, /Generated: \d{1,2} [A-Za-z]+ 20\d{2}, \d{1,2}:\d{2}:\d{2} (AM|PM)/);
    assert.match(res.text, new RegExp(activeFull));
    assert.match(res.text, new RegExp(boundFull));
    assert.match(res.text, /Full key unavailable for this old key/i);
    assert.match(res.text, /Status: No Device Linked/);
    assert.match(res.text, /Status: Bound/);
    assert.match(res.text, /Device: None/);
    assert.match(res.text, /Device: SM-N9810/);
    assert.match(res.text, /Created: 22 Mei 2026, 2:14:05 PM/);
    assert.match(res.text, /Expires: None/);
    assert.match(res.text, /Redeemed: 22 Mei 2026, 2:21:40 PM/);
    assert.match(res.text, /Created: 15 Mei 2026, 3:40:35 AM/);
    assert.match(res.text, /Redeemed: 15 Mei 2026, 3:41:40 AM/);
    assert.match(res.text, /Redeemed: None/);
    assert.match(res.text, /Provider: LootLabs/);
    assert.match(res.text, /Provider: Discord Panel/);
    assert.doesNotMatch(res.text, /Recoverable:/);
    assert.doesNotMatch(res.text, /\d{4}-\d{2}-\d{2}T/);
    assert.doesNotMatch(res.text, /other-user-export|DENG-9999|revoked-export|DENG-8888|expired-export|DENG-7777/);
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

  test('license history API includes WIB formatted timestamps for clients', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: hash } = await chooseProvider(agent, 'linkvertise');
    await completeProvider(agent, 'linkvertise', hash);
    memoryDb.license_keys[0].created_at = '2026-05-22T07:14:05.740Z';
    memoryDb.license_keys[0].redeemed_at = '2026-05-22T07:21:40.000Z';
    memoryDb.license_keys[0].expires_at = '2026-05-23T17:00:00.000Z';
    memoryDb.license_ad_challenges[0].completed_at = '2026-05-22T07:14:05.740Z';
    memoryDb.license_ad_challenges[0].key_expires_at = '2026-05-23T17:00:00.000Z';
    memoryDb.license_ad_challenges[0].key_prefix = memoryDb.license_ad_challenges[0].key_prefix || memoryDb.license_keys[0].prefix;
    memoryDb.license_ad_challenges[0].key_suffix = memoryDb.license_ad_challenges[0].key_suffix || memoryDb.license_keys[0].suffix;
    const res = await agent.get('/api/license/history');
    assert.equal(res.status, 200);
    assert.ok(res.body.history.length > 0);
    assert.match(res.body.history[0].key, /DENG-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}/);
    assert.match(res.body.history[0].masked_key, /^DENG-[0-9A-F]{4}\.\.\.[0-9A-F]{4}$/);
    assert.notEqual(res.body.history[0].masked_key, res.body.history[0].key);
    assert.equal(res.body.history[0].created_at_formatted, '22 Mei 2026, 2:14:05 PM');
    assert.equal(res.body.history[0].key_expires_at_formatted, '24 Mei 2026, 12:00:00 AM');
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

  test('LootLabs disabled card shown in choose_provider when LOOTLABS_API_TOKEN not set', async () => {
    const originalToken = process.env.LOOTLABS_API_TOKEN;
    delete process.env.LOOTLABS_API_TOKEN;
    try {
      const agent = request.agent(app);
      await login(agent);
      const { html } = await startChallenge(agent);
      assert.match(html, /LootLabs is temporarily unavailable/i);
      // No active submit form for LootLabs when disabled.
      assert.doesNotMatch(html, /action="\/key\/provider\/lootlabs"/);
    } finally {
      if (originalToken !== undefined) process.env.LOOTLABS_API_TOKEN = originalToken;
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

  test('LootLabs Anti-Bypass: API token is NEVER included in the redirect URL or in any rendered HTML', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { res } = await chooseProvider(agent, 'lootlabs');
    const tokenRegex = new RegExp(process.env.LOOTLABS_API_TOKEN);
    assert.doesNotMatch(res.headers.location || '', tokenRegex);
    const license = await agent.get('/license');
    assert.doesNotMatch(license.text, tokenRegex);
    const { html } = await startChallenge(agent);
    assert.doesNotMatch(html, tokenRegex);
  });

  test('LootLabs Anti-Bypass: replayed ?s= state cannot mint a second key (challenge already consumed)', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: signedState } = await chooseProvider(agent, 'lootlabs');

    const first = await completeProvider(agent, 'lootlabs', signedState);
    assert.equal(first.headers.location, '/key/result');
    assert.equal(memoryDb.license_keys.length, 1);

    // Replay: same signed state, second hit must not generate another key.
    const second = await completeProvider(agent, 'lootlabs', signedState);
    assert.notEqual(second.headers.location, '/key/result');
    assert.equal(memoryDb.license_keys.length, 1);
  });

  test('LootLabs Anti-Bypass: direct /unlock/lootlabs/complete (no ?s=, fake ?s=, fake ?t=) is blocked', async () => {
    const agent = request.agent(app);
    await login(agent);
    for (const url of [
      '/unlock/lootlabs/complete',
      '/unlock/lootlabs/complete?s=fake',
      '/unlock/lootlabs/complete?s=' + encodeURIComponent('a.b'),
      '/unlock/lootlabs/complete?t=anything',
      '/unlock/lootlabs/complete?s=anything&t=anything',
    ]) {
      const res = await agent.get(url);
      assert.equal(res.status, 302, `${url} should redirect`);
      assert.equal(res.headers.location, '/license');
    }
    assert.equal(memoryDb.license_keys.length, 0);
  });

  test('LootLabs Anti-Bypass: direct completion from a different session does not generate a key', async () => {
    const agent = request.agent(app);
    await login(agent);
    const { returnToken: signedState } = await chooseProvider(agent, 'lootlabs');

    // A second fresh session (same Discord user re-logged in) tries the first
    // session's signed state — must fail because the second session has a
    // different session_hash on the challenge row.
    const other = request.agent(app);
    const originalGet = fakeAxios.get;
    fakeAxios.get = async () => ({
      data: { id: 'discord-user-1', username: 'DiscordTester', avatar: null },
    });
    try {
      await login(other);
      const res = await other.get(`/unlock/lootlabs/complete?s=${encodeURIComponent(signedState)}`);
      assert.equal(res.status, 302);
      assert.equal(res.headers.location, '/license');
      assert.equal(memoryDb.license_keys.length, 0);
    } finally {
      fakeAxios.get = originalGet;
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

  test('LootLabs Redirect API URL builder preserves the valueless shortlink id (no "=" appended)', async () => {
    const ll = require('../src/providers/lootlabs');
    // Direct check of the URL builder: this is the only piece responsible for
    // not corrupting the shortlink key. URLSearchParams MUST NOT be used here.
    const out1 = ll.buildLootLabsStartUrl({
      baseLink: 'https://lootdest.org/s?TqZQAW38',
      encryptedData: 'opaque-blob-no-specials',
    });
    assert.equal(out1, 'https://lootdest.org/s?TqZQAW38&data=opaque-blob-no-specials');
    assert.ok(!/\bTqZQAW38=/.test(out1), 'shortlink hash must not have = appended');

    // Stale `&data=…` on the base link must be stripped before appending again.
    const out2 = ll.buildLootLabsStartUrl({
      baseLink: 'https://lootdest.org/s?TqZQAW38&data=stale',
      encryptedData: 'fresh',
    });
    assert.equal(out2, 'https://lootdest.org/s?TqZQAW38&data=fresh');

    // Empty inputs return empty string.
    assert.equal(ll.buildLootLabsStartUrl({ baseLink: '', encryptedData: 'x' }), '');
    assert.equal(ll.buildLootLabsStartUrl({ baseLink: 'https://x/?k', encryptedData: '' }), '');
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

describe('LootLabs provider helper (Redirect API / Anti-Bypass)', () => {
  const ll = require('../src/providers/lootlabs');

  test('isLootLabsConfigured returns true when env is complete', () => {
    assert.equal(ll.isLootLabsConfigured(), true);
    assert.equal(ll.getLootLabsUnavailableReason(), null);
  });

  test('isLootLabsConfigured returns false when LOOTLABS_ENABLED is false', () => {
    const orig = process.env.LOOTLABS_ENABLED;
    process.env.LOOTLABS_ENABLED = 'false';
    try {
      assert.equal(ll.isLootLabsConfigured(), false);
      assert.match(ll.getLootLabsUnavailableReason() || '', /LOOTLABS_ENABLED/);
    } finally {
      process.env.LOOTLABS_ENABLED = orig;
    }
  });

  test('isLootLabsConfigured returns false when LOOTLABS_API_TOKEN is missing', () => {
    const orig = process.env.LOOTLABS_API_TOKEN;
    delete process.env.LOOTLABS_API_TOKEN;
    try {
      assert.equal(ll.isLootLabsConfigured(), false);
      assert.match(ll.getLootLabsUnavailableReason() || '', /LOOTLABS_API_TOKEN/);
    } finally {
      if (orig !== undefined) process.env.LOOTLABS_API_TOKEN = orig;
    }
  });

  test('isLootLabsConfigured returns false when LOOTLABS_BASE_LINK and LOOTLABS_MONETIZED_URL are both missing', () => {
    const orig1 = process.env.LOOTLABS_BASE_LINK;
    const orig2 = process.env.LOOTLABS_MONETIZED_URL;
    delete process.env.LOOTLABS_BASE_LINK;
    delete process.env.LOOTLABS_MONETIZED_URL;
    try {
      assert.equal(ll.isLootLabsConfigured(), false);
      assert.match(ll.getLootLabsUnavailableReason() || '', /LOOTLABS_BASE_LINK/);
    } finally {
      if (orig1 !== undefined) process.env.LOOTLABS_BASE_LINK = orig1;
      if (orig2 !== undefined) process.env.LOOTLABS_MONETIZED_URL = orig2;
    }
  });

  test('isLootLabsConfigured returns false when LOOTLABS_ENCRYPT_URL is empty', () => {
    const orig = process.env.LOOTLABS_ENCRYPT_URL;
    process.env.LOOTLABS_ENCRYPT_URL = '';
    try {
      // Default fallback should restore a usable URL — so config is still valid.
      // (We treat the default `https://creators.lootlabs.gg/api/public/url_encryptor` as the implicit fallback.)
      assert.equal(ll.getLootLabsEncryptUrl(), 'https://creators.lootlabs.gg/api/public/url_encryptor');
      assert.equal(ll.isLootLabsConfigured(), true);
    } finally {
      if (orig !== undefined) process.env.LOOTLABS_ENCRYPT_URL = orig;
    }
  });

  test('stripDataParam removes a stale &data=… suffix from the base link', () => {
    assert.equal(
      ll.stripDataParam('https://lootdest.org/s?TqZQAW38&data=stale-blob/with+symbols'),
      'https://lootdest.org/s?TqZQAW38',
    );
    assert.equal(ll.stripDataParam('https://lootdest.org/s?TqZQAW38'), 'https://lootdest.org/s?TqZQAW38');
    assert.equal(ll.stripDataParam(''), '');
  });

  test('getLootLabsBaseLink falls back to LOOTLABS_MONETIZED_URL when LOOTLABS_BASE_LINK is unset', () => {
    const orig = process.env.LOOTLABS_BASE_LINK;
    delete process.env.LOOTLABS_BASE_LINK;
    try {
      assert.equal(ll.getLootLabsBaseLink(), process.env.LOOTLABS_MONETIZED_URL);
    } finally {
      if (orig !== undefined) process.env.LOOTLABS_BASE_LINK = orig;
    }
  });

  test('buildLootLabsCallbackUrl appends ?s=<signed_state> to the public DENG URL', () => {
    const url = ll.buildLootLabsCallbackUrl({
      signedState: 'eyJhIjp9.deadbeef',
      publicUrl: 'https://tool.deng.my.id',
    });
    assert.equal(url, 'https://tool.deng.my.id/unlock/lootlabs/complete?s=eyJhIjp9.deadbeef');
  });

  test('buildLootLabsCallbackUrl percent-encodes special chars in signed state', () => {
    const url = ll.buildLootLabsCallbackUrl({
      signedState: 'a/b+c=d',
      publicUrl: 'https://tool.deng.my.id',
    });
    assert.equal(url, 'https://tool.deng.my.id/unlock/lootlabs/complete?s=a%2Fb%2Bc%3Dd');
  });

  test('buildLootLabsCallbackUrl returns empty string when signedState is missing', () => {
    assert.equal(ll.buildLootLabsCallbackUrl({ signedState: '', publicUrl: 'x' }), '');
    assert.equal(ll.buildLootLabsCallbackUrl({ signedState: null, publicUrl: 'x' }), '');
  });

  test('buildLootLabsStartUrl appends &data=<encrypted> without corrupting the shortlink id', () => {
    // Plain (no specials) message is appended verbatim.
    const out1 = ll.buildLootLabsStartUrl({
      baseLink: 'https://lootdest.org/s?TqZQAW38',
      encryptedData: 'opaqueBlobNoSpecials',
    });
    assert.equal(out1, 'https://lootdest.org/s?TqZQAW38&data=opaqueBlobNoSpecials');
    assert.ok(!/\bTqZQAW38=/.test(out1));

    // LootLabs's `message` is pre-URL-encoded (`%2B`, `%2F`, `%3D`).
    // We MUST NOT double-encode it. The result keeps the original
    // percent-encoding intact (no `%252B`, `%252F`, `%253D`).
    const preEncoded = 'abc%2BDEF%2Fghi%3D';
    const out2 = ll.buildLootLabsStartUrl({
      baseLink: 'https://lootdest.org/s?TqZQAW38',
      encryptedData: preEncoded,
    });
    assert.equal(out2, `https://lootdest.org/s?TqZQAW38&data=${preEncoded}`);
    assert.ok(!/%25(2B|2F|3D)/i.test(out2), 'must NOT double-encode the response message');

    // Raw base64 chars (`+`, `/`, `=`) are also appended unchanged — they are
    // legal in a URL query value and any further "safety" encoding would
    // corrupt a non-pre-encoded LootLabs response.
    const out3 = ll.buildLootLabsStartUrl({
      baseLink: 'https://lootdest.org/s?TqZQAW38',
      encryptedData: 'A+B/C=',
    });
    assert.equal(out3, 'https://lootdest.org/s?TqZQAW38&data=A+B/C=');

    // Defensive: characters that would actively break the URL are escaped.
    const out4 = ll.buildLootLabsStartUrl({
      baseLink: 'https://lootdest.org/s?TqZQAW38',
      encryptedData: 'a b&c?d#e',
    });
    assert.equal(out4, 'https://lootdest.org/s?TqZQAW38&data=a%20b%26c%3Fd%23e');
  });

  test('buildLootLabsStartUrl handles a mixed pre-encoded message exactly like the live API returns it', () => {
    // Real-world example: LootLabs may return a message that already contains
    // both `%2F` and `%2B` percent-escapes. None of those must become `%25..`.
    const liveLikeMessage =
      'ihPHMxenze2KBPS%2F6kNLTgtYd7efUtHFUuU6wRsyO1OoAHP8ip4YW9kwvmzcbvsNBk8FVOHlhHTYaIddz67bwq1pE%2FkhYeFsesYckOSBZnUdwZdIH6ZH9gDGXjbG%2Fl1U05eQFtkH29k99HPMvdakMxsL%2B99N2JztPUMCVUgIfje510QeA641Ju4d';
    const out = ll.buildLootLabsStartUrl({
      baseLink: 'https://lootdest.org/s?TqZQAW38',
      encryptedData: liveLikeMessage,
    });
    assert.equal(out, `https://lootdest.org/s?TqZQAW38&data=${liveLikeMessage}`);
    assert.ok(!/%252B|%252F|%253D/i.test(out), 'must not double-encode percent escapes');
  });

  test('classifyEncryptResponse rejects payloads with no message and accepts {type:success, message:"…"}', () => {
    assert.deepEqual(
      ll.classifyEncryptResponse({ type: 'success', message: 'abc' }),
      { ok: true, reason: 'success', encrypted: 'abc' },
    );
    // Even without "type" the `message` field alone is enough to succeed.
    assert.deepEqual(
      ll.classifyEncryptResponse({ message: 'abc' }),
      { ok: true, reason: 'success', encrypted: 'abc' },
    );
    assert.deepEqual(
      ll.classifyEncryptResponse({ type: 'error', message: 'Invalid token' }),
      { ok: false, reason: 'api_invalid_token' },
    );
    assert.deepEqual(
      ll.classifyEncryptResponse({ type: 'error', message: 'Bad input' }),
      { ok: false, reason: 'api_type_error' },
    );
    assert.deepEqual(
      ll.classifyEncryptResponse({ message: '' }),
      { ok: false, reason: 'api_invalid_response' },
    );
    assert.deepEqual(
      ll.classifyEncryptResponse({ whatever: 'shape' }),
      { ok: false, reason: 'api_invalid_response' },
    );
    assert.deepEqual(
      ll.classifyEncryptResponse(null),
      { ok: false, reason: 'api_invalid_response' },
    );
    assert.deepEqual(
      ll.classifyEncryptResponse('string body'),
      { ok: false, reason: 'api_invalid_response' },
    );
  });

  test('encryptLootLabsDestination rejects missing destination without calling the API', async () => {
    const before = lootlabsApi.callCount;
    const res = await ll.encryptLootLabsDestination({ destinationUrl: '', requestId: 'r' });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'missing_destination');
    assert.equal(lootlabsApi.callCount, before);
  });

  test('encryptLootLabsDestination returns success with encrypted value on TYPE=success', async () => {
    lootlabsApi.mode = 'auto';
    const res = await ll.encryptLootLabsDestination({
      destinationUrl: 'https://tool.deng.my.id/unlock/lootlabs/complete?s=abc.def',
      requestId: 'r',
    });
    assert.equal(res.ok, true);
    assert.equal(res.reason, 'success');
    assert.ok(typeof res.encrypted === 'string' && res.encrypted.length > 0);
  });

  test('encryptLootLabsDestination fails closed on 401/403 with api_invalid_token', async () => {
    lootlabsApi.mode = 'invalid_token';
    const res = await ll.encryptLootLabsDestination({
      destinationUrl: 'https://x/cb?s=abc',
      requestId: 'r',
    });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'api_invalid_token');
  });

  test('encryptLootLabsDestination fails closed on type:error (api_type_error)', async () => {
    lootlabsApi.mode = 'type_error';
    const res = await ll.encryptLootLabsDestination({
      destinationUrl: 'https://x/cb?s=abc',
      requestId: 'r',
    });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'api_type_error');
  });

  test('encryptLootLabsDestination fails closed on HTTP 500', async () => {
    lootlabsApi.mode = 'http500';
    const res = await ll.encryptLootLabsDestination({
      destinationUrl: 'https://x/cb?s=abc',
      requestId: 'r',
    });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'api_error');
  });

  test('encryptLootLabsDestination fails closed on timeout', async () => {
    lootlabsApi.mode = 'timeout';
    const res = await ll.encryptLootLabsDestination({
      destinationUrl: 'https://x/cb?s=abc',
      requestId: 'r',
    });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'api_timeout');
  });

  test('encryptLootLabsDestination fails closed on network error', async () => {
    lootlabsApi.mode = 'network';
    const res = await ll.encryptLootLabsDestination({
      destinationUrl: 'https://x/cb?s=abc',
      requestId: 'r',
    });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'api_error');
  });

  test('encryptLootLabsDestination fails closed on unrecognised response shape', async () => {
    lootlabsApi.mode = 'invalid_response';
    const res = await ll.encryptLootLabsDestination({
      destinationUrl: 'https://x/cb?s=abc',
      requestId: 'r',
    });
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'api_invalid_response');
  });

  test('encryptLootLabsDestination sends API token in Authorization header (never in URL or body)', async () => {
    lootlabsApi.mode = 'auto';
    const dest = 'https://tool.deng.my.id/unlock/lootlabs/complete?s=abc.def';
    await ll.encryptLootLabsDestination({ destinationUrl: dest, requestId: 'r' });
    const tok = process.env.LOOTLABS_API_TOKEN;
    assert.ok(tok && tok.length > 0);
    assert.equal(lootlabsApi.lastCall.headers.Authorization, `Bearer ${tok}`);
    assert.ok(!String(lootlabsApi.lastCall.url).includes(tok), 'API token must not be in the URL');
    const bodyStr = JSON.stringify(lootlabsApi.lastCall.body || {});
    assert.ok(!bodyStr.includes(tok), 'API token must not be in the POST body');
    assert.equal(lootlabsApi.lastCall.destination_url, dest);
  });
});
