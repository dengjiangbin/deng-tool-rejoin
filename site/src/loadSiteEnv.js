'use strict';

const path = require('path');
const dotenv = require('dotenv');

function loadSiteEnv() {
  const siteDir = path.join(__dirname, '..');
  const rootEnv = path.join(siteDir, '..', '.env');
  const siteEnv = path.join(siteDir, '.env');
  dotenv.config({ path: siteEnv });
  dotenv.config({ path: rootEnv });
  dotenv.config({ path: path.join(siteDir, '..', 'env') });
  if (process.env.NODE_ENV === 'production') {
    dotenv.config({ path: rootEnv, override: true });
    dotenv.config({ path: siteEnv, override: true });
  }
}

module.exports = { loadSiteEnv };
