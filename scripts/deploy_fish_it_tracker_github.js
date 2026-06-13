#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const DIST = path.resolve(process.argv[2] || path.join(ROOT, 'dist', 'tracker.lua'));
const REPO = 'dengjiangbin/fish-it';
const BRANCH = 'main';
const REMOTE_PATH = 'tracker.lua';
const TOMBSTONE = 'error("This FishTracker loader path is retired. Use https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua only.")';

function getToken() {
  const out = execSync('git credential fill', {
    input: 'protocol=https\nhost=github.com\n\n',
  }).toString();
  const m = out.match(/^password=(.+)$/m);
  if (!m) throw new Error('GitHub credential unavailable');
  return m[1].trim();
}

function gh(method, apiPath, body) {
  const https = require('https');
  const token = getToken();
  return new Promise((resolve, reject) => {
    const payload = body == null ? null : JSON.stringify(body);
    const req = https.request({
      hostname: 'api.github.com',
      path: apiPath,
      method,
      headers: {
        Authorization: `token ${token}`,
        'User-Agent': 'deng-fish-it-deploy',
        Accept: 'application/vnd.github+json',
        ...(payload ? {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(payload),
        } : {}),
      },
    }, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; });
      res.on('end', () => {
        let parsed = data;
        try { parsed = data ? JSON.parse(data) : null; } catch (_) { /* keep raw */ }
        if (res.statusCode >= 400) {
          const msg = typeof parsed === 'object' && parsed && parsed.message
            ? parsed.message
            : data;
          reject(new Error(`${method} ${apiPath} -> HTTP ${res.statusCode}: ${msg}`));
          return;
        }
        resolve({ status: res.statusCode, data: parsed });
      });
    });
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

async function upsertFile(repo, filePath, content, message) {
  let sha;
  try {
    const existing = await gh('GET', `/repos/${repo}/contents/${encodeURIComponent(filePath)}?ref=${BRANCH}`);
    sha = existing.data.sha;
  } catch (_) {
    sha = undefined;
  }
  const body = {
    message,
    content: Buffer.from(content, 'utf8').toString('base64'),
    branch: BRANCH,
  };
  if (sha) body.sha = sha;
  const res = await gh('PUT', `/repos/${repo}/contents/${encodeURIComponent(filePath)}`, body);
  return res.data.commit.sha;
}

async function deleteFile(repo, filePath, message) {
  try {
    const existing = await gh('GET', `/repos/${repo}/contents/${encodeURIComponent(filePath)}?ref=${BRANCH}`);
    await gh('DELETE', `/repos/${repo}/contents/${encodeURIComponent(filePath)}`, {
      message,
      sha: existing.data.sha,
      branch: BRANCH,
    });
    return true;
  } catch (err) {
    if (/404/.test(String(err.message))) return false;
    throw err;
  }
}

(async () => {
  if (!fs.existsSync(DIST)) {
    console.error('Missing dist tracker:', DIST);
    process.exit(1);
  }
  const tracker = fs.readFileSync(DIST, 'utf8');
  const mainSha = await upsertFile(REPO, REMOTE_PATH, tracker, 'Deploy obfuscated FishTracker at main/tracker.lua (NEW_FISH_IT_ONLY_2026_06_11)');
  // GitHub raw CDN for /main/ can serve stale bytes after an in-place update. Delete+recreate
  // forces raw.githubusercontent.com/<repo>/main/tracker.lua to match the latest blob.
  try {
    const existing = await gh('GET', `/repos/${REPO}/contents/${encodeURIComponent(REMOTE_PATH)}?ref=${BRANCH}`);
    await gh('DELETE', `/repos/${REPO}/contents/${encodeURIComponent(REMOTE_PATH)}`, {
      message: 'Cache-bust delete tracker.lua before redeploy',
      sha: existing.data.sha,
      branch: BRANCH,
    });
    await upsertFile(REPO, REMOTE_PATH, tracker, 'Redeploy tracker.lua after cache-bust delete');
  } catch (err) {
    console.warn('RAW_CACHE_BUST_SKIP', err.message);
  }
  console.log('FISH_IT_DEPLOY OK');
  console.log('  repo:', `https://github.com/${REPO}`);
  console.log('  raw:', `https://raw.githubusercontent.com/${REPO}/${BRANCH}/${REMOTE_PATH}`);
  console.log('  commit:', mainSha);
  console.log('  bytes:', Buffer.byteLength(tracker, 'utf8'));

  const tombstoneTargets = [
    { repo: REPO, path: 'dist/tracker.lua', note: 'retire fish-it dist path' },
    { repo: 'dengjiangbin/deng-tool-rejoin', path: 'tracker.lua', note: 'retire deng-tool-rejoin root tracker.lua' },
    { repo: 'dengjiangbin/deng-tool-rejoin', path: 'dist/tracker.lua', note: 'retire deng-tool-rejoin dist tracker.lua' },
    { repo: 'dengjiangbin/deng-fishtracker-dist', path: 'dist/tracker.lua', note: 'retire deng-fishtracker-dist dist tracker.lua' },
    { repo: 'dengjiangbin/deng-fishtracker-dist', path: 'tracker.lua', note: 'retire deng-fishtracker-dist root tracker.lua' },
  ];

  for (const target of tombstoneTargets) {
    try {
      const sha = await upsertFile(target.repo, target.path, TOMBSTONE, `Tombstone ${target.path} — ${target.note}`);
      console.log('TOMBSTONE OK', target.repo, target.path, sha.slice(0, 7));
    } catch (err) {
      console.warn('TOMBSTONE SKIP', target.repo, target.path, err.message);
    }
  }

  await deleteFile(REPO, 'dist/README-DIST-TRACKER.md', 'Remove obsolete dist docs from fish-it').catch(() => {});

  const crypto = require('crypto');
  const https = require('https');
  const expectedSha = crypto.createHash('sha256').update(tracker, 'utf8').digest('hex');
  const rawUrl = `https://raw.githubusercontent.com/${REPO}/${BRANCH}/${REMOTE_PATH}`;
  const deadline = Date.now() + 120000;
  let rawFresh = false;
  while (Date.now() < deadline) {
    await new Promise((resolve) => {
      https.get(`${rawUrl}?v=${Date.now()}`, (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => {
          const body = Buffer.concat(chunks).toString('utf8');
          const sha = crypto.createHash('sha256').update(body, 'utf8').digest('hex');
          rawFresh = sha === expectedSha;
          resolve();
        });
      }).on('error', () => resolve());
    });
    if (rawFresh) break;
    await new Promise((r) => setTimeout(r, 5000));
  }
  if (rawFresh) {
    console.log('RAW_CDN_OK', rawUrl);
  } else {
    console.warn('RAW_CDN_STALE', rawUrl, 'expected', expectedSha.slice(0, 12));
  }
})().catch((err) => {
  console.error('FISH_IT_DEPLOY FAILED:', err.message);
  process.exit(1);
});
