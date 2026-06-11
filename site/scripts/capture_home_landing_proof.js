#!/usr/bin/env node
'use strict';

const path = require('path');
const { chromium } = require('playwright');

const PROOF = path.join(__dirname, '..', 'proofs', 'home_landing_proof.html');
const LOGIN_PROOF = path.join(__dirname, '..', 'proofs', 'home_login_proof.html');
const OUT = path.join(__dirname, '..', 'proofs');
const homeUrl = 'file:///' + PROOF.replace(/\\/g, '/').replace(/ /g, '%20');

async function buildLoginProof() {
  const fs = require('fs');
  const loginBody = fs.readFileSync(path.join(__dirname, '..', 'views', 'login.ejs'), 'utf8')
    .replace(/<%= assetVersion %>/g, 'proof')
    .replace(/<%[\s\S]*?%>/g, '');
  const html = `<!DOCTYPE html><html lang="en" data-theme="dark" data-public-page="1"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign In - DENG Tool</title><link rel="stylesheet" href="../public/css/style.css"><link rel="stylesheet" href="../public/css/public-theme.css"><link rel="stylesheet" href="../public/css/login-page.css"></head><body class="auth-layout">${loginBody.replace(/<\/?link[^>]*>\n?/g, '').replace(/<\/?script[^>]*>[\s\S]*?<\/script>\n?/g, '')}</body></html>`;
  fs.writeFileSync(LOGIN_PROOF, html, 'utf8');
}

async function shot(page, file, viewport) {
  await page.setViewportSize(viewport);
  await page.goto(page.__url, { waitUntil: 'networkidle' });
  await page.screenshot({ path: path.join(OUT, file), fullPage: true });
}

async function main() {
  const { execFileSync } = require('child_process');
  execFileSync(process.execPath, [path.join(__dirname, 'gen_home_landing_proof.js')], { stdio: 'inherit' });
  await buildLoginProof();
  const browser = await chromium.launch();
  const page = await browser.newPage();
  page.__url = homeUrl;
  await shot(page, 'home_landing_desktop.png', { width: 1440, height: 900 });
  await page.locator('#statistic').scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(OUT, 'home_landing_stats_desktop.png'), fullPage: false });
  page.__url = 'file:///' + LOGIN_PROOF.replace(/\\/g, '/').replace(/ /g, '%20');
  await shot(page, 'home_login_desktop.png', { width: 1440, height: 900 });
  await shot(page, 'home_login_mobile.png', { width: 390, height: 844 });
  page.__url = homeUrl;
  await shot(page, 'home_landing_mobile.png', { width: 390, height: 844 });
  await browser.close();
  console.log('HOME_LANDING_SCREENSHOTS_OK', OUT);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
