'use strict';

/**
 * Port reclaim helper for the tracker PM2 services (site / ingest / read).
 *
 * THE BUG THIS FIXES (root cause of the recurring "5-minute dead period"):
 *   On Windows, when PM2 restarts a service the previous fork sometimes does NOT
 *   die — it keeps the listening port (8791/8792/8793) bound forever as an
 *   orphan that PM2 no longer tracks. The freshly-spawned, PM2-tracked instance
 *   then hits EADDRINUSE on every retry, gives up, exits "for a clean restart",
 *   PM2 respawns it, it fails to bind again … producing a 2000+ restart crash
 *   loop. While that loop runs, the orphan keeps accepting uploads but the
 *   constant churn means debounced heartbeat flushes never reach disk, so
 *   lastRealRobloxStatusAt freezes and online accounts turn false-red past the
 *   195s grace.
 *
 * THE FIX:
 *   Before exiting on a persistent EADDRINUSE, find the process actually holding
 *   OUR port. If it is a node / PM2 ProcessContainerFork that is NOT us, it can
 *   only be a stale fork of THIS same service (only one service binds a given
 *   port). Kill it, then let the caller retry the bind. The PM2-tracked instance
 *   reclaims its own port from the zombie instead of crash-looping forever.
 *
 * Safety constraints (all must hold before we kill):
 *   - pid !== process.pid (never kill ourselves)
 *   - the holder is a node.exe process (avoid killing unrelated software)
 *   - it is the listener on the exact port we are trying to bind
 */

const { execSync } = require('child_process');
const http = require('http');

/**
 * Probe http://host:port/health. Resolves true ONLY if a live server answers
 * 2xx quickly. Used to distinguish a HEALTHY holder (another good instance that
 * must never be killed — killing it is what opened the Cloudflare 502 gap) from
 * a hung/dead orphan that legitimately needs reclaiming.
 */
function probeHealthy(port, host, timeoutMs = 1200) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    const req = http.request(
      { host: host || '127.0.0.1', port, path: '/health', method: 'GET', timeout: timeoutMs },
      (res) => {
        const ok = res.statusCode >= 200 && res.statusCode < 300;
        res.resume();
        res.on('end', () => finish(ok));
        res.on('close', () => finish(ok));
      },
    );
    req.on('error', () => finish(false));
    req.on('timeout', () => { req.destroy(); finish(false); });
    req.end();
  });
}

// IMPORTANT: use NATIVE Windows tools (netstat / tasklist / taskkill) only.
// PowerShell cmdlets (Get-NetTCPConnection / Get-CimInstance) have multi-second
// cold-start latency that, when run synchronously inside the listen-error
// handler, blows past the retry window and PM2's listen_timeout — which is what
// let the orphan forks live forever. netstat/tasklist start in ~tens of ms.

function run(command, timeoutMs) {
  try {
    return execSync(command, { encoding: 'utf8', timeout: timeoutMs || 4000, windowsHide: true });
  } catch (err) {
    return (err && (err.stdout || '')) || '';
  }
}

/** PIDs currently LISTENing on the given TCP port (Windows, via netstat). */
function findListenerPids(port) {
  if (process.platform !== 'win32') return [];
  const out = run('netstat -ano -p TCP', 4000);
  const pids = new Set();
  const re = new RegExp(`\\sTCP\\s+\\S*:${port}\\s+\\S+\\s+LISTENING\\s+(\\d+)`, 'i');
  for (const line of out.split(/\r?\n/)) {
    const m = line.match(re);
    if (m) pids.add(parseInt(m[1], 10));
  }
  return [...pids];
}

/** Returns { pid, name } for a Windows PID (via tasklist), or null. */
function describeProcess(pid) {
  if (process.platform !== 'win32') return null;
  const out = run(`tasklist /FI "PID eq ${pid}" /FO CSV /NH`, 4000).trim();
  // CSV: "node.exe","9616","Console","1","380,000 K"
  const m = out.match(/^"([^"]+)","(\d+)"/);
  if (!m) return null;
  return { pid, name: (m[1] || '').trim() };
}

function killPid(pid) {
  try {
    execSync(`taskkill /F /PID ${pid}`, { encoding: 'utf8', timeout: 4000, windowsHide: true });
    return true;
  } catch (_) {
    return false;
  }
}

/**
 * Attempt to reclaim `port` from a stale orphan fork.
 *
 * @param {object} [opts]
 * @param {Set<number>|number[]} [opts.onlyPids] If provided, ONLY kill holders
 *   whose pid is in this set. This is the "persistent holder" guard: the caller
 *   passes the pid(s) that were holding the port at the FIRST EADDRINUSE, so a
 *   different process that grabbed the port mid-wait (a freshly-spawned healthy
 *   sibling, NOT a stuck orphan) is never killed. Without this guard two
 *   overlapping forks reclaim-kill each other forever (the 2000+ restart loop).
 * @returns {{ reclaimed: boolean, killedPids: number[], holders: object[], deferredPids: number[] }}
 */
function reclaimPort(port, logPrefix, opts = {}) {
  const result = { reclaimed: false, killedPids: [], holders: [], deferredPids: [] };
  // Injectable deps make the persistent-holder guard unit-testable without a real
  // Windows port/process. Production always uses the native netstat/tasklist impls.
  const _findListenerPids = opts._findListenerPids || findListenerPids;
  const _describeProcess = opts._describeProcess || describeProcess;
  const _killPid = opts._killPid || killPid;
  const platform = opts._platform || process.platform;
  if (platform !== 'win32') return result;
  const selfPid = opts._selfPid != null ? opts._selfPid : process.pid;
  const onlyPids = opts.onlyPids
    ? new Set([...opts.onlyPids].map((p) => parseInt(p, 10)))
    : null;
  let pids;
  try {
    pids = _findListenerPids(port);
  } catch (_) {
    return result;
  }
  for (const pid of pids) {
    if (pid === selfPid) continue;
    const info = _describeProcess(pid);
    result.holders.push(info || { pid });
    // Only kill node processes (the only thing that should ever bind our port is
    // a stale fork of THIS service — one service per port). Never kill non-node.
    const isNode = info && /node\.exe/i.test(info.name || '');
    if (!isNode) {
      console.warn('%s port %d held by non-node pid %d (%s) — NOT killing', logPrefix, port, pid, (info && info.name) || '?');
      continue;
    }
    // Persistent-holder guard: if the port is now held by a pid that was NOT the
    // original stuck holder, it is a normal restart hand-off (a healthy sibling
    // just bound), not a stuck orphan. Killing it would restart the mutual-kill
    // loop — defer instead and let the bind retry / clean PM2 restart settle it.
    if (onlyPids && !onlyPids.has(pid)) {
      result.deferredPids.push(pid);
      console.warn('%s port %d now held by pid %d (not the original stuck holder) — deferring, likely a restart race not an orphan', logPrefix, port, pid);
      continue;
    }
    console.warn('%s reclaiming port %d from stale orphan node pid %d', logPrefix, port, pid);
    if (_killPid(pid)) {
      result.killedPids.push(pid);
      result.reclaimed = true;
    }
  }
  return result;
}

/**
 * Robust listen-with-reclaim loop shared by all three tracker PM2 services.
 *
 * Behaviour on EADDRINUSE:
 *   - Retry the bind with a short delay (handles the normal restart race where
 *     the previous instance is still releasing the socket within kill_timeout).
 *   - If the port is STILL held after `reclaimAfterMs` (default 1500ms — far
 *     below PM2's listen_timeout of 12s), reclaim it by killing the orphan fork
 *     and bind immediately. Binding fast keeps THIS (PM2-tracked) child as the
 *     listener instead of overshooting listen_timeout and being abandoned as a
 *     new orphan — which was the engine of the 2000+ restart crash loop.
 *   - Allow a few reclaim attempts (in case a fresh orphan races in), then exit
 *     for a clean PM2 restart only as a last resort.
 */
function listenWithReclaim(server, port, host, logPrefix, opts = {}) {
  // reclaimAfterMs MUST exceed PM2's kill_timeout (8000ms) for these services.
  // A normally-restarting sibling is given up to kill_timeout to flush and exit
  // gracefully; only after that window can a still-present holder be a genuine
  // stuck orphan. Reclaiming earlier kills the healthy sibling mid-flush and is
  // exactly what produced the 2000+ restart mutual-kill loop on 8792.
  const reclaimAfterMs = opts.reclaimAfterMs != null ? opts.reclaimAfterMs : 9000;
  const retryDelayMs = opts.retryDelayMs != null ? opts.retryDelayMs : 400;
  // Keep maxMs below PM2 listen_timeout (30000ms) but above reclaimAfterMs so a
  // reclaim attempt actually gets a chance before we exit for a clean restart.
  const maxMs = opts.maxMs != null ? opts.maxMs : 22000;
  const maxReclaims = opts.maxReclaims != null ? opts.maxReclaims : 3;

  // Health gate is injectable for tests; production probes the real /health.
  const _probeHealthy = opts._probeHealthy || probeHealthy;

  let retryStartedAt = 0;
  let reclaims = 0;
  let lastReclaimAt = 0;
  // pid(s) holding the port at the FIRST EADDRINUSE — the only ones we ever
  // treat as a stuck orphan. A different pid that grabs the port mid-wait is a
  // healthy hand-off and must never be killed (persistent-holder guard).
  let originalHolders = null;
  // Set once we confirm a HEALTHY server already owns the port. We then keep
  // retrying quietly as a warm spare and NEVER kill it — killing a healthy
  // holder is precisely what opened the Cloudflare 502 windows.
  let warmSpare = false;

  function start() { server.listen(port, host); }
  function retrySoon(delay) {
    setTimeout(() => { try { server.close(); } catch (_) { /* not listening */ } start(); }, delay);
  }

  server.on('listening', () => {
    retryStartedAt = 0;
    originalHolders = null;
    warmSpare = false;
    console.log(`${logPrefix} Listening on http://${host}:${port}`);
  });

  server.on('error', (err) => {
    if (!err || err.code !== 'EADDRINUSE') {
      console.error(`${logPrefix} Listen error:`, err);
      process.exit(1);
      return;
    }
    const nowMs = Date.now();
    if (!retryStartedAt) retryStartedAt = nowMs;
    if (originalHolders == null) {
      // Capture once (cheap netstat). These are the candidate stuck-orphan pids.
      try { originalHolders = new Set(findListenerPids(port).filter((p) => p !== process.pid)); }
      catch (_) { originalHolders = new Set(); }
    }
    const waitedMs = nowMs - retryStartedAt;

    // Decide once per second after the graceful-release window: is the holder a
    // LIVE server (leave it alone) or a hung/dead orphan (reclaim it)?
    if (waitedMs >= reclaimAfterMs && reclaims < maxReclaims && (nowMs - lastReclaimAt) >= 1000) {
      lastReclaimAt = nowMs;
      Promise.resolve(_probeHealthy(port, host)).then((healthy) => {
        if (healthy) {
          // A good instance is serving. Do NOT kill it. Stand by and bind the
          // instant it frees (legit restart). This breaks the kill→PM2-restart
          // →kill loop that PM2 reported as a 16s SIGINT restart cycle.
          if (!warmSpare) {
            warmSpare = true;
            console.warn(`${logPrefix} ${port} is held by a HEALTHY instance — standing by as warm spare (will NOT kill it; binds the instant it frees)`);
          }
          retryStartedAt = nowMs;   // reset window so we re-probe periodically
          originalHolders = null;
          retrySoon(Math.max(retryDelayMs, 1000));
          return;
        }
        // Holder does not answer /health → genuine hung/dead orphan → reclaim.
        reclaims += 1;
        const r = reclaimPort(port, logPrefix, { onlyPids: originalHolders });
        if (r.reclaimed) {
          console.warn(`${logPrefix} reclaimed ${port} from DEAD orphan pid(s) %j — rebinding`, r.killedPids);
          retryStartedAt = 0;
          originalHolders = null;
          warmSpare = false;
        }
        retrySoon(retryDelayMs);
      }).catch(() => retrySoon(retryDelayMs));
      return;
    }

    // A warm spare never exits — it waits out a healthy holder indefinitely.
    if (waitedMs <= maxMs || warmSpare) {
      if (!warmSpare) {
        console.warn(`${logPrefix} ${port} busy, retrying bind in ${retryDelayMs}ms (waited ${waitedMs}ms)`);
      }
      retrySoon(warmSpare ? Math.max(retryDelayMs, 1000) : retryDelayMs);
      return;
    }
    console.error(`${logPrefix} ${port} still busy after ${waitedMs}ms — exiting for clean PM2 restart`);
    process.exit(1);
  });

  start();
}

module.exports = { findListenerPids, describeProcess, killPid, reclaimPort, listenWithReclaim, probeHealthy };
