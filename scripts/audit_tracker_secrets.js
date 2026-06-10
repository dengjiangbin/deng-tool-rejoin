#!/usr/bin/env node
/**
 * Static secret audit for tracker Lua sources (raw dev + protected dist).
 * Fails if obvious API keys, tokens, or signing secrets appear in file text.
 */
const fs = require('fs');
const path = require('path');

const SECRET_PATTERNS = [
  { name: 'OpenAI-style API key', re: /\bsk-[A-Za-z0-9]{20,}\b/ },
  { name: 'GitHub PAT', re: /\bghp_[A-Za-z0-9]{20,}\b/ },
  { name: 'AWS access key', re: /\bAKIA[0-9A-Z]{16}\b/ },
  { name: 'Stripe live key', re: /\bsk_live_[A-Za-z0-9]+\b/ },
  { name: 'Discord bot token', re: /\b[A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}\b/ },
  { name: 'Luraph API key assignment', re: /luraph[_-]?api[_-]?key\s*=\s*["'][^"']+["']/i },
  { name: 'Generic apiKey assignment', re: /\bapi[_-]?key\s*=\s*["'][A-Za-z0-9_\-]{16,}["']/i },
  { name: 'Webhook URL with secret path', re: /discord(?:app)?\.com\/api\/webhooks\/\d+\/[A-Za-z0-9_-]+/i },
  { name: 'Supabase service role', re: /SUPABASE_SERVICE_ROLE/i },
  { name: 'Private signing secret assignment', re: /\b(signing[_-]?secret|private[_-]?salt|admin[_-]?token)\s*=\s*["'][^"']{8,}["']/i },
  { name: 'Bearer token assignment', re: /Bearer\s+[A-Za-z0-9._\-]{20,}/ },
  { name: 'Database connection string', re: /postgres(?:ql)?:\/\/[^\s"']+:[^\s"']+@/i },
];

function auditFile(filePath) {
  const abs = path.resolve(filePath);
  if (!fs.existsSync(abs)) {
    return { file: abs, ok: false, missing: true, hits: [] };
  }
  const src = fs.readFileSync(abs, 'utf8');
  const hits = [];
  for (const { name, re } of SECRET_PATTERNS) {
    if (re.test(src)) hits.push(name);
  }
  return { file: abs, ok: hits.length === 0, missing: false, hits, bytes: Buffer.byteLength(src, 'utf8') };
}

function auditPaths(paths) {
  const results = paths.map(auditFile);
  const failed = results.filter((r) => !r.ok);
  if (failed.length) {
    console.error('TRACKER_SECRET_AUDIT FAILED');
    for (const r of failed) {
      if (r.missing) console.error(`  - missing: ${r.file}`);
      else console.error(`  - ${r.file}: ${r.hits.join(', ')}`);
    }
    process.exit(1);
  }
  console.log('TRACKER_SECRET_AUDIT OK');
  for (const r of results) {
    console.log(`  file: ${r.file}`);
    console.log(`  bytes: ${r.bytes}`);
  }
}

if (require.main === module) {
  const root = path.join(__dirname, '..');
  const paths = process.argv.slice(2).length
    ? process.argv.slice(2)
    : [
      path.join(root, 'tracker.lua'),
      path.join(root, 'dist', 'tracker.lua'),
    ];
  auditPaths(paths);
}

module.exports = { auditFile, auditPaths, SECRET_PATTERNS };
