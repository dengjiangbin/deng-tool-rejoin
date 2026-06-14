'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const {
  MINIMUM_TRACKER_BUILD,
  PRODUCTION_TRACKER_BUILD,
  isAllowedTrackerBuild,
  isProductionTrackerBuild,
} = require('./fishitTrackerBuild');
const ALLOWED_TRACKER_CHANNEL = 'fish-it-main';
const ALLOWED_TRACKER_RAW_URL = 'https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua';

const LEGACY_SCRIPT_SOURCE_PATTERNS = [
  /deng-tool-rejoin\/main\/(?:dist\/)?tracker\.lua/i,
  /deng-fishtracker-dist\/main\/dist\/tracker\.lua/i,
  /fish-it\/main\/dist\/tracker\.lua/i,
  /tool\.deng\.my\.id\/tracker\.lua/i,
];

const LEGACY_TRACKER_BUILD_PATTERNS = [
  /^BLOCKER10ZT[0-9]/i,
  /^BLOCKER10Z[A-S]/i,
  /^BLOCKER10ZTG/i,
  /^BLOCKER10ZL/i,
  /^BLOCKER10ZW/i,
  /^BLOCKER10[0-9]/i,
  /^BLOCKER[0-9]/i,
];

let cachedScriptPathHash = null;

function resolveLocalTrackerPathHash() {
  if (cachedScriptPathHash) return cachedScriptPathHash;
  try {
    const distPath = path.join(__dirname, '..', '..', 'dist', 'tracker.lua');
    if (fs.existsSync(distPath)) {
      cachedScriptPathHash = crypto.createHash('sha256').update(fs.readFileSync(distPath)).digest('hex');
      return cachedScriptPathHash;
    }
  } catch (_) {
    /* ignore */
  }
  cachedScriptPathHash = null;
  return null;
}

function normaliseText(raw, maxLen = 240) {
  if (raw == null) return null;
  const s = String(raw).trim();
  if (!s) return null;
  return s.slice(0, maxLen);
}

function extractTrackerProof(body) {
  const b = body && typeof body === 'object' ? body : {};
  const proof = b.trackerClientProof && typeof b.trackerClientProof === 'object'
    ? b.trackerClientProof
    : {};
  return {
    trackerBuild: normaliseText(b.trackerBuild || proof.trackerBuild, 120),
    trackerChannel: normaliseText(b.trackerChannel || proof.trackerChannel, 80),
    scriptSource: normaliseText(b.scriptSource || proof.scriptSource, 240),
    scriptPathHash: normaliseText(b.scriptPathHash || proof.scriptPathHash || b.scriptSignature || proof.scriptSignature, 128),
  };
}

function isLegacyScriptSource(scriptSource) {
  if (!scriptSource) return false;
  return LEGACY_SCRIPT_SOURCE_PATTERNS.some((re) => re.test(scriptSource));
}

function isLegacyTrackerBuild(build) {
  if (!build) return true;
  const s = String(build);
  if (isAllowedTrackerBuild(s)) return false;
  if (s.includes('LOADER_REGISTER_LIMIT_FIX')) return true;
  if (s.includes('LOADER_FIX_REGISTER_LIMIT')) return true;
  if (s.includes('NEW_FISH_IT_ONLY')) return true;
  return LEGACY_TRACKER_BUILD_PATTERNS.some((re) => re.test(s));
}

function validateTrackerClientProof(body) {
  const proof = extractTrackerProof(body);
  const reasons = [];

  if (!proof.trackerBuild) reasons.push('missing_tracker_build');
  else if (isLegacyTrackerBuild(proof.trackerBuild)) {
    reasons.push('outdated_tracker_build');
    return {
      ok: false,
      status: 403,
      error: 'OUTDATED_TRACKER_BUILD',
      reasons,
      required: {
        trackerBuild: PRODUCTION_TRACKER_BUILD,
        trackerChannel: ALLOWED_TRACKER_CHANNEL,
        scriptSource: ALLOWED_TRACKER_RAW_URL,
      },
      proof,
    };
  } else if (!isAllowedTrackerBuild(proof.trackerBuild)) {
    reasons.push('outdated_tracker_build');
    return {
      ok: false,
      status: 403,
      error: 'OUTDATED_TRACKER_BUILD',
      reasons,
      required: {
        trackerBuild: PRODUCTION_TRACKER_BUILD,
        trackerChannel: ALLOWED_TRACKER_CHANNEL,
        scriptSource: ALLOWED_TRACKER_RAW_URL,
      },
      proof,
    };
  }

  if (!proof.trackerChannel) reasons.push('missing_tracker_channel');
  else if (proof.trackerChannel !== ALLOWED_TRACKER_CHANNEL) reasons.push('tracker_channel_not_allowed');

  if (!proof.scriptSource) reasons.push('missing_script_source');
  else if (proof.scriptSource !== ALLOWED_TRACKER_RAW_URL) {
    if (isLegacyScriptSource(proof.scriptSource)) reasons.push('legacy_script_source');
    else reasons.push('script_source_not_allowed');
  }

  const expectedHash = resolveLocalTrackerPathHash();
  if (expectedHash && proof.scriptPathHash && proof.scriptPathHash !== expectedHash) {
    reasons.push('script_path_hash_mismatch');
  }

  if (reasons.length) {
    return {
      ok: false,
      status: 403,
      error: 'tracker_client_rejected',
      reasons,
      required: {
        trackerBuild: PRODUCTION_TRACKER_BUILD,
        trackerChannel: ALLOWED_TRACKER_CHANNEL,
        scriptSource: ALLOWED_TRACKER_RAW_URL,
      },
      proof,
    };
  }

  return { ok: true, proof };
}

function mergeApprovedTrackerProof(body) {
  const b = body && typeof body === 'object' ? { ...body } : {};
  const proof = b.trackerClientProof && typeof b.trackerClientProof === 'object'
    ? { ...b.trackerClientProof }
    : {};
  b.trackerBuild = normaliseText(b.trackerBuild || proof.trackerBuild, 120) || MINIMUM_TRACKER_BUILD;
  if (isLegacyTrackerBuild(b.trackerBuild)) b.trackerBuild = MINIMUM_TRACKER_BUILD;
  b.trackerChannel = normaliseText(b.trackerChannel || proof.trackerChannel, 80) || ALLOWED_TRACKER_CHANNEL;
  b.scriptSource = normaliseText(b.scriptSource || proof.scriptSource, 240) || ALLOWED_TRACKER_RAW_URL;
  b.trackerClientProof = {
    ...proof,
    trackerBuild: b.trackerBuild,
    trackerChannel: b.trackerChannel,
    scriptSource: b.scriptSource,
  };
  return b;
}

function prepareTrackerRequestBody(body, { testMode = false } = {}) {
  const incoming = body && typeof body === 'object' ? body : {};
  if (!testMode || process.env.FISHIT_REQUIRE_TRACKER_PROOF_IN_TEST === '1') return incoming;
  const proof = extractTrackerProof(incoming);
  const testingLegacyBuild = !!(proof.trackerBuild && isLegacyTrackerBuild(proof.trackerBuild));
  const testingLegacySource = isLegacyScriptSource(proof.scriptSource);
  if ((testingLegacyBuild || testingLegacySource) && proof.trackerChannel) return incoming;
  if (proof.trackerChannel && proof.scriptSource && proof.trackerBuild && !isLegacyTrackerBuild(proof.trackerBuild)) {
    return incoming;
  }
  return mergeApprovedTrackerProof(incoming);
}

module.exports = {
  ALLOWED_TRACKER_CHANNEL,
  ALLOWED_TRACKER_RAW_URL,
  MINIMUM_TRACKER_BUILD,
  extractTrackerProof,
  validateTrackerClientProof,
  mergeApprovedTrackerProof,
  prepareTrackerRequestBody,
  isLegacyScriptSource,
  isLegacyTrackerBuild,
  resolveLocalTrackerPathHash,
};
