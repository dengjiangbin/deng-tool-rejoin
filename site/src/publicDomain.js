'use strict';
/**
 * Public website domain cutover: aio.deng.my.id (canonical) with tool.deng.my.id
 * kept for internal APIs, unlock callbacks, tracker uploads, and legacy clients.
 */

const PUBLIC_BRAND_NAME = 'DENG All In One';
const PUBLIC_BRAND_SHORT = 'DENG AIO';
const CANONICAL_PUBLIC_HOST = 'aio.deng.my.id';
const LEGACY_PUBLIC_HOST = 'tool.deng.my.id';
/** @deprecated alias kept for callers expecting LEGACY_PUBLIC_HOST naming */
const INTERNAL_BACKEND_HOST = LEGACY_PUBLIC_HOST;

const LEGACY_PUBLIC_REDIRECT_PATHS = new Set([
  '/',
  '/login',
  '/download',
  '/license',
  '/dashboard',
  '/tracker',
  '/inventory',
  '/fishit',
  '/app',
  '/fishit-tracker',
]);

function cleanEnv(name, fallback = '') {
  const raw = Object.prototype.hasOwnProperty.call(process.env, name) ? process.env[name] : fallback;
  const cleaned = String(raw || '').trim().replace(/^['"]|['"]$/g, '').trim();
  if (cleaned) return cleaned;
  return String(fallback || '').trim().replace(/^['"]|['"]$/g, '').trim();
}

function canonicalPublicUrl() {
  return cleanEnv('TOOL_SITE_PUBLIC_URL', `https://${CANONICAL_PUBLIC_HOST}`).replace(/\/+$/, '');
}

function internalApiBaseUrl() {
  return cleanEnv('TOOL_SITE_INTERNAL_URL', `https://${LEGACY_PUBLIC_HOST}`).replace(/\/+$/, '');
}

function oauthCallbackBaseUrl() {
  return cleanEnv('OAUTH_CALLBACK_BASE', canonicalPublicUrl()).replace(/\/+$/, '');
}

/** Preferred web OAuth callback — aio public domain. */
function preferredOAuthCallbackUri() {
  return cleanEnv(
    'DISCORD_REDIRECT_URI',
    `${oauthCallbackBaseUrl()}/api/aio/auth/callback`,
  );
}

/** Alternate web OAuth callback alias registered in Discord Developer Portal. */
function alternateOAuthCallbackUri() {
  return cleanEnv(
    'DISCORD_AIO_WEB_REDIRECT_URI',
    `${oauthCallbackBaseUrl()}/auth/discord/callback`,
  );
}

/** APK OAuth callback (defaults to preferred public callback). */
function aioApkOAuthCallbackUri() {
  return cleanEnv('DISCORD_AIO_REDIRECT_URI', preferredOAuthCallbackUri());
}

function oauthDiscordCallbackUri() {
  return alternateOAuthCallbackUri();
}

function isOAuthCallbackPath(pathname) {
  const path = String(pathname || '');
  return path === '/auth/discord/callback' || path === '/api/aio/auth/callback';
}

function requestHost(req) {
  const forwarded = req.headers['x-forwarded-host'];
  if (forwarded) {
    const first = String(forwarded).split(',')[0].trim().toLowerCase();
    if (first) return first.split(':')[0];
  }
  const host = req.headers.host || req.hostname || '';
  return String(host).split(':')[0].toLowerCase();
}

function isLegacyPublicHost(host) {
  return String(host || '').toLowerCase() === LEGACY_PUBLIC_HOST;
}

function isCanonicalPublicHost(host) {
  return String(host || '').toLowerCase() === CANONICAL_PUBLIC_HOST;
}

function isLegacyPublicRedirectPath(pathname) {
  const path = String(pathname || '/');
  return LEGACY_PUBLIC_REDIRECT_PATHS.has(path);
}

function isApiOrInternalPath(pathname) {
  const path = String(pathname || '');
  if (path.startsWith('/api/')) return true;
  if (path.startsWith('/unlock/')) return true;
  if (path.startsWith('/auth/')) return true;
  if (path.startsWith('/public/')) return true;
  if (path.startsWith('/downloads/')) return true;
  if (path === '/health') return true;
  if (path === '/tracker.lua') return true;
  return false;
}

/** High-volume tracker upload APIs — skip express-session to avoid file-store churn. */
function isSessionlessPath(pathname) {
  const path = String(pathname || '');
  if (path === '/health') return true;
  if (path.startsWith('/api/fishit-tracker/')) return true;
  if (path.startsWith('/api/tracker/')) return true;
  return false;
}

function legacyPublicPageRedirectTarget(req) {
  const canonical = canonicalPublicUrl();
  const path = req.path || '/';
  const qs = req.originalUrl && req.originalUrl.includes('?')
    ? req.originalUrl.slice(req.originalUrl.indexOf('?'))
    : '';
  return `${canonical}${path}${qs}`;
}

function legacyPublicPageRedirectMiddleware(req, res, next) {
  if (process.env.PUBLIC_DOMAIN_REDIRECT_ENABLED === 'false') return next();
  if (req.method !== 'GET' && req.method !== 'HEAD') return next();
  if (!isLegacyPublicHost(requestHost(req))) return next();
  if (isApiOrInternalPath(req.path)) return next();
  if (!isLegacyPublicRedirectPath(req.path)) return next();
  return res.redirect(301, legacyPublicPageRedirectTarget(req));
}

function resolveDiscordRedirectUri(req, options = {}) {
  if (options.callbackUri) return String(options.callbackUri).trim();
  if (options.variant === 'alternate') return alternateOAuthCallbackUri();
  return preferredOAuthCallbackUri();
}

function isCanonicalPublicRequest(req) {
  return isCanonicalPublicHost(requestHost(req));
}

function oauthReturnPublicBase(req) {
  if (isCanonicalPublicRequest(req)) return canonicalPublicUrl();
  if (isLegacyPublicHost(requestHost(req))) return internalApiBaseUrl();
  return canonicalPublicUrl();
}

function buildCanonicalPageUrl(req) {
  const base = canonicalPublicUrl();
  const path = req.path || '/';
  if (path === '/') return `${base}/`;
  return `${base}${path}`;
}

module.exports = {
  PUBLIC_BRAND_NAME,
  PUBLIC_BRAND_SHORT,
  CANONICAL_PUBLIC_HOST,
  LEGACY_PUBLIC_HOST,
  INTERNAL_BACKEND_HOST,
  canonicalPublicUrl,
  internalApiBaseUrl,
  oauthCallbackBaseUrl,
  preferredOAuthCallbackUri,
  alternateOAuthCallbackUri,
  aioApkOAuthCallbackUri,
  oauthDiscordCallbackUri,
  isOAuthCallbackPath,
  requestHost,
  isLegacyPublicHost,
  isCanonicalPublicHost,
  isCanonicalPublicRequest,
  isLegacyPublicRedirectPath,
  isApiOrInternalPath,
  isSessionlessPath,
  legacyPublicPageRedirectMiddleware,
  resolveDiscordRedirectUri,
  oauthReturnPublicBase,
  buildCanonicalPageUrl,
};
