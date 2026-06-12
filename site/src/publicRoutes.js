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

function sendPublicPage(res, view, locals) {
  res.set(PUBLIC_NO_STORE);
  return res.render(view, locals);
}

router.get('/', (req, res) => {
  if (req.session.user) return res.redirect('/dashboard');
  return sendPublicPage(res, 'home', {
    title: 'DENG Tool - Roblox Automation & Stat Tracker',
    metaDescription: 'DENG Tool is a Roblox automation and stat-tracking suite with live Fish It inventory, Rejoin agents, licenses, and monitoring in one dashboard.',
  });
});

router.get('/login', (req, res) => {
  const returnPath = safeReturnPath(req.query.return || req.query.next);
  if (req.session.user) {
    return res.redirect(returnPath || '/dashboard');
  }
  if (returnPath) req.session.authReturnTo = returnPath;
  const apkEmbed = req.query.apk === '1' || req.query.apk === 'true';
  return sendPublicPage(res, 'login', {
    title: 'Sign In - DENG Tool',
    authReturnTo: returnPath || '',
    apkEmbed,
  });
});

module.exports = router;
