'use strict';

/**
 * Login-only public routes for the portal process (8790).
 */
const express = require('express');
const { safeReturnPath } = require('./auth');

const router = express.Router();

const PUBLIC_NO_STORE = {
  'Cache-Control': 'no-store, no-cache, must-revalidate, proxy-revalidate',
  Pragma: 'no-cache',
  Expires: '0',
};

router.get('/login', (req, res) => {
  const returnPath = safeReturnPath(req.query.return || req.query.next);
  const apkEmbed = req.query.apk === '1' || req.query.apk === 'true';
  if (req.session.user) {
    const dest = apkEmbed ? '/tracker?apk=1' : (returnPath || '/tracker');
    return res.redirect(dest);
  }
  if (returnPath) req.session.authReturnTo = returnPath;
  res.set(PUBLIC_NO_STORE);
  return res.render('login', {
    title: 'Sign In - DENG All In One',
    authReturnTo: returnPath || '',
    apkEmbed,
    bodyClass: 'auth-layout',
  });
});

router.get('/logout', (req, res) => {
  if (req.session) {
    req.session.destroy(() => {
      res.clearCookie('deng_sid');
      res.redirect('/login');
    });
    return;
  }
  res.redirect('/login');
});

module.exports = router;
