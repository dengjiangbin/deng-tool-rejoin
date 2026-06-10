#!/usr/bin/env node
/**
 * BLOCKER10ZP: verify clean public dist repo — root tracker.lua 404, dist/tracker.lua 200.
 */
const https = require('https');
const {
  PUBLIC_TRACKER_GITHUB_REPO,
  LEGACY_TRACKER_GITHUB_REPO,
  CLEAN_PUBLIC_TRACKER_GITHUB_REPO,
  PROTECTED_DIST_RAW_URL,
} = require('../site/src/fishitTrackerLoadstring');

function fetchHead(url) {
  return new Promise((resolve, reject) => {
    https.get(`${url}?v=${Date.now()}`, (res) => {
      res.resume();
      resolve(res.statusCode || 0);
    }).on('error', reject);
  });
}

async function checkRepo(repo, label) {
  const root = `https://raw.githubusercontent.com/${repo}/main/tracker.lua`;
  const dist = `https://raw.githubusercontent.com/${repo}/main/dist/tracker.lua`;
  const rootStatus = await fetchHead(root);
  const distStatus = await fetchHead(dist);
  const errors = [];
  if (rootStatus === 200) errors.push(`${label}: root tracker.lua is public (HTTP ${rootStatus})`);
  if (distStatus !== 200) errors.push(`${label}: dist/tracker.lua missing (HTTP ${distStatus})`);
  return { label, root, dist, rootStatus, distStatus, errors };
}

(async () => {
  const checks = await Promise.all([
    checkRepo(LEGACY_TRACKER_GITHUB_REPO, 'legacy'),
  ]);
  try {
    const clean = await checkRepo(CLEAN_PUBLIC_TRACKER_GITHUB_REPO, 'clean');
    if (clean.distStatus === 200) checks.push(clean);
    else console.log('SKIP clean repo check: dist not live yet (HTTP ' + clean.distStatus + ')');
  } catch (e) {
    console.log('SKIP clean repo check:', e.message);
  }
  const errors = checks.flatMap((c) => c.errors);
  if (errors.length) {
    console.error('PUBLIC_DIST_REPO_VALIDATION FAILED');
    for (const err of errors) console.error('  -', err);
    for (const c of checks) {
      console.error(`  ${c.label} root=${c.rootStatus} dist=${c.distStatus}`);
    }
    process.exit(1);
  }
  console.log('PUBLIC_DIST_REPO_VALIDATION OK');
  for (const c of checks) {
    const repoName = c.label === 'clean' ? CLEAN_PUBLIC_TRACKER_GITHUB_REPO : (c.label === 'legacy' ? LEGACY_TRACKER_GITHUB_REPO : PUBLIC_TRACKER_GITHUB_REPO);
    console.log(`  ${c.label} repo:`, repoName);
    console.log(`  ${c.label} root tracker.lua:`, c.rootStatus);
    console.log(`  ${c.label} dist/tracker.lua:`, c.distStatus);
  }
  console.log('  canonical loadstring URL:', PROTECTED_DIST_RAW_URL);
})().catch((e) => {
  console.error('PUBLIC_DIST_REPO_VALIDATION FAILED:', e.message);
  process.exit(1);
});
