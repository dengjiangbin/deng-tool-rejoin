'use strict';

// 2026-06-16 detail-card polish regression. Covers:
//  - detail/instance cards use the NEUTRAL surface (no rarity background),
//    while overview grid cards keep their rarity colors,
//  - centralized mutation style resolver colors EVERY real mutation
//    (Gold / Albino / Gemstone / Stone / Sandy / known list / deterministic
//    fallback) and never renders nil/null/undefined,
//  - detail typography matches the overview grid (.ft-card-name / weight),
//  - the 2-column detail layout is preserved.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const src = fs.readFileSync(SOURCE_PATH, 'utf8');

// Extract the self-contained mutation helpers from the EJS <script> block and
// evaluate them so we can test real behavior (not just string presence).
function loadMutationHelpers() {
  const start = src.indexOf('const FT_MUTATION_COLORS = {');
  const end = src.indexOf('// Canonical rarity key for detail-card coloring');
  assert.ok(start >= 0 && end > start, 'mutation helper block must be present');
  const snippet = src.slice(start, end);
  // eslint-disable-next-line no-new-func
  const factory = new Function(
    `${snippet}; return { FT_MUTATION_COLORS, ftMutationHashColor, ftMutationColor, ftMutationStyle, ftNormalizeNonNil };`,
  );
  return factory();
}

const M = loadMutationHelpers();

describe('mutation style resolver (BLOCKER polish #2)', () => {
  test('Gold is gold/yellow', () => {
    assert.match(M.ftMutationStyle('Gold'), /#fbbf24/i);
  });

  test('Albino is intentional white with readable shadow/outline', () => {
    const s = M.ftMutationStyle('Albino');
    assert.match(s, /#fff(fff)?/i);
    assert.match(s, /text-shadow/);
  });

  test('Gemstone is a green→red gradient with a solid fallback color', () => {
    const s = M.ftMutationStyle('Gemstone');
    assert.match(s, /linear-gradient/);
    assert.match(s, /background-clip:text/);
    assert.match(s, /text-fill-color:transparent/);
    assert.match(s, /#34d399/i, 'fallback solid color present for unsupported browsers');
  });

  test('Stone is light chocolate / brown', () => {
    assert.match(M.ftMutationStyle('Stone'), /#c8a06b/i);
  });

  test('Sandy is sand color', () => {
    assert.match(M.ftMutationStyle('Sandy'), /#e3c879/i);
  });

  test('other known mutations are colored (not plain default)', () => {
    for (const name of ['Shiny', 'Big', 'Ghost', 'Holographic', 'Rainbow', 'Darkened', 'Frozen', 'Electric', 'Mythic', 'Celestial']) {
      const c = M.ftMutationColor(name);
      assert.ok(c, `${name} must resolve a color`);
      assert.notEqual(c.toLowerCase(), '#cbd5e1', `${name} must not be the old default`);
    }
  });

  test('unknown mutation gets a deterministic, non-default color', () => {
    const a1 = M.ftMutationColor('Zorblax');
    const a2 = M.ftMutationColor('Zorblax');
    const b = M.ftMutationColor('Qwftpln');
    assert.equal(a1, a2, 'same name → same color');
    assert.notEqual(a1, b, 'different names → different colors');
    assert.match(a1, /^hsl\(/, 'fallback is a generated hsl color');
  });

  test('nil/null/undefined/empty render nothing', () => {
    for (const bad of ['nil', 'null', 'undefined', 'none', '', '   ', null, undefined]) {
      assert.equal(M.ftNormalizeNonNil(bad), '', `${JSON.stringify(bad)} must normalize to empty`);
    }
    assert.equal(M.ftNormalizeNonNil('Gold'), 'Gold');
  });
});

describe('detail card neutral background (BLOCKER polish #1)', () => {
  test('no rarity background rules on .ft-inst-card', () => {
    assert.doesNotMatch(src, /\.ft-inst-card\[data-rarity=/);
  });
  test('renderFishInstanceCard emits a neutral card (no data-rarity attr)', () => {
    assert.doesNotMatch(src, /class="ft-inst-card" data-rarity=/);
  });
  test('overview grid rarity colors are still generated elsewhere', () => {
    const rarityStyle = fs.readFileSync(path.join(__dirname, '..', 'src', 'fishitTrackerRarityStyle.js'), 'utf8');
    assert.match(rarityStyle, /buildFtCardRarityCss/);
    assert.match(rarityStyle, /ft-card rarity backgrounds/);
  });
});

describe('detail typography matches overview grid (BLOCKER polish #3)', () => {
  test('detail name uses the overview .ft-card-name sizing (13px/800/16px)', () => {
    assert.match(src, /\.ft-inst-card__name\s*\{[^}]*font-size:13px;[^}]*font-weight:800;[^}]*line-height:16px;/);
  });
  test('detail weight uses the overview metadata sizing (11px/14px)', () => {
    assert.match(src, /\.ft-inst-card__weight\s*\{[^}]*font-size:11px;[^}]*line-height:14px;/);
  });
});

describe('detail card render uses centralized resolver (BLOCKER polish #2)', () => {
  test('renderFishInstanceCard uses ftMutationStyle for the mutation label', () => {
    assert.match(src, /const mutStyle = ftMutationStyle\(realMut\)/);
    assert.match(src, /ft-inst-card__mut" style="\$\{escHtml\(mutStyle\)\}"/);
  });
  test('mutation label only renders when a real mutation exists', () => {
    assert.match(src, /realMut\s*\n?\s*\?\s*`<div class="ft-inst-card__mut"/);
  });
  test('weight still renders independently of mutation', () => {
    assert.match(src, /ft-inst-card__weight">Weight: \$\{escHtml\(card\.weight\)\}/);
  });
});

describe('two-column detail layout preserved (BLOCKER polish #4)', () => {
  test('desktop detail grid is multi/two-column', () => {
    assert.match(src, /\.ft-detail-instances\s*\{[^}]*grid-template-columns:repeat\(auto-fill,minmax\(/);
  });
  test('narrow mobile collapses to one column', () => {
    assert.match(src, /@media \(max-width:340px\)\s*\{\s*\.ft-detail-instances\s*\{\s*grid-template-columns:1fr/);
  });
});
