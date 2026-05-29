#!/usr/bin/env node
'use strict';
const path = require('path');

process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH
  || path.join(__dirname, '..', '..', 'DENG Fish It', 'data', 'deng-fish-it.sqlite');

const fishit = require('../site/src/fishitDb');
fishit._resetCache();

const audit = fishit.auditSpeciesImages();
const spotlight = ['Elshark Gran Maja', 'Elshark Grand Maja', 'Skeleton Narwhal', 'King Jelly'];

console.log('Fish It image audit');
console.log('DB:', fishit.DB_PATH);
console.log('Total species:', audit.total);
console.log('With image:', audit.with_image);
console.log('Missing image:', audit.missing);
console.log('');
console.log('Spotlight:');
for (const name of spotlight) {
  const r = fishit.resolveSpeciesImageSource(name, null);
  console.log(`  ${name}: ${r.url ? r.source + ' → ' + r.url.slice(0, 72) + '...' : 'MISSING'}`);
}
if (audit.missing_names.length) {
  console.log('\nMissing (first 25):');
  audit.missing_names.slice(0, 25).forEach((n) => console.log('  -', n));
}
