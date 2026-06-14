'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const HOME_CSS = path.join(__dirname, '..', 'public', 'css', 'home.css');
const HOME_EJS = path.join(__dirname, '..', 'views', 'home.ejs');
const PUBLIC_THEME = path.join(__dirname, '..', 'public', 'css', 'public-theme.css');

describe('home public theme styling', () => {
  test('homepage uses light blue-pink gradient shell and glass cards', () => {
    const css = fs.readFileSync(HOME_CSS, 'utf8');
    const theme = fs.readFileSync(PUBLIC_THEME, 'utf8');
    assert.match(css, /#dbeafe/);
    assert.match(css, /#fce7f3/);
    assert.match(css, /#60a5fa/);
    assert.match(css, /#f9a8d4/);
    assert.match(css, /\.deng-home[\s\S]*linear-gradient\(135deg,\s*#dbeafe\s*0%,\s*#fce7f3\s*100%\)/);
    assert.match(css, /backdrop-filter:\s*blur\(18px\)/);
    assert.match(theme, /public-home-layout[\s\S]*color-scheme:\s*light/);
    assert.doesNotMatch(css, /#050816/);
    assert.doesNotMatch(css, /#00c7a3/i);
    assert.doesNotMatch(css, /#00e0b8/i);
  });

  test('CTA, navbar, and eco cards use light glass styling with gradient accents', () => {
    const css = fs.readFileSync(HOME_CSS, 'utf8');
    assert.match(css, /\.deng-home-btn--primary\s*\{[\s\S]*var\(--deng-gradient-primary\)/);
    assert.match(css, /\.deng-home-btn--primary\s*\{[\s\S]*#ffffff/);
    assert.match(css, /\.deng-home-nav__inner[\s\S]*backdrop-filter:\s*blur/);
    assert.match(css, /\.deng-home-eco-card[\s\S]*rgba\(255,\s*255,\s*255,\s*0\.72\)/);
    assert.match(css, /\.deng-home-brand__text/);
    assert.match(css, /\.deng-home-stat-card--status-green/);
  });

  test('visible homepage brand is DENG All In One (not DENG Tool)', () => {
    const html = fs.readFileSync(HOME_EJS, 'utf8');
    assert.match(html, /DENG All In One/);
    assert.doesNotMatch(html, /DENG Tool\b/);
    assert.match(html, /dataset\.theme = 'light'/);
  });
});
