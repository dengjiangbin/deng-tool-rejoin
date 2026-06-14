'use strict';

const path = require('path');
const fs = require('fs');

process.chdir(path.join(__dirname, '..'));
delete process.env.FISHIT_LIVE_SESSIONS_PATH;
process.env.FISHIT_SESSION_SHARDED = '1';
process.env.FISHIT_LIVE_SESSIONS_DIR = path.join(__dirname, '..', 'data', 'fishit_live_sessions');

const sessionStore = require('../src/fishitSessionStore');

const legacy = path.join(__dirname, '..', 'data', 'fishit_live_sessions.json');
if (!fs.existsSync(legacy)) {
  console.error('No legacy monolith at', legacy);
  process.exit(1);
}

const before = fs.statSync(legacy);
const result = sessionStore.migrateToShardedStorageIfNeeded();
console.log(JSON.stringify({
  legacyBytes: before.size,
  result,
  shardedIndex: fs.existsSync(path.join(process.env.FISHIT_LIVE_SESSIONS_DIR, 'index.json')),
}, null, 2));
