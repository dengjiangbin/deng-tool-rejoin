#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const OUT = path.join(ROOT, 'proofs', 'home_landing_proof.html');

function read(rel) {
  return fs.readFileSync(path.join(ROOT, rel), 'utf8');
}

function main() {
  const html = `<!DOCTYPE html>
<html lang="en" data-theme="dark" data-public-page="1">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DENG Tool - Roblox Automation & Stat Tracker</title>
  <meta name="description" content="DENG Tool is a Roblox automation and stat-tracking suite with live Fish It inventory, Rejoin agents, licenses, and monitoring in one dashboard.">
  <link rel="stylesheet" href="../public/css/style.css">
  <link rel="stylesheet" href="../public/css/public-theme.css">
  <link rel="stylesheet" href="../public/css/home.css">
</head>
<body class="auth-layout">
${read('views/home.ejs').replace(/<%= assetVersion %>/g, 'proof').replace(/<\/?script[^>]*>[\s\S]*?<\/script>\n?/g, '').replace(/<\/?link[^>]*>\n?/g, '')}
<script>${read('public/js/home.js')}</script>
</body>
</html>`;
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, html, 'utf8');
  console.log('HOME_LANDING_PROOF', OUT);
}

main();
