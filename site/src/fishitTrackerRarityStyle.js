'use strict';

/** Canonical tracker card rarity styling (BLOCKER10ZP). Epic = purple, Mythic = red. */
const FT_RARITY_STYLE = {
  COMMON: {
    classNames: ['ft-rarity-COMMON', 'ft-rarity-common'],
    background: 'linear-gradient(135deg,#e2e8f0 0%,#94a3b8 100%)',
    accent: '#9ca3af',
  },
  UNCOMMON: {
    classNames: ['ft-rarity-UNCOMMON', 'ft-rarity-uncommon'],
    background: '#65a30d',
    accent: '#84cc16',
  },
  RARE: {
    classNames: ['ft-rarity-RARE', 'ft-rarity-rare'],
    background: '#2563eb',
    accent: '#60a5fa',
  },
  EPIC: {
    classNames: ['ft-rarity-EPIC', 'ft-rarity-epic'],
    background: '#9333ea',
    accent: '#a855f7',
  },
  LEGENDARY: {
    classNames: ['ft-rarity-LEGENDARY', 'ft-rarity-legendary'],
    background: '#ea580c',
    accent: '#ff8c00',
  },
  MYTHIC: {
    classNames: ['ft-rarity-MYTHIC', 'ft-rarity-mythic'],
    background: '#dc2626',
    accent: '#ef4444',
  },
  SECRET: {
    classNames: ['ft-rarity-SECRET', 'ft-rarity-secret'],
    background: '#16d487',
    accent: '#00ff7f',
  },
  FORGOTTEN: {
    classNames: ['ft-rarity-FORGOTTEN', 'ft-rarity-forgotten'],
    background: 'linear-gradient(135deg,#9ca3af 0%,#6b7280 42%,#374151 100%)',
    accent: '#e5e7eb',
  },
};

const RARITY_KEY_ALIASES = {
  common: 'COMMON',
  uncommon: 'UNCOMMON',
  rare: 'RARE',
  epic: 'EPIC',
  legendary: 'LEGENDARY',
  legend: 'LEGENDARY',
  mythic: 'MYTHIC',
  secret: 'SECRET',
  forgotten: 'FORGOTTEN',
};

function normalizeRarityKey(rarity) {
  if (!rarity) return 'COMMON';
  const key = String(rarity).trim().toLowerCase();
  return RARITY_KEY_ALIASES[key] || 'COMMON';
}

function ftRarityClass(rarity) {
  const key = normalizeRarityKey(rarity);
  return (FT_RARITY_STYLE[key] || FT_RARITY_STYLE.COMMON).classNames[0];
}

function ftRarityBackground(rarity) {
  const key = normalizeRarityKey(rarity);
  return (FT_RARITY_STYLE[key] || FT_RARITY_STYLE.COMMON).background;
}

function buildFtCardRarityCss() {
  const lines = ['/* BLOCKER10ZP ft-card rarity backgrounds — canonical map */'];
  for (const style of Object.values(FT_RARITY_STYLE)) {
    const selector = style.classNames.map((c) => `.${c}`).join(', ');
    lines.push(`${selector} { background:${style.background}; }`);
  }
  lines.push('.ft-rarity-COMMON, .ft-rarity-common { color:#0f172a; }');
  lines.push('.ft-rarity-COMMON .ft-card-name, .ft-rarity-common .ft-card-name { color:#0f172a; text-shadow:none; }');
  lines.push('.ft-rarity-COMMON .ft-card-weight, .ft-rarity-common .ft-card-weight { color:rgba(15,23,42,.82); }');
  return lines.join('\n    ');
}

function buildTrackerRarityJsBootstrap() {
  const ftClass = {};
  for (const [alias, key] of Object.entries(RARITY_KEY_ALIASES)) {
    ftClass[alias] = FT_RARITY_STYLE[key].classNames[0];
  }
  return [
    `const FT_RARITY_CLASS = ${JSON.stringify(ftClass)};`,
    'function ftRarityClass(r) { return r ? (FT_RARITY_CLASS[String(r).toLowerCase()] || \'ft-rarity-COMMON\') : \'ft-rarity-COMMON\'; }',
  ].join('\n  ');
}

module.exports = {
  FT_RARITY_STYLE,
  RARITY_KEY_ALIASES,
  normalizeRarityKey,
  ftRarityClass,
  ftRarityBackground,
  buildFtCardRarityCss,
  buildTrackerRarityJsBootstrap,
};
