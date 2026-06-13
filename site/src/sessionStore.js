'use strict';

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const session = require('express-session');

const RETRYABLE_FS_CODES = new Set(['EBUSY', 'EPERM', 'EACCES', 'ENOENT']);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

class FileSessionStore extends session.Store {
  constructor(options = {}) {
    super();
    this.dir = options.dir || path.join(os.tmpdir(), 'deng-tool-site-sessions');
    this.ttlMs = options.ttlMs || 7 * 24 * 60 * 60 * 1000;
    fs.mkdirSync(this.dir, { recursive: true });
    this._startMaintenance();
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

  _startMaintenance() {
    setImmediate(() => {
      this._runMaintenanceBatched().catch((err) => {
        console.warn('[sessionStore] maintenance failed:', err.message);
      });
    });
  }

  async _runMaintenanceBatched() {
    const batchSize = 200;
    let tmpRemoved = 0;
    let pruned = 0;
    const cutoff = Date.now() - this.ttlMs;
    let entries;
    try {
      entries = await fs.promises.readdir(this.dir);
    } catch (err) {
      console.warn('[sessionStore] readdir failed:', err.message);
      return;
    }

    const now = Date.now();
    for (let i = 0; i < entries.length; i += batchSize) {
      const slice = entries.slice(i, i + batchSize);
      for (const name of slice) {
        const full = path.join(this.dir, name);
        try {
          if (name.endsWith('.tmp')) {
            const st = await fs.promises.stat(full);
            if (now - st.mtimeMs > 60_000) {
              await fs.promises.unlink(full);
              tmpRemoved++;
            }
            continue;
          }
          if (name.endsWith('.json')) {
            const st = await fs.promises.stat(full);
            if (st.mtimeMs < cutoff) {
              await fs.promises.unlink(full);
              pruned++;
            }
          }
        } catch {
          // Ignore per-file errors during maintenance.
        }
      }
      await sleep(0);
    }

    if (tmpRemoved > 0) {
      console.log(`[sessionStore] removed ${tmpRemoved} stale tmp session files`);
    }
    if (pruned > 0) {
      console.log(`[sessionStore] pruned ${pruned} expired session files`);
    }
  }

  _cleanTmpFiles() {
    // Kept for tests — production uses _runMaintenanceBatched.
    let removed = 0;
    try {
      const now = Date.now();
      for (const name of fs.readdirSync(this.dir)) {
        if (!name.endsWith('.tmp')) continue;
        const full = path.join(this.dir, name);
        try {
          const st = fs.statSync(full);
          if (now - st.mtimeMs > 60_000) {
            fs.unlinkSync(full);
            removed++;
          }
        } catch {
          // Ignore per-file errors during cleanup.
        }
      }
      if (removed > 0) {
        console.log(`[sessionStore] removed ${removed} stale tmp session files`);
      }
    } catch (err) {
      console.warn('[sessionStore] tmp cleanup failed:', err.message);
    }
  }

  _pruneExpiredByMtime() {
    const cutoff = Date.now() - this.ttlMs;
    let removed = 0;
    try {
      for (const name of fs.readdirSync(this.dir)) {
        if (!name.endsWith('.json')) continue;
        const full = path.join(this.dir, name);
        try {
          const st = fs.statSync(full);
          if (st.mtimeMs < cutoff) {
            fs.unlinkSync(full);
            removed++;
          }
        } catch {
          // Ignore per-file errors during prune.
        }
      }
      if (removed > 0) {
        console.log(`[sessionStore] pruned ${removed} expired session files`);
      }
    } catch (err) {
      console.warn('[sessionStore] prune failed:', err.message);
    }
  }

  async _renameOrCopyWithRetry(tmp, file, attempt = 0) {
    const maxAttempts = 6;
    try {
      await fs.promises.rename(tmp, file);
      return;
    } catch (renameErr) {
      if (RETRYABLE_FS_CODES.has(renameErr.code) && attempt < maxAttempts) {
        await sleep(Math.min(40 * (attempt + 1), 300));
        return this._renameOrCopyWithRetry(tmp, file, attempt + 1);
      }
      if (!RETRYABLE_FS_CODES.has(renameErr.code)) {
        await fs.promises.unlink(tmp).catch(() => {});
        throw renameErr;
      }
    }

    try {
      await fs.promises.copyFile(tmp, file);
    } finally {
      await fs.promises.unlink(tmp).catch(() => {});
    }
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
      } catch {
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
      if (writeErr) {
        if (RETRYABLE_FS_CODES.has(writeErr.code)) {
          console.warn('[sessionStore] write skipped:', writeErr.code);
          return callback(null);
        }
        return callback(writeErr);
      }
      this._renameOrCopyWithRetry(tmp, file)
        .then(() => callback(null))
        .catch((err) => {
          if (RETRYABLE_FS_CODES.has(err?.code)) {
            console.warn('[sessionStore] set skipped after retries:', err.code);
            return callback(null);
          }
          callback(err);
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
