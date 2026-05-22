'use strict';
const express      = require('express');
const helmet       = require('helmet');
const session      = require('express-session');
const rateLimit    = require('express-rate-limit');
const ejsLayouts   = require('express-ejs-layouts');
const path         = require('path');

const routes = require('./routes');
const { FileSessionStore } = require('./sessionStore');
const packageJson = require('../package.json');

const app = express();
app.disable('x-powered-by');
const assetVersion = (
  process.env.TOOL_SITE_ASSET_VERSION ||
  process.env.GIT_COMMIT ||
  `${packageJson.version}-${Date.now()}`
).replace(/[^A-Za-z0-9._-]/g, '');

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
      imgSrc:    ["'self'", 'data:', 'https://cdn.discordapp.com'],
      connectSrc:["'self'"],
      frameSrc:  ["'none'"],
      objectSrc: ["'none'"],
      baseUri:   ["'self'"],
      formAction:["'self'"],
      frameAncestors: ["'none'"],
      upgradeInsecureRequests: process.env.NODE_ENV === 'production' ? [] : [],
    },
  },
  hsts: process.env.NODE_ENV === 'production'
    ? { maxAge: 31536000, includeSubDomains: true }
    : false,
}));

// ---------------------------------------------------------------
// Trust proxy (behind nginx/Caddy)
// ---------------------------------------------------------------
app.set('trust proxy', 1);

// ---------------------------------------------------------------
// Rate limiter (global – 200 req / 15 min per IP)
// ---------------------------------------------------------------
const globalLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 200,
  skip: () => process.env.NODE_ENV === 'test',
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many requests, please try again later.' },
});
app.use(globalLimiter);

// ---------------------------------------------------------------
// Body parsers
// ---------------------------------------------------------------
app.use(express.urlencoded({ extended: false, limit: '16kb' }));
app.use(express.json({ limit: '16kb' }));

// ---------------------------------------------------------------
// Session (HttpOnly, Secure in prod, SameSite=Lax)
// ---------------------------------------------------------------
const sessionSecret = process.env.TOOL_SITE_COOKIE_SECRET;
if (!sessionSecret || sessionSecret.length < 32) {
  throw new Error('TOOL_SITE_COOKIE_SECRET must be at least 32 characters');
}

app.use(session({
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
  },
}));

// ---------------------------------------------------------------
// Template engine: EJS + layouts
// ---------------------------------------------------------------
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, '..', 'views'));
app.use(ejsLayouts);
app.set('layout', 'layout');
app.set('layout extractScripts', true);

// ---------------------------------------------------------------
// Static files
// ---------------------------------------------------------------
app.use('/public', express.static(path.join(__dirname, '..', 'public'), {
  maxAge: process.env.NODE_ENV === 'production' ? '1d' : 0,
}));
app.use('/assets', express.static(path.join(__dirname, '..', 'public'), {
  maxAge: process.env.NODE_ENV === 'production' ? '1d' : 0,
}));

// ---------------------------------------------------------------
// CSRF middleware – attach token to res.locals for all EJS views
// ---------------------------------------------------------------
app.use((req, _res, next) => {
  if (!req.session.csrfToken) {
    req.session.csrfToken = require('crypto').randomBytes(32).toString('hex');
  }
  next();
});

// ---------------------------------------------------------------
// Flash-message helper (lightweight, no extra dep)
// ---------------------------------------------------------------
app.use((req, res, next) => {
  res.locals.flash = req.session.flash || {};
  res.locals.csrfToken = req.session.csrfToken;
  res.locals.user = req.session.user || null;
  res.locals.publicUrl = process.env.TOOL_SITE_PUBLIC_URL || 'https://tool.deng.my.id';
  res.locals.assetVersion = assetVersion;
  delete req.session.flash;
  next();
});

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
app.use((err, _req, res, _next) => {
  console.error('[deng-tool-site] Unhandled error:', err);
  const code = err.status || 500;
  res.status(code).render('error', { code, message: 'An unexpected error occurred.' });
});

module.exports = app;
