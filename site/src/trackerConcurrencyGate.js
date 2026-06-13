'use strict';
/**
 * Limits concurrent heavy tracker inventory uploads so AIO/auth routes stay responsive.
 * Status heartbeats bypass the gate; inventory work is always queued — never dropped.
 */

const DEFAULT_MAX = Number(process.env.TRACKER_UPLOAD_MAX_CONCURRENT || 12);
const SLOT_STALE_MS = Number(process.env.TRACKER_UPLOAD_SLOT_STALE_MS || 120_000);

let active = 0;
const waiters = [];

function releaseSlot() {
  active = Math.max(0, active - 1);
  const next = waiters.shift();
  if (next) setImmediate(next);
}

function acquireSlot() {
  return new Promise((resolve) => {
    const tryAcquire = () => {
      if (active < DEFAULT_MAX) {
        active += 1;
        resolve(releaseSlot);
      } else {
        waiters.push(tryAcquire);
      }
    };
    tryAcquire();
  });
}

function isStatusOnlyUpload(req) {
  const body = req && req.body;
  return body && body.type === 'tracker_status';
}

function runGated(label, handler, req, res) {
  const started = process.hrtime.bigint();
  let released = false;
  const done = () => {
    if (released) return;
    released = true;
    releaseSlot();
    const ms = Number(process.hrtime.bigint() - started) / 1e6;
    if (ms >= 500) {
      console.warn(`[tracker-gate] slow ${label} ${Math.round(ms)}ms active=${active} queued=${waiters.length}`);
    }
  };
  const watchdog = setTimeout(() => {
    if (!released) {
      console.error(
        `[tracker-gate] stale slot force-release ${label} active=${active} queued=${waiters.length}`,
      );
      done();
    }
  }, SLOT_STALE_MS);
  const clearWatchdog = () => clearTimeout(watchdog);
  acquireSlot().then(() => {
    res.once('finish', () => {
      clearWatchdog();
      done();
    });
    res.once('close', () => {
      clearWatchdog();
      done();
    });
    try {
      handler(req, res);
    } catch (err) {
      clearWatchdog();
      done();
      console.error(`[tracker-gate] ${label} failed:`, err && err.message ? err.message : err);
      if (!res.headersSent) {
        res.status(500).json({ ok: false, error: 'tracker_upload_failed' });
      }
    }
  });
}

/**
 * Wrap inventory upload handler. tracker_status heartbeats are never queued.
 * Inventory uploads are always accepted and queued — never dropped with HTTP 202.
 */
function wrapTrackerUpload(label, handler) {
  return function gatedTrackerUpload(req, res) {
    if (isStatusOnlyUpload(req)) {
      return handler(req, res);
    }
    if (waiters.length >= DEFAULT_MAX * 4) {
      console.warn(
        `[tracker-gate] deep queue ${label} active=${active} queued=${waiters.length}`,
      );
    }
    if (active < DEFAULT_MAX) {
      return runGated(label, handler, req, res);
    }
    setImmediate(() => runGated(label, handler, req, res));
  };
}

function stats() {
  return { active, queued: waiters.length, max: DEFAULT_MAX };
}

function _resetForTests() {
  active = 0;
  waiters.length = 0;
}

module.exports = {
  wrapTrackerUpload,
  stats,
  _resetForTests,
};
