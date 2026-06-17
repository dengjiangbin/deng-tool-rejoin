'use strict';

// Read-only honest inspection of Arayaaa_30. Compares the CURRENT live inventory
// (playerDataFishItems) with the prior cached lastGoodPublicFishItems, and feeds
// both through the real computeRubyGemstoneTopCard helper to prove whether the
// count is a data reality or a detection bug. No hardcoding, no fixes, no fakes.

const fs = require('fs');
const path = require('path');
const ruby = require('../src/fishitRubyGemstoneCount');

const ACCOUNTS = path.join(__dirname, '..', 'data', 'fishit_live_sessions', 'accounts');

function findShard(user) {
  const want = `${user.toLowerCase()}.json`;
  for (const f of fs.readdirSync(ACCOUNTS)) if (f.toLowerCase() === want) return path.join(ACCOUNTS, f);
  return null;
}

const shardPath = findShard('Arayaaa_30');
const j = JSON.parse(fs.readFileSync(shardPath, 'utf8'));

const nameFields = ['cleanName', 'baseFishName', 'fishName', 'name', 'displayName', 'itemName'];
const mutFields = ['mutation', 'mutationName', 'mutationType', 'modifier'];

function rubyRows(arr) {
  return (arr || []).filter((it) => {
    const names = nameFields.map((f) => String(it[f] || '').toLowerCase().trim());
    return names.some((n) => n === 'ruby');
  });
}

const current = Array.isArray(j.playerDataFishItems) ? j.playerDataFishItems : [];
const lastGood = Array.isArray(j.lastGoodPublicFishItems) ? j.lastGoodPublicFishItems : [];

const report = {
  username: 'Arayaaa_30',
  shard: path.basename(shardPath),
  isOnline: j.isOnline,
  lastInventoryAt: j.lastInventoryAt,
  current: {
    source: 'playerDataFishItems (latest live upload)',
    rawInstances: current.length,
    rubyNamedRows: rubyRows(current).length,
    rubyGemstoneCount: ruby.computeRubyGemstoneTopCard({ fishItems: current }).count,
  },
  lastGoodPublic: {
    source: 'lastGoodPublicFishItems (prior cached public snapshot)',
    rows: lastGood.length,
    rubyNamedRows: rubyRows(lastGood).length,
    rubyGemstoneCount: ruby.computeRubyGemstoneTopCard({ fishItems: lastGood }).count,
  },
};

// Show the Ruby row from lastGood with its instance mutations (proof the helper sees it).
const lgRuby = rubyRows(lastGood);
report.lastGoodPublic.rubyRowProof = lgRuby.map((r) => ({
  name: r.name || r.cleanName || r.baseFishName,
  cardMutation: r.mutation || null,
  instanceMutations: (r.ownedInstances || []).map((i) => i.mutation || i.mutationName || i.mutationType || i.modifier).filter(Boolean),
  amount: r.amount ?? r.count ?? (r.ownedInstances || []).length,
}));

console.log(JSON.stringify(report, null, 2));
fs.writeFileSync(path.join(__dirname, '..', 'proofs', 'arayaaa_30_ruby_gemstone_inspection.json'), JSON.stringify({ capturedAt: new Date().toISOString(), report }, null, 2));
