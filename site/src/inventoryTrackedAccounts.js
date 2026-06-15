'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const supabase = require('./db');

const USERNAME_KEY_RE = /^[a-z0-9_]{3,20}$/;
const memoryStore = new Map();
const FILE_STORE_PATH = process.env.INVENTORY_TRACKED_ACCOUNTS_PATH
  || path.join(__dirname, '..', '..', 'data', 'inventory_tracked_accounts.json');

let storageMode = null; // null | 'supabase' | 'file'

function useMemoryStore() {
  return process.env.NODE_ENV === 'test' || process.env.INVENTORY_ACCOUNTS_MEMORY === '1';
}

function supabaseErrorText(error) {
  if (!error) return '';
  return [
    error.message,
    error.details,
    error.hint,
    error.code,
  ].filter(Boolean).map(String).join(' ');
}

function shouldUseFileStoreFallback(error) {
  const text = supabaseErrorText(error).toLowerCase();
  if (!text) return false;
  return /inventory_tracked_accounts|schema cache|does not exist|relation|pgrst205|pgrst204|could not find the table|fetch failed|enotfound|getaddrinfo|econnrefused|network|placeholder\.supabase|invalid api key|jwt expired|invalid jwt|service role|42p01/i.test(text);
}

function activateFileStore(reason, error) {
  storageMode = 'file';
  console.warn(
    '[inventory-accounts] Supabase unavailable (%s); using file store at %s (%s)',
    reason,
    FILE_STORE_PATH,
    supabaseErrorText(error).slice(0, 180),
  );
}

function normalizeDiscordUserId(value) {
  const id = String(value || '').trim();
  return /^\d{5,32}$/.test(id) ? id : '';
}

function normalizeRobloxUsername(value) {
  const raw = String(value || '').trim();
  if (!raw) return { username: '', key: '' };
  const key = raw.toLowerCase();
  if (!USERNAME_KEY_RE.test(key)) return { username: '', key: '' };
  return { username: raw, key };
}

function normalizeUsernameList(values) {
  const out = [];
  const seen = new Set();
  if (!Array.isArray(values)) return out;
  for (const value of values) {
    const { username, key } = normalizeRobloxUsername(value);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push({ username, key });
  }
  return out;
}

function newRecord(discordUserId, username, key, sortIndex) {
  const now = new Date().toISOString();
  return {
    id: crypto.randomUUID(),
    discord_user_id: discordUserId,
    site_user_id: null,
    roblox_username: username,
    roblox_username_key: key,
    roblox_user_id: null,
    display_name: username,
    sort_index: sortIndex,
    last_seen_at: null,
    last_inventory_sync_at: null,
    created_at: now,
    updated_at: now,
  };
}

function serializeRecord(row) {
  if (!row || typeof row !== 'object') return null;
  return {
    id: row.id,
    discordUserId: row.discord_user_id,
    siteUserId: row.site_user_id || null,
    robloxUsername: row.roblox_username,
    robloxUsernameKey: row.roblox_username_key,
    robloxUserId: row.roblox_user_id != null ? Number(row.roblox_user_id) : null,
    displayName: row.display_name || row.roblox_username,
    sortIndex: Number(row.sort_index) || 0,
    lastSeenAt: row.last_seen_at || null,
    lastInventorySyncAt: row.last_inventory_sync_at || null,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function activeStorage() {
  if (useMemoryStore()) return 'memory';
  if (storageMode === 'file') return 'file';
  if (storageMode === 'supabase') return 'supabase';
  return 'supabase';
}

function loadFileStoreRaw() {
  try {
    if (!fs.existsSync(FILE_STORE_PATH)) return {};
    const parsed = JSON.parse(fs.readFileSync(FILE_STORE_PATH, 'utf8'));
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (err) {
    console.warn('[inventory-accounts] file store read failed:', err && err.message ? err.message : err);
    return {};
  }
}

function saveFileStoreRaw(data) {
  fs.mkdirSync(path.dirname(FILE_STORE_PATH), { recursive: true });
  const tmp = `${FILE_STORE_PATH}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2));
  fs.renameSync(tmp, FILE_STORE_PATH);
}

function fileBucket(store, discordUserId) {
  if (!store[discordUserId]) store[discordUserId] = {};
  return store[discordUserId];
}

function memoryBucket(discordUserId) {
  const key = normalizeDiscordUserId(discordUserId);
  if (!memoryStore.has(key)) memoryStore.set(key, new Map());
  return memoryStore.get(key);
}

function sortRecords(rows) {
  return [...rows]
    .sort((a, b) => (a.sort_index - b.sort_index) || String(a.created_at).localeCompare(String(b.created_at)))
    .map(serializeRecord);
}

function listFromBucket(bucket) {
  return sortRecords([...bucket.values()]);
}

async function listTrackedAccounts(discordUserId) {
  const ownerId = normalizeDiscordUserId(discordUserId);
  if (!ownerId) return [];

  if (useMemoryStore()) {
    return listFromBucket(memoryBucket(ownerId));
  }

  if (activeStorage() === 'file') {
    const store = loadFileStoreRaw();
    const bucket = fileBucket(store, ownerId);
    return sortRecords(Object.values(bucket));
  }

  const { data, error } = await supabase
    .from('inventory_tracked_accounts')
    .select('*')
    .eq('discord_user_id', ownerId)
    .order('sort_index', { ascending: true })
    .order('created_at', { ascending: true });
  if (error) {
    if (shouldUseFileStoreFallback(error)) {
      activateFileStore('list_failed', error);
      return listTrackedAccounts(ownerId);
    }
    throw new Error(supabaseErrorText(error) || 'list_failed');
  }
  storageMode = 'supabase';
  return (data || []).map(serializeRecord);
}

async function addTrackedAccounts(discordUserId, usernames, opts = {}) {
  const ownerId = normalizeDiscordUserId(discordUserId);
  if (!ownerId) {
    const err = new Error('invalid_discord_user');
    err.code = 'invalid_discord_user';
    throw err;
  }

  const entries = normalizeUsernameList(usernames);
  if (!entries.length) {
    return {
      added: [],
      skipped: [],
      accounts: await listTrackedAccounts(ownerId),
      storage: activeStorage(),
    };
  }

  if (useMemoryStore()) {
    const bucket = memoryBucket(ownerId);
    const added = [];
    const skipped = [];
    let sortIndex = bucket.size;
    for (const entry of entries) {
      if (bucket.has(entry.key)) {
        skipped.push(entry.username);
        continue;
      }
      const row = newRecord(ownerId, entry.username, entry.key, sortIndex);
      bucket.set(entry.key, row);
      added.push(serializeRecord(row));
      sortIndex += 1;
    }
    const accounts = await listTrackedAccounts(ownerId);
    return { added, skipped, accounts, storage: 'memory' };
  }

  if (activeStorage() === 'file') {
    const store = loadFileStoreRaw();
    const bucket = fileBucket(store, ownerId);
    const added = [];
    const skipped = [];
    let sortIndex = Object.keys(bucket).length;
    for (const entry of entries) {
      if (bucket[entry.key]) {
        skipped.push(entry.username);
        continue;
      }
      const row = newRecord(ownerId, entry.username, entry.key, sortIndex);
      bucket[entry.key] = row;
      added.push(serializeRecord(row));
      sortIndex += 1;
    }
    saveFileStoreRaw(store);
    const accounts = await listTrackedAccounts(ownerId);
    return { added, skipped, accounts, storage: 'file' };
  }

  const existing = await listTrackedAccounts(ownerId);
  const existingKeys = new Set(existing.map((row) => row.robloxUsernameKey));
  let sortIndex = existing.length;
  const added = [];
  const skipped = [];
  const inserts = [];

  for (const entry of entries) {
    if (existingKeys.has(entry.key)) {
      skipped.push(entry.username);
      continue;
    }
    existingKeys.add(entry.key);
    const row = newRecord(ownerId, entry.username, entry.key, sortIndex);
    sortIndex += 1;
    inserts.push(row);
    added.push(serializeRecord(row));
  }

  if (inserts.length) {
    const { error } = await supabase.from('inventory_tracked_accounts').insert(inserts);
    if (error) {
      if (shouldUseFileStoreFallback(error)) {
        activateFileStore('insert_failed', error);
        return addTrackedAccounts(ownerId, usernames, opts);
      }
      throw new Error(supabaseErrorText(error) || 'insert_failed');
    }
  }

  storageMode = 'supabase';
  return {
    added,
    skipped,
    accounts: await listTrackedAccounts(ownerId),
    storage: 'supabase',
  };
}

async function removeTrackedAccount(discordUserId, usernameKey) {
  const ownerId = normalizeDiscordUserId(discordUserId);
  const key = String(usernameKey || '').trim().toLowerCase();
  if (!ownerId || !USERNAME_KEY_RE.test(key)) {
    const err = new Error('invalid_account');
    err.code = 'invalid_account';
    throw err;
  }

  if (useMemoryStore()) {
    const bucket = memoryBucket(ownerId);
    const existed = bucket.delete(key);
    if (!existed) {
      const err = new Error('not_found');
      err.code = 'not_found';
      throw err;
    }
    return { ok: true, accounts: await listTrackedAccounts(ownerId), storage: 'memory' };
  }

  if (activeStorage() === 'file') {
    const store = loadFileStoreRaw();
    const bucket = fileBucket(store, ownerId);
    if (!bucket[key]) {
      const err = new Error('not_found');
      err.code = 'not_found';
      throw err;
    }
    delete bucket[key];
    saveFileStoreRaw(store);
    return { ok: true, accounts: await listTrackedAccounts(ownerId), storage: 'file' };
  }

  const { data, error } = await supabase
    .from('inventory_tracked_accounts')
    .delete()
    .eq('discord_user_id', ownerId)
    .eq('roblox_username_key', key)
    .select('id');
  if (error) {
    if (shouldUseFileStoreFallback(error)) {
      activateFileStore('delete_failed', error);
      return removeTrackedAccount(ownerId, key);
    }
    throw new Error(supabaseErrorText(error) || 'delete_failed');
  }
  if (!data || !data.length) {
    const err = new Error('not_found');
    err.code = 'not_found';
    throw err;
  }
  storageMode = 'supabase';
  return { ok: true, accounts: await listTrackedAccounts(ownerId), storage: 'supabase' };
}

/**
 * Remove EVERY tracked Roblox username for one Discord owner. Scoped strictly to
 * the owner's inventory_tracked_accounts rows — it never touches the Discord
 * account/user record or any unrelated data. Returns the (now empty) list.
 */
async function removeAllTrackedAccounts(discordUserId) {
  const ownerId = normalizeDiscordUserId(discordUserId);
  if (!ownerId) {
    const err = new Error('invalid_account');
    err.code = 'invalid_account';
    throw err;
  }

  if (useMemoryStore()) {
    const bucket = memoryBucket(ownerId);
    const removed = bucket.size;
    bucket.clear();
    return { ok: true, removed, accounts: [], storage: 'memory' };
  }

  if (activeStorage() === 'file') {
    const store = loadFileStoreRaw();
    const bucket = fileBucket(store, ownerId);
    const removed = Object.keys(bucket).length;
    store[ownerId] = {};
    saveFileStoreRaw(store);
    return { ok: true, removed, accounts: [], storage: 'file' };
  }

  const { data, error } = await supabase
    .from('inventory_tracked_accounts')
    .delete()
    .eq('discord_user_id', ownerId)
    .select('id');
  if (error) {
    if (shouldUseFileStoreFallback(error)) {
      activateFileStore('delete_all_failed', error);
      return removeAllTrackedAccounts(ownerId);
    }
    throw new Error(supabaseErrorText(error) || 'delete_all_failed');
  }
  storageMode = 'supabase';
  return { ok: true, removed: Array.isArray(data) ? data.length : 0, accounts: [], storage: 'supabase' };
}

async function migrateTrackedAccounts(discordUserId, usernames, opts = {}) {
  return addTrackedAccounts(discordUserId, usernames, opts);
}

function resetMemoryStoreForTests() {
  memoryStore.clear();
  storageMode = null;
}

function resetStorageModeForTests() {
  storageMode = null;
}

/** Count unique Roblox usernames registered across all Discord owners (file/memory store). */
function countRegisteredTrackedUsernamesSync() {
  const seen = new Set();
  if (useMemoryStore()) {
    for (const bucket of memoryStore.values()) {
      for (const key of bucket.keys()) seen.add(key);
    }
    return seen.size;
  }
  const store = loadFileStoreRaw();
  for (const bucket of Object.values(store)) {
    if (!bucket || typeof bucket !== 'object') continue;
    for (const key of Object.keys(bucket)) {
      if (USERNAME_KEY_RE.test(key)) seen.add(key);
    }
  }
  return seen.size;
}

/** Resolve Discord owner for a Roblox username key (case-insensitive). */
function resolveOwnerDiscordIdForUsernameSync(usernameKey) {
  const key = String(usernameKey || '').trim().toLowerCase();
  if (!USERNAME_KEY_RE.test(key)) return null;
  if (useMemoryStore()) {
    for (const [ownerId, bucket] of memoryStore.entries()) {
      if (bucket.has(key)) return ownerId;
    }
    return null;
  }
  const store = loadFileStoreRaw();
  for (const [ownerId, bucket] of Object.entries(store)) {
    if (!bucket || typeof bucket !== 'object') continue;
    if (Object.prototype.hasOwnProperty.call(bucket, key)) {
      const normalized = normalizeDiscordUserId(ownerId);
      if (normalized) return normalized;
    }
  }
  return null;
}

async function listDiscordOwnersForUsernameKey(usernameKey) {
  const ownerId = resolveOwnerDiscordIdForUsernameSync(usernameKey);
  return ownerId ? [ownerId] : [];
}

module.exports = {
  normalizeDiscordUserId,
  normalizeRobloxUsername,
  normalizeUsernameList,
  listTrackedAccounts,
  addTrackedAccounts,
  removeTrackedAccount,
  removeAllTrackedAccounts,
  migrateTrackedAccounts,
  countRegisteredTrackedUsernamesSync,
  resolveOwnerDiscordIdForUsernameSync,
  listDiscordOwnersForUsernameKey,
  resetMemoryStoreForTests,
  resetStorageModeForTests,
};
