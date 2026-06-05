'use strict';
/**
 * Parse catch UI text into fish name vs rarity (BLOCKER10P).
 */

const rarityLabels = require('./fishitRarityLabels');

const NON_FISH_PHRASES = [
  'you caught',
  'new fish',
  'inventory full',
  'equipped',
  'sold',
  'purchased',
  'quest',
  'level up',
  'achievement',
  'warning',
  'error',
  'disconnect',
];

const WEIGHT_RE = /^[\d.,\s]+(?:kg|lb|lbs)?$/i;
const SUFFIX_RE = /\s*(?:!|\.|kg|lbs|lb)\s*$/i;

function cleanToken(raw) {
  if (raw == null) return '';
  let s = String(raw).trim().replace(SUFFIX_RE, '').trim();
  if (!s || s.length < 2 || s.length > 80) return '';
  if (/^\d+$/.test(s)) return '';
  if (/^item\s*#\s*\d+$/i.test(s)) return '';
  if (WEIGHT_RE.test(s)) return '';
  const low = s.toLowerCase();
  for (const phrase of NON_FISH_PHRASES) {
    if (low.includes(phrase)) return '';
  }
  return s;
}

/**
 * Split combined UI text like "Forgotten King Crab" into rarity + fish name.
 */
function splitRarityPrefix(text) {
  const s = cleanToken(text);
  if (!s) return { fishNameCandidate: null, rarityCandidate: null, raw: text };
  const parts = s.split(/\s+/);
  if (parts.length >= 2 && rarityLabels.isRarityLabel(parts[0])) {
    const fish = parts.slice(1).join(' ').trim();
    if (fish && !rarityLabels.isBlockedLearnName(fish)) {
      return {
        fishNameCandidate: fish,
        rarityCandidate: parts[0],
        raw: text,
        parserDecision: 'rarity_prefix_stripped',
      };
    }
  }
  if (rarityLabels.isRarityLabel(s)) {
    return {
      fishNameCandidate: null,
      rarityCandidate: s,
      raw: text,
      parserDecision: 'rarity_only',
    };
  }
  if (rarityLabels.isBlockedLearnName(s)) {
    return {
      fishNameCandidate: null,
      rarityCandidate: null,
      raw: text,
      parserDecision: 'blocked_status_label',
    };
  }
  return {
    fishNameCandidate: s,
    rarityCandidate: null,
    raw: text,
    parserDecision: 'fish_name_direct',
  };
}

function parseCatchInput(raw) {
  const source = (raw && raw.source) ? String(raw.source).slice(0, 40) : 'catch_notification';
  let detectedAt = raw && raw.detectedAt;
  if (typeof detectedAt === 'number') {
    detectedAt = new Date(detectedAt * 1000).toISOString();
  } else if (typeof detectedAt !== 'string') {
    detectedAt = new Date().toISOString();
  }

  const text = raw && (raw.fishName ?? raw.name ?? raw.text ?? raw.rawText);
  const split = splitRarityPrefix(text);

  return {
    source,
    detectedAt,
    rawText: text != null ? String(text) : null,
    fishNameCandidate: split.fishNameCandidate,
    rarityCandidate: split.rarityCandidate,
    parserDecision: split.parserDecision || 'empty',
    confidence: split.fishNameCandidate ? (split.parserDecision === 'fish_name_direct' ? 0.8 : 0.7) : 0,
  };
}

/** Legacy normalizer — returns fish name only, never a rarity label. */
function normalizeCatchFishName(raw) {
  const parsed = parseCatchInput(typeof raw === 'object' ? raw : { fishName: raw });
  return parsed.fishNameCandidate || null;
}

module.exports = {
  parseCatchInput,
  splitRarityPrefix,
  normalizeCatchFishName,
  cleanToken,
};
