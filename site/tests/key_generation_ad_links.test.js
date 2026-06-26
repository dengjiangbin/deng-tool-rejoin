'use strict';

const { test, describe, before, after } = require('node:test');
const assert = require('node:assert/strict');

const lootlabs = require('../src/providers/lootlabs');
const linkvertise = require('../src/providers/linkvertise');

const NEW_LOOTLABS = 'https://lootdest.org/s?kb1mUj43';
const OLD_LOOTLABS = 'https://lootdest.org/s?TqZQAW38';
const LINKVERTISE = 'https://link-hub.net/5914830/XEpUhZ8TdtyV';
const STALE_SUBDO_MARKERS = ['subdo', 'TqZQAW38'];

describe('key generation ad links', () => {
  const envBackup = {};

  before(() => {
    for (const key of [
      'LOOTLABS_ENABLED',
      'LOOTLABS_BASE_LINK',
      'LOOTLABS_MONETIZED_URL',
      'LOOTLABS_API_TOKEN',
      'LOOTLABS_ENCRYPT_URL',
      'LINKVERTISE_TARGET_LINK_URL',
      'LINKVERTISE_MONETIZED_URL',
    ]) {
      envBackup[key] = process.env[key];
    }
    process.env.LOOTLABS_ENABLED = 'true';
    process.env.LOOTLABS_BASE_LINK = NEW_LOOTLABS;
    process.env.LOOTLABS_MONETIZED_URL = NEW_LOOTLABS;
    process.env.LOOTLABS_API_TOKEN = 'test-lootlabs-api-token-very-long-do-not-log-1234567890abcdef';
    process.env.LOOTLABS_ENCRYPT_URL = 'https://creators.lootlabs.gg/api/public/url_encryptor';
    process.env.LINKVERTISE_TARGET_LINK_URL = LINKVERTISE;
    process.env.LINKVERTISE_MONETIZED_URL = LINKVERTISE;
  });

  after(() => {
    for (const [key, value] of Object.entries(envBackup)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  });

  test('LootLabs base link uses new slug kb1mUj43 not old TqZQAW38', () => {
    const base = lootlabs.getLootLabsBaseLink();
    assert.equal(base, NEW_LOOTLABS);
    assert.notEqual(base, OLD_LOOTLABS);
  });

  test('stale LOOTLABS_BASE_LINK env is normalized to kb1mUj43', () => {
    process.env.LOOTLABS_BASE_LINK = OLD_LOOTLABS;
    assert.equal(lootlabs.getLootLabsBaseLink(), NEW_LOOTLABS);
    process.env.LOOTLABS_BASE_LINK = NEW_LOOTLABS;
  });

  test('template placeholder in LOOTLABS_BASE_LINK env is stripped', () => {
    process.env.LOOTLABS_BASE_LINK = `${NEW_LOOTLABS}&url={url}`;
    assert.equal(lootlabs.getLootLabsBaseLink(), NEW_LOOTLABS);
    process.env.LOOTLABS_BASE_LINK = NEW_LOOTLABS;
  });

  test('LootLabs redirect builder never emits stale subdo/old slug markers', () => {
    const url = lootlabs.buildLootLabsStartUrl({
      baseLink: NEW_LOOTLABS,
      encryptedData: 'opaque-blob',
    });
    for (const marker of STALE_SUBDO_MARKERS) {
      assert.ok(!url.includes(marker), `generated URL must not contain ${marker}: ${url}`);
    }
    assert.ok(url.startsWith(`${NEW_LOOTLABS}&data=`));
  });

  test('Linkvertise still returns configured link-hub URL', () => {
    assert.equal(linkvertise.getLinkvertiseTargetLinkUrl(), LINKVERTISE);
    assert.ok(linkvertise.getLinkvertiseTargetLinkUrl().includes('link-hub.net'));
  });

  test('missing LootLabs env vars still use canonical kb1mUj43 default', () => {
    delete process.env.LOOTLABS_BASE_LINK;
    delete process.env.LOOTLABS_MONETIZED_URL;
    assert.equal(lootlabs.getLootLabsBaseLink(), NEW_LOOTLABS);
    assert.equal(lootlabs.isLootLabsConfigured(), true);
    process.env.LOOTLABS_BASE_LINK = NEW_LOOTLABS;
    process.env.LOOTLABS_MONETIZED_URL = NEW_LOOTLABS;
  });
});
