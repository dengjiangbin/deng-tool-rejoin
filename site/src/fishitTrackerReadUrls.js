'use strict';

/** Public read URLs for aio.deng.my.id — web port 8791, not ingest /api/fishit-tracker/*. */
function trackerReadAssetUrl(baseUrl, category, filename, version = '0') {
  const base = String(baseUrl || '').replace(/\/$/, '');
  const file = String(filename || '').split(/[/\\]/).pop();
  if (!file) return null;
  const v = version != null ? String(version) : '0';
  return `${base}/api/tracker/assets/${category}/${file}?v=${v}`;
}

function trackerReadManualAssetUrl(baseUrl, category, filename) {
  const base = String(baseUrl || '').replace(/\/$/, '');
  const cat = String(category || 'item').trim().toLowerCase();
  const file = String(filename || '').split(/[/\\]/).pop();
  if (!file) return null;
  return `${base}/api/tracker/assets/manual/${cat}/${file}`;
}

function trackerReadImageUrl(baseUrl, assetId) {
  const base = String(baseUrl || '').replace(/\/$/, '');
  const id = String(assetId || '').trim();
  if (!/^\d{10,22}$/.test(id)) return null;
  return `${base}/api/tracker/image/${id}`;
}

module.exports = {
  trackerReadAssetUrl,
  trackerReadManualAssetUrl,
  trackerReadImageUrl,
};
