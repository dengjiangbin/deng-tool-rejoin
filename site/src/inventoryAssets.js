'use strict';

const fs = require('fs');
const path = require('path');

const MANIFEST_PATH = path.join(__dirname, 'inventoryAssetManifest.json');

function loadManifest() {
  try {
    return JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
  } catch {
    return null;
  }
}

function inventoryAssetUrls() {
  const manifest = loadManifest();
  if (!manifest || !manifest.css || !manifest.js) {
    return {
      cssUrl: '/public/assets/inventory.fallback.css',
      jsUrl: '/public/assets/inventory.fallback.js',
      marker: 'inventory_assets_missing',
    };
  }
  return {
    cssUrl: `/public/assets/${manifest.css}`,
    jsUrl: `/public/assets/${manifest.js}`,
    marker: manifest.marker || 'inventory_assets',
    cssHash: manifest.cssHash,
    jsHash: manifest.jsHash,
  };
}

// The live deploy marker shown in the /tracker HTML (data-tracker-ui-deploy,
// meta[name=tracker-ui-deploy], data-ui-marker, window.__TRACKER_UI_DEPLOY) MUST
// reflect the bundle that is actually serving, so a grep/curl of production proves
// which build is live. We derive it from the asset manifest marker (the same
// source the hashed JS/CSS URLs come from) instead of a hardcoded constant that
// silently lags every rebuild. Falls back to the supplied constant only when the
// manifest is missing/placeholder.
function inventoryDeployMarker(fallback) {
  const { marker } = inventoryAssetUrls();
  if (marker && !/^inventory_assets/.test(marker)) return marker;
  return fallback || marker || '';
}

module.exports = {
  loadManifest,
  inventoryAssetUrls,
  inventoryDeployMarker,
};
