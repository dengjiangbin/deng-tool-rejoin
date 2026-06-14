'use strict';
/**
 * Public marketing + auth entry routes.
 * Mounted before protected routers so `/` and `/login` are never intercepted.
 */
const express = require('express');
const { safeReturnPath } = require('./auth');

const router = express.Router();

const PUBLIC_NO_STORE = {
  'Cache-Control': 'no-store, no-cache, must-revalidate, proxy-revalidate',
  Pragma: 'no-cache',
  Expires: '0',
};

function sendPublicPage(res, view, locals = {}) {
  res.set(PUBLIC_NO_STORE);
  const bodyClass = locals.bodyClass || (view === 'home' ? 'public-home-layout' : 'auth-layout');
  return res.render(view, { ...locals, bodyClass });
}

router.get('/', (req, res) => {
  if (req.session.user) return res.redirect('/dashboard');
  let initialHomeStats = { trackedCount: 0, onlineCount: 0 };
  try {
    const fishitTrackerRoutes = require('./fishitTrackerRoutes');
    if (typeof fishitTrackerRoutes.buildPublicTrackerStatsPayload === 'function') {
      const payload = fishitTrackerRoutes.buildPublicTrackerStatsPayload();
      initialHomeStats = {
        trackedCount: Number(payload.trackedCount) || 0,
        onlineCount: Number(payload.onlineCount) || 0,
      };
    }
  } catch (_) { /* non-blocking */ }
  return sendPublicPage(res, 'home', {
    title: 'DENG All In One - Roblox Automation & Stat Tracker',
    metaDescription: 'DENG All In One is a Roblox automation and stat-tracking suite with live Fish It inventory, Rejoin agents, licenses, and monitoring in one dashboard.',
    bodyClass: 'public-home-layout',
    initialHomeStats,
  });
});

router.get('/login', (req, res) => {
  const returnPath = safeReturnPath(req.query.return || req.query.next);
  const apkEmbed = req.query.apk === '1' || req.query.apk === 'true';
  if (req.session.user) {
    const dest = apkEmbed ? '/tracker?apk=1' : (returnPath || '/dashboard');
    return res.redirect(dest);
  }
  if (returnPath) req.session.authReturnTo = returnPath;
  return sendPublicPage(res, 'login', {
    title: 'Sign In - DENG All In One',
    authReturnTo: returnPath || '',
    apkEmbed,
    bodyClass: 'auth-layout',
  });
});

module.exports = router;
