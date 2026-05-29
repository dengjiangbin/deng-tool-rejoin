#!/usr/bin/env node
'use strict';
const fs = require('fs');
const path = require('path');

const REQUIRED = 'https://tool.deng.my.id';
const config = path.join(__dirname, '..', 'ios', 'DENGMonitor', 'Core', 'AppConfig.swift');
const FORBIDDEN_HOST = /\b(?:localhost|127\.0\.0\.1|staging\.example\.com|rejoin\.deng\.my\.id)\b/i;

if (!fs.existsSync(config)) {
  console.error('Missing', config);
  process.exit(1);
}
const cfg = fs.readFileSync(config, 'utf8');
if (!cfg.includes(`baseURL = "${REQUIRED}"`)) {
  console.error('AppConfig.baseURL must be', REQUIRED);
  process.exit(1);
}

function walk(dir, out = []) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name);
    if (fs.statSync(p).isDirectory()) walk(p, out);
    else if (name.endsWith('.swift') && !p.includes('Tests')) out.push(p);
  }
  return out;
}

const iosRoot = path.join(__dirname, '..', 'ios');
let ok = true;
for (const f of walk(iosRoot)) {
  const lines = fs.readFileSync(f, 'utf8').split('\n');
  for (const line of lines) {
    if (!line.includes('http')) continue;
    const urls = line.match(/https?:\/\/[^\s"')]+/g) || [];
    for (const u of urls) {
      if (FORBIDDEN_HOST.test(u)) {
        console.error(`FAIL ${f}: forbidden URL ${u}`);
        ok = false;
      }
      if (u.includes('http') && !u.startsWith(REQUIRED) && !u.includes('apple.com') && !u.includes('DTDs')) {
        console.error(`FAIL ${f}: non-production URL ${u}`);
        ok = false;
      }
    }
  }
}

if (!ok) process.exit(1);
console.log('OK iOS production base URL');
