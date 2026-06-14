'use strict';

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const session = require('express-session');

const RETRYABLE_FS_CODES = new Set(['EBUSY', 'EPERM', 'EACCES', 'ENOENT']);

let ebusyRetryCount = 0;
let ebusySwallowCount = 0;
let lastMaintenanceStats = null;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

class FileSessionStore extends session.Store {
  constructor(options = {}) {
    super();
    this.dir = options.dir || path.join(os.tmpdir(), 'deng-tool-site-sessions');
    this.ttlMs = options.ttlMs || 7 * 24 * 60 * 60 * 1000;
    fs.mkdirSync(this.dir, { recursive: true });
    this.anonymousTtlMs = Number(process.env.SESSION_ANONYMOUS_TTL_MS || 24 * 60 * 60 * 1000);
    this._startMaintenance();
    if (process.env.NODE_ENV !== 'test') {
      const intervalMs = Number(process.env.SESSION_MAINTENANCE_INTERVAL_MS || 15 * 60 * 1000);
      setInterval(() => {
        this._runMaintenanceBatched().catch((err) => {
          console.warn('[sessionStore] scheduled maintenance failed:', err.message);
        });
      }, intervalMs).unref();
    }
  }

  _isAuthenticatedSessionData(sess) {
    if (!sess || typeof sess !== 'object') return false;
    if (sess.user && typeof sess.user === 'object') return true;
    if (sess.site_user_id) return true;
    if (sess.discord_user_id) return true;
    return false;
  }

  _isAnonymousWrapped(wrapped) {
    return !this._isAuthenticatedSessionData(wrapped?.session);
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
    const started = Date.now();
    const batchSize = 200;
    let tmpRemoved = 0;
    let pruned = 0;
    let anonymousPruned = 0;
    const cutoff = Date.now() - this.ttlMs;
    const anonymousCutoff = Date.now() - this.anonymousTtlMs;
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
              continue;
            }
            if (st.mtimeMs < anonymousCutoff) {
              try {
                const text = await fs.promises.readFile(full, 'utf8');
                const wrapped = JSON.parse(text);
                if (this._isAnonymousWrapped(wrapped)) {
                  await fs.promises.unlink(full);
                  anonymousPruned++;
                }
              } catch {
                // Ignore unreadable session files during cleanup.
              }
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
    if (anonymousPruned > 0) {
      console.log(`[sessionStore] pruned ${anonymousPruned} anonymous session files`);
    }
    lastMaintenanceStats = {
      tmpRemoved,
      pruned,
      anonymousPruned,
      durationMs: Date.now() - started,
      finishedAt: new Date().toISOString(),
    };
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
        ebusyRetryCount += 1;
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
    const isAuth = this._isAuthenticatedSessionData(sess);
    fs.writeFile(tmp, wrapped, { encoding: 'utf8', mode: 0o600 }, (writeErr) => {
      if (writeErr) {
        if (RETRYABLE_FS_CODES.has(writeErr.code) && !isAuth) {
          console.warn('[sessionStore] write skipped (anonymous):', writeErr.code);
          return callback(null);
        }
        return callback(writeErr);
      }
      this._renameOrCopyWithRetry(tmp, file)
        .then(() => callback(null))
        .catch((err) => {
          if (RETRYABLE_FS_CODES.has(err?.code) && !isAuth) {
            ebusySwallowCount += 1;
            console.warn('[sessionStore] set skipped after retries (anonymous):', err.code);
            return callback(null);
          }
          console.error('[sessionStore] set failed for sid=%s auth=%s code=%s', sid.slice(0, 8), isAuth, err?.code || err?.message);
          callback(err || new Error('session_store_write_failed'));
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

function getSessionStoreMetrics(dir) {
  const target = dir || path.join(os.tmpdir(), 'deng-tool-site-sessions');
  let jsonCount = 0;
  let tmpCount = 0;
  let oldestMtime = null;
  try {
    for (const name of fs.readdirSync(target)) {
      const full = path.join(target, name);
      const st = fs.statSync(full);
      if (name.endsWith('.tmp')) tmpCount += 1;
      if (name.endsWith('.json')) jsonCount += 1;
      if (oldestMtime == null || st.mtimeMs < oldestMtime) oldestMtime = st.mtimeMs;
    }
  } catch {
    // ignore
  }
  return {
    dir: target,
    jsonCount,
    tmpCount,
    oldestMtime,
    ebusyRetryCount,
    ebusySwallowCount,
    lastMaintenanceStats,
  };
}

module.exports = { FileSessionStore, getSessionStoreMetrics };
