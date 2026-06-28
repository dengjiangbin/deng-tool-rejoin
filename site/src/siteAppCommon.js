'use strict';

const express = require('express');
const compression = require('compression');
const helmet = require('helmet');
const session = require('express-session');
const ejsLayouts = require('express-ejs-layouts');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

const { FileSessionStore } = require('./sessionStore');
const { buildSessionCookieOptions } = require('./sessionCookieConfig');
const { resolveTrustProxySetting } = require('./rateLimitUtils');
const {
  canonicalPublicUrl,
  legacyPublicPageRedirectMiddleware,
  buildCanonicalPageUrl,
  isSessionlessPath,
} = require('./publicDomain');
const { expressAccessLogMiddleware } = require('./requestAccessLog');
const { mountHealthz } = require('./healthz');
const packageJson = require('../package.json');

function latestAssetStamp(includeTrackerView) {
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
  ];
  if (includeTrackerView) {
    files.push(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'));
  }
  let newest = 0;
  for (const file of files) {
    try {
      newest = Math.max(newest, Math.floor(fs.statSync(file).mtimeMs));
    } catch {
      // optional asset
    }
  }
  return newest || Date.now();
}

function buildAssetVersion(includeTrackerView) {
  return [
    process.env.TOOL_SITE_ASSET_VERSION,
    process.env.GIT_COMMIT,
    packageJson.version,
    latestAssetStamp(includeTrackerView),
  ].filter(Boolean).join('-').replace(/[^A-Za-z0-9._-]/g, '');
}

function createSiteExpressApp(options = {}) {
  const service = options.service || 'deng-site';
  const port = Number(options.port || process.env.TOOL_SITE_PORT || 8791);
  const includeTrackerViewAssets = options.includeTrackerViewAssets !== false;

  const app = express();
  app.disable('x-powered-by');
  app.use(compression());
  app.use(expressAccessLogMiddleware(service));
  app.use((req, res, next) => {
    res.set('X-DENG-Served-By', service);
    res.set('X-DENG-Site-Port', String(port));
    next();
  });

  app.use(helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        scriptSrc: [
          "'self'",
          "'unsafe-inline'",
          'https://publisher.linkvertise.com',
        ],
        styleSrc: ["'self'", "'unsafe-inline'"],
        imgSrc: [
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
        connectSrc: ["'self'"],
        frameSrc: ["'none'"],
        objectSrc: ["'none'"],
        baseUri: ["'self'"],
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

  app.set('trust proxy', resolveTrustProxySetting());
  app.use(legacyPublicPageRedirectMiddleware);

  if (options.stabilityRoutes !== false) {
    app.use('/', require('./stabilityRoutes'));
  }

  mountHealthz(app, service, port);
  app.get('/health', (_req, res) => {
    res.set('Cache-Control', 'no-store');
    res.json({
      status: 'ok',
      service,
      port,
      timestamp: new Date().toISOString(),
    });
  });

  const sessionSecret = process.env.TOOL_SITE_COOKIE_SECRET;
  if (!sessionSecret || sessionSecret.length < 32) {
    throw new Error('TOOL_SITE_COOKIE_SECRET must be at least 32 characters');
  }

  const assetVersion = buildAssetVersion(includeTrackerViewAssets);
  app.locals.assetVersion = assetVersion;
  app.locals.flash = {};
  app.locals.csrfToken = '';
  app.locals.user = null;
  app.locals.publicUrl = canonicalPublicUrl();

  const sessionMiddleware = session({
    name: 'deng_sid',
    store: new FileSessionStore({
      dir: process.env.TOOL_SITE_SESSION_DIR,
      ttlMs: 7 * 24 * 60 * 60 * 1000,
    }),
    secret: sessionSecret,
    resave: false,
    saveUninitialized: false,
    cookie: buildSessionCookieOptions(),
  });

  app.use((req, res, next) => {
    if (isSessionlessPath(req.path, req.method)) return next();
    return sessionMiddleware(req, res, next);
  });

  app.use((req, _res, next) => {
    if (isSessionlessPath(req.path, req.method) || !req.session) return next();
    const authenticated = Boolean(
      req.session.user || req.session.site_user_id || req.session.discord_user_id,
    );
    if (!req.session.csrfToken && (authenticated || req.method !== 'GET')) {
      req.session.csrfToken = crypto.randomBytes(32).toString('hex');
    }
    next();
  });

  if (!options.deferBodyParsers) {
    mountStandardBodyParsers(app);
  }

  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.set('view cache', false);
  app.use(ejsLayouts);
  app.set('layout', 'layout');
  app.set('layout extractScripts', true);

  app.use('/public', express.static(path.join(__dirname, '..', 'public'), {
    maxAge: process.env.NODE_ENV === 'production' ? '1d' : 0,
    setHeaders: (res, filePath) => {
      if (/[\\/]assets[\\/][^\\/]+\.[a-f0-9]{8,}\.(css|js)$/i.test(filePath)) {
        res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
      } else if (/[\\/]css[\\/]home\.css$/i.test(filePath)) {
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

  app.use((err, req, res, next) => {
    if (err && (err.code === 'EBUSY' || err.code === 'EPERM' || err.code === 'EACCES')) {
      console.warn(`[${service}] Recoverable filesystem error:`, err.code, req.path);
      if (req.path.startsWith('/api/')) {
        if (!res.headersSent) return res.status(503).json({ ok: false, error: 'temporarily_busy' });
        return;
      }
      if (!res.headersSent) {
        return res.status(503).render('error', { code: 503, message: 'Server is busy. Please retry.' });
      }
      return;
    }
    next(err);
  });

  return app;
}

function mountStandardBodyParsers(app) {
  app.use(express.urlencoded({ extended: false, limit: '16kb' }));
  app.use(express.json({ limit: '16kb' }));
}

module.exports = {
  createSiteExpressApp,
  buildAssetVersion,
  mountStandardBodyParsers,
};
