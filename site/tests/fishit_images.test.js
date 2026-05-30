'use strict';

const { describe, test, before } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const fs = require('fs');

const DEFAULT_DB = path.join(__dirname, '..', '..', 'DENG Fish It', 'data', 'deng-fish-it.sqlite');
const hasDb = fs.existsSync(DEFAULT_DB);

describe('Fish image resolution (DB-backed)', { skip: !hasDb }, () => {
  let fishit;

  before(() => {
    process.env.FISHIT_DB_PATH = DEFAULT_DB;
    delete require.cache[require.resolve('../src/fishitDb')];
    fishit = require('../src/fishitDb');
    fishit._resetCache();
  });

  const spotlight = [
    { name: 'Elshark Grand Maja', alias: true },
    { name: 'Elshark Gran Maja', alias: false },
    { name: 'Skeleton Narwhal', alias: false },
    { name: 'King Jelly', alias: false },
  ];

  for (const { name } of spotlight) {
    test(`${name} resolves a real imageUrl`, () => {
      const r = fishit.resolveSpeciesImageSource(name, null);
      assert.ok(r.url, `${name} should have image from ${r.source}`);
      assert.ok(/^https?:\/\//.test(r.url), 'must be http URL');
      assert.ok(!/fallback/i.test(r.url), 'must not be fallback');
    });
  }

  test('Elshark Grand Maja alias resolves to the canonical Gran image', () => {
    const gran = fishit.resolveSpeciesImageSource('Elshark Gran Maja', null);
    const grand = fishit.resolveSpeciesImageSource('Elshark Grand Maja', null);
    assert.equal(grand.url, gran.url);
    assert.equal(grand.source, gran.source);
  });

  test('audit reports species with images', () => {
    const audit = fishit.auditSpeciesImages();
    assert.ok(audit.total > 0);
    assert.ok(audit.with_image > 0);
    assert.ok(audit.with_image <= audit.total);
  });
});
