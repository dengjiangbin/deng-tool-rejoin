'use strict';

/**
 * /tracker top-summary card icons — delegates to owned top-grid asset cache.
 * See fishitTrackerTopGridAssets.js for DB resolve → copy → manifest → public URL flow.
 */

const topGrid = require('./fishitTrackerTopGridAssets');

module.exports = {
  ONLINE_AVATAR_URL: topGrid.ONLINE_AVATAR_URL,
  TOP_GRID_CARD_ORDER: topGrid.TOP_GRID_CARD_ORDER,
  TOP_GRID_ASSET_KEYS: topGrid.TOP_GRID_ASSET_KEYS,
  syncTopGridAssets: topGrid.syncTopGridAssets,
  resolveTopGridIcons: topGrid.resolveTopGridIcons,
  resolveTopSummaryIcons: topGrid.resolveTopSummaryIcons,
  representativeFishByRarity: topGrid.representativeFishByRarity,
  rubyIcon: topGrid.rubySource,
  evolvedStoneIcon: topGrid.evolvedStoneSource,
  runicStoneIcon: topGrid.runicManualSource,
};
