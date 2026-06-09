'use strict';
/**
 * Production-safe license generation diagnostic for a website user.
 * Usage: node scripts/debug_license_generation_user.js hazeelniyt
 */
const path = require('path');
module.paths.unshift(path.join(__dirname, '..', 'site', 'node_modules'));
const dotenv = require('dotenv');
dotenv.config({ path: path.join(__dirname, '..', 'site', '.env') });
dotenv.config({ path: path.join(__dirname, '..', '.env') });
dotenv.config({ path: path.join(__dirname, '..', 'env') });

const username = process.argv[2] || process.env.DEBUG_LICENSE_USER || '';
const discordArg = process.argv.find((a) => a.startsWith('--discord='));
const discordUserId = discordArg ? discordArg.slice('--discord='.length) : (process.env.DEBUG_LICENSE_DISCORD || '');
if (!username && !discordUserId) {
  console.error('Usage: node scripts/debug_license_generation_user.js <username> [--discord=<id>]');
  process.exit(1);
}

async function main() {
  const routes = require(path.join(__dirname, '..', 'site', 'src', 'routes'));
  const report = await routes.buildLicenseUserDebugReport({ username, discordUserId });
  console.log(JSON.stringify(report, null, 2));
  if (!report.ok) process.exit(2);
  if (!report.stateSecretConfigured) {
    console.error('\nACTION REQUIRED: set TOOL_SITE_STATE_SECRET (>=32 chars) in .env and restart deng-tool-site');
    process.exit(3);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
