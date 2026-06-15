'use strict';

/**
 * Tracker upload acceptance — per-account coalescing instead of hard 429 rejections.
 * Roblox clients share egress IPs; IP-based rate limits break hundreds of accounts.
 */

const { trackerUploadKey } = require('./trackerUploadRateLimit');
const { recordCoalescedUpload } = require('./trackerRouteMetrics');

const COALESCE_WINDOW_MS = Number(process.env.TRACKER_UPLOAD_COALESCE_MS || 750);
const MAX_BODY_BYTES = Number(process.env.TRACKER_UPLOAD_MAX_BODY_BYTES || 512 * 1024);

/** @type {Map<string, { at: number, hash: string, count: number }>} */
const recentByLane = new Map();

function uploadLaneKey(req) {
  const body = req.body || {};
  const account = trackerUploadKey(req);
  const type = String(body.type || body.payloadType || 'inventory_snapshot').trim().toLowerCase();
  if (body.leaderstatsOnlyUpload === true || body.uploadPath === 'playerdata_leaderstats_only') {
    return `${account}:required_leaderstats`;
  }
  if (body.debugUpload === true || body.uploadPath === 'debug_upload' || body.uploadMode === 'debug') {
    return `${account}:debug_upload`;
  }
  if (type === 'tracker_status' || body.uploadPath === 'required_status') {
    return `${account}:tracker_status`;
  }
  if (body.playerDataGameItemDbProof?.uploadPath === 'playerdata_gameitemdb'
    || body.inventorySource === 'playerdata_gameitemdb') {
    return `${account}:playerdata_gameitemdb`;
  }
  return `${account}:${type || 'inventory_snapshot'}`;
}

function lanePayloadHash(body) {
  try {
    const ps = body?.playerStats || {};
    return [
      body?.type || '',
      body?.uploadPath || '',
      body?.uploadSeq ?? '',
      ps.coins ?? '',
      ps.totalCaught ?? '',
      Array.isArray(body?.fishItems) ? body.fishItems.length : 0,
      Array.isArray(body?.stoneItems) ? body.stoneItems.length : 0,
      body?.inventoryChecksum || body?.leaderstatsChecksum || '',
    ].join('|');
  } catch {
    return String(Date.now());
  }
}

function isValidTrackerUploadBody(req) {
  const body = req.body;
  if (!body || typeof body !== 'object') return false;
  const username = body.username != null ? String(body.username).trim() : '';
  const userId = body.userId != null ? String(body.userId).trim() : '';
  const runId = body.runId != null ? String(body.runId).trim() : '';
  return Boolean(username || userId || runId);
}

function trackerUploadCoalesceMiddleware(req, res, next) {
  const contentLength = Number(req.headers['content-length'] || 0);
  if (contentLength > MAX_BODY_BYTES) {
    return res.status(413).json({
      ok: false,
      error: 'payload_too_large',
      limit: MAX_BODY_BYTES,
    });
  }

  if (!isValidTrackerUploadBody(req)) {
    return res.status(400).json({
      ok: false,
      error: 'missing_tracker_identity',
      message: 'username, userId, or runId required',
    });
  }

  const laneKey = uploadLaneKey(req);
  const hash = lanePayloadHash(req.body);
  const now = Date.now();
  const prev = recentByLane.get(laneKey);

  if (prev && (now - prev.at) < COALESCE_WINDOW_MS && prev.hash === hash) {
    recordCoalescedUpload();
    req.trackerUploadCoalesced = true;
    req.trackerCoalesceCount = (prev.count || 1) + 1;
    recentByLane.set(laneKey, { at: now, hash, count: req.trackerCoalesceCount });
    if (!res.headersSent) {
      return res.status(202).json({
        ok: true,
        accepted: true,
        coalesced: true,
        note: 'duplicate_lane_upload_coalesced',
        lane: laneKey.split(':').slice(-1)[0],
        minNextUploadSeconds: 60,
      });
    }
    return undefined;
  }

  recentByLane.set(laneKey, { at: now, hash, count: 1 });
  if (recentByLane.size > 50000) {
    const cutoff = now - COALESCE_WINDOW_MS * 4;
    for (const [k, v] of recentByLane) {
      if (v.at < cutoff) recentByLane.delete(k);
    }
  }

  return next();
}

function _resetForTests() {
  recentByLane.clear();
}

module.exports = {
  uploadLaneKey,
  trackerUploadCoalesceMiddleware,
  isValidTrackerUploadBody,
  _resetForTests,
};
