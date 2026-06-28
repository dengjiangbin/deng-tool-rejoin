'use strict';

/**
 * Portal/license/ad-unlock paths must reach Express before tracker read proxy
 * work so they stay fast while tracker polls flood 8791 → 8793.
 */

const PORTAL_EXACT = new Set([
  '/login',
  '/dashboard',
  '/download',
  '/license',
  '/key/result',
  '/key/provider',
]);

const PORTAL_PREFIXES = [
  '/license/',
  '/key/',
  '/unlock/',
  '/auth/',
  '/api/license/',
  '/api/key/',
  '/api/aio/',
];

function isPortalPriorityPath(pathname, method) {
  const path = String(pathname || '').split('?')[0];
  if (PORTAL_EXACT.has(path)) return true;
  for (const prefix of PORTAL_PREFIXES) {
    if (path.startsWith(prefix)) return true;
  }
  const m = String(method || 'GET').toUpperCase();
  if (m === 'POST') {
    if (path === '/license/generate') return true;
    if (path.startsWith('/license/provider')) return true;
    if (path.startsWith('/key/provider')) return true;
  }
  return false;
}

module.exports = {
  isPortalPriorityPath,
  PORTAL_EXACT,
  PORTAL_PREFIXES,
};
