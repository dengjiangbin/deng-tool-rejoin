'use strict';

/**
 * Complaint #3 (2026-06-28): "Discord auth timeout when users want to login."
 *
 * Root cause: upsertDiscordUser() awaited Supabase queries with NO timeout, so a
 * slow/unreachable Supabase made the OAuth callback hang until something far
 * upstream gave up — users just saw a spinner / "auth timeout".
 *
 * Fix: each Supabase query in upsertDiscordUser is wrapped in withSupabaseTimeout
 * (which also ABORTS the request). On timeout it throws "<label> upstream request
 * timeout", which isTransientDbError() matches, so the user falls back to a
 * Discord-only session and login still completes fast.
 */

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = 'https://aio.deng.my.id/api/aio/auth/callback';
// Keep the test fast: time out the (hung) Supabase query after 200ms.
process.env.AUTH_SUPABASE_TIMEOUT_MS = '200';

/** A query builder that NEVER resolves — simulates a hung Supabase/Postgres. */
function makeHangingBuilder() {
  let aborted = false;
  const builder = {
    select() { return builder; },
    insert() { return builder; },
    update() { return builder; },
    eq() { return builder; },
    abortSignal(signal) {
      if (signal) {
        signal.addEventListener('abort', () => { aborted = true; });
      }
      return builder;
    },
    maybeSingle() { return builder; },
    single() { return builder; },
    // Thenable that hangs forever (until withSupabaseTimeout aborts + rejects).
    then() { return new Promise(() => {}); },
    wasAborted() { return aborted; },
  };
  return builder;
}

const dbId = path.join(__dirname, '..', 'src', 'db.js');

function installHangingDb() {
  require.cache[dbId] = {
    id: dbId,
    filename: dbId,
    loaded: true,
    exports: { from() { return makeHangingBuilder(); } },
  };
}

function clearAuthCache() {
  delete require.cache[require.resolve('../src/auth')];
  delete require.cache[require.resolve('../src/upstreamTimeout')];
}

describe('Discord login survives a hung Supabase', () => {
  beforeEach(() => {
    installHangingDb();
    clearAuthCache();
  });

  test('upsertDiscordUser fast-fails to a Discord-only session on timeout', async () => {
    const { upsertDiscordUser } = require('../src/auth');
    const discordUser = { id: '123456789012345678', username: 'OutageUser', avatar: null, email: null };

    const t0 = Date.now();
    const user = await upsertDiscordUser(discordUser, {});
    const elapsed = Date.now() - t0;

    // Must NOT hang: completes shortly after the 200ms timeout.
    assert.ok(elapsed < 3000, `login resolved fast (took ${elapsed}ms)`);
    // Falls back to a Discord-only session (no real site_users row).
    assert.equal(user.discord_user_id, discordUser.id);
    assert.equal(user.discord_username, 'OutageUser');
    assert.ok(user.id, 'discord-only session still has a stable synthetic id');
  });

  test('with allowFallback:false the timeout surfaces as a transient error', async () => {
    const { upsertDiscordUser, isTransientDbError } = require('../src/auth');
    const discordUser = { id: '222333444555666777', username: 'NoFallback', avatar: null, email: null };

    await assert.rejects(
      () => upsertDiscordUser(discordUser, {}, { allowFallback: false }),
      (err) => {
        assert.ok(isTransientDbError(err), `error should be classified transient: ${err.message}`);
        return true;
      },
    );
  });
});
