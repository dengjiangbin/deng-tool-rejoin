'use strict';

// Smoke test for the extracted buildBackpackBodyForKey + precompute Ruby card.
// Loads the edited fishitTrackerRoutes module in a fresh process (the live site
// still runs the old code until restarted) and builds a backpack for a real
// active user. No fake data.

const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '..', '.env') });

process.env.TRACKER_WEB_MODE = process.env.TRACKER_WEB_MODE || '1';
process.env.SKIP_TRACKER_UPLOAD_ROUTES = process.env.SKIP_TRACKER_UPLOAD_ROUTES || '1';
process.env.FISHIT_SESSION_SHARDED = process.env.FISHIT_SESSION_SHARDED || '1';
process.env.FISHIT_LIVE_SESSIONS_DIR = process.env.FISHIT_LIVE_SESSIONS_DIR
  || 'C:\\Users\\Administrator\\Desktop\\DENG Tool Rejoin\\site\\data\\fishit_live_sessions';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH
  || 'C:\\Users\\Administrator\\Desktop\\DENG Fish It\\data\\deng-fish-it.sqlite';

const routes = require('../src/fishitTrackerRoutes');

async function main() {
  const user = process.argv[2] || 'denghub2';
  // Give the lazy session loader a moment + force a disk sync.
  routes.syncLiveTrackFromDisk();
  await new Promise((r) => setTimeout(r, 800));
  routes.syncLiveTrackFromDisk();

  const liteRes = await routes.buildBackpackBodyForKey(user, {
    wantLite: true,
    baseUrl: 'https://aio.deng.my.id',
    syncDisk: true,
  });
  const out = {
    user,
    status: liteRes.status,
    responseMode: liteRes.body && liteRes.body.responseMode,
    username: liteRes.body && liteRes.body.username,
    fishTypes: liteRes.body && Array.isArray(liteRes.body.fishItems) ? liteRes.body.fishItems.length : null,
    stoneTypes: liteRes.body && Array.isArray(liteRes.body.stoneItems) ? liteRes.body.stoneItems.length : null,
    totemTypes: liteRes.body && Array.isArray(liteRes.body.totemItems) ? liteRes.body.totemItems.length : null,
    topCards: liteRes.body && liteRes.body.topCards,
    status_field: liteRes.body && liteRes.body.status,
    isOnline: liteRes.body && liteRes.body.isOnline,
    bodyBytes: liteRes.body ? Buffer.byteLength(JSON.stringify(liteRes.body)) : 0,
  };
  console.log(JSON.stringify(out, null, 2));
  process.exit(0);
}

main().catch((err) => {
  console.error('smoke failed', err);
  process.exit(1);
});
