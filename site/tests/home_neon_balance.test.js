'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const HOME_CSS = path.join(__dirname, '..', 'public', 'css', 'home.css');
const HOME_EJS = path.join(__dirname, '..', 'views', 'home.ejs');
const PUBLIC_THEME = path.join(__dirname, '..', 'public', 'css', 'public-theme.css');

describe('home public theme styling', () => {
  test('homepage uses dark grid shell and green accent tokens', () => {
    const css = fs.readFileSync(HOME_CSS, 'utf8');
    const theme = fs.readFileSync(PUBLIC_THEME, 'utf8');
    assert.match(css, /\.deng-home::before[\s\S]*background-image:[\s\S]*linear-gradient/);
    assert.match(theme, /--status-green:\s*#00e6a8/);
    assert.match(css, /\.deng-home-stat-card--status-green/);
    assert.match(css, /\.deng-home-nav__link\.is-active[\s\S]*var\(--public-accent\)/);
  });

  test('CTA, navbar, and eco cards use current public theme styling', () => {
    const css = fs.readFileSync(HOME_CSS, 'utf8');
    assert.match(css, /\.deng-home-btn--primary\s*\{[\s\S]*#f8fafc/);
    assert.match(css, /\.deng-home-nav__inner[\s\S]*backdrop-filter:\s*blur/);
    assert.match(css, /\.deng-home-eco-card/);
    assert.match(css, /\.deng-home-brand__text/);
  });

  test('visible homepage brand is DENG All In One (not DENG Tool)', () => {
    const html = fs.readFileSync(HOME_EJS, 'utf8');
    assert.match(html, /DENG All In One/);
    assert.doesNotMatch(html, /DENG Tool\b/);
  });
});
