'use strict';
/**
 * Rarity / status label classifier (BLOCKER10P).
 * These must never be learned or displayed as fish names.
 */

const RARITY_LABELS = new Set([
  'forgotten',
  'common',
  'uncommon',
  'rare',
  'epic',
  'legendary',
  'legend',
  'mythic',
  'mythical',
  'secret',
  'limited',
  'event',
  'exotic',
  'divine',
  'celestial',
  'special',
  'unknown',
  'mutation',
  'mutated',
  'shiny',
  'normal',
  'basic',
  'premium',
  'exclusive',
]);

const STATUS_LABELS = new Set([
  'fish',
  'caught',
  'new',
  'inventory',
  'item',
  'items',
  'catch',
  'you caught',
  'new fish',
]);

function normalizeToken(raw) {
  return String(raw || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function isRarityLabel(raw) {
  const t = normalizeToken(raw);
  if (!t) return false;
  if (RARITY_LABELS.has(t)) return true;
  // Single-word exact match only for multi-word — "Forgotten Angelfish" is not a rarity label.
  const words = t.split(/\s+/);
  if (words.length === 1 && RARITY_LABELS.has(words[0])) return true;
  return false;
}

function isGenericStatusLabel(raw) {
  const t = normalizeToken(raw);
  if (!t) return false;
  if (STATUS_LABELS.has(t)) return true;
  for (const phrase of STATUS_LABELS) {
    if (t === phrase || t.startsWith(`${phrase} `)) return true;
  }
  return false;
}

function isBlockedLearnName(raw) {
  return isRarityLabel(raw) || isGenericStatusLabel(raw);
}

function getRarityLabelsBlocked() {
  return [...RARITY_LABELS].sort();
}

module.exports = {
  RARITY_LABELS,
  STATUS_LABELS,
  isRarityLabel,
  isGenericStatusLabel,
  isBlockedLearnName,
  getRarityLabelsBlocked,
};
