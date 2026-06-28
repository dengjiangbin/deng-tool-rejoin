'use strict';

// Regression test for the global Supabase fetch timeout added to site/src/db.js.
//
// This is the systemic safety net that keeps the DB strong/stable: every Supabase
// request must abort at a hard ceiling instead of hanging for the full Postgres
// statement_timeout (~125s) and leaking a PostgREST connection. A burst of those
// leaks is exactly what exhausted the pool and took key generation/history down.
//
// We mock @supabase/supabase-js so we can capture the `global.fetch` that db.js
// wires in, and we mock globalThis.fetch with a request that hangs forever unless
// it is aborted — proving the ceiling actually fires.

const test = require('node:test');
const assert = require('node:assert');

test('db.js wires a global fetch that aborts a hung request at the ceiling', async () => {
  process.env.SUPABASE_URL = 'http://example.invalid';
  process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
  process.env.SUPABASE_GLOBAL_FETCH_TIMEOUT_MS = '1000';

  // Capture the options object db.js passes to createClient.
  let captured = null;
  const supaPath = require.resolve('@supabase/supabase-js');
  require.cache[supaPath] = {
    id: supaPath,
    filename: supaPath,
    loaded: true,
    exports: {
      createClient: (_url, _key, options) => {
        captured = options;
        return { __stub: true };
      },
    },
  };

  // Base fetch that never resolves on its own — only an abort can settle it.
  const origFetch = globalThis.fetch;
  let underlyingAborted = false;
  globalThis.fetch = (_input, init) =>
    new Promise((_resolve, reject) => {
      const sig = init && init.signal;
      if (sig) {
        sig.addEventListener(
          'abort',
          () => {
            underlyingAborted = true;
            reject(new Error('the request was aborted'));
          },
          { once: true },
        );
      }
    });

  try {
    delete require.cache[require.resolve('../src/db')];
    require('../src/db');

    assert.ok(
      captured && captured.global && typeof captured.global.fetch === 'function',
      'db.js should wire a custom global.fetch',
    );

    const start = Date.now();
    await assert.rejects(
      () => captured.global.fetch('http://example.invalid/rest/v1/license_keys', {}),
      /abort/i,
      'a hung request must reject (abort) rather than hang forever',
    );
    const elapsed = Date.now() - start;

    assert.ok(underlyingAborted, 'the underlying fetch must receive the abort signal');
    assert.ok(
      elapsed >= 800 && elapsed < 3000,
      `should abort near the 1000ms ceiling, got ${elapsed}ms`,
    );
  } finally {
    globalThis.fetch = origFetch;
    delete require.cache[require.resolve('../src/db')];
    delete require.cache[supaPath];
    delete process.env.SUPABASE_GLOBAL_FETCH_TIMEOUT_MS;
  }
});

test('db.js forwards a caller abort signal (per-call timeouts still work)', async () => {
  process.env.SUPABASE_URL = 'http://example.invalid';
  process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
  // High ceiling so the global timer does NOT fire — the caller signal must.
  process.env.SUPABASE_GLOBAL_FETCH_TIMEOUT_MS = '60000';

  let captured = null;
  const supaPath = require.resolve('@supabase/supabase-js');
  require.cache[supaPath] = {
    id: supaPath,
    filename: supaPath,
    loaded: true,
    exports: {
      createClient: (_url, _key, options) => {
        captured = options;
        return { __stub: true };
      },
    },
  };

  const origFetch = globalThis.fetch;
  globalThis.fetch = (_input, init) =>
    new Promise((_resolve, reject) => {
      const sig = init && init.signal;
      if (sig) {
        sig.addEventListener('abort', () => reject(new Error('aborted by caller')), { once: true });
      }
    });

  try {
    delete require.cache[require.resolve('../src/db')];
    require('../src/db');

    const caller = new AbortController();
    const p = captured.global.fetch('http://example.invalid/x', { signal: caller.signal });
    setTimeout(() => caller.abort(), 50);
    await assert.rejects(() => p, /abort/i, 'a caller abort must propagate to the underlying fetch');
  } finally {
    globalThis.fetch = origFetch;
    delete require.cache[require.resolve('../src/db')];
    delete require.cache[supaPath];
    delete process.env.SUPABASE_GLOBAL_FETCH_TIMEOUT_MS;
  }
});
