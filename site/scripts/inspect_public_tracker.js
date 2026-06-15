'use strict';
const { fetchPublicTrackerRaw } = require('../src/fishitPublicTrackerBuild');
const crypto = require('crypto');

(async () => {
  const raw = await fetchPublicTrackerRaw();
  const m = raw.match(/local __B=\[\[([\s\S]*?)\]\]\s*\nlocal __A=/);
  const dec = Buffer.from(m[1], 'base64').toString('utf8');
  const sha256 = crypto.createHash('sha256').update(raw, 'utf8').digest('hex');
  const build = (dec.match(/TRACKER_BUILD\s*=\s*"([^"]+)"/) || [])[1];
  const interval = (dec.match(/lightSyncIntervalSeconds\s*=\s*(\d+)/) || [])[1];
  console.log(JSON.stringify({
    sha256,
    build,
    lightSyncIntervalSeconds: interval,
    hasSingleton: /DENG_TRACKER_UPLOAD_SINGLETON|DENG_TRACKER_RUNNING/.test(dec),
    has502Backoff: /502|503/.test(dec),
    hasUploadSkipCooldown: dec.includes('UPLOAD_SKIP_COOLDOWN'),
    hasDebugDisabled: dec.includes('DEBUG_UPLOAD_DISABLED_PRODUCTION'),
    hasUploadFailTransient: dec.includes('UPLOAD_FAIL_TRANSIENT'),
    throttleLeaderstats: (dec.match(/required_leaderstats\s*=\s*(\d+)/) || [])[1],
  }, null, 2));
})().catch((e) => { console.error(e); process.exit(1); });
