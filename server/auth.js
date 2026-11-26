'use strict';
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const crypto = require('crypto');
const db = require('./db');

const SECRET = process.env.JWT_SECRET || crypto.randomBytes(32).toString('hex');
if (!process.env.JWT_SECRET) {
  console.warn('[auth] JWT_SECRET not set — using a random one (sessions reset on restart). Set it in discloud.config env.');
}

const sha256 = (s) => crypto.createHash('sha256').update(s).digest('hex');
const hashPw = (pw) => bcrypt.hash(pw, 11);
const checkPw = (pw, h) => bcrypt.compare(pw, h);

const signUser = (user) => jwt.sign({ uid: user.id, u: user.username, role: user.role }, SECRET, { expiresIn: '30d' });

// Web session (Bearer JWT) -> req.user
function requireUser(req, res, next) {
  const m = /^Bearer (.+)$/.exec(req.headers.authorization || '');
  if (!m) return res.status(401).json({ error: 'no token' });
  try {
    const p = jwt.verify(m[1], SECRET);
    const user = db.prepare('SELECT id, username, email, role, plan FROM users WHERE id = ?').get(p.uid);
    if (!user) return res.status(401).json({ error: 'invalid user' });
    req.user = user;
    next();
  } catch { return res.status(401).json({ error: 'bad token' }); }
}

// Device auth (Bearer device token) -> req.device
function requireDevice(req, res, next) {
  const m = /^Bearer (.+)$/.exec(req.headers.authorization || '');
  if (!m) return res.status(401).json({ error: 'no device token' });
  const dev = db.prepare('SELECT * FROM devices WHERE token_hash = ?').get(sha256(m[1]));
  if (!dev) return res.status(401).json({ error: 'unknown device' });
  // saved login is invalidated when the account password changes (sess_ver bumped)
  const u = db.prepare('SELECT sess_ver FROM users WHERE id = ?').get(dev.user_id);
  if (!u || u.sess_ver !== dev.sess_ver) return res.status(401).json({ error: 'reauth', reason: 'password changed' });
  req.device = dev;
  next();
}

const newToken = () => crypto.randomBytes(24).toString('base64url');
const newId = (p) => p + '_' + crypto.randomBytes(8).toString('hex');

module.exports = { SECRET, sha256, hashPw, checkPw, signUser, requireUser, requireDevice, newToken, newId };
