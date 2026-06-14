'use strict';

const crypto = require('crypto');
const https = require('https');

const PUBLIC_TRACKER_RAW_URL = 'https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua';

function decodeDistTrackerBuild(raw) {
  const text = String(raw || '');
  const match = text.match(/local __B=\[\[([\s\S]*?)\]\]\s*\nlocal __A=/);
  if (!match) return null;
  try {
    const decoded = Buffer.from(match[1], 'base64').toString('utf8');
    const buildMatch = decoded.match(/TRACKER_BUILD\s*=\s*"([^"]+)"/);
    return buildMatch ? buildMatch[1] : null;
  } catch {
    return null;
  }
}

function fetchPublicTrackerRaw(timeoutMs = 15000) {
  const url = `${PUBLIC_TRACKER_RAW_URL}?v=${Date.now()}`;
  return new Promise((resolve, reject) => {
    const req = https.get(url, { timeout: timeoutMs }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf8');
        if (res.statusCode !== 200) {
          reject(new Error(`public_tracker_fetch_http_${res.statusCode}`));
          return;
        }
        resolve(body);
      });
    });
    req.on('error', reject);
    req.on('timeout', () => {
      req.destroy();
      reject(new Error('public_tracker_fetch_timeout'));
    });
  });
}

async function loadPublicTrackerBuildProof() {
  const raw = await fetchPublicTrackerRaw();
  const sha256 = crypto.createHash('sha256').update(raw, 'utf8').digest('hex');
  const headerMatch = raw.match(/DENG protected tracker dist \| ([^|]+) \|/);
  const build = decodeDistTrackerBuild(raw);
  return {
    url: PUBLIC_TRACKER_RAW_URL,
    sha256,
    bytes: Buffer.byteLength(raw, 'utf8'),
    buildMarker: build,
    distHeaderBuild: headerMatch ? headerMatch[1].trim() : null,
  };
}

module.exports = {
  PUBLIC_TRACKER_RAW_URL,
  decodeDistTrackerBuild,
  fetchPublicTrackerRaw,
  loadPublicTrackerBuildProof,
};
