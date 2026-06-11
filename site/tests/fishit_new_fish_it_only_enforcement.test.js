'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_REQUIRE_TRACKER_PROOF_IN_TEST = '1';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';
const trackerRouter = require('../src/fishitTrackerRoutes');
const {
  MINIMUM_TRACKER_BUILD,
  ALLOWED_TRACKER_CHANNEL,
  ALLOWED_TRACKER_RAW_URL,
} = require('../src/fishitTrackerChannelEnforcement');
const { CLEAN_TRACKER_LOADSTRING, PROTECTED_TRACKER_RAW_URL } = require('../src/fishitTrackerLoadstring');

function makeApp() {
  const app = express();
  app.use(trackerRouter);
  return app;
}

function validPayload(extra = {}) {
  return {
    username: 'FishItProofUser',
    userId: 123456,
    isOnline: true,
    type: 'tracker_status',
    clientOrigin: 'roblox_tracker',
    evidenceSourceMode: 'live_roblox',
    trackerBuild: MINIMUM_TRACKER_BUILD,
    trackerChannel: ALLOWED_TRACKER_CHANNEL,
    scriptSource: ALLOWED_TRACKER_RAW_URL,
    trackerClientProof: {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      trackerChannel: ALLOWED_TRACKER_CHANNEL,
      scriptSource: ALLOWED_TRACKER_RAW_URL,
    },
    ...extra,
  };
}

describe('NEW_FISH_IT_ONLY tracker channel enforcement', () => {
  test('public loader uses fish-it/main/tracker.lua with cache-busted build query', () => {
    assert.equal(
      CLEAN_TRACKER_LOADSTRING,
      'loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua?v=LOADER_REGISTER_LIMIT_FIX_2026_06_11"))()',
    );
    assert.equal(PROTECTED_TRACKER_RAW_URL, ALLOWED_TRACKER_RAW_URL);
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /\/dist\/tracker\.lua/);
  });

  test('new tracker proof payload is accepted', async () => {
    const res = await request(makeApp())
      .post('/api/fishit-tracker/update-backpack')
      .send(validPayload())
      .expect(200);
    assert.equal(res.body.ok, true);
  });

  test('missing trackerBuild is rejected with 403', async () => {
    const body = validPayload();
    delete body.trackerBuild;
    delete body.trackerClientProof.trackerBuild;
    const res = await request(makeApp())
      .post('/api/fishit-tracker/update-backpack')
      .send(body)
      .expect(403);
    assert.equal(res.body.error, 'tracker_client_rejected');
    assert.match(res.body.reasons.join(','), /missing_tracker_build/);
  });

  test('old tracker build is rejected with 403', async () => {
    const res = await request(makeApp())
      .post('/api/fishit-tracker/update-backpack')
      .send(validPayload({
        trackerBuild: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
        trackerClientProof: {
          trackerBuild: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
          trackerChannel: ALLOWED_TRACKER_CHANNEL,
          scriptSource: ALLOWED_TRACKER_RAW_URL,
        },
      }))
      .expect(403);
    assert.equal(res.body.error, 'tracker_client_rejected');
    assert.match(res.body.reasons.join(','), /old_tracker_build/);
  });

  test('legacy script source path is rejected with 403', async () => {
    const res = await request(makeApp())
      .post('/api/fishit-tracker/update-backpack')
      .send(validPayload({
        scriptSource: 'https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/dist/tracker.lua',
        trackerClientProof: {
          trackerBuild: MINIMUM_TRACKER_BUILD,
          trackerChannel: ALLOWED_TRACKER_CHANNEL,
          scriptSource: 'https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/dist/tracker.lua',
        },
      }))
      .expect(403);
    assert.equal(res.body.error, 'tracker_client_rejected');
    assert.match(res.body.reasons.join(','), /legacy_script_source/);
  });
});
