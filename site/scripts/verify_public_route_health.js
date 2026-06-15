'use strict';

// BLOCKER A/B/H — public route health probe for aio.deng.my.id.
// Proves the Roblox upload endpoint returns backend JSON through the Cloudflare
// tunnel, never a Cloudflare 530 / text/html gateway error page.
//
// Usage: node scripts/verify_public_route_health.js
// Exit 0 = healthy, exit 1 = gateway HTML / 530 detected.

const https = require('https');

const BASE = process.env.DENG_PUBLIC_BASE || 'https://aio.deng.my.id';
const UPLOAD = `${BASE}/api/fishit-tracker/update-backpack`;

function probe(method, url, body) {
  return new Promise((resolve, reject) => {
    const payload = body == null ? null : JSON.stringify(body);
    const r = https.request(new URL(url), {
      method,
      headers: {
        'User-Agent': 'Roblox/WinInet',
        ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {}),
      },
      timeout: 20000,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        resolve({
          status: res.statusCode,
          ct: String(res.headers['content-type'] || ''),
          route: res.headers['x-deng-tracker-route'] || null,
          servedBy: res.headers['x-deng-served-by'] || null,
          bodyPrefix: text.slice(0, 200),
          isHtml: /text\/html/i.test(String(res.headers['content-type'] || '')) || /<html|<!doctype html/i.test(text.slice(0, 200)),
        });
      });
    });
    r.on('error', reject);
    r.on('timeout', () => { r.destroy(); reject(new Error('timeout')); });
    if (payload) r.write(payload);
    r.end();
  });
}

const GATEWAY = new Set([520, 521, 522, 523, 524, 525, 526, 530]);

async function main() {
  const failures = [];

  const root = await probe('GET', `${BASE}/`);
  console.log(`/                 status=${root.status} ct=${root.ct} route=${root.route || '-'}`);
  if (GATEWAY.has(root.status)) failures.push(`/ returned gateway ${root.status}`);

  // Empty payload must be a backend validation response (400 JSON), never gateway HTML.
  const empty = await probe('POST', UPLOAD, {});
  console.log(`update-backpack{} status=${empty.status} ct=${empty.ct} route=${empty.route || '-'} servedBy=${empty.servedBy || '-'}`);
  console.log(`  body: ${empty.bodyPrefix.replace(/\s+/g, ' ').slice(0, 140)}`);
  if (GATEWAY.has(empty.status)) failures.push(`update-backpack returned gateway ${empty.status}`);
  if (empty.isHtml) failures.push('update-backpack returned text/html (Cloudflare gateway page)');
  if (!/json/i.test(empty.ct)) failures.push(`update-backpack content-type not JSON: ${empty.ct}`);

  if (failures.length) {
    console.error('PUBLIC_ROUTE_HEALTH FAIL');
    for (const f of failures) console.error('  - ' + f);
    process.exit(1);
  }
  console.log('PUBLIC_ROUTE_HEALTH OK (backend JSON, no Cloudflare 530/HTML)');
}

main().catch((e) => { console.error('PUBLIC_ROUTE_HEALTH ERROR', e.message); process.exit(1); });
