'use strict';

let webProxyForwardCount = 0;
let ingestDirectCount = 0;
let ingestViaProxyCount = 0;
let rateLimit429Count = 0;
let queue503Count = 0;

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
}

function getTrackerRouteMetrics() {
  return {
    webProxyForwardCount,
    ingestDirectCount,
    ingestViaProxyCount,
    rateLimit429Count,
    queue503Count,
  };
}

function _resetForTests() {
  webProxyForwardCount = 0;
  ingestDirectCount = 0;
  ingestViaProxyCount = 0;
  rateLimit429Count = 0;
  queue503Count = 0;
}

module.exports = {
  recordWebProxyForward,
  recordIngestRequest,
  recordRateLimit429,
  recordQueue503,
  getTrackerRouteMetrics,
  _resetForTests,
};
