'use strict';
/**
 * Central session cookie settings for express-session.
 * Host-only cookies on aio.deng.my.id by default (no Domain attribute).
 */

function cleanEnv(name, fallback = '') {
  return String(process.env[name] || fallback).trim();
}

function resolveSessionCookieDomain() {
  const explicit = cleanEnv('TOOL_SITE_COOKIE_DOMAIN');
  if (explicit === 'none' || explicit === 'host-only') return undefined;
  if (explicit) return explicit;
  // Production default: host-only cookie (omit Domain) for aio.deng.my.id / WebView.
  if (process.env.NODE_ENV === 'production') return undefined;
  return undefined;
}

function resolveSessionCookieSecure() {
  const raw = cleanEnv('TOOL_SITE_COOKIE_SECURE');
  if (/^(0|false|no|off)$/i.test(raw)) return false;
  if (/^(1|true|yes|on)$/i.test(raw)) return true;
  if (raw === 'auto') return 'auto';
  // auto: honor X-Forwarded-Proto behind Cloudflare when trust proxy is enabled.
  return process.env.NODE_ENV === 'production' ? 'auto' : false;
}

function buildSessionCookieOptions() {
  const domain = resolveSessionCookieDomain();
  const secure = resolveSessionCookieSecure();
  const cookie = {
    httpOnly: true,
    secure,
    sameSite: 'lax',
    maxAge: 7 * 24 * 60 * 60 * 1000,
    path: '/',
  };
  if (domain) cookie.domain = domain;
  return cookie;
}

function buildClearSessionCookieOptions() {
  const cookie = buildSessionCookieOptions();
  return {
    path: cookie.path,
    ...(cookie.domain ? { domain: cookie.domain } : {}),
  };
}

function describeSessionCookieConfig() {
  const cookie = buildSessionCookieOptions();
  return {
    name: 'deng_sid',
    httpOnly: cookie.httpOnly,
    secure: cookie.secure,
    sameSite: cookie.sameSite,
    path: cookie.path,
    domain: cookie.domain || null,
    maxAgeMs: cookie.maxAge,
    hostOnly: !cookie.domain,
  };
}

module.exports = {
  buildSessionCookieOptions,
  buildClearSessionCookieOptions,
  describeSessionCookieConfig,
  resolveSessionCookieDomain,
  resolveSessionCookieSecure,
};
