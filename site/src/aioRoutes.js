'use strict';
/**
 * DENG AIO — APK-specific backend API (additive, versioned under /api/aio/*).
 */

const express = require('express');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const axios = require('axios');

const aioSessionStore = require('./aioSessionStore');
const aioDatasetCache = require('./aioDatasetCache');
const { aioTimingMiddleware } = require('./aioRequestTiming');
const { fetchDiscordUser, upsertDiscordUser, toSessionUser } = require('./auth');
const inventoryTrackedAccounts = require('./inventoryTrackedAccounts');
const fishitDb = require('./fishitDb');
const fishitTrackerRoutes = require('./fishitTrackerRoutes');
const { createUserRateLimit } = require('./rateLimitUtils');

const DISCORD_API = 'https://discord.com/api/v10';
const SCOPES = 'identify';

const DATASETS = ['profile', 'dashboard', 'accounts', 'tracker', 'app'];

let cachedAppPayload = null;
let cachedAppPayloadAt = 0;
const APP_PAYLOAD_TTL_MS = 60 * 1000;

const { internalApiBaseUrl } = require('./publicDomain');

function publicBaseUrl() {
  return (process.env.TOOL_SITE_PUBLIC_URL || 'https://aio.deng.my.id').replace(/\/+$/, '');
}

function appScheme() {
  return (process.env.DENG_AIO_APP_SCHEME || 'deng-aio').trim();
}

function loadApkManifest() {
  const file = process.env.DENG_AIO_RELEASE_MANIFEST
    || path.join(__dirname, '..', '..', 'releases', 'android', 'latest.json');
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

function latestAppPayload() {
  const now = Date.now();
  if (cachedAppPayload && (now - cachedAppPayloadAt) < APP_PAYLOAD_TTL_MS) {
    return cachedAppPayload;
  }
  const m = loadApkManifest();
  const base = publicBaseUrl();
  if (!m) {
    cachedAppPayload = {
      available: false,
      versionName: null,
      versionCode: null,
      apkUrl: `${base}/downloads/deng-all-in-one-apk-latest.apk`,
      sha256: null,
      sizeBytes: null,
      changelog: [],
      forceUpdate: false,
      minSupportedVersionCode: 0,
    };
  } else {
    const fileName = m.file_name || null;
    cachedAppPayload = {
      available: true,
      versionName: m.version_name || null,
      versionCode: m.version_code != null ? Number(m.version_code) : null,
      fileName,
      apkUrl: `${base}/downloads/deng-all-in-one-apk-latest.apk`,
      versionedApkUrl: fileName ? `${base}/downloads/${fileName}` : null,
      sha256: m.sha256 || null,
      sizeBytes: m.size_bytes != null ? Number(m.size_bytes) : null,
      releasedAt: m.released_at || null,
      changelog: Array.isArray(m.changelog) ? m.changelog : [],
      forceUpdate: m.force_update === true,
      minSupportedVersionCode: m.min_supported_version_code != null
        ? Number(m.min_supported_version_code)
        : 0,
    };
  }
  cachedAppPayloadAt = now;
  return cachedAppPayload;
}

function buildProfileDataset(user) {
  return {
    discordUserId: user.discordUserId || null,
    username: user.username || null,
    avatar: user.avatar || null,
    siteUserId: user.siteUserId || null,
  };
}

async function buildAccountsDataset(discordUserId) {
  const rows = await inventoryTrackedAccounts.listTrackedAccounts(discordUserId);
  return {
    accounts: (rows || []).map((r) => {
      const username = r.robloxUsername || r.roblox_username || r.username || null;
      const usernameKey = r.robloxUsernameKey || r.roblox_username_key
        || (username ? String(username).toLowerCase() : null);
      return {
        username,
        usernameKey,
        displayName: r.displayName || r.display_name || username,
        sortIndex: r.sortIndex != null ? r.sortIndex : (r.sort_index != null ? r.sort_index : null),
        lastSeenAt: r.lastSeenAt || r.last_seen_at || null,
        lastInventorySyncAt: r.lastInventorySyncAt || r.last_inventory_sync_at || null,
        createdAt: r.createdAt || r.created_at || null,
      };
    }).filter((a) => a.username),
  };
}

function buildDashboardDataset(discordUserId, trackedAccounts, period) {
  const data = fishitDb.getOwnerDashboard(discordUserId, trackedAccounts, period || 'all', {
    queryStartedAt: Date.now(),
  });
  if (data && data.debug) delete data.debug;
  return data;
}

async function buildDatasetData(name, ctx) {
  switch (name) {
    case 'profile':
      return buildProfileDataset(ctx.user);
    case 'accounts':
      return buildAccountsDataset(ctx.user.discordUserId);
    case 'dashboard': {
      const accounts = ctx.trackedAccounts
        || (await inventoryTrackedAccounts.listTrackedAccounts(ctx.user.discordUserId));
      return buildDashboardDataset(ctx.user.discordUserId, accounts, ctx.period);
    }
    case 'tracker':
      return fishitTrackerRoutes.buildAioTrackerDataset(ctx.user.discordUserId, {
        baseUrl: publicBaseUrl(),
        fast: true,
      });
    case 'app':
      return latestAppPayload();
    default:
      return null;
  }
}

function datasetBuilder(name, ctx) {
  return () => buildDatasetData(name, ctx);
}

function warmUserDatasets(ctx) {
  const builders = {};
  for (const name of DATASETS) {
    builders[name] = datasetBuilder(name, ctx);
  }
  aioDatasetCache.warmAll(ctx.user.discordUserId, builders);
}

function extractBearer(req) {
  const h = req.headers.authorization || '';
  const m = /^Bearer\s+(.+)$/i.exec(typeof h === 'string' ? h : '');
  return m ? m[1].trim() : null;
}

function requireAioAuth(req, res, next) {
  const token = extractBearer(req);
  if (!token) {
    return res.status(401).json({ ok: false, error: 'auth_required', message: 'Sign in with Discord.' });
  }
  const session = aioSessionStore.resolveSession(token);
  if (!session || !session.discordUserId) {
    return res.status(401).json({ ok: false, error: 'invalid_session', message: 'Session expired. Sign in again.' });
  }
  req.aioUser = session;
  req.aioToken = token;
  return next();
}

function buildAioAuthUrl(req) {
  const clientId = process.env.DISCORD_CLIENT_ID || '';
  if (!clientId || !process.env.DISCORD_CLIENT_SECRET) {
    throw new Error('Discord OAuth is not configured');
  }
  const state = crypto.randomBytes(24).toString('hex');
  req.session.aioOauthState = state;
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: aioRedirectUri(),
    response_type: 'code',
    scope: SCOPES,
    state,
    prompt: 'consent',
  });
  return `${DISCORD_API}/oauth2/authorize?${params}`;
}

async function exchangeAioCode(code) {
  const params = new URLSearchParams({
    client_id: process.env.DISCORD_CLIENT_ID || '',
    client_secret: process.env.DISCORD_CLIENT_SECRET || '',
    grant_type: 'authorization_code',
    code,
    redirect_uri: aioRedirectUri(),
  });
  const { data } = await axios.post(
    `${DISCORD_API}/oauth2/token`,
    params.toString(),
    { headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, timeout: 8000 },
  );
  return data;
}

function renderDeepLinkHtml(deepLink) {
  const safe = String(deepLink).replace(/"/g, '&quot;');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="0; url=${safe}">
<title>Returning to DENG AIO…</title>
<style>body{font-family:system-ui,Segoe UI,Roboto,sans-serif;background:#050816;color:#e8ecf8;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;text-align:center;padding:24px}
a{color:#7aa2ff;font-weight:600}</style></head>
<body><div><h2>Signing you in…</h2>
<p>If DENG AIO does not open automatically, <a href="${safe}">tap here to return to the app</a>.</p>
</div><script>location.replace(${JSON.stringify(deepLink)});</script></body></html>`;
}

const router = express.Router();
router.use(express.json({ limit: '32kb' }));
router.use(express.urlencoded({ extended: false, limit: '32kb' }));
router.use(aioTimingMiddleware);

const aioAuthLimiter = createUserRateLimit({
  keyPrefix: 'aio-auth',
  windowMs: 60_000,
  max: 30,
});
const aioSyncLimiter = createUserRateLimit({
  keyPrefix: 'aio-sync',
  windowMs: 60_000,
  max: 240,
});

router.get('/api/aio/auth/start', aioAuthLimiter, (req, res) => {
  try {
    return res.redirect(buildAioAuthUrl(req));
  } catch (err) {
    console.error('[aio] auth start failed:', err && err.message ? err.message : err);
    return res.status(500).send('Discord sign-in is not configured.');
  }
});

router.get('/api/aio/auth/callback', aioAuthLimiter, async (req, res) => {
  const { code, state, error } = req.query;
  const scheme = appScheme();
  if (error) {
    return res.redirect(`${scheme}://auth/callback?error=${encodeURIComponent(String(error))}`);
  }
  const expected = req.session && req.session.aioOauthState;
  if (req.session) delete req.session.aioOauthState;
  if (!code || !state || !expected || String(state) !== String(expected)) {
    return res.redirect(`${scheme}://auth/callback?error=invalid_state`);
  }
  try {
    const tokens = await exchangeAioCode(String(code));
    const discordUser = await fetchDiscordUser(tokens.access_token);
    // Portal DB upsert must not block the deep-link redirect.
    upsertDiscordUser(discordUser, tokens).catch(() => null);
    const { code: loginCode } = aioSessionStore.createLoginCode({
      discordUserId: discordUser.id,
      siteUserId: null,
      username: discordUser.username || null,
      avatar: discordUser.avatar || null,
    });
    const deepLink = `${scheme}://auth/callback?code=${encodeURIComponent(loginCode)}`;
    res.set('Content-Type', 'text/html; charset=utf-8');
    return res.status(200).send(renderDeepLinkHtml(deepLink));
  } catch (err) {
    console.error('[aio] auth callback failed:', err && err.message ? err.message : err);
    return res.redirect(`${scheme}://auth/callback?error=auth_failed`);
  }
});

router.post('/api/aio/auth/exchange', aioAuthLimiter, (req, res) => {
  const body = req.body || {};
  const code = typeof body.code === 'string' ? body.code.trim() : '';
  if (!code) return res.status(400).json({ ok: false, error: 'missing_code' });
  const user = aioSessionStore.consumeLoginCode(code);
  if (!user || !user.discordUserId) {
    return res.status(401).json({ ok: false, error: 'invalid_or_expired_code' });
  }
  const session = aioSessionStore.createSession(user, body.device_name || body.deviceName);
  return res.json({
    ok: true,
    appSessionToken: session.token,
    expiresAt: session.expiresAt,
    user: {
      discordUserId: user.discordUserId,
      username: user.username || null,
      avatar: user.avatar || null,
    },
    bootstrapRequired: true,
  });
});

/** Mint a one-time web cookie bridge code for APK WebViews after bearer auth. */
router.post('/api/aio/auth/web-bootstrap', requireAioAuth, aioAuthLimiter, (req, res) => {
  const user = req.aioUser;
  try {
    const { code, expiresInSeconds } = aioSessionStore.createLoginCode({
      discordUserId: user.discordUserId,
      siteUserId: user.siteUserId || null,
      username: user.username || null,
      avatar: user.avatar || null,
    });
    const publicBase = publicBaseUrl();
    return res.json({
      ok: true,
      bridgeUrl: `${publicBase}/auth/web-bridge?code=${encodeURIComponent(code)}&return=${encodeURIComponent('/tracker?apk=1')}`,
      expiresInSeconds,
    });
  } catch (err) {
    console.error('[aio] web-bootstrap failed:', err && err.message ? err.message : err);
    return res.status(500).json({ ok: false, error: 'bootstrap_failed' });
  }
});

router.post('/api/aio/auth/logout', (req, res) => {
  const token = extractBearer(req);
  const revoked = token ? aioSessionStore.revokeSession(token) : false;
  return res.json({ ok: true, revoked });
});

router.get('/api/aio/me', requireAioAuth, (req, res) => {
  return res.json({
    ok: true,
    user: {
      discordUserId: req.aioUser.discordUserId,
      username: req.aioUser.username || null,
      avatar: req.aioUser.avatar || null,
    },
    expiresAt: req.aioUser.expiresAt || null,
  });
});

router.get('/api/aio/app/latest', (_req, res) => {
  return res.json({ ok: true, ...latestAppPayload() });
});

// Metadata only — never rebuilds heavy datasets inline.
router.get('/api/aio/bootstrap', aioSyncLimiter, requireAioAuth, async (req, res) => {
  try {
    const discordUserId = req.aioUser.discordUserId;
    const ctx = { user: req.aioUser, period: 'all' };
    const datasets = {};
    for (const name of DATASETS) {
      datasets[name] = aioDatasetCache.getMeta(discordUserId, name);
    }
    warmUserDatasets(ctx);
    return res.json({
      ok: true,
      profile: buildProfileDataset(req.aioUser),
      permissions: { tracker: true, dashboard: true, rejoin: true, packages: true },
      datasets,
      requiredDatasets: DATASETS,
      app: latestAppPayload(),
      serverNow: new Date().toISOString(),
    });
  } catch (err) {
    console.error('[aio] bootstrap failed:', err && err.message ? err.message : err);
    return res.status(500).json({ ok: false, error: 'bootstrap_failed' });
  }
});

router.get('/api/aio/sync/manifest', aioSyncLimiter, requireAioAuth, async (req, res) => {
  try {
    const discordUserId = req.aioUser.discordUserId;
    const ctx = { user: req.aioUser, period: 'all' };
    const datasets = DATASETS.map((name) => ({
      name,
      ...aioDatasetCache.getMeta(discordUserId, name),
    }));
    warmUserDatasets(ctx);
    return res.json({ ok: true, datasets, serverNow: new Date().toISOString() });
  } catch (err) {
    console.error('[aio] manifest failed:', err && err.message ? err.message : err);
    return res.status(500).json({ ok: false, error: 'manifest_failed' });
  }
});

router.get('/api/aio/sync/full', aioSyncLimiter, requireAioAuth, async (req, res) => {
  const dataset = String(req.query.dataset || '').trim();
  if (!DATASETS.includes(dataset)) {
    return res.status(400).json({ ok: false, error: 'unknown_dataset' });
  }
  try {
    const ctx = {
      user: req.aioUser,
      period: req.query.period ? String(req.query.period) : 'all',
    };
    const built = await aioDatasetCache.getOrBuild(
      req.aioUser.discordUserId,
      dataset,
      datasetBuilder(dataset, ctx),
    );
    return res.json({
      ok: true,
      dataset,
      version: built.version,
      cursor: built.version,
      lastUpdatedAt: built.lastUpdatedAt,
      data: built.data,
    });
  } catch (err) {
    console.error('[aio] sync full failed:', err && err.message ? err.message : err);
    return res.status(500).json({ ok: false, error: 'sync_full_failed' });
  }
});

router.get('/api/aio/sync/delta', aioSyncLimiter, requireAioAuth, async (req, res) => {
  const dataset = String(req.query.dataset || '').trim();
  if (!DATASETS.includes(dataset)) {
    return res.status(400).json({ ok: false, error: 'unknown_dataset' });
  }
  const since = req.query.since ? String(req.query.since) : null;
  try {
    const ctx = {
      user: req.aioUser,
      period: req.query.period ? String(req.query.period) : 'all',
    };
    const cached = aioDatasetCache.getBuilt(req.aioUser.discordUserId, dataset);
    if (cached && since && since === cached.version) {
      aioDatasetCache.scheduleBuild(
        req.aioUser.discordUserId,
        dataset,
        datasetBuilder(dataset, ctx),
      );
      return res.json({ ok: true, dataset, changed: false, cursor: cached.version });
    }
    const built = await aioDatasetCache.getOrBuild(
      req.aioUser.discordUserId,
      dataset,
      datasetBuilder(dataset, ctx),
    );
    if (since && since === built.version) {
      return res.json({ ok: true, dataset, changed: false, cursor: built.version });
    }
    return res.json({
      ok: true,
      dataset,
      changed: true,
      cursor: built.version,
      lastUpdatedAt: built.lastUpdatedAt,
      data: built.data,
    });
  } catch (err) {
    console.error('[aio] sync delta failed:', err && err.message ? err.message : err);
    return res.status(500).json({ ok: false, error: 'sync_delta_failed' });
  }
});

router.post('/api/aio/sync/ack', aioSyncLimiter, requireAioAuth, (req, res) => {
  const body = req.body || {};
  const dataset = String(body.dataset || '').trim();
  const cursor = body.cursor != null ? String(body.cursor) : null;
  if (!DATASETS.includes(dataset)) {
    return res.status(400).json({ ok: false, error: 'unknown_dataset' });
  }
  aioSessionStore.setAck(req.aioUser.discordUserId, dataset, cursor);
  return res.json({ ok: true, dataset, cursor });
});

module.exports = router;
module.exports.requireAioAuth = requireAioAuth;
module.exports.DATASETS = DATASETS;
module.exports.latestAppPayload = latestAppPayload;
module.exports.buildDatasetData = buildDatasetData;
