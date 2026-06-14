'use strict';

let webProxyForwardCount = 0;
let ingestDirectCount = 0;
let ingestViaProxyCount = 0;
let rateLimit429Count = 0;
let queue503Count = 0;
let accepted202Count = 0;
let hardFail503Count = 0;
let coalescedUploadCount = 0;
let deferredEnrichmentCount = 0;
let droppedOldQueuedWorkCount = 0;
let latestPersistSuccessCount = 0;
let latestPersistFailureCount = 0;
let responseBeforeEnrichmentCount = 0;

function recordWebProxyForward() {
  webProxyForwardCount += 1;
}

function recordIngestRequest(viaWebProxy) {
  if (viaWebProxy) ingestViaProxyCount += 1;
  else ingestDirectCount += 1;
}

function recordRateLimit429() {
  rateLimit429Count += 1;
}

function recordQueue503() {
  queue503Count += 1;
  hardFail503Count += 1;
}

function recordAccepted202() {
  accepted202Count += 1;
}

function recordHardFail503() {
  hardFail503Count += 1;
  queue503Count += 1;
}

function recordCoalescedUpload() {
  coalescedUploadCount += 1;
}

function recordDeferredEnrichment() {
  deferredEnrichmentCount += 1;
}

function recordDroppedOldQueuedWork() {
  droppedOldQueuedWorkCount += 1;
}

function recordLatestPersistSuccess() {
  latestPersistSuccessCount += 1;
}

function recordLatestPersistFailure() {
  latestPersistFailureCount += 1;
}

function recordResponseBeforeEnrichment() {
  responseBeforeEnrichmentCount += 1;
}

function getTrackerRouteMetrics() {
  return {
    webProxyForwardCount,
    ingestDirectCount,
    ingestViaProxyCount,
    directIngestPublicCount: ingestDirectCount,
    proxyFallbackCount: ingestViaProxyCount,
    rateLimit429Count,
    rateLimited429Count: rateLimit429Count,
    queue503Count,
    hardFail503Count,
    accepted202Count,
    coalescedUploadCount,
    deferredEnrichmentCount,
    droppedOldQueuedWorkCount,
    latestPersistSuccessCount,
    latestPersistFailureCount,
    responseBeforeEnrichmentCount,
  };
}

function _resetForTests() {
  webProxyForwardCount = 0;
  ingestDirectCount = 0;
  ingestViaProxyCount = 0;
  rateLimit429Count = 0;
  queue503Count = 0;
  accepted202Count = 0;
  hardFail503Count = 0;
  coalescedUploadCount = 0;
  deferredEnrichmentCount = 0;
  droppedOldQueuedWorkCount = 0;
  latestPersistSuccessCount = 0;
  latestPersistFailureCount = 0;
  responseBeforeEnrichmentCount = 0;
}

module.exports = {
  recordWebProxyForward,
  recordIngestRequest,
  recordRateLimit429,
  recordQueue503,
  recordAccepted202,
  recordHardFail503,
  recordCoalescedUpload,
  recordDeferredEnrichment,
  recordDroppedOldQueuedWork,
  recordLatestPersistSuccess,
  recordLatestPersistFailure,
  recordResponseBeforeEnrichment,
  getTrackerRouteMetrics,
  _resetForTests,
};
