'use strict';
/**
 * Parse catch UI text into base fish name, mutation, rarity, weight (BLOCKER10U).
 */

const rarityLabels = require('./fishitRarityLabels');

const NON_FISH_PHRASES = [
  'you caught', 'new fish', 'inventory full', 'equipped', 'sold', 'purchased',
  'quest', 'level up', 'achievement', 'warning', 'error', 'disconnect',
];

const WEIGHT_ONLY_RE = /^[\d.,\s]+[kK]?(?:\s*kg)?$/i;
const WEIGHT_PAREN_RE = /\s*\(\s*([\d.,]+)\s*([kK])?\s*(?:kg|k)?\s*\)\s*$/i;
const WEIGHT_SUFFIX_RE = /\s+([\d.,]+)\s*([kK])?\s*kg\s*$/i;
const SUFFIX_RE = /\s*(?:!|\.)\s*$/i;

/** Mutation/effect labels — longest multi-word first (BLOCKER10U). */
const MUTATION_LABELS_ORDERED = [
  'fairy dust', 'radioactive shiny',
  'big', 'shiny', 'baby', 'giant', 'mutated', 'albino', 'darkened', 'glossy',
  'mosaic', 'silver', 'golden', 'gold', 'mythical', 'frozen', 'electric',
  'sandy', 'corrupt', 'ghost', 'midnight', 'radioactive', 'galaxy',
  'holographic',
];

const MUTATION_LABELS = new Set(MUTATION_LABELS_ORDERED.map((l) => l.toLowerCase()));

function cleanToken(raw) {
  if (raw == null) return '';
  let s = String(raw).trim().replace(SUFFIX_RE, '').trim();
  if (!s || s.length < 2 || s.length > 120) return '';
  if (/^\d+$/.test(s)) return '';
  if (/^item\s*#\s*\d+$/i.test(s)) return '';
  if (WEIGHT_ONLY_RE.test(s)) return '';
  const low = s.toLowerCase();
  for (const phrase of NON_FISH_PHRASES) {
    if (low.includes(phrase)) return '';
  }
  return s;
}

function parseWeightValue(raw, hasK) {
  const s = String(raw || '').trim().replace(',', '.');
  const val = parseFloat(s);
  if (!Number.isFinite(val)) return null;
  return hasK ? val * 1000 : val;
}

function stripWeightFromText(text) {
  let s = String(text || '').trim();
  let weightKg = null;

  const paren = s.match(WEIGHT_PAREN_RE);
  if (paren) {
    weightKg = parseWeightValue(paren[1], !!paren[2]);
    s = s.replace(WEIGHT_PAREN_RE, '').trim();
    return { text: s, weightKg };
  }

  const suffix = s.match(WEIGHT_SUFFIX_RE);
  if (suffix) {
    weightKg = parseWeightValue(suffix[1], !!suffix[2]);
    s = s.replace(WEIGHT_SUFFIX_RE, '').trim();
  }

  return { text: s, weightKg };
}

function stripAllMutationPrefixes(text) {
  let s = String(text || '').trim();
  if (!s) return { mutation: null, baseFishName: null, displayName: null };
  const mutations = [];
  let changed = true;
  while (changed) {
    changed = false;
    const parts = s.split(/\s+/);
    for (const label of MUTATION_LABELS_ORDERED) {
      const labelParts = label.split(/\s+/);
      if (parts.length > labelParts.length) {
        const head = parts.slice(0, labelParts.length).join(' ');
        if (head.toLowerCase() === label.toLowerCase()) {
          mutations.push(head);
          s = parts.slice(labelParts.length).join(' ').trim();
          changed = true;
          break;
        }
      }
    }
  }
  if (!s) return { mutation: null, baseFishName: null, displayName: null };
  const mutation = mutations.length ? mutations.join(' ') : null;
  return {
    mutation,
    baseFishName: s,
    displayName: mutation ? `${mutation} ${s}` : s,
  };
}

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
      source, detectedAt, rawText: null,
      fishNameCandidate: null, baseFishName: null, displayName: null,
      mutation: null, rarityCandidate: null, weightKg: null,
      parserDecision: 'empty', confidence: 0,
    };
  }

  const { text: noWeight, weightKg } = stripWeightFromText(rawText);
  const raritySplit = splitRarityPrefix(noWeight);
  let baseFishName = raritySplit.fishNameCandidate;
  let mutation = null;
  let displayName = baseFishName;
  let parserDecision = raritySplit.parserDecision || 'empty';

  if (baseFishName) {
    const mutSplit = stripAllMutationPrefixes(baseFishName);
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
      source, detectedAt, rawText,
      fishNameCandidate: null, baseFishName: null, displayName: null,
      mutation, rarityCandidate: raritySplit.rarityCandidate, weightKg,
      parserDecision: rarityLabels.isRarityLabel(baseFishName) ? 'rarity_only' : 'blocked_status_label',
      confidence: 0,
    };
  }

  return {
    source, detectedAt, rawText,
    fishNameCandidate: baseFishName,
    baseFishName,
    displayName: baseFishName ? displayName : null,
    mutation,
    rarityCandidate: raritySplit.rarityCandidate,
    weightKg,
    parserDecision,
    confidence: baseFishName ? 0.75 : 0,
  };
}

/** Full canonicalization for catalog repair (BLOCKER10U). */
function canonicalizeFishName(rawName, extra) {
  const raw = String(rawName || '').trim();
  const parsed = parseCatchInput({
    rawText: raw,
    fishName: raw,
    rarityCandidate: extra?.rarity || extra?.rarityCandidate || null,
  });
  const base = parsed.baseFishName || parsed.fishNameCandidate;
  const changed = !!(base && (
    raw !== base
    || (parsed.displayName && raw !== parsed.displayName && raw.includes('('))
    || (parsed.mutation && !String(extra?.mutation || '').includes(parsed.mutation))
  ));
  let reason = 'unchanged';
  if (raw.includes('(') && parsed.weightKg != null) reason = 'weight_suffix_stripped';
  else if (parsed.mutation && raw.toLowerCase() !== (parsed.displayName || '').toLowerCase()) {
    reason = 'mutation_prefix_stripped';
  } else if (changed) reason = 'name_canonicalized';

  return {
    rawName: raw,
    baseFishName: base,
    displayName: parsed.displayName || base,
    mutation: parsed.mutation,
    rarity: parsed.rarityCandidate || extra?.rarity || null,
    weightKg: parsed.weightKg != null ? parsed.weightKg : (extra?.weightKg ?? null),
    changed,
    reason,
    parserDecision: parsed.parserDecision,
  };
}

function baseFishNameForConflict(name) {
  if (!name) return null;
  return canonicalizeFishName(name).baseFishName
    || parseCatchInput({ fishName: name }).fishNameCandidate;
}

function normalizeCatchFishName(raw) {
  const parsed = parseCatchInput(typeof raw === 'object' ? raw : { fishName: raw });
  return parsed.baseFishName || parsed.fishNameCandidate || null;
}

module.exports = {
  parseCatchInput,
  canonicalizeFishName,
  splitRarityPrefix,
  stripWeightFromText,
  stripAllMutationPrefixes,
  parseWeightValue,
  baseFishNameForConflict,
  normalizeCatchFishName,
  cleanToken,
  MUTATION_LABELS,
  MUTATION_LABELS_ORDERED,
};
