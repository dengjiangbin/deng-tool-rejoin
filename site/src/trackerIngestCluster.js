'use strict';

/**
 * Optional multi-process ingest bootstrap. Session shards are disk-backed; each
 * worker reloads the target account shard before merging an upload so cluster
 * workers do not split presence state. Enable with TRACKER_INGEST_WORKERS > 1.
 */

const cluster = require('cluster');
const os = require('os');

function resolveWorkerCount() {
  const raw = Number(process.env.TRACKER_INGEST_WORKERS || 0);
  if (!Number.isFinite(raw) || raw <= 1) return 1;
  return Math.min(Math.floor(raw), 16);
}

function runIngestCluster(startWorker) {
  const workers = resolveWorkerCount();
  if (workers <= 1 || !cluster.isPrimary) {
    startWorker();
    return;
  }

  let primaryShuttingDown = false;
  const port = parseInt(process.env.TRACKER_INGEST_PORT || '8792', 10);
  try {
    const { preBindReclaimSingleOwner } = require('./reclaimPort');
    const killed = preBindReclaimSingleOwner(port, '[deng-tracker-ingest]');
    if (killed > 0) {
      const waitUntil = Date.now() + 1200;
      while (Date.now() < waitUntil) { /* brief spin before worker bind */ }
    }
  } catch (_) { /* best effort */ }

  console.log(
    '[deng-tracker-ingest] cluster primary spawning %d workers (cpus=%d)',
    workers,
    os.cpus().length,
  );

  process.env.TRACKER_INGEST_CLUSTER = '1';

  for (let i = 0; i < workers; i += 1) {
    cluster.fork();
  }

  cluster.on('exit', (worker, code, signal) => {
    if (primaryShuttingDown) return;
    console.warn(
      '[deng-tracker-ingest] worker pid=%s exit code=%s signal=%s — reforking',
      worker.process.pid,
      code,
      signal,
    );
    cluster.fork();
  });

  function shutdownPrimary(signal) {
    if (primaryShuttingDown) return;
    primaryShuttingDown = true;
    console.log(`[deng-tracker-ingest] cluster primary ${signal} — stopping workers`);
    for (const id of Object.keys(cluster.workers || {})) {
      try { cluster.workers[id].process.kill('SIGTERM'); } catch (_) { /* ignore */ }
    }
    setTimeout(() => process.exit(0), 9000).unref();
  }

  process.on('SIGTERM', () => shutdownPrimary('SIGTERM'));
  process.on('SIGINT', () => shutdownPrimary('SIGINT'));
}

module.exports = {
  resolveWorkerCount,
  runIngestCluster,
};
