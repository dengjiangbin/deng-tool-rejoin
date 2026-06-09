'use strict';
/**
 * Live proof: create a real session for a user and POST provider selection
 * against the running deng-tool-site (PM2). Logs appear in deng-tool-site-out.log.
 *
 * Usage: node scripts/proof_license_live_redirect.js hazeelniyt
 */
const crypto = require('crypto');
const http = require('http');
const path = require('path');
const { URLSearchParams } = require('url');

module.paths.unshift(path.join(__dirname, '..', 'site', 'node_modules'));
const dotenv = require('dotenv');
dotenv.config({ path: path.join(__dirname, '..', 'site', '.env') });
dotenv.config({ path: path.join(__dirname, '..', '.env') });
dotenv.config({ path: path.join(__dirname, '..', 'env') });

const username = process.argv[2] || 'hazeelniyt';
const host = process.env.TOOL_SITE_HOST || '127.0.0.1';
const port = parseInt(process.env.TOOL_SITE_PORT || '8791', 10);
const cookieSecret = process.env.TOOL_SITE_COOKIE_SECRET;

if (!cookieSecret || cookieSecret.length < 32) {
  console.error('TOOL_SITE_COOKIE_SECRET missing');
  process.exit(1);
}

function signSid(sid) {
  const signature = require('cookie-signature').sign(sid, cookieSecret);
  return `s:${signature}`;
}

function httpRequest(method, reqPath, { cookie = '', body = '', headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: host,
      port,
      path: reqPath,
      method,
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        Accept: 'application/json',
        Cookie: cookie,
        ...headers,
      },
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        resolve({
          status: res.statusCode,
          headers: res.headers,
          body: Buffer.concat(chunks).toString('utf8'),
        });
      });
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

async function main() {
  const routes = require(path.join(__dirname, '..', 'site', 'src', 'routes'));
  const report = await routes.buildLicenseUserDebugReport({ username });
  if (!report.ok) {
    console.error(JSON.stringify(report, null, 2));
    process.exit(2);
  }
  if (!report.stateSecretConfigured) {
    console.error('stateSecretConfigured=false — fix TOOL_SITE_STATE_SECRET first');
    process.exit(3);
  }

  const { FileSessionStore } = require(path.join(__dirname, '..', 'site', 'src', 'sessionStore'));
  const store = new FileSessionStore();
  const sid = crypto.randomBytes(24).toString('hex');
  const csrfToken = crypto.randomBytes(32).toString('hex');
  const sessionData = {
    cookie: {
      originalMaxAge: 7 * 24 * 60 * 60 * 1000,
      expires: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000),
      secure: false,
      httpOnly: true,
      path: '/',
    },
    csrfToken,
    user: {
      id: report.account.siteUserId,
      site_user_id: report.account.siteUserId,
      discord_user_id: report.account.discordUserId,
      discord_username: report.account.username,
      username: report.account.username,
    },
  };

  await new Promise((resolve, reject) => {
    store.set(sid, sessionData, (err) => (err ? reject(err) : resolve()));
  });

  const signedSid = signSid(sid);
  const cookie = `deng_sid=${encodeURIComponent(signedSid)}`;

  const startBody = new URLSearchParams({ _csrf: csrfToken }).toString();
  const startRes = await httpRequest('POST', '/api/key/start', { cookie, body: startBody });
  console.log('start', { status: startRes.status, body: startRes.body.slice(0, 200) });

  let challengeId = null;
  let csrf = csrfToken;
  try {
    const startJson = JSON.parse(startRes.body);
    challengeId = startJson.challenge_id || startJson.attempt_id || null;
  } catch {
    const challengeMatch = startRes.body.match(/name="challenge_id"\s+value="([^"]+)"/)
      || startRes.body.match(/data-challenge-id="([^"]+)"/);
    challengeId = challengeMatch ? challengeMatch[1] : null;
    const csrfMatch = startRes.body.match(/name="_csrf"\s+value="([^"]+)"/);
    csrf = csrfMatch ? csrfMatch[1] : csrfToken;
  }

  if (!challengeId) {
    console.error('Could not extract challenge_id from start response');
    console.error(startRes.body.slice(0, 500));
    process.exit(4);
  }

  const providerBody = new URLSearchParams({
    _csrf: csrf,
    challenge_id: challengeId,
    provider: 'linkvertise',
  }).toString();
  const providerRes = await httpRequest('POST', '/api/key/provider/linkvertise', {
    cookie,
    body: providerBody,
    headers: { Accept: 'application/json' },
  });

  console.log('provider', {
    status: providerRes.status,
    location: providerRes.headers.location || null,
    body: providerRes.body.slice(0, 300),
  });

  if (providerRes.status !== 200 && providerRes.status !== 303) {
    process.exit(5);
  }

  let redirectUrl = providerRes.headers.location || '';
  try {
    const parsed = JSON.parse(providerRes.body);
    redirectUrl = parsed.redirect_url || redirectUrl;
  } catch {
    // HTML redirect response
  }

  console.log(JSON.stringify({
    ok: true,
    serverCommit: report.serverCommit,
    account: report.account,
    createdAttemptId: challengeId,
    redirectUrlCreated: Boolean(redirectUrl),
    redirectHost: redirectUrl ? new URL(redirectUrl).hostname : null,
    message: 'Check PM2 deng-tool-site-out.log for [GENERATE_KEY_START] redirectUrlCreated=true',
  }, null, 2));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
