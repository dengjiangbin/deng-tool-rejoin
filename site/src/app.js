'use strict';
const express      = require('express');
const compression  = require('compression');
const helmet       = require('helmet');
const session      = require('express-session');
const ejsLayouts   = require('express-ejs-layouts');
const path         = require('path');
const fs           = require('fs');

const routes = require('./routes');
const publicRoutes = require('./publicRoutes');
const monitorRoutes = require('./monitorRoutes');
const fishitRoutes = require('./fishitRoutes');
const fishitTrackerRoutes = require('./fishitTrackerRoutes');
const fishitGlobalAdminRoutes = require('./fishitGlobalAdminRoutes');
const aioRoutes = require('./aioRoutes');
const { FileSessionStore } = require('./sessionStore');
const { resolveTrustProxySetting } = require('./rateLimitUtils');
const {
  canonicalPublicUrl,
  legacyPublicPageRedirectMiddleware,
  buildCanonicalPageUrl,
  isSessionlessPath,
} = require('./publicDomain');
const packageJson = require('../package.json');

const app = express();
app.disable('x-powered-by');
app.use(compression());

function latestAssetStamp() {
  const publicDir = path.join(__dirname, '..', 'public');
  const files = [
    path.join(publicDir, 'css', 'style.css'),
    path.join(publicDir, 'css', 'public-theme.css'),
    path.join(publicDir, 'css', 'home.css'),
    path.join(publicDir, 'css', 'login-page.css'),
    path.join(publicDir, 'js', 'app.js'),
    path.join(publicDir, 'js', 'count-up-stats.js'),
    path.join(publicDir, 'js', 'home.js'),
    path.join(publicDir, 'js', 'login-page.js'),
    path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'),
  ];
  let newest = 0;
  files.forEach((file) => {
    try {
      newest = Math.max(newest, Math.floor(fs.statSync(file).mtimeMs));
    } catch {
      // Missing optional assets should not block server startup.
    }
  });
  return newest || Date.now();
}

const assetVersion = [
  process.env.TOOL_SITE_ASSET_VERSION,
  process.env.GIT_COMMIT,
  packageJson.version,
  latestAssetStamp(),
].filter(Boolean).join('-').replace(/[^A-Za-z0-9._-]/g, '');

app.locals.assetVersion = assetVersion;
app.locals.flash = {};
app.locals.csrfToken = '';
app.locals.user = null;
app.locals.publicUrl = canonicalPublicUrl();

// ---------------------------------------------------------------
// Security headers (helmet)
// ---------------------------------------------------------------
app.use(helmet({
  contentSecurityPolicy: {
    directives: {
      defaultSrc: ["'self'"],
      scriptSrc: [
        "'self'",
        "'unsafe-inline'",          // needed for small inline scripts in EJS
        'https://publisher.linkvertise.com', // Linkvertise Full Script provider
      ],
      styleSrc:  ["'self'", "'unsafe-inline'"],
      imgSrc:    [
        "'self'",
        'data:',
        'https://cdn.discordapp.com',
        'https://media.discordapp.net',
        'https://rbxcdn.com',
        'https://tr.rbxcdn.com',
        'https://*.rbxcdn.com',
        'https://thumbnails.roblox.com',
        'https://*.roblox.com',
      ],
      connectSrc:["'self'"],
      frameSrc:  ["'none'"],
      objectSrc: ["'none'"],
      baseUri:   ["'self'"],
      // form-action also restricts redirects that follow a form submission.
      // The provider POST returns a 303 to the ad provider host, so the
      // provider domains MUST be allow-listed here or the browser will
      // silently block the redirect (CSP report only, no visible error).
      formAction: [
        "'self'",
        'https://link-hub.net',
        'https://linkvertise.com',
        'https://*.linkvertise.com',
        'https://lootdest.org',
        'https://*.lootlabs.gg',
      ],
      frameAncestors: ["'none'"],
      upgradeInsecureRequests: process.env.NODE_ENV === 'production' ? [] : [],
    },
  },
  hsts: process.env.NODE_ENV === 'production'
    ? { maxAge: 31536000, includeSubDomains: true }
    : false,
}));

// ---------------------------------------------------------------
// Trust proxy (Cloudflare / nginx / PM2 reverse proxy)
// ---------------------------------------------------------------
app.set('trust proxy', resolveTrustProxySetting());

// Redirect safe public website pages from legacy tool host to canonical aio host.
app.use(legacyPublicPageRedirectMiddleware);

// Fast health probe — before session/tracker routers so proxies never queue behind uploads.
app.get('/health', (_req, res) => {
  res.set('Cache-Control', 'no-store');
  res.json({
    status: 'ok',
    service: 'deng-tool-site',
    port: parseInt(process.env.TOOL_SITE_PORT || '8791', 10),
    timestamp: new Date().toISOString(),
  });
});

// ---------------------------------------------------------------
// Session (HttpOnly, Secure in prod, SameSite=Lax)
// Mounted before Fish It tracker routes so /inventory can read user profile.
// Skipped for high-volume tracker upload APIs (no session file I/O per POST).
// ---------------------------------------------------------------
const sessionSecret = process.env.TOOL_SITE_COOKIE_SECRET;
if (!sessionSecret || sessionSecret.length < 32) {
  throw new Error('TOOL_SITE_COOKIE_SECRET must be at least 32 characters');
}

const sessionMiddleware = session({
  name: 'deng_sid',
  store: new FileSessionStore({
    dir: process.env.TOOL_SITE_SESSION_DIR,
    ttlMs: 7 * 24 * 60 * 60 * 1000,
  }),
  secret: sessionSecret,
  resave: false,
  saveUninitialized: false,
  cookie: {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 7 * 24 * 60 * 60 * 1000, // 7 days
    ...(process.env.NODE_ENV === 'production' ? { domain: '.deng.my.id' } : {}),
  },
});

app.use((req, res, next) => {
  if (isSessionlessPath(req.path)) return next();
  return sessionMiddleware(req, res, next);
});

// Discord OAuth — after session, before tracker routers (avoids upload congestion).
app.use('/', require('./oauthRoutes'));

app.use((req, _res, next) => {
  if (isSessionlessPath(req.path) || !req.session) return next();
  if (!req.session.csrfToken) {
    req.session.csrfToken = require('crypto').randomBytes(32).toString('hex');
  }
  next();
});

// ---------------------------------------------------------------
// Fish It Live Backpack Tracker (mounted BEFORE the global body
// parsers so that the route-level express.json({ limit: '512kb' })
// handlers inside fishitTrackerRoutes take precedence.  The global
// 16 KB parser would otherwise reject large tracker payloads with a
// 413 before the route is even matched.)
// ---------------------------------------------------------------
app.use('/', fishitTrackerRoutes);
app.use('/', fishitGlobalAdminRoutes);
const fishitInventoryManualImageAdminRoutes = require('./fishitInventoryManualImageAdminRoutes');
app.use('/', fishitInventoryManualImageAdminRoutes);

// ---------------------------------------------------------------
// Body parsers
// ---------------------------------------------------------------
app.use(express.urlencoded({ extended: false, limit: '16kb' }));
app.use(express.json({ limit: '16kb' }));

// ---------------------------------------------------------------
// Template engine: EJS + layouts
// ---------------------------------------------------------------
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, '..', 'views'));
// Never cache EJS in production — stale tracker UI survived PM2 restarts when port was held by a zombie process.
app.set('view cache', false);
app.use(ejsLayouts);
app.set('layout', 'layout');
app.set('layout extractScripts', true);

// ---------------------------------------------------------------
// Static files
// ---------------------------------------------------------------
app.use('/public', express.static(path.join(__dirname, '..', 'public'), {
  maxAge: process.env.NODE_ENV === 'production' ? '1d' : 0,
  setHeaders: (res, filePath) => {
    if (/[\\/]assets[\\/][^\\/]+\.[a-f0-9]{8,}\.(css|js)$/i.test(filePath)) {
      res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
    } else if (/[\\/]css[\\/]home\.css$/i.test(filePath)) {
      // Homepage skin changes often; avoid long CDN/browser retention of stale ?v= URLs.
      res.setHeader('Cache-Control', 'public, max-age=300, must-revalidate');
    } else if (/[\\/]css[\\/]login-page\.css$/i.test(filePath)) {
      res.setHeader('Cache-Control', 'public, max-age=300, must-revalidate');
    } else if (/[\\/](css|js|images)[\\/][^\\/]+\.(css|js|png|jpg|jpeg|webp|gif|svg|ico)$/i.test(filePath)) {
      res.setHeader('Cache-Control', 'public, max-age=86400');
    }
  },
}));
app.use('/assets', express.static(path.join(__dirname, '..', 'public'), {
  maxAge: process.env.NODE_ENV === 'production' ? '1d' : 0,
}));
app.use('/images', express.static(path.join(__dirname, '..', 'public', 'images'), {
  maxAge: process.env.NODE_ENV === 'production' ? '7d' : 0,
  setHeaders: (res, filePath) => {
    if (/\.(png|jpg|jpeg|webp|gif|svg|ico)$/i.test(filePath)) {
      res.setHeader('Cache-Control', 'public, max-age=604800');
    }
  },
}));

// ---------------------------------------------------------------
// Flash-message helper (lightweight, no extra dep)
// ---------------------------------------------------------------
app.use((req, res, next) => {
  const sess = req.session || {};
  res.locals.flash = sess.flash || {};
  res.locals.csrfToken = sess.csrfToken || '';
  res.locals.user = sess.user || null;
  res.locals.publicUrl = canonicalPublicUrl();
  res.locals.canonicalUrl = buildCanonicalPageUrl(req);
  res.locals.assetVersion = assetVersion;
  if (req.session) delete req.session.flash;
  next();
});

// ---------------------------------------------------------------
// Mount monitor routes BEFORE main routes so their dedicated body
// parsers (32KB JSON, 1.5MB raw image) take precedence over the
// 16KB global parsers configured above.
// ---------------------------------------------------------------
app.use('/', monitorRoutes);

// DENG AIO APK API (update manifest, optional OAuth/sync for future native paths).
app.use('/', aioRoutes);

// Fish It stats API (public global + authenticated /me/* routes).
app.use('/', fishitRoutes);

// (fishitTrackerRoutes already mounted before body parsers above)

// ---------------------------------------------------------------
// Public landing + login (after EJS layouts; before protected routes)
// ---------------------------------------------------------------
app.use('/', publicRoutes);

// ---------------------------------------------------------------
// Mount routes
// ---------------------------------------------------------------
app.use('/', routes);

// ---------------------------------------------------------------
// 404 handler
// ---------------------------------------------------------------
app.use((_req, res) => {
  res.status(404).render('error', { code: 404, message: 'Page not found.' });
});

// ---------------------------------------------------------------
// Global error handler
// ---------------------------------------------------------------
// eslint-disable-next-line no-unused-vars
app.use((err, req, res, _next) => {
  if (err && (err.code === 'EBUSY' || err.code === 'EPERM' || err.code === 'EACCES')) {
    console.warn('[deng-tool-site] Recoverable filesystem error:', err.code, req.path);
    if (req.path.startsWith('/api/')) {
      return res.status(503).json({ ok: false, error: 'temporarily_busy' });
    }
    return res.status(503).render('error', { code: 503, message: 'Server is busy. Please retry.' });
  }
  console.error('[deng-tool-site] Unhandled error:', err);
  // Return JSON for API routes (e.g. PayloadTooLargeError from body parsers)
  // so Lua clients receive a parseable error body, not an HTML page.
  if (err.type === 'entity.too.large' && req.path.startsWith('/api/')) {
    return res.status(413).json({
      ok: false,
      error: 'payload_too_large',
      limit: err.limit || '16kb',
      message: 'Request body exceeds the allowed size limit.',
    });
  }
  const code = err.status || 500;
  res.status(code).render('error', { code, message: 'An unexpected error occurred.' });
});

module.exports = app;
