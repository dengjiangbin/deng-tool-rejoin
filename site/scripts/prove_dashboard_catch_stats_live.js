'use strict';

/**
 * Live proof: GET /api/tracker/dashboard reads Secret/Forgotten catch stats
 * from DENG Fish It bot DB (same source as Discord !d / !s).
 */

const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { sign } = require('cookie-signature');
const dotenv = require('dotenv');
const { FileSessionStore } = require('../src/sessionStore');

const ROOT = path.join(__dirname, '..', '..');
dotenv.config({ path: path.join(__dirname, '..', '.env') });
dotenv.config({ path: path.join(ROOT, '.env') });
if (process.env.NODE_ENV === 'production') {
  dotenv.config({ path: path.join(ROOT, '.env'), override: true });
}

const HOST = process.env.PROVE_HOST || '127.0.0.1';
const PORT = Number(process.env.PROVE_PORT || process.env.TOOL_SITE_PORT || 8791);
const LOCAL_BASE = process.env.PROVE_BASE_URL || `http://${HOST}:${PORT}`;
const SECRET = process.env.TOOL_SITE_COOKIE_SECRET || '';
const SESSION_DIR = process.env.TOOL_SITE_SESSION_DIR
  || path.join(ROOT, 'data', 'site-sessions');
const DISCORD_ID = process.env.PROVE_DISCORD_ID || '915851106280681492';
const OUT_PATH = process.env.PROVE_OUT
  || path.join(__dirname, '..', 'proofs', 'dashboard_catch_stats_live_proof.json');

function requestRaw(url, options) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const req = http.request({
      hostname: parsed.hostname,
      port: parsed.port || 80,
      path: parsed.pathname + parsed.search,
      method: options.method || 'GET',
      headers: {
        Accept: 'application/json',
        Cookie: options.cookie || '',
      },
    }, (res) => {
      let text = '';
      res.on('data', (chunk) => { text += chunk; });
      res.on('end', () => {
        resolve({ status: res.statusCode, text });
      });
    });
    req.on('error', reject);
    req.end();
  });
}

async function createSignedSession() {
  const sid = crypto.randomBytes(24).toString('hex');
  const session = {
    cookie: {
      originalMaxAge: 604800000,
      expires: new Date(Date.now() + 604800000).toISOString(),
      secure: true,
      httpOnly: true,
      path: '/',
      sameSite: 'lax',
    },
    user: {
      id: 'proof-dashboard-user',
      site_user_id: 'proof-dashboard-user',
      username: 'neptune_75',
      discord_user_id: DISCORD_ID,
      discord_username: 'neptune_75',
    },
    site_user_id: 'proof-dashboard-user',
    discord_user_id: DISCORD_ID,
    csrfToken: crypto.randomBytes(32).toString('hex'),
  };
  const store = new FileSessionStore({ dir: SESSION_DIR });
  await new Promise((resolve, reject) => {
    store.set(sid, session, (err) => (err ? reject(err) : resolve()));
  });
  return `deng_sid=s:${sign(sid, SECRET)}`;
}

async function main() {
  if (!SECRET || SECRET.length < 32) {
    throw new Error('TOOL_SITE_COOKIE_SECRET missing or too short');
  }

  const fishitDbPath = process.env.FISHIT_DB_PATH
    || path.join(ROOT, '..', 'DENG Fish It', 'data', 'deng-fish-it.sqlite');
  process.env.FISHIT_DB_PATH = fishitDbPath;

  const fishitDb = require('../src/fishitDb');
  fishitDb._resetCache();
  const direct = fishitDb.getOwnerDashboard(DISCORD_ID, [], 'all', {
    authDiscordUsername: 'neptune_75',
  });

  const cookie = await createSignedSession();
  const apiRes = await requestRaw(
    `${LOCAL_BASE}/api/tracker/dashboard?period=all&debug=1`,
    { cookie },
  );
  let apiBody = null;
  try { apiBody = JSON.parse(apiRes.text); } catch (_) { /* ignore */ }

  const pageRes = await requestRaw(`${LOCAL_BASE}/tracker`, { cookie });
  const pageText = pageRes.text || '';
  const manifest = require('../src/inventoryAssetManifest.json');

  const proof = {
    at: new Date().toISOString(),
    localBase: LOCAL_BASE,
    discordUserId: DISCORD_ID,
    fishitDbPath,
    dbStatus: fishitDb.getDbConnectionInfo ? fishitDb.getDbConnectionInfo() : null,
    direct: {
      available: direct.available,
      statsState: direct.statsState,
      emptyReason: direct.emptyReason,
      secretCaught: direct.cards && direct.cards.secretCaught,
      forgottenCaught: direct.cards && direct.cards.forgottenCaught,
      fishCardCount: Array.isArray(direct.fishCards) ? direct.fishCards.length : 0,
      dailyWithData: Array.isArray(direct.dailyCaught)
        ? direct.dailyCaught.filter((row) => Number(row.totalCaught) > 0).length
        : 0,
      identityMatchMode: direct.debug && direct.debug.identityMatchMode,
    },
    api: {
      status: apiRes.status,
      ok: apiBody && apiBody.ok,
      statsState: apiBody && apiBody.statsState,
      available: apiBody && apiBody.available,
      emptyReason: apiBody && apiBody.emptyReason,
      secretCaught: apiBody && apiBody.cards && apiBody.cards.secretCaught,
      forgottenCaught: apiBody && apiBody.cards && apiBody.cards.forgottenCaught,
      fishCardCount: apiBody && Array.isArray(apiBody.fishCards) ? apiBody.fishCards.length : 0,
      dailyWithData: apiBody && Array.isArray(apiBody.dailyCaught)
        ? apiBody.dailyCaught.filter((row) => Number(row.totalCaught) > 0).length
        : 0,
      debugDbPath: apiBody && apiBody.debug && apiBody.debug.botDbPath,
    },
    page: {
      status: pageRes.status,
      deployMarker: manifest.marker,
      hasNewJs: pageText.includes(manifest.js),
      hasDashboardStatusNotice: pageText.includes('dashboardStatusNotice'),
    },
    pass: !!(
      direct.statsState === 'ok'
      && direct.available === true
      && (direct.cards.secretCaught > 0 || direct.cards.forgottenCaught > 0)
      && apiRes.status === 200
      && apiBody
      && apiBody.statsState === 'ok'
      && apiBody.available === true
      && pageText.includes(manifest.js)
    ),
  };

  fs.mkdirSync(path.dirname(OUT_PATH), { recursive: true });
  fs.writeFileSync(OUT_PATH, `${JSON.stringify(proof, null, 2)}\n`);
  console.log(JSON.stringify(proof, null, 2));
  if (!proof.pass) process.exitCode = 1;
}

main().catch((err) => {
  console.error('[prove_dashboard_catch_stats_live] failed:', err && err.message ? err.message : err);
  process.exit(1);
});
