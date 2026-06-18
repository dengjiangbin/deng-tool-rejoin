'use strict';
// 6-minute live observation of online accounts via the read API (the same
// endpoint + presence contract the browser polls). Proves: account stays green,
// timers advance from real revisions, and no lane gap approaches the 195s hard
// offline / 5-minute dead-period failure mode.
const http = require('http');
const fs = require('fs');
const path = require('path');

const USERS = (process.argv[2] || 'usaxz10,phantomkernels,priadivine387').split(',');
const DURATION_MS = parseInt(process.argv[3] || '375000', 10); // ~6.25 min
const INTERVAL_MS = 5000;
const OUT = path.join(__dirname, 'three_lane_deadperiod_observation_2026_06_18.json');

function getJson(p) {
  return new Promise((resolve) => {
    http.get('http://127.0.0.1:8793' + p, (r) => {
      let s = '';
      r.on('data', (d) => { s += d; });
      r.on('end', () => { try { resolve({ code: r.statusCode, headers: r.headers, json: JSON.parse(s) }); } catch (_) { resolve({ code: r.statusCode, headers: r.headers, json: null }); } });
    }).on('error', () => resolve({ code: 0, json: null }));
  });
}

async function sampleUser(u) {
  const base = '/api/fishit-tracker/get-backpack/' + u;
  const a = await getJson(base);
  const hash = a.headers && a.headers['x-deng-snapshot-hash'];
  const b = await getJson(base + '?h=' + encodeURIComponent(hash || 'x'));
  const p = (b.json && b.json.presence) || {};
  return {
    code: a.code,
    presenceState: p.presenceState,
    isOnline: p.isOnline,
    statusAge: p.statusAgeSeconds,
    leaderstatsAge: p.leaderstatsAgeSeconds,
    inventoryAge: p.inventoryAgeSeconds,
    statusRev: p.statusRevision,
    statusReportId: p.statusReportId,
    leaderstatsRev: p.leaderstatsRevision,
    inventoryRev: p.inventoryRevision,
    reportIdentitySource: p.reportIdentitySource,
  };
}

(async () => {
  const start = Date.now();
  const state = {};
  for (const u of USERS) {
    state[u] = {
      samples: 0, http200: 0, httpOther: 0,
      maxStatusAge: 0, maxLeaderstatsAge: 0, maxInventoryAge: 0,
      everRed: false, redWhileOnlineSeen: false,
      statusRevs: new Set(), leaderstatsRevs: new Set(), inventoryRevs: new Set(),
      identitySources: new Set(), greenSamples: 0,
    };
  }
  while (Date.now() - start < DURATION_MS) {
    for (const u of USERS) {
      const s = await sampleUser(u);
      const st = state[u];
      st.samples += 1;
      if (s.code === 200) st.http200 += 1; else st.httpOther += 1;
      if (typeof s.statusAge === 'number') st.maxStatusAge = Math.max(st.maxStatusAge, s.statusAge);
      if (typeof s.leaderstatsAge === 'number') st.maxLeaderstatsAge = Math.max(st.maxLeaderstatsAge, s.leaderstatsAge);
      if (typeof s.inventoryAge === 'number') st.maxInventoryAge = Math.max(st.maxInventoryAge, s.inventoryAge);
      if (s.presenceState === 'online') st.greenSamples += 1;
      if (s.presenceState === 'offline') { st.everRed = true; }
      if (s.statusRev != null) st.statusRevs.add(s.statusRev);
      if (s.leaderstatsRev != null) st.leaderstatsRevs.add(s.leaderstatsRev);
      if (s.inventoryRev != null) st.inventoryRevs.add(s.inventoryRev);
      if (s.reportIdentitySource) st.identitySources.add(s.reportIdentitySource);
    }
    await new Promise((r) => setTimeout(r, INTERVAL_MS));
  }
  const report = { observedMs: Date.now() - start, intervalMs: INTERVAL_MS, users: {} };
  for (const u of USERS) {
    const st = state[u];
    report.users[u] = {
      samples: st.samples, http200: st.http200, httpOther: st.httpOther,
      greenSamples: st.greenSamples, everWentRed: st.everRed,
      maxStatusAgeSeconds: st.maxStatusAge,
      maxLeaderstatsAgeSeconds: st.maxLeaderstatsAge,
      maxInventoryAgeSeconds: st.maxInventoryAge,
      distinctStatusRevisions: st.statusRevs.size,
      distinctLeaderstatsRevisions: st.leaderstatsRevs.size,
      distinctInventoryRevisions: st.inventoryRevs.size,
      identitySources: [...st.identitySources],
    };
  }
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  console.log('OBSERVATION_DONE', JSON.stringify(report));
})();
