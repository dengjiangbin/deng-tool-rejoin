'use strict';
/**
 * DENG AIO — APK-specific backend API (additive, versioned under /api/aio/*).
 */

const express = require('express');
const fs = require('fs');
const path = require('path');

const aioSessionStore = require('./aioSessionStore');
const aioDatasetCache = require('./aioDatasetCache');
const { aioTimingMiddleware } = require('./aioRequestTiming');
const { buildDiscordAuthUrl } = require('./auth');
const { aioApkOAuthCallbackUri, internalApiBaseUrl } = require('./publicDomain');
const inventoryTrackedAccounts = require('./inventoryTrackedAccounts');
const fishitDb = require('./fishitDb');
const fishitTrackerRoutes = require('./fishitTrackerRoutes');
const { createUserRateLimit } = require('./rateLimitUtils');

const DATASETS = ['profile', 'dashboard', 'accounts', 'tracker', 'app'];

let cachedAppPayload = null;
let cachedAppPayloadAt = 0;
const APP_PAYLOAD_TTL_MS = 60 * 1000;

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

function aioRedirectUri() {
  return aioApkOAuthCallbackUri();
}

function buildAioAuthUrl(req) {
  return buildDiscordAuthUrl(req, {
    authReturnTo: '/tracker',
    returnPublicUrl: publicBaseUrl(),
    oauthApkReturn: true,
    callbackUri: aioRedirectUri(),
  });
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

// OAuth callback is handled by oauthRoutes (shared handler at /api/aio/auth/callback).

router.post('/api/aio/auth/exchange', aioAuthLimiter, (req, res) => {
  const body = req.body || {};
  const code = typeof body.code === 'string' ? body.code.trim() : '';
  if (!code) {
    console.warn('[aio] APK_AUTH_FAIL_STAGE=exchange_missing_code');
    return res.status(400).json({ ok: false, error: 'missing_code' });
  }
  console.log('[aio] APK_AUTH_EXCHANGE_RECEIVED codeLen=%d', code.length);
  const user = aioSessionStore.consumeLoginCode(code);
  if (!user || !user.discordUserId) {
    console.warn('[aio] APK_AUTH_FAIL_STAGE=exchange_invalid_or_expired codeLen=%d', code.length);
    return res.status(401).json({ ok: false, error: 'invalid_or_expired_code' });
  }
  const session = aioSessionStore.createSession(user, body.device_name || body.deviceName);
  console.log('[aio] APK_AUTH_EXCHANGE_SUCCESS discordUserId=%s', user.discordUserId);
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
    console.log('[aio] APK_AUTH_WEB_BOOTSTRAP_CREATED discordUserId=%s', user.discordUserId);
    return res.json({
      ok: true,
      bridgeUrl: `${publicBase}/auth/web-bridge?code=${encodeURIComponent(code)}&return=${encodeURIComponent('/tracker?apk=1')}&apk=1`,
      expiresInSeconds,
      handoffMarker: 'APK_DISCORD_AUTH_HANDOFF_COMPLETION_FIX_2026_06_14',
    });
  } catch (err) {
    console.error('[aio] APK_AUTH_FAIL_STAGE=bootstrap_failed error=%s', err && err.message ? err.message : err);
    return res.status(500).json({ ok: false, error: 'bootstrap_failed' });
  }
});

/** Cookie-session probe for APK WebView after web-bridge (no bearer token). */
router.get('/api/aio/auth/web-session', (req, res) => {
  const sessionUser = req.session && req.session.user ? req.session.user : null;
  if (sessionUser) {
    console.log(
      '[aio] APK_AUTH_WEB_SESSION_OK discordUserId=%s',
      sessionUser.discord_user_id || req.session?.discord_user_id || 'unknown',
    );
  }
  res.set('Cache-Control', 'no-store');
  return res.json({
    ok: true,
    authenticated: !!sessionUser,
    discordUserId: sessionUser?.discord_user_id || req.session?.discord_user_id || null,
    username: sessionUser?.username || null,
    handoffMarker: 'APK_DISCORD_AUTH_HANDOFF_COMPLETION_FIX_2026_06_14',
  });
});

router.post('/api/aio/auth/apk-open-attempt', aioAuthLimiter, (_req, res) => {
  console.log('[aio] APK_AUTH_APP_OPEN_ATTEMPT client=custom_tabs');
  res.set('Cache-Control', 'no-store');
  return res.status(204).end();
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
