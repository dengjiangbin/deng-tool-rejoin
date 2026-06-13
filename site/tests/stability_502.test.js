'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-stability';
process.env.TOOL_SITE_STATE_SECRET = 'test-state-secret-that-is-long-enough-for-stability';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = 'https://tool.deng.my.id/auth/discord/callback';

const { isSessionlessPath } = require('../src/publicDomain');
const { FileSessionStore } = require('../src/sessionStore');

describe('502 stability fixes', () => {
  test('isSessionlessPath skips tracker uploads and health only', () => {
    assert.equal(isSessionlessPath('/health'), true);
    assert.equal(isSessionlessPath('/api/fishit-tracker/update-backpack'), true);
    assert.equal(isSessionlessPath('/api/tracker/upload'), true);
    assert.equal(isSessionlessPath('/login'), false);
    assert.equal(isSessionlessPath('/auth/discord'), false);
    assert.equal(isSessionlessPath('/tracker'), false);
  });

  test('FileSessionStore retries EBUSY on rename', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'deng-session-busy-'));
    const store = new FileSessionStore({ dir, ttlMs: 60_000 });
    const sid = 'busy-session-id';
    const file = store._file(sid);
    const tmp = `${file}.manual.tmp`;

    await fs.promises.writeFile(tmp, JSON.stringify({ expires_at: Date.now() + 60_000, session: {} }));

    let renameCalls = 0;
    const originalRename = fs.promises.rename.bind(fs.promises);
    fs.promises.rename = async (from, to) => {
      renameCalls += 1;
      if (renameCalls === 1) {
        const err = new Error('resource busy');
        err.code = 'EBUSY';
        throw err;
      }
      return originalRename(from, to);
    };

    try {
      await store._renameOrCopyWithRetry(tmp, file);
      assert.ok(fs.existsSync(file));
      assert.equal(renameCalls, 2);
    } finally {
      fs.promises.rename = originalRename;
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  test('FileSessionStore prunes expired sessions by mtime', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'deng-session-prune-'));
    const store = new FileSessionStore({ dir, ttlMs: 1000 });
    const oldFile = path.join(dir, 'old.json');
    fs.writeFileSync(oldFile, '{}');
    const oldTime = Date.now() - 10_000;
    fs.utimesSync(oldFile, oldTime / 1000, oldTime / 1000);

    store._pruneExpiredByMtime();
    assert.equal(fs.existsSync(oldFile), false);
    fs.rmSync(dir, { recursive: true, force: true });
  });
});
