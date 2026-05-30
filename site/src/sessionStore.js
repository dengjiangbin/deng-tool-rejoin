'use strict';

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const session = require('express-session');

class FileSessionStore extends session.Store {
  constructor(options = {}) {
    super();
    this.dir = options.dir || path.join(os.tmpdir(), 'deng-tool-site-sessions');
    this.ttlMs = options.ttlMs || 7 * 24 * 60 * 60 * 1000;
    fs.mkdirSync(this.dir, { recursive: true });
  }

  _file(sid) {
    const safe = crypto.createHash('sha256').update(String(sid)).digest('hex');
    return path.join(this.dir, `${safe}.json`);
  }

  _expiry(sess) {
    const cookieExpiry = sess?.cookie?.expires ? new Date(sess.cookie.expires).getTime() : 0;
    return Number.isFinite(cookieExpiry) && cookieExpiry > 0
      ? cookieExpiry
      : Date.now() + this.ttlMs;
  }

  get(sid, callback) {
    const file = this._file(sid);
    fs.readFile(file, 'utf8', (err, text) => {
      if (err) {
        if (err.code === 'ENOENT') return callback(null, null);
        return callback(err);
      }
      try {
        const wrapped = JSON.parse(text);
        if (wrapped.expires_at && wrapped.expires_at < Date.now()) {
          return this.destroy(sid, () => callback(null, null));
        }
        return callback(null, wrapped.session || null);
      } catch (parseErr) {
        fs.unlink(file, () => callback(null, null));
      }
    });
  }

  set(sid, sess, callback = () => {}) {
    const file = this._file(sid);
    const tmp = `${file}.${process.pid}.${Date.now()}.${crypto.randomBytes(4).toString('hex')}.tmp`;
    const wrapped = JSON.stringify({
      expires_at: this._expiry(sess),
      session: sess,
    });
    fs.writeFile(tmp, wrapped, { encoding: 'utf8', mode: 0o600 }, (writeErr) => {
      if (writeErr) return callback(writeErr);
      fs.rename(tmp, file, (renameErr) => {
        if (!renameErr) return callback(null);
        if (!['EPERM', 'EACCES', 'ENOENT'].includes(renameErr.code)) {
          fs.unlink(tmp, () => callback(renameErr));
          return;
        }
        fs.copyFile(tmp, file, (copyErr) => {
          fs.unlink(tmp, () => callback(copyErr || null));
        });
      });
    });
  }

  destroy(sid, callback = () => {}) {
    fs.unlink(this._file(sid), (err) => {
      if (err && err.code !== 'ENOENT') return callback(err);
      return callback(null);
    });
  }

  touch(sid, sess, callback = () => {}) {
    this.get(sid, (err, existing) => {
      if (err) return callback(err);
      if (!existing) return callback(null);
      return this.set(sid, sess, callback);
    });
  }
}

module.exports = { FileSessionStore };
