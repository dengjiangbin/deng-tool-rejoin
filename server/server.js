'use strict';
try { require('dotenv').config(); } catch { /* dotenv optional (e.g. VPS uses --env-file) */ }
const express = require('express');
const path = require('path');
const crypto = require('crypto');
const db = require('./db');
const A = require('./auth');
const { sendResetMail } = require('./mailer');
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const app = express();
app.use(express.json({ limit: '512kb' }));
app.use(express.static(path.join(__dirname, 'public')));

const now = () => Date.now();
const json = (s) => { try { return JSON.parse(s); } catch { return null; } };
const VALID_ACTIONS = ['start_all', 'stop_all', 'rejoin_all', 'push_script', 'remove_script', 'set_target', 'set_settings', 'set_key',
  'start_clone', 'stop_clone', 'rejoin_clone', 'set_clone_auto']; // *_clone take payload.pkg to target one clone

function ensureSettings(uid) {
  db.prepare('INSERT OR IGNORE INTO settings (user_id) VALUES (?)').run(uid);
  return db.prepare('SELECT * FROM settings WHERE user_id = ?').get(uid);
}
// queue one command to every device a user owns
function queueAll(uid, action, payload) {
  const devs = db.prepare('SELECT id FROM devices WHERE user_id = ?').all(uid);
  const ins = db.prepare('INSERT INTO commands (device_id, action, payload, created_at) VALUES (?,?,?,?)');
  const tx = db.transaction(() => devs.forEach(d => ins.run(d.id, action, JSON.stringify(payload || {}), now())));
  tx();
  return devs.length;
}

/* ---------------- AUTH (web) ---------------- */
app.post('/api/register', async (req, res) => {
  const { username, password, email } = req.body || {};
  if (!/^[a-zA-Z0-9_.-]{3,24}$/.test(username || ''))
    return res.status(400).json({ error: 'username must be 3-24 chars (letters, numbers, _ . -)' });
  if (!EMAIL_RE.test(email || ''))
    return res.status(400).json({ error: 'a valid email is required (for password resets)' });
  if (!password || password.length < 6)
    return res.status(400).json({ error: 'password must be at least 6 characters' });
  if (db.prepare('SELECT 1 FROM users WHERE username = ?').get(username))
    return res.status(409).json({ error: 'username taken' });
  if (db.prepare('SELECT 1 FROM users WHERE email = ?').get(email))
    return res.status(409).json({ error: 'email already registered' });
  const info = db.prepare('INSERT INTO users (username, email, pass_hash, created_at) VALUES (?,?,?,?)')
    .run(username, email, await A.hashPw(password), now());
  const user = db.prepare('SELECT id, username, email, role, plan FROM users WHERE id = ?').get(info.lastInsertRowid);
  ensureSettings(user.id);
  res.json({ token: A.signUser(user), user });
});

// request a reset link (by email or username). Always returns ok (no account enumeration).
app.post('/api/forgot', async (req, res) => {
  const { id } = req.body || {};              // id = email or username
  const row = db.prepare('SELECT * FROM users WHERE email = ? OR username = ?').get(id || '', id || '');
  if (row && row.email) {
    const token = A.newToken();
    db.prepare('INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?,?,?)')
      .run(A.sha256(token), row.id, now() + 30 * 60 * 1000);
    const base = process.env.PUBLIC_URL || (req.protocol + '://' + req.get('host'));
    try { await sendResetMail(row.email, base + '/reset.html?token=' + token); } catch (e) { console.error('[forgot] mail error', e.message); }
  }
  res.json({ ok: true, message: 'If that account exists, a reset link has been sent.' });
});

app.post('/api/reset', async (req, res) => {
  const { token, password } = req.body || {};
  if (!password || password.length < 6) return res.status(400).json({ error: 'password must be at least 6 characters' });
  const row = db.prepare('SELECT * FROM password_resets WHERE token_hash = ?').get(A.sha256(token || ''));
  if (!row || row.used || row.expires_at < now()) return res.status(400).json({ error: 'reset link is invalid or expired' });
  db.prepare('UPDATE users SET pass_hash = ?, sess_ver = sess_ver + 1 WHERE id = ?').run(await A.hashPw(password), row.user_id);
  db.prepare('UPDATE password_resets SET used = 1 WHERE token_hash = ?').run(row.token_hash);
  db.prepare('DELETE FROM devices WHERE user_id = ?').run(row.user_id); // sign out all phones on password change
  res.json({ ok: true });
});

// change password while logged in
app.post('/api/account/password', A.requireUser, async (req, res) => {
  const { old_password, new_password } = req.body || {};
  if (!new_password || new_password.length < 6) return res.status(400).json({ error: 'new password must be at least 6 characters' });
  const row = db.prepare('SELECT pass_hash FROM users WHERE id = ?').get(req.user.id);
  if (!(await A.checkPw(old_password || '', row.pass_hash))) return res.status(401).json({ error: 'current password is wrong' });
  db.prepare('UPDATE users SET pass_hash = ?, sess_ver = sess_ver + 1 WHERE id = ?').run(await A.hashPw(new_password), req.user.id);
  db.prepare('DELETE FROM devices WHERE user_id = ?').run(req.user.id); // sign out all phones on password change
  res.json({ ok: true });
});

// update email while logged in
app.post('/api/account/email', A.requireUser, (req, res) => {
  const { email } = req.body || {};
  if (!EMAIL_RE.test(email || '')) return res.status(400).json({ error: 'invalid email' });
  if (db.prepare('SELECT 1 FROM users WHERE email = ? AND id <> ?').get(email, req.user.id))
    return res.status(409).json({ error: 'email already registered' });
  db.prepare('UPDATE users SET email = ? WHERE id = ?').run(email, req.user.id);
  res.json({ ok: true });
});

app.post('/api/login', async (req, res) => {
  const { username, password } = req.body || {};
  const row = db.prepare('SELECT * FROM users WHERE username = ?').get(username || '');
  if (!row || !(await A.checkPw(password || '', row.pass_hash)))
    return res.status(401).json({ error: 'wrong username or password' });
  const user = { id: row.id, username: row.username, role: row.role, plan: row.plan };
  res.json({ token: A.signUser(user), user });
});

app.get('/api/me', A.requireUser, (req, res) => res.json({ user: req.user }));

/* ---------------- PAIRING ---------------- */
// logged-in user asks for a short code to type (or auto-send) on the phone
app.post('/api/pair/start', A.requireUser, (req, res) => {
  const code = ('' + crypto.randomInt(0, 1e6)).padStart(6, '0');
  db.prepare('INSERT OR REPLACE INTO pair_codes (code, user_id, expires_at, used) VALUES (?,?,?,0)')
    .run(code, req.user.id, now() + 10 * 60 * 1000);
  res.json({ code, expires_in: 600 });
});

// agent redeems a pairing code -> gets a device token (no password on the phone)
app.post('/api/device/pair', (req, res) => {
  const { code, name } = req.body || {};
  const row = db.prepare('SELECT * FROM pair_codes WHERE code = ?').get(code || '');
  if (!row || row.used || row.expires_at < now())
    return res.status(400).json({ error: 'invalid or expired code' });
  db.prepare('UPDATE pair_codes SET used = 1 WHERE code = ?').run(code);
  res.json(createDevice(row.user_id, name));
});

// agent logs in with the user's own username/password -> device token
app.post('/api/device/login', async (req, res) => {
  const { username, password, name } = req.body || {};
  const row = db.prepare('SELECT * FROM users WHERE username = ?').get(username || '');
  if (!row || !(await A.checkPw(password || '', row.pass_hash)))
    return res.status(401).json({ error: 'wrong username or password' });
  res.json(createDevice(row.id, name));
});

function createDevice(uid, name) {
  const id = A.newId('dev');
  const token = A.newToken();
  const dn = (name && ('' + name).slice(0, 40)) || ('phone-' + id.slice(4, 10));
  const sv = db.prepare('SELECT sess_ver FROM users WHERE id = ?').get(uid).sess_ver;
  db.prepare('INSERT INTO devices (id, user_id, name, token_hash, sess_ver, created_at) VALUES (?,?,?,?,?,?)')
    .run(id, uid, dn, A.sha256(token), sv, now());
  ensureSettings(uid);
  return { device_id: id, device_token: token, name: dn };
}

/* ---------------- AGENT (device token) ---------------- */
// one call: report status + pull pending commands + current fleet config
app.post('/api/device/heartbeat', A.requireDevice, (req, res) => {
  const d = req.device;
  db.prepare('UPDATE devices SET last_seen = ?, status_json = ? WHERE id = ?')
    .run(now(), JSON.stringify(req.body?.status || {}), d.id);
  const cmds = db.prepare("SELECT id, action, payload FROM commands WHERE device_id = ? AND status = 'pending' ORDER BY id").all(d.id);
  const s = ensureSettings(d.user_id);
  res.json({
    commands: cmds.map(c => ({ id: c.id, action: c.action, payload: json(c.payload) || {} })),
    config: { target: json(s.target_json), scripts: json(s.scripts_json) || [], settings: json(s.config_json) || {} },
  });
});

app.post('/api/device/ack', A.requireDevice, (req, res) => {
  const { id, ok, result } = req.body || {};
  const c = db.prepare('SELECT * FROM commands WHERE id = ? AND device_id = ?').get(id, req.device.id);
  if (!c) return res.status(404).json({ error: 'no such command' });
  db.prepare('UPDATE commands SET status = ?, result = ? WHERE id = ?')
    .run(ok ? 'done' : 'failed', ('' + (result || '')).slice(0, 2000), id);
  res.json({ ok: true });
});

/* ---------------- WEB DASHBOARD (JWT) ---------------- */
app.get('/api/fleet', A.requireUser, (req, res) => {
  const devs = db.prepare('SELECT id, name, last_seen, status_json FROM devices WHERE user_id = ? ORDER BY created_at').all(req.user.id);
  const online = (ls) => ls && (now() - ls < 45000);
  res.json({
    devices: devs.map(d => ({ id: d.id, name: d.name, online: online(d.last_seen), last_seen: d.last_seen, status: json(d.status_json) || {} })),
  });
});

app.post('/api/command', A.requireUser, (req, res) => {
  const { action, payload, device_ids } = req.body || {};
  if (!VALID_ACTIONS.includes(action)) return res.status(400).json({ error: 'bad action' });
  let n;
  if (Array.isArray(device_ids) && device_ids.length) {
    const own = new Set(db.prepare('SELECT id FROM devices WHERE user_id = ?').all(req.user.id).map(d => d.id));
    const ins = db.prepare('INSERT INTO commands (device_id, action, payload, created_at) VALUES (?,?,?,?)');
    n = 0;
    for (const id of device_ids) if (own.has(id)) { ins.run(id, action, JSON.stringify(payload || {}), now()); n++; }
  } else {
    n = queueAll(req.user.id, action, payload);
  }
  res.json({ queued: n });
});

// scripts + target + settings: persist to fleet config AND queue a command so agents apply now
app.get('/api/config', A.requireUser, (req, res) => {
  const s = ensureSettings(req.user.id);
  res.json({ target: json(s.target_json), scripts: json(s.scripts_json) || [], settings: json(s.config_json) || {} });
});
app.post('/api/scripts', A.requireUser, (req, res) => {
  const s = ensureSettings(req.user.id);
  const scripts = json(s.scripts_json) || [];
  const { op, name, body } = req.body || {};
  if (op === 'add') {
    if (!name || !body) return res.status(400).json({ error: 'name and body required' });
    scripts.push({ name: ('' + name).slice(0, 40), size: body.length, ts: now() });
    db.prepare('UPDATE settings SET scripts_json = ? WHERE user_id = ?').run(JSON.stringify(scripts), req.user.id);
    queueAll(req.user.id, 'push_script', { name, body });
  } else if (op === 'remove') {
    const left = scripts.filter(x => x.name !== name);
    db.prepare('UPDATE settings SET scripts_json = ? WHERE user_id = ?').run(JSON.stringify(left), req.user.id);
    queueAll(req.user.id, 'remove_script', { name });
  } else return res.status(400).json({ error: 'op must be add|remove' });
  res.json({ ok: true });
});
app.post('/api/target', A.requireUser, (req, res) => {
  const t = req.body || {};
  db.prepare('UPDATE settings SET target_json = ? WHERE user_id = ?').run(JSON.stringify(t), req.user.id);
  queueAll(req.user.id, 'set_target', t);
  res.json({ ok: true });
});
app.post('/api/settings', A.requireUser, (req, res) => {
  db.prepare('UPDATE settings SET config_json = ? WHERE user_id = ?').run(JSON.stringify(req.body || {}), req.user.id);
  queueAll(req.user.id, 'set_settings', req.body || {});
  res.json({ ok: true });
});

// Delta Key: push a license key to every clone's cache (shared or separate).
// The key is NOT stored server-side; it's queued to the devices and then discarded.
app.post('/api/key', A.requireUser, (req, res) => {
  const { key, mode } = req.body || {};
  if (!/^[A-Za-z0-9._-]{1,512}$/.test(key || '')) return res.status(400).json({ error: 'key must be 1-512 chars (letters, numbers, . _ -)' });
  if (mode !== 'shared' && mode !== 'separate') return res.status(400).json({ error: 'mode must be shared or separate' });
  const n = queueAll(req.user.id, 'set_key', { key, mode });
  res.json({ queued: n });
});

app.get('/api/health', (_, res) => res.json({ ok: true, ts: now() }));

const PORT = process.env.PORT || 8080;
app.listen(PORT, () => console.log('DENG Rejoin backend on :' + PORT));
