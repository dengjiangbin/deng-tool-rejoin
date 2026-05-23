'use strict';

const fs = require('fs');
const path = require('path');

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return;
  const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim();
    let value = trimmed.slice(eq + 1).trim();
    value = value.replace(/^['"]|['"]$/g, '');
    if (!Object.prototype.hasOwnProperty.call(process.env, key)) {
      process.env[key] = value;
    }
  }
}

loadEnvFile(path.join(__dirname, '..', '.env'));
loadEnvFile(path.join(__dirname, '..', 'env'));

const supabase = require('../site/src/db');

function usage() {
  console.log('Usage: node scripts/backfill_site_license_owners.js --dry-run|--apply [--discord-user-id ID]');
}

function parseArgs(argv) {
  const args = { apply: false, dryRun: false, discordUserId: '' };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--apply') args.apply = true;
    else if (arg === '--dry-run') args.dryRun = true;
    else if (arg === '--discord-user-id') {
      args.discordUserId = String(argv[i + 1] || '').trim();
      i += 1;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  if (args.apply === args.dryRun) throw new Error('Pass exactly one of --dry-run or --apply');
  return args;
}

function backupPath() {
  const stamp = new Date().toISOString().replace(/[:.]/g, '').replace('T', '_').slice(0, 15);
  const dir = path.join(__dirname, '..', 'data', 'backups');
  fs.mkdirSync(dir, { recursive: true });
  return path.join(dir, `site_license_owner_backfill_${stamp}.json`);
}

async function fetchCandidates(discordUserId) {
  let query = supabase
    .from('license_keys')
    .select('id, owner_discord_id, site_user_id, status, created_at')
    .is('owner_discord_id', null);
  if (discordUserId) {
    const { data: siteUsers, error: userError } = await supabase
      .from('site_users')
      .select('id, discord_user_id')
      .eq('discord_user_id', discordUserId);
    if (userError) throw userError;
    const siteIds = (siteUsers || []).map((row) => row.id).filter(Boolean);
    if (!siteIds.length) return [];
    query = query.in('site_user_id', siteIds);
  }
  const { data, error } = await query;
  if (error) throw error;
  return data || [];
}

async function fetchSiteUsers(siteIds) {
  if (!siteIds.length) return new Map();
  const { data, error } = await supabase
    .from('site_users')
    .select('id, discord_user_id')
    .in('id', siteIds);
  if (error) throw error;
  return new Map((data || []).map((row) => [row.id, row.discord_user_id]));
}

async function ensureLicenseUser(discordUserId) {
  const { error } = await supabase
    .from('license_users')
    .upsert({
      discord_user_id: discordUserId,
      discord_username: 'DENG Tool Portal User',
      max_keys: 999999,
      is_owner: false,
      is_blocked: false,
    }, { onConflict: 'discord_user_id' });
  if (error) throw error;
}

async function main() {
  const args = parseArgs(process.argv);
  const candidates = await fetchCandidates(args.discordUserId);
  const siteIds = [...new Set(candidates.map((row) => row.site_user_id).filter(Boolean))];
  const siteUsers = await fetchSiteUsers(siteIds);

  const fixable = [];
  const skipped = [];
  for (const row of candidates) {
    const discordUserId = siteUsers.get(row.site_user_id);
    if (!row.site_user_id || !discordUserId) {
      skipped.push({ id: row.id, reason: 'missing_site_user_discord_user_id' });
      continue;
    }
    if (String(row.status || '').toLowerCase() === 'revoked') {
      skipped.push({ id: row.id, reason: 'revoked_not_relinked' });
      continue;
    }
    fixable.push({ id: row.id, site_user_id: row.site_user_id, discord_user_id: discordUserId });
  }

  let backup = '';
  if (args.apply && fixable.length) {
    backup = backupPath();
    fs.writeFileSync(backup, `${JSON.stringify({
      created_at: new Date().toISOString(),
      rows: candidates,
    }, null, 2)}\n`);
    for (const row of fixable) {
      await ensureLicenseUser(row.discord_user_id);
      const { error } = await supabase
        .from('license_keys')
        .update({ owner_discord_id: row.discord_user_id })
        .eq('id', row.id)
        .is('owner_discord_id', null);
      if (error) throw error;
    }
  }

  console.log(JSON.stringify({
    mode: args.apply ? 'apply' : 'dry-run',
    rows_scanned: candidates.length,
    rows_fixable: fixable.length,
    rows_fixed: args.apply ? fixable.length : 0,
    rows_skipped_or_manual_review: skipped.length,
    backup_path: backup || null,
  }, null, 2));
}

main().catch((err) => {
  console.error(`Backfill failed: ${err.message || err}`);
  usage();
  process.exit(1);
});
