'use strict';
/**
 * Parse catch UI text into base fish name, mutation, rarity, weight (BLOCKER10T).
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
const WEIGHT_PAREN_RE = /\s*\(\s*([\d.,]+)\s*(?:kg|k|lbs?)?\s*\)\s*$/i;
const SUFFIX_RE = /\s*(?:!|\.|kg|lbs|lb)\s*$/i;

/** Mutation/prefix labels — separate from rarity (BLOCKER10T). */
const MUTATION_LABELS = new Set([
  'shiny', 'big', 'baby', 'giant', 'mutated', 'albino', 'darkened', 'glossy',
  'mosaic', 'silver', 'golden', 'mythical', 'frozen', 'electric',
]);

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

function stripWeightFromText(text) {
  const s = String(text || '').trim();
  const m = s.match(WEIGHT_PAREN_RE);
  if (!m) return { text: s, weightKg: null };
  const weightKg = parseFloat(String(m[1]).replace(',', '.'));
  return {
    text: s.replace(WEIGHT_PAREN_RE, '').trim(),
    weightKg: Number.isFinite(weightKg) ? weightKg : null,
  };
}

function stripMutationPrefix(text) {
  const s = String(text || '').trim();
  if (!s) return { mutation: null, baseFishName: null, displayName: null };
  const parts = s.split(/\s+/);
  if (parts.length >= 2 && MUTATION_LABELS.has(parts[0].toLowerCase())) {
    const mutation = parts[0];
    const base = parts.slice(1).join(' ').trim();
    if (!base) return { mutation: null, baseFishName: null, displayName: null };
    return {
      mutation,
      baseFishName: base,
      displayName: `${mutation} ${base}`,
    };
  }
  return { mutation: null, baseFishName: s, displayName: s };
}

/**
 * Split combined UI text like "Forgotten King Crab" into rarity + fish name.
 * Does not strip weight or mutation — use parseCatchInput for full parse.
 */
function splitRarityPrefix(text) {
  const s = cleanToken(text);
  if (!s) return { fishNameCandidate: null, rarityCandidate: null, raw: text, parserDecision: 'empty' };
  const parts = s.split(/\s+/);
  if (parts.length >= 2 && rarityLabels.isRarityLabel(parts[0])
      && !MUTATION_LABELS.has(parts[0].toLowerCase())) {
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
  if (rarityLabels.isRarityLabel(s) && !MUTATION_LABELS.has(s.toLowerCase())) {
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

  const text = raw && (raw.rawText ?? raw.fishName ?? raw.name ?? raw.text);
  const rawText = text != null ? String(text) : null;
  if (!rawText || !rawText.trim()) {
    return {
      source,
      detectedAt,
      rawText: null,
      fishNameCandidate: null,
      baseFishName: null,
      displayName: null,
      mutation: null,
      rarityCandidate: null,
      weightKg: null,
      parserDecision: 'empty',
      confidence: 0,
    };
  }

  const { text: noWeight, weightKg } = stripWeightFromText(rawText);
  const raritySplit = splitRarityPrefix(noWeight);
  let baseFishName = raritySplit.fishNameCandidate;
  let mutation = null;
  let displayName = baseFishName;
  let parserDecision = raritySplit.parserDecision || 'empty';

  if (baseFishName) {
    const mutSplit = stripMutationPrefix(baseFishName);
    mutation = mutSplit.mutation;
    baseFishName = mutSplit.baseFishName;
    displayName = mutSplit.displayName;
    if (raritySplit.rarityCandidate) {
      displayName = `${raritySplit.rarityCandidate} ${displayName}`;
    }
    if (raritySplit.rarityCandidate && mutation) {
      parserDecision = 'rarity_and_mutation_stripped';
    } else if (raritySplit.rarityCandidate) {
      parserDecision = 'rarity_prefix_stripped';
    } else if (mutation) {
      parserDecision = 'mutation_prefix_stripped';
    }
  }

  if (baseFishName && rarityLabels.isBlockedLearnName(baseFishName)) {
    return {
      source,
      detectedAt,
      rawText,
      fishNameCandidate: null,
      baseFishName: null,
      displayName: null,
      mutation,
      rarityCandidate: raritySplit.rarityCandidate,
      weightKg,
      parserDecision: rarityLabels.isRarityLabel(baseFishName) ? 'rarity_only' : 'blocked_status_label',
      confidence: 0,
    };
  }

  const confidence = baseFishName
    ? (parserDecision === 'fish_name_direct' ? 0.8 : 0.75)
    : 0;

  return {
    source,
    detectedAt,
    rawText,
    fishNameCandidate: baseFishName,
    baseFishName,
    displayName: baseFishName ? displayName : null,
    mutation,
    rarityCandidate: raritySplit.rarityCandidate,
    weightKg,
    parserDecision,
    confidence,
  };
}

/** Catalog/conflict identity — strips weight and mutation from a stored name. */
function baseFishNameForConflict(name) {
  if (!name) return null;
  const parsed = parseCatchInput({ fishName: name });
  return parsed.baseFishName || parsed.fishNameCandidate || null;
}

/** Legacy normalizer — returns base fish name only, never rarity/mutation label. */
function normalizeCatchFishName(raw) {
  const parsed = parseCatchInput(typeof raw === 'object' ? raw : { fishName: raw });
  return parsed.baseFishName || parsed.fishNameCandidate || null;
}

module.exports = {
  parseCatchInput,
  splitRarityPrefix,
  stripWeightFromText,
  stripMutationPrefix,
  baseFishNameForConflict,
  normalizeCatchFishName,
  cleanToken,
  MUTATION_LABELS,
};
