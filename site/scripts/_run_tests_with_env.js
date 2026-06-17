'use strict';

// Dev helper: run the npm test suite with the repo-root .env loaded so tests
// that require the app at module load (Supabase / cookie secret) don't fail on
// a missing environment. Mirrors how the PM2 entry points load .env.

const path = require('path');
const { spawnSync } = require('child_process');

require('dotenv').config({ path: path.join(__dirname, '..', '..', '.env') });
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

const pkg = require('../package.json');
const testArgs = pkg.scripts.test.replace(/^node --test /, '').trim().split(/\s+/);

const res = spawnSync(process.execPath, ['--test', ...testArgs], {
  cwd: path.join(__dirname, '..'),
  stdio: 'inherit',
  env: process.env,
});
process.exit(res.status || 0);
