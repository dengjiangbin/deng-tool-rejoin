'use strict';

const os = require('os');
const { getMetrics: getWebEventLoopMetrics } = require('./trackerEventLoopMonitor');
const { getTrackerRouteMetrics } = require('./trackerRouteMetrics');
const { getSessionStoreMetrics } = require('./sessionStore');
const { getDiskFreeStatus, isDriveUsedForWrites } = require('./diskMonitor');
const fishitSessionStore = require('./fishitSessionStore');

function safeRequire(name) {
  try {
    return require(name);
  } catch {
    return null;
  }
}

function buildStabilityStatus() {
  const mem = process.memoryUsage();
  const disk = getDiskFreeStatus();
  const sessionDir = process.env.TOOL_SITE_SESSION_DIR || '';
  const sessionMetrics = getSessionStoreMetrics(sessionDir || undefined);
  const fishitStore = fishitSessionStore.getSessionFileMetrics
    ? fishitSessionStore.getSessionFileMetrics()
    : fishitSessionStore.getStoreMeta();

  let trackerGate = null;
  const gate = safeRequire('./trackerConcurrencyGate');
  if (gate && typeof gate.stats === 'function') trackerGate = gate.stats();

  const service = process.env.TRACKER_INGEST_MODE === '1'
    ? 'deng-tracker-ingest'
    : 'deng-tool-site';

  return {
    status: 'ok',
    service,
    timestamp: new Date().toISOString(),
    deployMarker: process.env.TOOL_SITE_ASSET_VERSION || null,
    gitCommit: process.env.GIT_COMMIT || null,
    ports: {
      web: Number(process.env.TOOL_SITE_PORT || 8791),
      ingest: Number(process.env.TRACKER_INGEST_PORT || 8792),
      controlPanel: 3099,
    },
    process: {
      pid: process.pid,
      uptimeSec: Math.round(process.uptime()),
      heapUsedPct: mem.heapTotal ? Math.round((mem.heapUsed / mem.heapTotal) * 1000) / 10 : null,
      rssMb: Math.round(mem.rss / 1024 / 1024),
      eventLoop: getWebEventLoopMetrics(),
    },
    trackerRoute: getTrackerRouteMetrics(),
    trackerQueue: trackerGate,
    sessions: {
      browser: sessionMetrics,
      fishitLive: fishitStore,
    },
    disk: {
      ...disk,
      appWritesToD: isDriveUsedForWrites('D'),
      dIsInstallMedia: disk.drives?.some((d) => d.drive === 'D:' && d.sizeBytes < 6 * 1024 ** 3) || false,
    },
    host: {
      platform: os.platform(),
      loadAvg: os.loadavg(),
      freeMemMb: Math.round(os.freemem() / 1024 / 1024),
    },
  };
}

module.exports = {
  buildStabilityStatus,
};
