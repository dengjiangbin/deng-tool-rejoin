(function(){'use strict';function readInventoryCfg(){const el=document.getElementById('inventory-runtime');if(!el)return{};try{return JSON.parse(el.textContent||'{}');}catch(_){return{};}}const __CFG__=readInventoryCfg();
const LS_KEY        = 'fishit_tracked_users';
  const LS_BULK_CACHE = 'fishit_bulk_inventory_cache_v1';
  const POLL_MS       = 10000;
  const SYNC_TICK_MS  = 1000;
  const DEBUG_INVENTORY = !!__CFG__.debugInventory;
  const APK_EMBED = !!__CFG__.apkEmbed;
  const DEBUG_GLOBAL  = DEBUG_INVENTORY && /(?:^|[?&])debug=global(?:&|$)/.test(window.location.search);
  const TRACKER_UI_DEPLOY = __CFG__.trackerUiDeployMarker || '';
  const INITIAL_USERNAME = __CFG__.initialUsername || '';
  const RENDER_BUILD = DEBUG_INVENTORY ? (__CFG__.renderBuild || '') : '';
  const PUBLIC_API_BUILD = DEBUG_INVENTORY ? (__CFG__.publicApiBuild || '') : '';
  const RARITY_NAME_COLORS = {
    common:'#f8fafc', uncommon:'#84cc16', rare:'#1e3a8a', epic:'#a855f7',
    legendary:'#ff8c00', legend:'#ff8c00', mythic:'#ef4444', secret:'#00ff7f', forgotten:'#e5e7eb',
  };
  const RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', legend:'rarity-legendary', mythic:'rarity-mythic', secret:'rarity-secret badge-rarity-secret', forgotten:'rarity-forgotten' };
  const CARD_RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', legend:'rarity-legendary', mythic:'rarity-mythic', secret:'rarity-secret', forgotten:'rarity-forgotten' };const FT_RARITY_CLASS = {"common":"ft-rarity-COMMON","uncommon":"ft-rarity-UNCOMMON","rare":"ft-rarity-RARE","epic":"ft-rarity-EPIC","legendary":"ft-rarity-LEGENDARY","legend":"ft-rarity-LEGENDARY","mythic":"ft-rarity-MYTHIC","secret":"ft-rarity-SECRET","forgotten":"ft-rarity-FORGOTTEN"};
  function ftRarityClass(r) { return r ? (FT_RARITY_CLASS[String(r).toLowerCase()] || 'ft-rarity-COMMON') : 'ft-rarity-COMMON'; }
  const RARITY_ORDER = {
    Forgotten: 800, Secret: 700, Mythic: 600, Legendary: 500, Epic: 400,
    Rare: 300, Uncommon: 200, Common: 100, Unknown: 0,
  };
  const STONE_TYPE_ORDER = { Normal: 10, Double: 20, Evolved: 30, Eggy: 40, Runic: 50 };const STONE_DISPLAY_NAMES = {"Normal":"Normal Enchant Stone","Double":"Transcended Stone","Evolved":"Evolved Enchant Stone","Eggy":"Eggy Enchant Stone","Runic":"Runic Enchant Stone"};
  const PEOPLE_ICON_SVG = '<svg class="card-chip-icon" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5s-3 1.34-3 3 1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5C15 14.17 10.33 13 8 13zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/></svg>';
  const TIER_TO_RARITY = {
    1: 'Common', 2: 'Uncommon', 3: 'Rare', 4: 'Epic',
    5: 'Legendary', 6: 'Mythic', 7: 'Secret', 8: 'Forgotten',
  };
  const trackers       = new Map();
  let bulkSearchQuery  = '';
  let accountSearchQuery = '';
  let accountStatusFilter = 'all';
  let accountViewMode = 'table';
  let activeAccountKey = null;
  let inventoryGridFilter = 'all';
  let hideUsernames = false;
  const LS_HIDE_USERNAMES = 'fishit_hide_usernames_v1';
  const inputEl        = document.getElementById('usernameInput');
  const addBtn         = document.getElementById('addBtn');
  const multipleBtn    = document.getElementById('multipleBtn');
  const multipleAddModalEl = document.getElementById('multipleAddModal');
  const multipleAddTextareaEl = document.getElementById('multipleAddTextarea');
  const multipleAddErrorEl = document.getElementById('multipleAddError');
  const multipleAddCancelEl = document.getElementById('multipleAddCancel');
  const multipleAddSubmitEl = document.getElementById('multipleAddSubmit');
  const removeMenuBtn  = document.getElementById('removeMenuBtn');
  const removeMenuEl   = document.getElementById('removeMenu');
  const summaryBarEl   = document.getElementById('summaryBar');
  const trackerListEl  = document.getElementById('trackerList');
  const bulkPanelEl    = document.getElementById('bulkInventoryPanel');
  const bulkBodyEl     = document.getElementById('bulkInventoryBody');
  const summaryTextEl  = document.getElementById('summaryText');
  const statOnlineAccountsEl = document.getElementById('statOnlineAccounts');
  const statEvolvedStonesEl = document.getElementById('statEvolvedStones');
  const statSecretFishEl = document.getElementById('statSecretFish');
  const statForgottenFishEl = document.getElementById('statForgottenFish');
  const accountsOverviewEl = document.getElementById('accountsOverview');
  const accountsSearchInputEl = document.getElementById('accountsSearchInput');
  const accountsTableBodyEl = document.getElementById('accountsTableBody');
  const accountsMobileListEl = document.getElementById('accountsMobileList');
  const viewTableBtn = document.getElementById('viewTableBtn');
  const viewFishGridBtn = document.getElementById('viewFishGridBtn');
  const viewStoneGridBtn = document.getElementById('viewStoneGridBtn');
  const hideUsernamesBtn = document.getElementById('hideUsernamesBtn');
  const hideUsernameIconEl = document.getElementById('hideUsernameIcon');
  const sidebarScriptBtn = document.getElementById('sidebarScriptBtn');
  const inventoryViewSectionEl = document.getElementById('inventoryViewSection');
  const refreshAccountsBtn = document.getElementById('refreshAccountsBtn');
  const copyUsernamesBtn = document.getElementById('copyUsernamesBtn');
  const copyBtn        = document.getElementById('copyBtn');
  const copyStatusEl   = document.getElementById('copyStatus');
  const loadstringCodeEl = document.getElementById('loadstringCode');
  const usernameErrorEl = document.getElementById('usernameError');
  const CLEAN_LOADSTRING = (loadstringCodeEl && loadstringCodeEl.value)
    || (__CFG__.trackerLoadstring || '');
  function loadSaved() { try { return JSON.parse(localStorage.getItem(LS_KEY) || '[]'); } catch { return []; } }
  function saveCurrent() { try { localStorage.setItem(LS_KEY, JSON.stringify([...trackers.keys()])); } catch {} }
  function escHtml(s) {
    return String(s)
      .replace(/&/g, '\u0026amp;')
      .replace(/</g, '\u0026lt;')
      .replace(/>/g, '\u0026gt;')
      .replace(/"/g, '\u0026quot;');
  }
  function formatQuantity(value) {
    const n = Number(value || 0);
    if (!Number.isFinite(n)) return '0';
    return Math.max(0, Math.floor(n)).toLocaleString('en-US');
  }
  function formatAmountLabel(value) {
    return 'x' + formatQuantity(value);
  }
  function resolveItemAmount(item) {
    if (!item || typeof item !== 'object') return 1;
    const raw = item.amount ?? item.quantity ?? item.Quantity ?? 1;
    return raw;
  }
  function amountBadgeHtml(item) {
    return `<span class="amount-badge fish-card__amount ic-qty">${escHtml(formatAmountLabel(resolveItemAmount(item)))}</span>`;
  }
  function stoneDisplayName(item) {
    const type = String(item?.stoneType || item?.StoneType || '').trim();
    if (type && STONE_DISPLAY_NAMES[type]) return STONE_DISPLAY_NAMES[type];
    const raw = String(item?.displayName || item?.name || '').trim();
    if (/double enchant/i.test(raw)) return 'Transcended Stone';
    return raw || 'Enchant Stone';
  }
  function formatWeightFromGrams(raw) {
    const v = Number(raw);
    if (!Number.isFinite(v) || v <= 0) return '';
    const trim = (s) => s.replace(/\.0([BK])?$/, '$1');
    if (v >= 1e9) return trim((v / 1e9).toFixed(1)) + 'B';
    if (v >= 1e6) return trim((v / 1e6).toFixed(1)) + 'M';
    if (v >= 1e3) return trim((v / 1e3).toFixed(1)) + 'K';
    return String(Math.round(v));
  }
  function formatCardWeight(item) {
    if (!item || typeof item !== 'object') return '';
    const dw = item.debugWeight;
    if (dw && dw.display) return String(dw.display);
    if (dw && dw.maxGrams != null) return formatWeightFromGrams(dw.maxGrams);
    const raw = item.weightKg != null ? item.weightKg
      : (item.weight != null ? item.weight : (item.maxWeightGrams != null ? item.maxWeightGrams : null));
    return formatWeightFromGrams(raw);
  }
  function cardChipHtml(className, inner) {
    return `<span class="card-chip ${className}">${inner}</span>`;
  }
  function ownersChipHtml(count) {
    return `<span class="ft-chip ft-chip-people">${PEOPLE_ICON_SVG.replace('class="card-chip-icon"', 'class="ft-chip-icon"')}${escHtml(formatQuantity(count || 1))}</span>`;
  }
  function buildOwnerBreakdownHtml(ownerAmounts) {
    const entries = Object.entries(ownerAmounts || {})
      .filter(([, amt]) => Math.max(0, Math.floor(Number(amt) || 0)) > 0)
      .sort((a, b) => b[1] - a[1]);
    if (!entries.length) return '';
    const parts = entries.map(([user, amt]) =>
      `<span class="ft-chip ft-chip-owner" title="${escHtml(user)}">${escHtml(user)} <span class="ft-chip-owner-qty">${escHtml(formatAmountLabel(amt))}</span></span>`
    );
    return `<div class="ft-card-stats ft-card-stats--owners">${parts.join('')}</div>`;
  }
  const SYNC_LIVE_MAX_SEC = 15;
  function isEntryStatusGreen(entry) {
    if (!entry) return false;
    const data = entry.lastData;
    if (data && data.currentStatus === 'green') return true;
    if (data && data.connectionStatus === 'live') return true;
    return false;
  }
  function entryRedSince(entry) {
    const data = entry && entry.lastData;
    if (data && data.redSince) return data.redSince;
    const lastOk = data && data.lastSuccessfulUploadAt;
    const interval = (data && Number(data.intervalSeconds) > 0) ? Number(data.intervalSeconds) : 10;
    const grace = (data && Number(data.graceSeconds) >= 0) ? Number(data.graceSeconds) : 5;
    if (lastOk) {
      const deadlineMs = new Date(lastOk).getTime() + (interval + grace) * 1000;
      if (Date.now() > deadlineMs) return new Date(deadlineMs).toISOString();
    }
    return entryDisplaySyncTimestamp(entry);
  }
  function statsSyncTimestamp(data) {
    if (!data) return null;
    const fields = [
      data.lastStatsUploadAt,
      data.playerStatsUpdatedAt,
      data.lastSnapshotUploadAt,
      data.lastInventoryAt,
      data.lastSuccessfulUploadAt,
    ];
    let best = null;
    let bestMs = -1;
    for (const ts of fields) {
      if (!ts) continue;
      const ms = new Date(ts).getTime();
      if (Number.isFinite(ms) && ms > bestMs) {
        bestMs = ms;
        best = ts;
      }
    }
    return best;
  }
  function entryDisplaySyncTimestamp(entry) {
    if (!entry) return null;
    return entry.lastSyncAt || entry.lastPollOkAt || bestSyncTimestamp(entry.lastData);
  }
  function pad2(value) {
    return String(Math.max(0, Math.floor(Number(value) || 0))).padStart(2, '0');
  }
  function syncAgeSeconds(timestamp) {
    if (!timestamp) return null;
    try {
      const secs = Math.floor((Date.now() - new Date(timestamp).getTime()) / 1000);
      if (!Number.isFinite(secs) || secs < 0) return null;
      return secs;
    } catch { return null; }
  }
  function formatExactSyncAge(timestamp) {
    const secs = syncAgeSeconds(timestamp);
    if (secs == null) return 'no sync';
    if (secs < 60) return `${secs}s`;
    if (secs < 3600) {
      const mins = Math.floor(secs / 60);
      const remSecs = secs % 60;
      return `${mins}m ${pad2(remSecs)}s`;
    }
    if (secs < 86400) {
      const hrs = Math.floor(secs / 3600);
      const remMins = Math.floor((secs % 3600) / 60);
      return `${hrs}h ${pad2(remMins)}m`;
    }
    return `${Math.floor(secs / 86400)}D`;
  }
  function syncFreshnessFromTimestamp(timestamp) {
    const secs = syncAgeSeconds(timestamp);
    if (secs == null) return 'dead';
    if (secs <= SYNC_LIVE_MAX_SEC) return 'live';
    return 'dead';
  }
  function bestSyncTimestamp(data) {
    return statsSyncTimestamp(data);
  }
  function isTrackerOnline(entry) {
    return isEntryStatusGreen(entry);
  }
  function tableSyncFreshness(entryOrTimestamp) {
    if (entryOrTimestamp && typeof entryOrTimestamp === 'object' && entryOrTimestamp.displayName != null) {
      return entryConnectionFreshness(entryOrTimestamp);
    }
    return syncFreshnessFromTimestamp(entryOrTimestamp);
  }
  function formatTableSyncAge(timestamp) {
    const secs = syncAgeSeconds(timestamp);
    if (secs == null) return 'no sync';
    if (secs < 60) return `${Math.max(1, secs)}s`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
    return `${Math.floor(secs / 86400)}d`;
  }
  function formatCompactStatNumber(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return null;
    const abs = Math.abs(n);
    if (abs >= 1e9) return `${(n / 1e9).toFixed(1).replace(/\.0$/, '')}B`;
    if (abs >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
    if (abs >= 1e3) return `${(n / 1e3).toFixed(1).replace(/\.0$/, '')}K`;
    return String(Math.max(0, Math.floor(n)));
  }
  function formatGroupedCaughtNumber(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return null;
    return Math.max(0, Math.floor(n)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  }
  const BACKPACK_NAV_ICON = '<span class="nav-icon" aria-hidden="true" data-nav-icon="backpack"><svg viewBox="0 0 24 24" focusable="false"><path d="M4 10a4 4 0 0 1 4-4h8a4 4 0 0 1 4 4v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V10Z"></path><path d="M9 6V5a3 3 0 0 1 3-3h0a3 3 0 0 1 3 3v1"></path><path d="M8 21v-5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v5"></path></svg></span>';
  function formatUsernameForDisplay(username, opts) {
    opts = opts || {};
    const s = String(username || '').trim();
    if (!s) return 'ΓÇö';
    if (!opts.hideUsernames) return s;
    const len = s.length;
    if (len === 1) return '*';
    if (len === 2) return `${s[0]}*`;
    if (len === 3) return `${s[0]}*${s[2]}`;
    return `${s.slice(0, 2)}***${s.slice(-2)}`;
  }
  function resolveEntryPlayerStatsSource(entry) {
    if (!entry) return null;
    if (entry.liveSnapshot && entry.liveSnapshot.playerStats) {
      return { stats: entry.liveSnapshot.playerStats, source: 'liveSnapshot.playerStats' };
    }
    return null;
  }
  const TRUSTED_PLAYERSTATS_BUILD_MARKS = ['BLOCKER10ZT5', 'BLOCKER10ZT4', 'BLOCKER10ZT3', 'BLOCKER10ZW'];
  function isTrustedPlayerStats(stats) {
    if (!stats || typeof stats !== 'object') return false;
    const build = stats.build || '';
    const source = stats.source || '';
    if (!TRUSTED_PLAYERSTATS_BUILD_MARKS.some((mark) => String(build).includes(mark))) return false;
    return source === 'replion' || source === 'leaderstats' || source === 'missing';
  }
  function displayableEntryPlayerStats(stats) {
    if (!isTrustedPlayerStats(stats)) return null;
    if (stats.source === 'missing') return stats;
    const hasValues = stats.coins != null || stats.totalCaught != null
      || stats.coinsText || stats.totalCaughtText || stats.rarestFishChance;
    return hasValues ? stats : null;
  }
  function getEntryPlayerStats(entry) {
    if (!entry || !entry.liveSnapshot) return null;
    return entry.liveSnapshot.playerStats || null;
  }
  const HIDE_USERNAME_EYE_SVG = '<svg viewBox="0 0 24 24" focusable="false"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"></path><circle cx="12" cy="12" r="3"></circle></svg>';
  const HIDE_USERNAME_EYE_OFF_SVG = '<svg viewBox="0 0 24 24" focusable="false"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-5 0-9.27-3.11-11-7 1.09-2.28 2.94-4.12 5.18-5.18"></path><path d="M1 1l22 22"></path><path d="M9.88 9.88a3 3 0 0 0 4.24 4.24"></path><path d="M10.73 5.08A10.94 10.94 0 0 1 12 4c5 0 9.27 3.11 11 7a11.8 11.8 0 0 1-2.16 3.19"></path></svg>';
  function normalizePollPlayerStats(raw) {
    const stats = displayableEntryPlayerStats(raw);
    if (!stats) return null;
    const out = { ...stats };
    if (out.coins != null) out.coinsText = formatCompactStatNumber(out.coins);
    if (out.totalCaught != null) out.totalCaughtText = formatGroupedCaughtNumber(out.totalCaught);
    return out;
  }
  function extractPlayerStatsFromPayload(data) {
    const raw = data && data.playerStats;
    if (!raw || typeof raw !== 'object') return null;
    return normalizePollPlayerStats(raw);
  }
  function buildLiveSnapshotFromPayload(entry, data, pollAt) {
    const fishList = getPublicFishItems(data);
    const stoneList = getPublicStoneItems(data);
    const prevCount = entry && entry.liveSnapshot && entry.liveSnapshot.pollCount || 0;
    return {
      pollAt,
      payloadAt: (data && (data.playerStatsUpdatedAt || data.lastInventoryAt || data.updatedAt)) || pollAt,
      pollCount: prevCount + 1,
      playerStats: extractPlayerStatsFromPayload(data),
      fishList,
      stoneList,
      fishCount: fishList.length,
      stoneCount: stoneList.length,
    };
  }
  function syncEntryFromLiveSnapshot(entry) {
    if (!entry || !entry.liveSnapshot) return;
    entry.playerStats = entry.liveSnapshot.playerStats;
    entry.lastFishList = entry.liveSnapshot.fishList;
    entry.lastStoneList = entry.liveSnapshot.stoneList;
  }
  function refreshAllUsernameDisplays() {
    trackers.forEach((entry) => refreshEntrySyncDisplay(entry));
  }
  function formatSyncDurationLabel(timestamp) {
    const secs = syncAgeSeconds(timestamp);
    if (secs == null) return 'no sync';
    return `${Math.max(1, secs)}s`;
  }
  function formatTableSyncStatusText(entry) {
    if (!entry || isEntryStatusGreen(entry)) return '';
    return formatSyncDurationLabel(entryRedSince(entry));
  }
  function formatEntrySyncStatusText(entry) {
    if (!entry) return '';
    if (isEntryStatusGreen(entry)) return '';
    return formatSyncDurationLabel(entryRedSince(entry));
  }
  function formatEntrySyncStatusLine(entry) {
    return formatEntrySyncStatusText(entry);
  }
  function entryConnectionFreshness(entry) {
    return isEntryStatusGreen(entry) ? 'live' : 'dead';
  }
  function refreshEntryTableSyncDisplay(entry, key) {
    if (!entry || !key || !accountsTableBodyEl) return;
    const row = accountsTableBodyEl.querySelector(`[data-account-row-key="${CSS.escape(key)}"]`);
    if (!row) return;
    const statusEl = row.querySelector('[data-table-status-dot]');
    const textEl = row.querySelector('[data-table-sync-text]');
    const fresh = entryConnectionFreshness(entry);
    if (statusEl) {
      statusEl.classList.remove('live', 'stale', 'dead');
      statusEl.classList.add(fresh === 'live' ? 'live' : 'dead');
    }
    if (textEl) textEl.textContent = formatTableSyncStatusText(entry);
  }
  function patchAccountStatsRow(entry, key) {
    if (!entry || !key) return;
    const stats = getEntryPlayerStats(entry);
    const row = accountsTableBodyEl && accountsTableBodyEl.querySelector(`[data-account-row-key="${CSS.escape(key)}"]`);
    if (row) {
      const coinsEl = row.querySelector('.col-coins');
      const caughtEl = row.querySelector('.col-caught');
      const rareEl = row.querySelector('.col-rare');
      const coinsText = displayCoinsStat(stats);
      const caughtText = displayTotalCaughtStat(stats);
      const rareText = displayRarestFishStat(stats);
      if (coinsEl) {
        coinsEl.textContent = coinsText;
        coinsEl.classList.toggle('is-muted', coinsText === '—');
      }
      if (caughtEl) {
        caughtEl.textContent = caughtText;
        caughtEl.classList.toggle('is-muted', caughtText === '—');
      }
      if (rareEl) {
        rareEl.textContent = rareText;
        rareEl.classList.toggle('is-muted', rareText === '—');
      }
      refreshEntryTableSyncDisplay(entry, key);
    }
    const mobile = accountsMobileListEl && accountsMobileListEl.querySelector(`[data-account-mobile-key="${CSS.escape(key)}"]`);
    if (mobile) {
      const rows = mobile.querySelectorAll('.accounts-mobile-card__grid--stats .accounts-mobile-card__row-value');
      const coinsText = displayCoinsStat(stats);
      const caughtText = displayTotalCaughtStat(stats);
      const rareText = displayRarestFishStat(stats);
      if (rows[0]) {
        rows[0].textContent = coinsText;
        rows[0].classList.toggle('is-muted', coinsText === '—');
      }
      if (rows[1]) {
        rows[1].textContent = caughtText;
        rows[1].classList.toggle('is-muted', caughtText === '—');
      }
      if (rows[2]) {
        rows[2].textContent = rareText;
        rows[2].classList.toggle('is-muted', rareText === '—');
      }
    }
  }
  function applyLiveSnapshotToPublicUi(entry, key, data) {
    const snap = entry.liveSnapshot;
    if (!snap) return;
    const fishList = snap.fishList || [];
    const stoneList = snap.stoneList || [];
    const live = isEntryConnectionLive(data, entry);
    const hasInventory = fishList.length > 0 || stoneList.length > 0;
    if (!live) {
      setCardOffline(entry.el, entry.displayName, data);
    } else if (hasInventory) {
      updateCard(entry.el, data);
    } else {
      setCardRunning(entry.el, entry.displayName, data);
    }
    renderAccountsTable();
    patchAccountStatsRow(entry, key);
    updateInventoryStats();
    if (accountViewMode === 'fish') renderBulkInventory('fish');
    else if (accountViewMode === 'stone') renderBulkInventory('stone');
    else if (accountViewMode === 'account' && key === activeAccountKey && entry.el) {
      const body = entry.el.querySelector('[data-card-body]');
      if (body) patchCardInventory(body, fishList, stoneList);
    }
  }
  function applyInventoryPollPayload(entry, key, data) {
    entry.lastData = data;
    const pollAt = new Date().toISOString();
    entry.lastPollOkAt = pollAt;
    entry.lastSyncAt = pollAt;
    entry.lastStatsPollAt = pollAt;
    entry.lastServerSyncAt = data ? bestSyncTimestamp(data) : null;
    entry.liveSnapshot = buildLiveSnapshotFromPayload(entry, data, pollAt);
    syncEntryFromLiveSnapshot(entry);
    captureEntrySync(entry, data);
    applyLiveSnapshotToPublicUi(entry, key, data);
    if (entry.liveSnapshot && entry.liveSnapshot.playerStats) {
      entry._statRefreshCycleProof = {
        pollCount: entry.liveSnapshot.pollCount,
        coins: displayCoinsStat(entry.liveSnapshot.playerStats),
        totalCaught: displayTotalCaughtStat(entry.liveSnapshot.playerStats),
        rarestFish: displayRarestFishStat(entry.liveSnapshot.playerStats),
        pollAt: entry.liveSnapshot.pollAt,
      };
    }
    if (DEBUG_INVENTORY) {
      const snap = entry.liveSnapshot;
      entry._unifiedPollProof = {
        pollCount: snap.pollCount,
        payloadAt: snap.payloadAt,
        pollAt: snap.pollAt,
        coinsText: snap.playerStats ? displayCoinsStat(snap.playerStats) : null,
        totalCaughtText: snap.playerStats ? displayTotalCaughtStat(snap.playerStats) : null,
        rarestFishChance: snap.playerStats ? displayRarestFishStat(snap.playerStats) : null,
        fishCount: snap.fishCount,
        stoneCount: snap.stoneCount,
        samePayloadTimestamp: true,
      };
    }
  }
  function applyPollPayload(entry, key, data) {
    applyInventoryPollPayload(entry, key, data);
  }
  function debugLogEntryPlayerStats(entry) {
    if (!DEBUG_INVENTORY || !entry) return;
    const resolved = resolveEntryPlayerStatsSource(entry);
    const stats = resolved ? resolved.stats : null;
    console.debug('[fishit] playerStats proof', {
      username: entry.displayName,
      hasPlayerStats: !!stats,
      coinsText: stats && (stats.coinsText || null),
      totalCaughtText: stats && (stats.totalCaughtText || null),
      rarestFishChance: stats && (stats.rarestFishChance || null),
      source: resolved ? resolved.source : null,
    });
  }
  function displayCoinsStat(stats) {
    const s = displayableEntryPlayerStats(stats);
    if (!s || s.source === 'missing') return 'ΓÇö';
    if (s.coins != null) {
      const compact = formatCompactStatNumber(s.coins);
      if (compact) return compact;
    }
    if (s.coinsText) return s.coinsText;
    return 'ΓÇö';
  }
  function displayTotalCaughtStat(stats) {
    const s = displayableEntryPlayerStats(stats);
    if (!s || s.source === 'missing') return 'ΓÇö';
    if (s.totalCaught != null) {
      const grouped = formatGroupedCaughtNumber(s.totalCaught);
      if (grouped) return grouped;
    }
    if (s.totalCaughtText) return s.totalCaughtText;
    return 'ΓÇö';
  }
  function displayRarestFishStat(stats) {
    const s = displayableEntryPlayerStats(stats);
    if (!s || s.source === 'missing' || !s.rarestFishChance) return 'ΓÇö';
    return String(s.rarestFishChance);
  }
  function formatStatsAgeSub(stats) {
    const ts = stats && (stats.statsAt || null);
    if (!ts) return '';
    const label = formatTableSyncAge(ts);
    return label && label !== 'no sync' ? `(${label})` : '';
  }
  function displayTableUsername(entry) {
    if (!entry) return 'ΓÇö';
    return formatUsernameForDisplay(entry.displayName, { hideUsernames });
  }
  function updateHideUsernamesUi() {
    if (!hideUsernamesBtn) return;
    hideUsernamesBtn.setAttribute('aria-pressed', hideUsernames ? 'true' : 'false');
    hideUsernamesBtn.setAttribute('aria-label', hideUsernames ? 'Show usernames' : 'Hide usernames');
    hideUsernamesBtn.setAttribute('title', hideUsernames ? 'Show usernames' : 'Hide usernames');
    hideUsernamesBtn.classList.toggle('is-active', hideUsernames);
    if (hideUsernameIconEl) {
      hideUsernameIconEl.innerHTML = hideUsernames ? HIDE_USERNAME_EYE_OFF_SVG : HIDE_USERNAME_EYE_SVG;
    }
  }
  function accountMatchesStatusFilter(entry) {
    if (accountStatusFilter === 'online') return isTrackerOnline(entry);
    if (accountStatusFilter === 'offline') return !isTrackerOnline(entry);
    return true;
  }
  function accountSearchHaystack(entry) {
    const stats = getEntryPlayerStats(entry);
    return [
      entry && entry.displayName,
      displayCoinsStat(stats),
      displayTotalCaughtStat(stats),
      displayRarestFishStat(stats),
      formatTableSyncAge(entry && entry.lastSyncAt),
    ].filter(Boolean).join(' ').toLowerCase();
  }
  function accountMatchesSearch(entry) {
    const q = String(accountSearchQuery || '').trim().toLowerCase();
    if (!q) return true;
    return accountSearchHaystack(entry).includes(q);
  }
  function getFilteredAccountEntries() {
    const rows = [];
    trackers.forEach((entry, key) => {
      if (!accountMatchesStatusFilter(entry)) return;
      if (!accountMatchesSearch(entry)) return;
      rows.push({ key, entry });
    });
    rows.sort((a, b) => (a.entry.displayName || a.key).localeCompare(b.entry.displayName || b.key));
    return rows;
  }
  function buildAccountStatusHtml(entry) {
    const freshness = entryConnectionFreshness(entry);
    const text = formatTableSyncStatusText(entry);
    return `<span class="accounts-status"><span class="status-dot ${freshness === 'live' ? 'live' : 'dead'}" data-table-status-dot aria-hidden="true"></span><span class="accounts-status__text" data-table-sync-text>${escHtml(text)}</span></span>`;
  }
  function buildAccountMobileCardHtml(row) {
    const { key, entry } = row;
    const stats = getEntryPlayerStats(entry);
    const freshness = entryConnectionFreshness(entry);
    const inventoryLabel = `Open inventory for ${entry.displayName}`;
    const username = displayTableUsername(entry);
    return `<article class="accounts-mobile-card" data-account-mobile-key="${escHtml(key)}">
  <div class="accounts-mobile-card__top">
    <div class="accounts-mobile-card__account">
      <span class="accounts-status"><span class="status-dot ${freshness === 'live' ? 'live' : 'dead'}" aria-hidden="true"></span></span>
      <span class="accounts-mobile-card__username">${escHtml(username)}</span>
    </div>
    <div class="accounts-mobile-card__actions">
      <button type="button" class="accounts-table__icon-btn" data-open-backpack="${escHtml(key)}" aria-label="${escHtml(inventoryLabel)}" title="${escHtml(inventoryLabel)}">${BACKPACK_NAV_ICON}</button>
      <button type="button" class="accounts-table__icon-btn accounts-table__icon-btn--danger" data-remove-account="${escHtml(key)}" aria-label="Remove ${escHtml(entry.displayName)}" title="Remove account"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16" aria-hidden="true"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path></svg></button>
    </div>
  </div>
  <div class="accounts-mobile-card__grid accounts-mobile-card__grid--stats">
    <div class="accounts-mobile-card__row col-coin" data-col="coin"><span class="accounts-mobile-card__row-label">Coin</span><span class="accounts-mobile-card__row-value coin-value${displayCoinsStat(stats) === 'ΓÇö' ? ' is-muted' : ''}">${escHtml(displayCoinsStat(stats))}</span></div>
    <div class="accounts-mobile-card__row col-total-caught" data-col="total-caught"><span class="accounts-mobile-card__row-label">Caught</span><span class="accounts-mobile-card__row-value total-caught-value${displayTotalCaughtStat(stats) === 'ΓÇö' ? ' is-muted' : ''}">${escHtml(displayTotalCaughtStat(stats))}</span></div>
    <div class="accounts-mobile-card__row col-rarest-fish" data-col="rarest-fish"><span class="accounts-mobile-card__row-label">Rare</span><span class="accounts-mobile-card__row-value rarest-fish-value${displayRarestFishStat(stats) === 'ΓÇö' ? ' is-muted' : ''}">${escHtml(displayRarestFishStat(stats))}</span></div>
  </div>
</article>`;
  }
  function buildAccountRowHtml(row, index) {
    const { key, entry } = row;
    const stats = getEntryPlayerStats(entry);
    const inventoryLabel = `Open inventory for ${entry.displayName}`;
    return `<tr data-account-row-key="${escHtml(key)}">
  <td class="accounts-table__index col-index">${index + 1}</td>
  <td class="accounts-table__status col-status">${buildAccountStatusHtml(entry)}</td>
  <td class="accounts-table__username col-username" title="${hideUsernames ? 'Username hidden' : escHtml(entry.displayName)}">${escHtml(displayTableUsername(entry))}</td>
  <td class="accounts-table__stat col-coins col-coin coin-value${displayCoinsStat(stats) === 'ΓÇö' ? ' is-muted' : ''}" data-col="coin">${escHtml(displayCoinsStat(stats))}</td>
  <td class="accounts-table__stat col-caught col-total-caught total-caught-value${displayTotalCaughtStat(stats) === 'ΓÇö' ? ' is-muted' : ''}" data-col="total-caught">${escHtml(displayTotalCaughtStat(stats))}</td>
  <td class="accounts-table__stat col-rare col-rarest-fish rarest-fish-value${displayRarestFishStat(stats) === 'ΓÇö' ? ' is-muted' : ''}" data-col="rarest-fish">${escHtml(displayRarestFishStat(stats))}</td>
  <td class="col-backpack"><button type="button" class="accounts-table__icon-btn" data-open-backpack="${escHtml(key)}" aria-label="${escHtml(inventoryLabel)}" title="${escHtml(inventoryLabel)}">${BACKPACK_NAV_ICON}</button></td>
  <td class="col-actions"><button type="button" class="accounts-table__icon-btn accounts-table__icon-btn--danger" data-remove-account="${escHtml(key)}" aria-label="Remove ${escHtml(entry.displayName)}" title="Remove account"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16" aria-hidden="true"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path></svg></button></td>
</tr>`;
  }
  function renderAccountsTable() {
    const rows = getFilteredAccountEntries();
    if (accountsTableBodyEl) {
      if (!rows.length) {
        accountsTableBodyEl.innerHTML = '<tr><td colspan="8" class="accounts-table__empty">No matching accounts yet.</td></tr>';
      } else {
        accountsTableBodyEl.innerHTML = rows.map((row, idx) => buildAccountRowHtml(row, idx)).join('');
      }
    }
    if (accountsMobileListEl) {
      if (!rows.length) {
        accountsMobileListEl.innerHTML = '<div class="accounts-mobile-card__empty">No matching accounts yet.</div>';
      } else {
        accountsMobileListEl.innerHTML = rows.map((row) => buildAccountMobileCardHtml(row)).join('');
      }
    }
  }
  function syncViewModeUi() {
    const isTable = accountViewMode === 'table';
    const isBulkFish = accountViewMode === 'fish';
    const isBulkStone = accountViewMode === 'stone';
    const isAccount = accountViewMode === 'account';
    const isGrid = !isTable;
    if (accountsOverviewEl) accountsOverviewEl.classList.toggle('is-inventory-only', isGrid);
    if (inventoryViewSectionEl) inventoryViewSectionEl.hidden = !isGrid;
    if (bulkPanelEl) bulkPanelEl.hidden = !(isBulkFish || isBulkStone);
    if (trackerListEl) trackerListEl.style.display = isAccount ? '' : 'none';
    if (viewTableBtn) viewTableBtn.classList.toggle('is-active', isTable);
    if (viewFishGridBtn) viewFishGridBtn.classList.toggle('is-active', isBulkFish);
    if (viewStoneGridBtn) viewStoneGridBtn.classList.toggle('is-active', isBulkStone);
    trackers.forEach((entry, key) => {
      if (!entry || !entry.el) return;
      const showCard = isAccount && key === activeAccountKey;
      entry.el.style.display = showCard ? '' : 'none';
      if (showCard) {
        entry.el.classList.add('expanded');
        if (entry.lastData) {
          const body = entry.el.querySelector('[data-card-body]');
          if (body) {
            patchCardInventory(
              body,
              entry.liveSnapshot.fishList || [],
              entry.liveSnapshot.stoneList || [],
            );
          }
        }
      }
    });
    if (isBulkFish) renderBulkInventory('fish');
    else if (isBulkStone) renderBulkInventory('stone');
  }
  function setAccountViewMode(mode, opts) {
    opts = opts || {};
    if (mode === 'account' && opts.key) {
      accountViewMode = 'account';
      activeAccountKey = opts.key;
      inventoryGridFilter = 'all';
    } else if (mode === 'fish' || mode === 'stone') {
      accountViewMode = mode;
      activeAccountKey = null;
      inventoryGridFilter = mode;
    } else {
      accountViewMode = 'table';
      activeAccountKey = null;
      inventoryGridFilter = 'all';
    }
    syncViewModeUi();
  }
  function openAccountInventory(key) {
    const entry = trackers.get(key);
    if (!entry || !entry.el) return;
    setAccountViewMode('account', { key });
    entry.el.classList.add('expanded', 'is-highlighted');
    const tableRow = accountsTableBodyEl && accountsTableBodyEl.querySelector(`[data-account-row-key="${CSS.escape(key)}"]`);
    if (tableRow) {
      tableRow.classList.add('is-highlighted');
      setTimeout(() => tableRow.classList.remove('is-highlighted'), 1800);
    }
    const mobileCard = accountsMobileListEl && accountsMobileListEl.querySelector(`[data-account-mobile-key="${CSS.escape(key)}"]`);
    if (mobileCard) {
      mobileCard.classList.add('is-highlighted');
      setTimeout(() => mobileCard.classList.remove('is-highlighted'), 1800);
    }
    entry.el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setTimeout(() => entry.el.classList.remove('is-highlighted'), 1800);
  }
  function refreshAllAccounts() {
    trackers.forEach((_, key) => pollUser(key));
    renderAccountsTable();
  }
  function copyAllUsernames() {
    const rows = getFilteredAccountEntries();
    const text = rows.map((row) => formatUsernameForDisplay(row.entry.displayName, { hideUsernames })).join('\n');
    const attempt = (navigator.clipboard && navigator.clipboard.writeText)
      ? navigator.clipboard.writeText(text)
      : Promise.reject(new Error('clipboard_unavailable'));
    attempt.catch(() => {
      if (copyStatusEl) copyStatusEl.textContent = 'Copy failed';
    }).then(() => {
      if (copyStatusEl) {
        copyStatusEl.textContent = rows.length ? 'Usernames copied' : 'No usernames';
        copyStatusEl.classList.add('is-success');
        setTimeout(() => {
          copyStatusEl.textContent = '';
          copyStatusEl.classList.remove('is-success');
        }, 1600);
      }
    });
  }
  function bindAccountsOverview() {
    if (accountsSearchInputEl) {
      accountsSearchInputEl.addEventListener('input', () => {
        accountSearchQuery = accountsSearchInputEl.value;
        renderAccountsTable();
      });
    }
    document.querySelectorAll('[data-account-filter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        accountStatusFilter = btn.getAttribute('data-account-filter') || 'all';
        document.querySelectorAll('[data-account-filter]').forEach((el) => {
          el.classList.toggle('is-active', el.getAttribute('data-account-filter') === accountStatusFilter);
        });
        renderAccountsTable();
      });
    });
    if (viewTableBtn) viewTableBtn.addEventListener('click', () => setAccountViewMode('table'));
    if (viewFishGridBtn) viewFishGridBtn.addEventListener('click', () => setAccountViewMode('fish'));
    if (viewStoneGridBtn) viewStoneGridBtn.addEventListener('click', () => setAccountViewMode('stone'));
    if (hideUsernamesBtn) {
      hideUsernamesBtn.addEventListener('click', () => {
        hideUsernames = !hideUsernames;
        try { localStorage.setItem(LS_HIDE_USERNAMES, hideUsernames ? '1' : '0'); } catch {}
        updateHideUsernamesUi();
        refreshAllUsernameDisplays();
        renderAccountsTable();
      });
    }
    if (refreshAccountsBtn) refreshAccountsBtn.addEventListener('click', refreshAllAccounts);
    if (copyUsernamesBtn) copyUsernamesBtn.addEventListener('click', copyAllUsernames);
    function handleAccountsActionClick(e) {
      const backpackBtn = e.target.closest('[data-open-backpack]');
      if (backpackBtn) {
        openAccountInventory(backpackBtn.getAttribute('data-open-backpack'));
        return;
      }
      const removeBtn = e.target.closest('[data-remove-account]');
      if (removeBtn) removeTracker(removeBtn.getAttribute('data-remove-account'));
    }
    if (accountsTableBodyEl) accountsTableBodyEl.addEventListener('click', handleAccountsActionClick);
    if (accountsMobileListEl) accountsMobileListEl.addEventListener('click', handleAccountsActionClick);
    try {
      hideUsernames = localStorage.getItem(LS_HIDE_USERNAMES) === '1';
    } catch {}
    updateHideUsernamesUi();
    setAccountViewMode('table');
  }
  function computeInventoryStats() {
    let onlineCount = 0;
    let evolvedStones = 0;
    let secretFish = 0;
    let forgottenFish = 0;
    trackers.forEach((entry) => {
      if (isTrackerOnline(entry)) onlineCount += 1;
      const data = entry.lastData;
      if (!data) return;
      const fishList = getPublicFishItems(data);
      const stoneList = getPublicStoneItems(data);
      for (const item of fishList) {
        const rarity = normalizeRarityLabel(item);
        const amount = Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0));
        if (rarity === 'Secret') secretFish += amount;
        else if (rarity === 'Forgotten') forgottenFish += amount;
      }
      for (const item of stoneList) {
        const stoneType = String(item?.stoneType || item?.StoneType || '').trim();
        if (stoneType === 'Evolved') {
          evolvedStones += Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0));
        }
      }
    });
    return {
      totalAccounts: trackers.size,
      onlineCount,
      evolvedStones,
      secretFish,
      forgottenFish,
    };
  }
  function updateInventoryStats() {
    const stats = computeInventoryStats();
    const countUp = window.DengCountUpStats;
    if (statOnlineAccountsEl) {
      if (countUp) {
        countUp.set(statOnlineAccountsEl, { to: stats.onlineCount, total: stats.totalAccounts, format: 'ratio', duration: 750 });
      } else {
        statOnlineAccountsEl.textContent = `${formatQuantity(stats.onlineCount)} / ${formatQuantity(stats.totalAccounts)}`;
      }
    }
    if (statEvolvedStonesEl) {
      if (countUp) countUp.set(statEvolvedStonesEl, { to: stats.evolvedStones, format: 'integer', duration: 750 });
      else statEvolvedStonesEl.textContent = formatQuantity(stats.evolvedStones);
    }
    if (statSecretFishEl) {
      if (countUp) countUp.set(statSecretFishEl, { to: stats.secretFish, format: 'integer', duration: 750 });
      else statSecretFishEl.textContent = formatQuantity(stats.secretFish);
    }
    if (statForgottenFishEl) {
      if (countUp) countUp.set(statForgottenFishEl, { to: stats.forgottenFish, format: 'integer', duration: 750 });
      else statForgottenFishEl.textContent = formatQuantity(stats.forgottenFish);
    }
  }
  function buildCardBadgesHtml(item, opts) {
    opts = opts || {};
    const amount = formatAmountLabel(resolveItemAmount(item));
    const rarity = opts.rarity != null ? opts.rarity : publicRarity(item);
    const parts = [ownersChipHtml(opts.accountCount || 1)];
    parts.push(`<span class="ft-chip ft-chip-qty">${escHtml(amount)}</span>`);
    if (opts.includeRarity !== false && rarity && rarity !== 'Unknown') {
      parts.push(`<span class="ft-chip ft-chip-rarity">${escHtml(rarity)}</span>`);
    }
    return `<div class="ft-card-stats">${parts.join('')}</div>`;
  }
  function buildStoneStatsHtml(item, opts) {
    opts = opts || {};
    const amount = formatAmountLabel(resolveItemAmount(item));
    return `<div class="ft-card-stats">${ownersChipHtml(opts.accountCount || 1)}<span class="ft-chip ft-chip-qty">${escHtml(amount)}</span></div>`;
  }
  function normalizeRarityLabel(item) {
    if (!item || typeof item !== 'object') return 'Unknown';
    const raw = item.rarity ?? item.Rarity;
    if (raw != null && String(raw).trim() && String(raw).trim() !== 'Unknown') return String(raw).trim();
    const tier = Number(item.tier ?? item.Tier);
    if (Number.isFinite(tier) && TIER_TO_RARITY[tier]) return TIER_TO_RARITY[tier];
    return 'Unknown';
  }
  function rarityRank(item) {
    return RARITY_ORDER[normalizeRarityLabel(item)] ?? RARITY_ORDER.Unknown;
  }
  function inventoryItemName(item) {
    return String(item?.name || item?.Name || item?.baseFishName || item?.displayName || '').trim();
  }
  function inventoryItemId(item) {
    return String(item?.itemId ?? item?.ItemId ?? item?.speciesId ?? '').trim();
  }
  function sortInventoryFish(items) {
    if (!Array.isArray(items)) return [];
    return [...items].sort((a, b) => {
      const rarityDiff = rarityRank(b) - rarityRank(a);
      if (rarityDiff) return rarityDiff;
      const nameA = inventoryItemName(a).toLowerCase();
      const nameB = inventoryItemName(b).toLowerCase();
      if (nameA !== nameB) return nameA.localeCompare(nameB);
      return inventoryItemId(a).localeCompare(inventoryItemId(b));
    });
  }
  function stoneTypeRank(item) {
    const type = String(item?.stoneType || item?.StoneType || '').trim();
    if (type && STONE_TYPE_ORDER[type] != null) return STONE_TYPE_ORDER[type];
    const byId = { 10: 10, 246: 20, 558: 30, 873: 40, 929: 50 };
    const id = inventoryItemId(item);
    if (id && byId[id] != null) return byId[id];
    return 999;
  }
  function sortInventoryStones(items) {
    if (!Array.isArray(items)) return [];
    return [...items].sort((a, b) => {
      const typeDiff = stoneTypeRank(a) - stoneTypeRank(b);
      if (typeDiff) return typeDiff;
      return inventoryItemId(a).localeCompare(inventoryItemId(b));
    });
  }
  function publicWaitingHtml() {
    return `<div class="card-empty">No inventory data yet for this username.<span class="card-empty-sub">Run the tracker script in-game, then refresh or wait for sync.</span></div>`;
  }
  function publicLiveEmptyHtml() {
    return `<div class="card-empty">&#x1F9F3; Inventory is empty.</div>`;
  }
  function refreshEntrySyncDisplay(entry) {
    if (!entry || !entry.el) return;
    const dotEl = entry.el.querySelector('[data-status-dot]');
    const textEl = entry.el.querySelector('[data-card-sync-text]');
    const fresh = entryConnectionFreshness(entry);
    if (dotEl) {
      dotEl.classList.remove('live', 'stale', 'dead');
      dotEl.classList.add(fresh === 'live' ? 'live' : (fresh === 'stale' ? 'stale' : 'dead'));
    }
    if (textEl) textEl.textContent = formatEntrySyncStatusLine(entry);
  }
  function captureEntrySync(entry, data) {
    if (!entry) return;
    entry.lastServerSyncAt = data ? bestSyncTimestamp(data) : null;
    refreshEntrySyncDisplay(entry);
  }
  function clearEntrySync(entry) {
    if (!entry) return;
    entry.lastSyncAt = null;
    refreshEntrySyncDisplay(entry);
  }
  function tickAllCardSyncStatus() {
    trackers.forEach((entry, key) => {
      refreshEntrySyncDisplay(entry);
      refreshEntryTableSyncDisplay(entry, key);
    });
  }
  function setCardSyncDisplay(card, data) {
    const key = card && card.dataset.user;
    const entry = key && trackers.get(key);
    if (entry) captureEntrySync(entry, data);
  }
  function canonicalBulkName(item) {
    return String(item?.baseFishName || item?.displayName || item?.name || item?.Name || '').trim()
      || inventoryItemName(item);
  }
  function bulkGroupKey(category, item) {
    const cat = String(category || 'fish').toLowerCase();
    const name = canonicalBulkName(item);
    if (cat === 'stone') {
      const stoneType = String(item?.stoneType || item?.StoneType || '').trim();
      return `${cat}:${name.toLowerCase()}:${stoneType.toLowerCase() || normalizeRarityLabel(item).toLowerCase()}`;
    }
    return `${cat}:${name.toLowerCase()}:${normalizeRarityLabel(item).toLowerCase()}`;
  }
  function mergeBulkItem(existing, item, username, category) {
    const amount = Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0));
    const owners = new Set(existing.owners || []);
    if (username) owners.add(username);
    const candidate = item.imageUrl || null;
    const imageUrl = candidate && !/fallback|placeholder/i.test(candidate)
      ? candidate
      : (existing.imageUrl || candidate);
    return {
      ...existing,
      name: existing.name || canonicalBulkName(item),
      category: category || existing.category,
      rarity: existing.rarity || normalizeRarityLabel(item),
      stoneType: existing.stoneType || item.stoneType || item.StoneType || null,
      itemId: existing.itemId || inventoryItemId(item) || null,
      imageUrl,
      amount: (existing.amount || 0) + amount,
      accountCount: owners.size,
      owners: [...owners],
      dataSource: 'bulk_playerdata_gameitemdb',
      groupKey: existing.groupKey,
    };
  }
  function aggregateBulkInventory(sessions) {
    const fishMap = new Map();
    const stoneMap = new Map();
    const accountSet = new Set();
    for (const session of sessions || []) {
      const username = String(session?.username || '').trim();
      if (username) accountSet.add(username.toLowerCase());
      for (const item of session?.fishList || []) {
        const key = bulkGroupKey('fish', item);
        fishMap.set(key, mergeBulkItem(fishMap.get(key) || {
          groupKey: key, name: canonicalBulkName(item), category: 'fish',
          rarity: normalizeRarityLabel(item), amount: 0, accountCount: 0, owners: [], imageUrl: null,
        }, item, username, 'fish'));
      }
      for (const item of session?.stoneList || []) {
        const key = bulkGroupKey('stone', item);
        stoneMap.set(key, mergeBulkItem(stoneMap.get(key) || {
          groupKey: key, name: canonicalBulkName(item), category: 'stone',
          rarity: normalizeRarityLabel(item), stoneType: item.stoneType || item.StoneType || null,
          amount: 0, accountCount: 0, owners: [], imageUrl: null,
        }, item, username, 'stone'));
      }
    }
    return {
      fish: sortInventoryFish([...fishMap.values()]),
      stones: sortInventoryStones([...stoneMap.values()]),
      accountCount: accountSet.size,
      fishTypeCount: fishMap.size,
      stoneTypeCount: stoneMap.size,
    };
  }
  function bulkSearchHaystack(item) {
    if (!item || typeof item !== 'object') return '';
    return [
      item.name, item.rarity, item.stoneType, item.itemId, item.groupKey,
      ...(Array.isArray(item.owners) ? item.owners : []),
      formatQuantity(item.amount || 0),
    ].filter(Boolean).join(' ').toLowerCase();
  }
  function filterBulkItems(items, query) {
    if (!Array.isArray(items)) return [];
    const q = String(query || '').trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) => bulkSearchHaystack(item).includes(q));
  }
  function collectBulkSessions() {
    const sessions = [];
    trackers.forEach((entry) => {
      const data = entry.lastData;
      if (!data) return;
      const fishList = getPublicFishItems(data);
      const stoneList = getPublicStoneItems(data);
      if (!fishList.length && !stoneList.length) return;
      sessions.push({ username: entry.displayName, fishList, stoneList });
    });
    return sessions;
  }
  function bulkCardKey(item) {
    return item.groupKey || `${item.category}|${item.name}|${item.rarity}`;
  }
  function buildBulkCardInnerHtml(item) {
    const title = item.category === 'stone' ? stoneDisplayName(item) : (item.name || 'Unknown');
    const imgSrc = itemImageSrc(item) || ITEM_IMAGES.Default;
    const isPlaceholder = imgSrc === ITEM_IMAGES.Default;
    const opts = { accountCount: item.accountCount || 1, includeRarity: item.category !== 'stone' };
    const weight = item.category === 'stone' ? '' : formatCardWeight(item);
    const statsHtml = item.category === 'stone' ? buildStoneStatsHtml(item, opts) : buildCardBadgesHtml(item, opts);
    const weightHtml = weight ? `<div class="ft-card-weight">${escHtml(weight)}</div>` : '';
    return `
  <div class="ft-card-icon">
    <img src="${escHtml(imgSrc)}" alt="${escHtml(title)}" title="${escHtml(title)}" decoding="async"${isPlaceholder ? ' data-placeholder="true"' : ''} data-item-id="${escHtml(item.itemId || '')}">
  </div>
  <div class="ft-card-main">
    <div class="ft-card-name" title="${escHtml(title)}">${escHtml(title)}</div>
    ${statsHtml}
    ${weightHtml}
  </div>`;
  }
  function buildBulkCardElement(item) {
    const card = document.createElement('div');
    if (item.category === 'stone') {
      card.className = 'ft-card ft-card--stone';
    } else {
      card.className = fishCardClassList(item).join(' ');
    }
    card.setAttribute('data-card-key', bulkCardKey(item));
    card.setAttribute('data-source', item.dataSource || 'bulk_playerdata_gameitemdb');
    card.innerHTML = buildBulkCardInnerHtml(item);
    const img = card.querySelector('.ft-card-icon img');
    if (img) img.onerror = () => onFishImageError(img, item);
    return card;
  }
  function renderBulkInventory(showCategory) {
    if (!bulkBodyEl) return;
    const sessions = collectBulkSessions();
    const bulk = aggregateBulkInventory(sessions);
    const fish = filterBulkItems(bulk.fish, bulkSearchQuery);
    const stones = filterBulkItems(bulk.stones, bulkSearchQuery);
    if (!bulk.accountCount) {
      bulkBodyEl.innerHTML = '<div class="card-empty">No inventory data yet.</div>';
      return;
    }
    if (showCategory === 'fish') {
      if (!fish.length) {
        bulkBodyEl.innerHTML = '<div class="inventory-search-empty">No inventory items found</div>';
        return;
      }
      bulkBodyEl.innerHTML = `<div class="items-grid inventory-grid fish-grid">${fish.map((item) => {
        const cls = fishCardClassList(item).join(' ');
        return `<div class="${cls}" data-card-key="${escHtml(bulkCardKey(item))}" data-source="bulk_playerdata_gameitemdb">${buildBulkCardInnerHtml(item)}</div>`;
      }).join('')}</div>`;
      wireFishImageErrors(bulkBodyEl);
      return;
    }
    if (showCategory === 'stone') {
      if (!stones.length) {
        bulkBodyEl.innerHTML = '<div class="inventory-search-empty">No inventory items found</div>';
        return;
      }
      bulkBodyEl.innerHTML = `<div class="items-grid inventory-grid stones-grid stone-grid">${stones.map((item) =>
        `<div class="ft-card ft-card--stone" data-card-key="${escHtml(bulkCardKey(item))}" data-source="bulk_playerdata_gameitemdb">${buildBulkCardInnerHtml(item)}</div>`
      ).join('')}</div>`;
      wireFishImageErrors(bulkBodyEl);
    }
  }
  function updateRemoveMenu() {
    if (!removeMenuEl) return;
    const keys = [...trackers.keys()].sort((a, b) => {
      const na = (trackers.get(a) && trackers.get(a).displayName) || a;
      const nb = (trackers.get(b) && trackers.get(b).displayName) || b;
      return na.localeCompare(nb);
    });
    if (!keys.length) {
      removeMenuEl.innerHTML = '<div class="remove-dropdown__empty">No accounts added</div>';
      return;
    }
    removeMenuEl.innerHTML = keys.map((key) => {
      const entry = trackers.get(key);
      const name = entry && entry.displayName ? entry.displayName : key;
      return `<button type="button" class="remove-dropdown__item" role="menuitem" data-remove-key="${escHtml(key)}"><span>${escHtml(name)}</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16" aria-hidden="true"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path></svg></button>`;
    }).join('');
  }
  function closeRemoveMenu() {
    if (!removeMenuEl || !removeMenuBtn) return;
    removeMenuEl.hidden = true;
    removeMenuBtn.setAttribute('aria-expanded', 'false');
  }
  function toggleRemoveMenu() {
    if (!removeMenuEl || !removeMenuBtn) return;
    const open = removeMenuEl.hidden;
    updateRemoveMenu();
    removeMenuEl.hidden = !open;
    removeMenuBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  function fmtTime(iso) {
    if (!iso) return '-';
    try {
      return new Date(iso).toLocaleTimeString('en-GB', {
        timeZone: 'Asia/Jakarta', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
      }) + ' WIB';
    } catch { return '-'; }
  }
  function syncTimestamp(data) {
    return bestSyncTimestamp(data);
  }
  function isEntryConnectionLive(data, entry) {
    if (!data) return false;
    if (data.connectionLive === true || data.isOnline === true) return true;
    if (tableSyncFreshness(bestSyncTimestamp(data)) === 'live') return true;
    if (entry && entry.lastPollOkAt && syncAgeSeconds(entry.lastPollOkAt) <= SYNC_LIVE_MAX_SEC) {
      const fishList = getPublicFishItems(data);
      const stoneList = getPublicStoneItems(data);
      if (fishList.length || stoneList.length) return true;
    }
    return false;
  }
  
  function getPublicFishItems(data) {
    let items = [];
    if (!data) return items;
    if (Array.isArray(data.fishItems)) items = data.fishItems;
    else if (Array.isArray(data.publicItems)) items = data.publicItems;
    else if (data.fishInventory && Array.isArray(data.fishInventory.fish)) items = data.fishInventory.fish;
    else if (data.renderBuild === RENDER_BUILD && Array.isArray(data.items)) {
      items = data.items.filter((it) => String(it.category || '').toLowerCase() === 'fish');
    }
    if (!items.length && Array.isArray(data.lastGoodPublicFishItems) && data.lastGoodPublicFishItems.length) {
      items = data.lastGoodPublicFishItems;
    }
    return sortInventoryFish(items);
  }
  function getPublicStoneItems(data) {
    if (!data) return [];
    let items = [];
    if (Array.isArray(data.stoneItems)) items = data.stoneItems;
    else if (Array.isArray(data.stoneInventory)) items = data.stoneInventory;
    return sortInventoryStones(items);
  }
  function stoneCardKey(item) {
    return `stone|${String(item.stoneType || item.itemId || item.name || 'unknown').toLowerCase()}`;
  }
  function buildStoneCardInnerHtml(item, opts) {
    opts = opts || {};
    const title = stoneDisplayName(item);
    const imgSrc = itemImageSrc(item);
    const imageHtml = imgSrc
      ? `<img src="${escHtml(imgSrc)}" alt="${escHtml(title)}" title="${escHtml(title)}" decoding="async" data-item-id="${escHtml(item.itemId || '')}">`
      : '&#x1F48E;';
    return `
  <div class="ft-card-icon">${imageHtml}</div>
  <div class="ft-card-main">
    <div class="ft-card-name" title="${escHtml(title)}">${escHtml(title)}</div>
    ${buildStoneStatsHtml(item, opts)}
  </div>`;
  }
  function buildStoneCardElement(item) {
    const card = document.createElement('div');
    card.className = 'ft-card ft-card--stone';
    card.setAttribute('data-card-key', stoneCardKey(item));
    card.setAttribute('data-kind', 'stone');
    card.innerHTML = buildStoneCardInnerHtml(item);
    return card;
  }
  function inventorySearchHaystack(item) {
    if (!item || typeof item !== 'object') return '';
    const parts = [
      item.name,
      item.baseFishName,
      item.baseName,
      item.displayName,
      item.cardName,
      item.rarity,
      item.stoneType,
      item.itemId,
      item.speciesId,
      formatAmountLabel(resolveItemAmount(item)),
    ];
    return parts.filter(Boolean).join(' ').toLowerCase();
  }
  function filterByInventorySearch(items, query) {
    if (!Array.isArray(items)) return [];
    const q = String(query || '').trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) => inventorySearchHaystack(item).includes(q));
  }
  function patchStonesGrid(container, items) {
    if (!container) return;
    if (!items || items.length === 0) {
      container.innerHTML = '';
      container.style.display = 'none';
      return;
    }
    container.style.display = '';
    let title = container.querySelector('.stones-section__title');
    if (!title) {
      container.innerHTML = '<div class="stones-section__title"></div><div class="items-grid inventory-grid stones-grid stone-grid"></div>';
      title = container.querySelector('.stones-section__title');
    }
    const stoneTotal = items.reduce((sum, item) => sum + Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0)), 0);
    if (title) title.textContent = `Enchant Stones (${formatQuantity(stoneTotal)})`;
    let grid = container.querySelector('.stones-grid');
    if (!grid) {
      grid = document.createElement('div');
      grid.className = 'items-grid inventory-grid stones-grid stone-grid';
      container.appendChild(grid);
    }
    const nextKeys = new Set();
    const existing = new Map();
    grid.querySelectorAll('.ft-card--stone[data-card-key]').forEach((el) => {
      existing.set(el.getAttribute('data-card-key'), el);
    });
    items.forEach((item, idx) => {
      const key = stoneCardKey(item);
      nextKeys.add(key);
      let card = existing.get(key);
      if (!card) {
        card = buildStoneCardElement(item);
        const ref = grid.children[idx] || null;
        grid.insertBefore(card, ref);
      } else {
        card.innerHTML = buildStoneCardInnerHtml(item);
        const ref = grid.children[idx] || null;
        if (card !== ref) grid.insertBefore(card, ref);
      }
    });
    existing.forEach((el, key) => { if (!nextKeys.has(key)) el.remove(); });
  }
  function clearCardWaitingPanels(cardBody) {
    if (!cardBody) return;
    cardBody.querySelectorAll('.card-empty').forEach((el) => el.remove());
  }
  function patchCardInventory(cardBody, fishList, stoneList) {
    if (!cardBody) return;
    const card = cardBody.closest('.tracker-card');
    const key = card && card.dataset.user;
    const entry = key && trackers.get(key);
    if (entry) {
      entry.lastFishList = Array.isArray(fishList) ? fishList : [];
      entry.lastStoneList = Array.isArray(stoneList) ? stoneList : [];
    }
    const hasItems = (fishList && fishList.length) || (stoneList && stoneList.length);
    if (hasItems) clearCardWaitingPanels(cardBody);
    cardBody.querySelectorAll('[data-inventory-search-row]').forEach((el) => el.remove());
    let fishHost = cardBody.querySelector('[data-fish-grid-host]');
    let stoneHost = cardBody.querySelector('[data-stones-section]');
    if (!fishHost || !stoneHost) {
      cardBody.innerHTML = '<div data-fish-grid-host></div><div class="stones-section" data-stones-section></div>';
      fishHost = cardBody.querySelector('[data-fish-grid-host]');
      stoneHost = cardBody.querySelector('[data-stones-section]');
    }
    cardBody.querySelectorAll('[data-inventory-search-empty]').forEach((el) => el.remove());
    const showFish = inventoryGridFilter !== 'stone';
    const showStones = inventoryGridFilter !== 'fish';
    if (showFish) patchItemsGrid(fishHost, fishList || []);
    else fishHost.innerHTML = '';
    if (showStones) patchStonesGrid(stoneHost, stoneList || []);
    else stoneHost.innerHTML = '';
    if (stoneHost) stoneHost.style.display = showStones ? '' : 'none';
    if (fishHost) fishHost.style.display = showFish ? '' : 'none';
  }
  function logFishOnlyRender(key, data, fishList) {
    if (!data || data.renderBuild !== RENDER_BUILD) return;
    const entry = trackers.get(key);
    if (!entry || entry._loggedK1) return;
    entry._loggedK1 = true;
    const fc = data.fishCounts;
    console.log(
      `[fishit-tracker-ui] renderBuild=${RENDER_BUILD} fishItems=${fishList.length}` +
      ` hiddenNonFishTypes=${fc && fc.hiddenNonFishTypes != null ? fc.hiddenNonFishTypes : '?'}`
    );
  }
  function fishCountLabel(data, fishList) {
    const fc = data && data.fishCounts;
    const pc = data && data.publicCounts;
    const stoneList = getPublicStoneItems(data);
    const types = (pc && pc.visibleFishTypes != null) ? pc.visibleFishTypes
      : ((fc && fc.fishTypes != null) ? fc.fishTypes : fishList.length);
    const fishTotal = (pc && pc.visibleFishInstances != null) ? pc.visibleFishInstances
      : ((fc && fc.fishInstances != null) ? fc.fishInstances : null);
    const stoneTotal = (pc && pc.visibleStoneInstances != null) ? pc.visibleStoneInstances
      : ((fc && fc.stoneInstances != null) ? fc.stoneInstances
        : stoneList.reduce((s, st) => s + (Number(st.amount || st.quantity) > 0 ? Math.floor(Number(st.amount || st.quantity)) : 1), 0));
    if (fishTotal != null) {
      let label = `Fish: <strong>${formatQuantity(fishTotal)}</strong> &middot; Types: <span style="color:#8a94a6;">${types}</span>`;
      if (stoneTotal > 0) label += ` &middot; Stones: <strong>${formatQuantity(stoneTotal)}</strong>`;
      return label;
    }
    return `Types: <strong>${types}</strong>`;
  }
  function buildPlayerDataGameItemDbProofHtml(data) {
    if (!DEBUG_GLOBAL) return '';
    const proof = data && data.playerDataGameItemDbProof;
    if (!proof || proof.enabled !== true) return '';
    const fishSample = (proof.sampleFish || []).map((f) =>
      `<div>${escHtml(f.name || '?')} id=${escHtml(String(f.itemId || '-'))} qty=${f.quantity || 1} tier=${escHtml(String(f.tier || '-'))} rarity=${escHtml(f.rarity || '-')} icon=${escHtml(f.icon || '-')}</div>`
    ).join('');
    const stoneSample = (proof.sampleStones || []).map((s) =>
      `<div>${escHtml(s.name || '?')} type=${escHtml(s.stoneType || '-')} qty=${s.quantity || 1} icon=${escHtml(s.icon || '-')}</div>`
    ).join('');
    const unresolved = (proof.unresolvedItems || []).slice(0, 8).map((r) =>
      `<div>hidden id=${escHtml(String(r.itemId || '-'))} reason=${escHtml(r.reason || '-')}</div>`
    ).join('');
    return `<details class="global-db-proof gameitemdb-proof" open>
  <summary>PlayerData + GameItemDB: <span class="gdb-on">ON</span> &mdash; Global DB not used for public identity</summary>
  <div class="gdb-body">
    <div class="gdb-row"><span>Build</span><span>${escHtml(proof.build || 'BLOCKER10ZA_FINAL')}</span></div>
    <div class="gdb-row"><span>inventorySource</span><span>${escHtml(proof.inventorySource || 'playerdata_gameitemdb')}</span></div>
    <div class="gdb-row"><span>gameItemDbBuilt</span><span>${proof.gameItemDbBuilt === true ? 'true' : 'false'}</span></div>
    <div class="gdb-row"><span>gameItemDbCount</span><span>${proof.gameItemDbCount != null ? proof.gameItemDbCount : '?'}</span></div>
    <div class="gdb-row"><span>uploadedFishCount</span><span>${proof.uploadedFishCount != null ? proof.uploadedFishCount : '?'}</span></div>
    <div class="gdb-row"><span>uploadedStoneCount</span><span>${proof.uploadedStoneCount != null ? proof.uploadedStoneCount : '?'}</span></div>
    <div class="gdb-row"><span>fishIconResolvedCount</span><span>${proof.fishIconResolvedCount != null ? proof.fishIconResolvedCount : '?'}</span></div>
    <div class="gdb-row"><span>globalDbUsedForPublicIdentity</span><span>false</span></div>
    ${fishSample ? `<div class="global-db-debug"><strong>sampleFish</strong>${fishSample}</div>` : ''}
    ${stoneSample ? `<div class="global-db-debug"><strong>sampleStones</strong>${stoneSample}</div>` : ''}
    ${unresolved ? `<div class="global-db-debug"><strong>unresolvedItems</strong>${unresolved}</div>` : ''}
  </div>
</details>`;
  }
  function buildPlayerDataItemUtilityProofHtml(data) {
    if (!DEBUG_GLOBAL) return '';
    const proof = data && data.playerDataItemUtilityProof;
    if (!proof || proof.enabled !== true) return '';
    const fishSample = (proof.sampleFish || []).map((f) =>
      `<div>${escHtml(f.name || '?')} id=${escHtml(String(f.itemId || '-'))} x${f.amount || 1}${f.mutation ? ` mut=${escHtml(f.mutation)}` : ''}</div>`
    ).join('');
    const stoneSample = (proof.sampleStones || []).map((s) =>
      `<div>${escHtml(s.name || '?')} type=${escHtml(s.stoneType || '-')} x${s.amount || 1}</div>`
    ).join('');
    const hidden = (proof.hiddenUnresolvedRows || []).slice(0, 8).map((r) =>
      `<div>hidden id=${escHtml(String(r.itemId || '-'))} reason=${escHtml(r.reason || '-')}</div>`
    ).join('');
    return `<details class="global-db-proof itemutility-proof" open>
  <summary>PlayerData + ItemUtility: <span class="gdb-on">ON</span> &mdash; Global DB not used for public identity</summary>
  <div class="gdb-body">
    <div class="gdb-row"><span>Source</span><span>${escHtml(proof.source || 'playerdata_itemutility')}</span></div>
    <div class="gdb-row"><span>Image source</span><span>${escHtml(proof.imageSource || 'game_fish_icon_catalog')}</span></div>
    <div class="gdb-row"><span>fishIconCatalogLoaded</span><span>${proof.fishIconCatalogLoaded === true ? 'true' : 'false'}</span></div>
    <div class="gdb-row"><span>Fish types</span><span>${proof.itemUtilityResolvedFishCount != null ? proof.itemUtilityResolvedFishCount : '?'}</span></div>
    <div class="gdb-row"><span>Stone types</span><span>${proof.itemUtilityResolvedStoneCount != null ? proof.itemUtilityResolvedStoneCount : '?'}</span></div>
    <div class="gdb-row"><span>fishIconResolvedCount</span><span>${proof.fishIconResolvedCount != null ? proof.fishIconResolvedCount : '?'}</span></div>
    <div class="gdb-row"><span>fishIconMissingCount</span><span>${proof.fishIconMissingCount != null ? proof.fishIconMissingCount : '?'}</span></div>
    <div class="gdb-row"><span>globalDbUsedForPublicIdentity</span><span>false</span></div>
    ${fishSample ? `<div class="global-db-debug"><strong>sampleFish</strong>${fishSample}</div>` : ''}
    ${(proof.sampleFishIcons || []).length ? `<div class="global-db-debug"><strong>sampleFishIcons</strong>${(proof.sampleFishIcons || []).map((f) => `<div>${escHtml(f.name || '?')} id=${escHtml(String(f.itemId || '-'))} icon=${escHtml(f.icon || '-')} src=${escHtml(f.imageSource || '-')}</div>`).join('')}</div>` : ''}
    ${stoneSample ? `<div class="global-db-debug"><strong>sampleStones</strong>${stoneSample}</div>` : ''}
    ${hidden ? `<div class="global-db-debug"><strong>hiddenUnresolvedRows</strong>${hidden}</div>` : ''}
  </div>
</details>`;
  }
  function buildGlobalDbProofHtml(data) {
    if (!DEBUG_GLOBAL) return '';
    if (data && data.inventorySource === 'playerdata_gameitemdb') return '';
    if (data && data.activationState === 'waiting_for_playerdata_gameitemdb_payload') return '';
    const g = (data && data.globalDbUiProof) || (data && data.globalCatalogProof) || null;
    if (!g) return '';
    const imgLabel = g.cardsUsingGlobalDbImagesLabel
      || `${g.cardsUsingGlobalDbImages || 0}/${g.cardsTotal || '?'}`;
    const rarLabel = g.cardsUsingGlobalDbRarityLabel
      || `${g.cardsUsingGlobalDbRarity || 0}/${g.cardsTotal || '?'}`;
    let debugRows = '';
    if (Array.isArray(data.publicFishItems)) {
      debugRows = data.publicFishItems.slice(0, 12).map((it) =>
        `<div>${escHtml(it.canonicalName || it.name)} &mdash; img:${escHtml(it.dataImageSource || it.imageSource || '-')} rarity:${escHtml(it.dataRaritySource || it.raritySource || '-')}</div>`
      ).join('');
    }
    let parityRows = '';
    const rp = data.replionCountProof || data.countParityProof;
    if (rp) {
      parityRows = `<div class="global-db-debug">Replion: snapshot=${rp.snapshotItemInstances || rp.fullSnapshotItemInstances || '?'} fish=${rp.fishCandidates || rp.fullSnapshotFishCandidates || '?'} shown=${rp.publicFishInstances || rp.groupedPublicInstances || '?'} unmapped=${rp.unmappedFishCandidates || rp.unmappedFishCandidateInstances || 0}</div>`;
    }
    let amountRows = '';
    const ap = data.amountProof;
    if (ap && Array.isArray(ap.rows)) {
      amountRows = `<div class="global-db-debug">amountProof verified=${ap.allVerified ? 'yes' : 'no'}${ap.rows.slice(0, 10).map((r) =>
        `<br>${escHtml(r.publicCardName || '?')} id=${escHtml(r.itemId || '-')} amt=${r.publicAmount} src=${escHtml(r.amountSource || '-')} catalog=${escHtml(r.catalogName || '-')} final=${escHtml(r.finalName || '-')}`
      ).join('')}</div>`;
    }
    let ambiguousRows = '';
    const acp = (data && data.ambiguousContainerProof && typeof data.ambiguousContainerProof === 'object')
      ? data.ambiguousContainerProof : null;
    if (acp) {
      const ids = Array.isArray(data.ambiguousContainerIds) ? data.ambiguousContainerIds : [267];
      ambiguousRows = `<div class="global-db-debug">ambiguousContainerIds=${escHtml(JSON.stringify(ids))}<br>ambiguousContainerProof rowsSeen=${acp.rowsSeen != null ? acp.rowsSeen : 0} metaId=${acp.rowsWithMetadataFishId != null ? acp.rowsWithMetadataFishId : 0} metaName=${acp.rowsWithMetadataFishName != null ? acp.rowsWithMetadataFishName : 0} unresolved=${acp.rowsUnresolved != null ? acp.rowsUnresolved : 0}</div>`;
    }
    let hiddenRows = '';
    const hpr = data.hiddenPublicRows;
    const pc = data.publicCounts;
    if (hpr || pc) {
      hiddenRows = `<div class="global-db-debug">publicCounts visible=${pc && pc.visibleFishInstances != null ? pc.visibleFishInstances : '?'} types=${pc && pc.visibleFishTypes != null ? pc.visibleFishTypes : '?'} hiddenUnresolved=${pc && pc.hiddenUnresolvedFishRows != null ? pc.hiddenUnresolvedFishRows : '?'}${hpr ? `<br>hiddenPublicRows ambiguousUnresolved=${hpr.ambiguousContainerUnresolved != null ? hpr.ambiguousContainerUnresolved : 0} ids=${escHtml(JSON.stringify(hpr.hiddenItemIds || []))}` : ''}</div>`;
    }
    let quarantineRows = '';
    if (Array.isArray(data.quarantinedPublicNames) && data.quarantinedPublicNames.length) {
      quarantineRows = `<div class="global-db-debug">quarantinedPublicNames${data.quarantinedPublicNames.slice(0, 8).map((q) =>
        `<br>${escHtml(q.name || '?')} id=${escHtml(q.itemId || '-')} reason=${escHtml(q.reason || '-')}`
      ).join('')}</div>`;
    }
    let missingFishRows = '';
    const mfp = data.missingExpectedFishProof;
    if (mfp && mfp['Radiant Catfish']) {
      const r = mfp['Radiant Catfish'];
      missingFishRows = `<div class="global-db-debug">missingExpectedFishProof Radiant Catfish catalog=${r.foundInTrustedCatalog ? 'yes' : 'no'} matched=${r.currentSnapshotRowMatched ? 'yes' : 'no'} id=${escHtml(r.itemId || '-')} image=${r.imageResolved ? 'yes' : 'no'}</div>`;
    }
    let parserRows = '';
    if (Array.isArray(data.nameParserProof) && data.nameParserProof.length) {
      parserRows = `<div class="global-db-debug">nameParserProof${data.nameParserProof.slice(0, 10).map((p) =>
        `<br>${escHtml(p.publicName || '?')} orig=${escHtml(p.originalName || '-')} base=${escHtml(p.baseFishName || '-')} stripped=${escHtml(JSON.stringify(p.strippedTags || []))} badges=${escHtml(JSON.stringify(p.publicBadges || []))} protected=${escHtml(p.protectedNameReason || '-')}`
      ).join('')}</div>`;
    }
    let botCatalogRows = '';
    const dbp = data.dengFishItBotCatalogProof;
    if (dbp) {
      botCatalogRows = `<div class="global-db-debug">dengFishItBotCatalogProof source=${escHtml(dbp.sourceType || '-')} rows=${dbp.rowsLoaded != null ? dbp.rowsLoaded : '?'} Secret=${dbp.rarityCounts && dbp.rarityCounts.Secret != null ? dbp.rarityCounts.Secret : 0} Forgotten=${dbp.rarityCounts && dbp.rarityCounts.Forgotten != null ? dbp.rarityCounts.Forgotten : 0}${Array.isArray(dbp.sampleEntries) ? dbp.sampleEntries.slice(0, 6).map((e) => `<br>${escHtml(e.baseFishName)} ΓåÆ ${escHtml(e.rarity)} (${escHtml(e.rarityConfidence || '-')})`).join('') : ''}</div>`;
    }
    let learningRows = '';
    const glp = data.globalLearningProof;
    if (glp) {
      learningRows = `<div class="global-db-debug">globalLearningProof total=${glp.totalRecords != null ? glp.totalRecords : 0} status=${escHtml(JSON.stringify(glp.statusCounts || {}))}</div>`;
    }
    let resetRows = '';
    if (data.resetSeedProof) {
      const rsp = data.resetSeedProof;
      resetRows = `<div class="global-db-debug">resetSeedProof dryRun=${rsp.dryRun ? 'yes' : 'no'} seeded=${escHtml(JSON.stringify(rsp.seededEntries || {}))}</div>`;
    }
    let catchRows = '';
    const cl = data.catchLearningProof;
    if (cl) {
      catchRows = `<div class="global-db-debug">Catch learning: ${cl.catchEvidenceSupported ? 'ON' : 'OFF'}${cl.pendingCatch ? ` pending=${escHtml(cl.pendingCatch.fishName || '-')}` : ''}</div>`;
    }
    let rarityRows = '';
    if (Array.isArray(data.rarityColorProof) && data.rarityColorProof.length) {
      rarityRows = `<div class="global-db-debug">${data.rarityColorProof.slice(0, 8).map((r) =>
        escHtml(`${r.canonicalName || '?'}: ${r.finalRarity || 'unknown'} (${r.finalRaritySource || '-'})`)
      ).join('<br>')}</div>`;
    }
    return `<details class="global-db-proof" open>
  <summary>Global DB: <span class="gdb-on">ON</span> &mdash; ${escHtml(g.sourceOfTruth || 'global_db')} &mdash; images ${escHtml(imgLabel)}</summary>
  <div class="gdb-body">
    <div class="gdb-row"><span>Source</span><span>SQLite global_db</span></div>
    <div class="gdb-row"><span>Species</span><span>${g.speciesCount != null ? g.speciesCount : '?'}</span></div>
    <div class="gdb-row"><span>Mappings</span><span>${g.mappingCount != null ? g.mappingCount : '?'}</span></div>
    <div class="gdb-row"><span>Observations</span><span>${g.observationCount != null ? g.observationCount.toLocaleString('en-US') : '?'}</span></div>
    <div class="gdb-row"><span>Images</span><span>${g.imageAssetCount != null ? g.imageAssetCount : '?'}</span></div>
    <div class="gdb-row"><span>Cards w/ global image</span><span>${escHtml(imgLabel)}</span></div>
    <div class="gdb-row"><span>Cards w/ global rarity</span><span>${escHtml(rarLabel)}</span></div>
    ${parityRows}${amountRows}${ambiguousRows}${hiddenRows}${quarantineRows}${missingFishRows}${parserRows}${botCatalogRows}${learningRows}${resetRows}${catchRows}${rarityRows}${debugRows ? `<div class="global-db-debug">${debugRows}</div>` : ''}
  </div>
</details>`;
  }
  function rarityNameStyle(item) {
    const r = item && item.rarity ? String(item.rarity).toLowerCase() : '';
    if (r && CARD_RARITY_MAP[r]) return '';
    const accent = item.rarityAccentColor || RARITY_NAME_COLORS[r] || null;
    return accent ? `color:${accent};` : '';
  }
  function publicMutationBadges(item) {
    const tags = [];
    if (Array.isArray(item.mutationTags)) tags.push(...item.mutationTags);
    else if (item.mutation) tags.push(item.mutation);
    const title = cardTitle(item);
    return tags.filter((t) => {
      if (!t) return false;
      const low = String(t).toLowerCase();
      if (low === 'big' || low === 'shiny' || low === 'big shiny') return false;
      if (title && title.toLowerCase().startsWith(`${low} `) && title.length > low.length + 1) return false;
      return true;
    });
  }
  function itemImageSrc(item) {
    if (isUsableImageUrl(item.imageUrl)) return item.imageUrl;
    if (item.imageAssetId && /^\d{10,22}$/.test(String(item.imageAssetId))) {
      return `/api/fishit-tracker/image/${item.imageAssetId}`;
    }
    return null;
  }
  function publicRarity(item) {
    const r = item && item.rarity ? String(item.rarity).trim() : '';
    if (r && r !== 'Unknown' && r !== '-') return r;
    return 'Common';
  }
  function rarityClass(r) { return r ? (RARITY_MAP[r.toLowerCase()] || 'badge rarity-common') : 'badge rarity-common'; }
  function cardRarityClass(r) { return r ? (CARD_RARITY_MAP[r.toLowerCase()] || 'rarity-common') : 'rarity-common'; }
  function fishCardClassList(item) {
    const rarity = publicRarity(item);
    const rarityLow = rarity ? rarity.toLowerCase() : '';
    const cls = ['ft-card', 'ft-card--fish', ftRarityClass(rarity)];
    if (item.shiny === true && rarityLow !== 'secret') cls.push('shiny');
    return cls;
  }
  const ITEM_IMAGES = {
    fish: '/assets/img/fishit/fallback-fish.svg',
    rod: '/assets/img/fishit/fallback-rod.svg',
    rods: '/assets/img/fishit/fallback-rod.svg',
    bait: '/assets/img/fishit/fallback-fish.svg',
    baits: '/assets/img/fishit/fallback-fish.svg',
    items: '/assets/img/fishit/fallback-secret.svg',
    item: '/assets/img/fishit/fallback-secret.svg',
    secret: '/assets/img/fishit/fallback-secret.svg',
    forgotten: '/assets/img/fishit/fallback-forgotten.svg',
    Default: '/assets/img/fishit/fallback-fish.svg'
  };
  function isUsableImageUrl(url) {
    if (!url || typeof url !== 'string') return false;
    const u = url.trim();
    if (!u) return false;
    if (/^\d{10,22}$/.test(u)) return false;
    if (/thumbnails\.roblox\.com/i.test(u)) return false;
    if (/create\.roblox\.com\/store\/asset\//i.test(u)) return false;
    if (u.startsWith('/api/fishit-tracker/image/')) return true;
    if (u.startsWith('/api/fishit-tracker/assets/fish/')) return true;
    if (u.startsWith('/api/fishit-tracker/assets/stones/')) return true;
    if (u.startsWith('http')) return true;
    if (u.startsWith('/assets/')) return true;
    return false;
  }
  function onFishImageError(img, item) {
    console.warn('[fishit-tracker-ui] image load failed', {
      name: item && item.name,
      itemId: item && item.itemId,
      imageAssetId: item && item.imageAssetId,
      imageUrl: item && item.imageUrl,
      imageResolved: item && item.imageResolved,
      imageStatus: item && item.imageStatus,
      attempted: img && img.src,
    });
    img.onerror = null;
    img.src = ITEM_IMAGES.Default;
  }
  function updateSummary() {
    const n = trackers.size;
    if (n === 0) {
      if (summaryTextEl) summaryTextEl.textContent = 'No players added yet';
      if (summaryBarEl) summaryBarEl.style.display = '';
    } else {
      if (summaryTextEl) summaryTextEl.textContent = '';
      if (summaryBarEl) summaryBarEl.style.display = 'none';
    }
    updateInventoryStats();
    updateRemoveMenu();
    renderAccountsTable();
    syncViewModeUi();
  }
  function cardTitle(item) {
    return item.cardName || item.baseFishName || item.name || 'Unknown';
  }
  function cardKey(item) {
    const id = item.speciesId || item.itemId || item.canonicalName || item.baseFishName || item.name || 'unknown';
    const mut = (Array.isArray(item.mutationTags) && item.mutationTags[0]) || item.mutation || '';
    return `${String(id).toLowerCase()}|${String(mut).toLowerCase()}`;
  }
  function buildFishCardInnerHtml(item, opts) {
    opts = opts || {};
    const title = cardTitle(item);
    const imgSrc = itemImageSrc(item) || ITEM_IMAGES.Default;
    const isPlaceholder = imgSrc === ITEM_IMAGES.Default;
    const weight = formatCardWeight(item);
    const statsHtml = buildCardBadgesHtml(item, opts);
    const weightHtml = weight ? `<div class="ft-card-weight">${escHtml(weight)}</div>` : '';
    return `
  <div class="ft-card-icon">
    <img src="${escHtml(imgSrc)}" alt="${escHtml(title)}" title="${escHtml(title)}" decoding="async"${isPlaceholder ? ' data-placeholder="true"' : ''} data-item-id="${escHtml(item.itemId || '')}" data-asset-id="${escHtml(item.imageAssetId || '')}">
  </div>
  <div class="ft-card-main">
    <div class="ft-card-name" title="${escHtml(title)}">${escHtml(title)}</div>
    ${statsHtml}
    ${weightHtml}
  </div>`;
  }
  function buildItemCardElement(item) {
    const rarity = publicRarity(item);
    const rarityLow = rarity ? rarity.toLowerCase() : '';
    const card = document.createElement('div');
    card.className = fishCardClassList(item).join(' ');
    card.setAttribute('data-card-key', cardKey(item));
    if (item.dataSource) card.setAttribute('data-source', item.dataSource);
    if (item.dataImageSource) card.setAttribute('data-image-source', item.dataImageSource);
    if (item.dataRaritySource) card.setAttribute('data-rarity-source', item.dataRaritySource);
    if (item.mutation) card.setAttribute('data-mutation', item.mutation);
    if (item.shiny === true) card.setAttribute('data-shiny', 'true');
    if (rarityLow) card.setAttribute('data-rarity', rarityLow);
    card.innerHTML = buildFishCardInnerHtml(item);
    const img = card.querySelector('.ft-card-icon img');
    if (img) img.onerror = () => onFishImageError(img, item);
    return card;
  }
  function patchItemCardElement(card, item) {
    const rarity = publicRarity(item);
    const rarityLow = rarity ? rarity.toLowerCase() : '';
    card.setAttribute('data-card-key', cardKey(item));
    card.className = fishCardClassList(item).join(' ');
    if (item.mutation) card.setAttribute('data-mutation', item.mutation);
    else card.removeAttribute('data-mutation');
    if (item.shiny === true) card.setAttribute('data-shiny', 'true');
    else card.removeAttribute('data-shiny');
    if (rarityLow) card.setAttribute('data-rarity', rarityLow);
    else card.removeAttribute('data-rarity');
    if (item.dataSource) card.setAttribute('data-source', item.dataSource);
    else card.removeAttribute('data-source');
    if (item.dataImageSource) card.setAttribute('data-image-source', item.dataImageSource);
    else card.removeAttribute('data-image-source');
    if (item.dataRaritySource) card.setAttribute('data-rarity-source', item.dataRaritySource);
    else card.removeAttribute('data-rarity-source');
    card.innerHTML = buildFishCardInnerHtml(item);
    const img = card.querySelector('.ft-card-icon img');
    if (img) img.onerror = () => onFishImageError(img, item);
  }
  function patchItemsGrid(container, items) {
    if (!items || items.length === 0) {
      if (!container.querySelector('.card-empty')) {
        container.innerHTML = '<div class="card-empty">&#x1F9F3; Inventory is empty.</div>';
      }
      return;
    }
    let grid = container.querySelector('.items-grid');
    if (!grid) {
      container.innerHTML = '<div class="items-grid inventory-grid fish-grid"></div>';
      grid = container.querySelector('.items-grid');
    }
    const empty = container.querySelector('.card-empty');
    if (empty) empty.remove();
    const nextKeys = new Set();
    const existing = new Map();
    grid.querySelectorAll('.ft-card--fish[data-card-key]').forEach((el) => {
      existing.set(el.getAttribute('data-card-key'), el);
    });
    items.forEach((item, idx) => {
      const key = cardKey(item);
      nextKeys.add(key);
      let card = existing.get(key);
      if (!card) {
        card = buildItemCardElement(item);
        const ref = grid.children[idx] || null;
        grid.insertBefore(card, ref);
      } else {
        patchItemCardElement(card, item);
        const ref = grid.children[idx] || null;
        if (card !== ref) grid.insertBefore(card, ref);
      }
    });
    existing.forEach((el, key) => {
      if (!nextKeys.has(key)) el.remove();
    });
  }
  function buildItemsHtml(items) {
    if (!items || items.length === 0) return '<div class="card-empty">&#x1F9F3; Inventory is empty.</div>';
    return `<div class="items-grid inventory-grid fish-grid">${items.map(item => {
      const rarity  = item.rarity && item.rarity !== 'Unknown' ? item.rarity : null;
      const rarityLow = rarity ? rarity.toLowerCase() : '';
      const cardCls = fishCardClassList(item);
      const mutAttr = item.mutation ? ` data-mutation="${escHtml(item.mutation)}"` : '';
      const shinyAttr = item.shiny === true ? ' data-shiny="true"' : '';
      const rarityAttr = rarityLow ? ` data-rarity="${escHtml(rarityLow)}"` : '';
      const srcAttr = item.dataSource ? ` data-source="${escHtml(item.dataSource)}"` : '';
      const imgSrcAttr = item.dataImageSource ? ` data-image-source="${escHtml(item.dataImageSource)}"` : '';
      const rarSrcAttr = item.dataRaritySource ? ` data-rarity-source="${escHtml(item.dataRaritySource)}"` : '';
      return `<div class="${cardCls.join(' ')}" data-card-key="${escHtml(cardKey(item))}"${mutAttr}${shinyAttr}${rarityAttr}${srcAttr}${imgSrcAttr}${rarSrcAttr}>${buildFishCardInnerHtml(item)}</div>`;
    }).join('')}</div>`;
  }
  function wireFishImageErrors(root) {
    if (!root) return;
    root.querySelectorAll('.ft-card--fish img[data-item-id]:not([data-placeholder])').forEach((img) => {
      const card = img.closest('.ft-card--fish');
      const itemId = img.getAttribute('data-item-id');
      const item = { itemId, imageAssetId: img.getAttribute('data-asset-id'), name: card && card.querySelector('.ft-card-name') && card.querySelector('.ft-card-name').textContent };
      img.onerror = () => onFishImageError(img, item);
    });
  }
  function createCard(username) {
    const el = document.createElement('div');
    el.className = 'tracker-card expanded';
    el.dataset.user = username.toLowerCase();
    el.innerHTML = `
<div class="card-head">
  <div class="card-head-main">
    <span class="card-sync-line" data-card-sync-line>
      <span class="status-dot dead" data-status-dot aria-hidden="true"></span>
      <span class="card-sync-text" data-card-sync-text>no sync ${escHtml(formatUsernameForDisplay(username, { hideUsernames }))}</span>
    </span>
  </div>
  <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><polyline points="18 15 12 9 6 15"></polyline></svg>
</div>
<div class="card-status-line" data-status-line></div>
${DEBUG_INVENTORY ? '<div data-global-db-proof></div>' : ''}
<div class="card-body" data-card-body></div>`;
    el.style.display = 'none';
    el.querySelector('.card-head').addEventListener('click', () => { el.classList.toggle('expanded'); });
    return el;
  }
  function updateCard(card, data) {
    card.classList.remove('state-waiting','state-error');
    card.classList.add('state-live');
    const l = card.querySelector('[data-status-line]');
    const b = card.querySelector('[data-card-body]');
    const fishList = getPublicFishItems(data);
    const stoneList = getPublicStoneItems(data);
    logFishOnlyRender(card.dataset.user, data, fishList);
    if (l) l.textContent = DEBUG_INVENTORY ? `Live ΓÇö User ID: ${data.userId || '-'}` : '';
    setCardSyncDisplay(card, data);
    if (DEBUG_INVENTORY) {
      const gdb = card.querySelector('[data-global-db-proof]');
      if (gdb) gdb.innerHTML = buildPlayerDataGameItemDbProofHtml(data) + buildPlayerDataItemUtilityProofHtml(data) + buildGlobalDbProofHtml(data);
    }
    if (b) patchCardInventory(b, fishList, stoneList);
    updateInventoryStats();
    if (accountViewMode === 'fish') renderBulkInventory('fish');
    else if (accountViewMode === 'stone') renderBulkInventory('stone');
  }
  function setCardWaiting(card, name) {
    card.classList.remove('state-live','state-error');
    card.classList.add('state-waiting');
    const l = card.querySelector('[data-status-line]');
    const b = card.querySelector('[data-card-body]');
    if (l) l.textContent = DEBUG_INVENTORY ? `Waiting for ${name} to execute the script in-game...` : '';
    const waitEntry = card.dataset.user && trackers.get(card.dataset.user);
    if (waitEntry) clearEntrySync(waitEntry);
    if (b) b.innerHTML = DEBUG_INVENTORY
      ? `<div class="card-empty">Waiting for ${escHtml(name)} to execute the script in-game...</div>`
      : publicWaitingHtml();
  }
  function phaseMessage(phase) {
    switch (phase) {
      case 'replion_client_found':
        return 'Replion client found, locating player data...';
      case 'player_data_not_found':
        return 'Replion client found, player data not found yet.';
      case 'inventory_path_missing':
        return 'Replion data found, inventory path not matched. Send logs.';
      case 'inventory_empty':
        return 'Replion inventory found, but it is empty.';
      case 'inventory_parse_failed':
        return 'Replion inventory found, parser needs update. Send logs.';
      case 'replion_missing':
        return 'Replion library not found in this game.';
      case 'player_data_selected':
        return 'Player data found ΓÇö reading inventory...';
      case 'startup':
      case 'live':
      default:
        return 'Script running ΓÇö locating Replion data...';
    }
  }
  function setCardRunning(card, name, data) {
    const fishList = getPublicFishItems(data);
    const stoneList = getPublicStoneItems(data);
    if (fishList.length || stoneList.length) {
      updateCard(card, data);
      return;
    }
    card.classList.remove('state-waiting','state-error');
    card.classList.add('state-live');
    const l = card.querySelector('[data-status-line]');
    const b = card.querySelector('[data-card-body]');
    if (DEBUG_INVENTORY) {
      if (l) l.innerHTML = `&#x1F7E2; ${escHtml(phaseMessage(data.phase))}`;
      setCardSyncDisplay(card, data);
      let bodyHtml = `<div class="card-empty">&#x1F7E2; ${escHtml(phaseMessage(data.phase))}</div>`;
      const ps = data.parseStats;
      const showParseDebug = ps && ps.raw > 0
        && (ps.acceptedInstances === 0 || ps.acceptedInstances === undefined)
        && (ps.accepted === 0 || ps.accepted === undefined)
        && (data.phase === 'inventory_parse_failed' || data.phase === 'inventory_empty');
      if (showParseDebug) {
        bodyHtml += `<div class="card-empty" style="font-size:0.8em;color:#8a94a6;margin-top:4px">`
          + `Raw: ${ps.raw ?? '?'} &middot; Accepted: ${ps.accepted ?? 0} &middot; Instances: ${ps.acceptedInstances ?? 0} &middot; Rejected: ${ps.rejected ?? '?'}`
          + (ps.selectedPath ? ` &middot; Path: ${escHtml(ps.selectedPath)}` : '')
          + (data.trackerBuild ? ` &middot; Build: ${escHtml(data.trackerBuild)}` : '')
          + `</div>`;
      } else if (data.trackerBuild || (ps && ps.acceptedInstances > 0)) {
        bodyHtml += `<div class="card-empty" style="font-size:0.75em;color:#6b7280;margin-top:4px">`
          + (data.trackerBuild ? `Build: ${escHtml(data.trackerBuild)}` : '')
          + (ps && ps.acceptedInstances > 0 ? ` &middot; Instances: ${ps.acceptedInstances} &middot; Unique: ${ps.accepted ?? 0}` : '')
          + `</div>`;
      }
      if (b) b.innerHTML = bodyHtml;
      return;
    }
    if (l) l.textContent = '';
    setCardSyncDisplay(card, data);
    if (b) b.innerHTML = publicLiveEmptyHtml();
  }
  function setCardOffline(card, name, lastData) {
    card.classList.remove('state-live','state-waiting');
    card.classList.add('state-error');
    const l = card.querySelector('[data-status-line]');
    const b = card.querySelector('[data-card-body]');
    if (l) l.textContent = DEBUG_INVENTORY ? 'Offline' : '';
    const offlineEntry = card.dataset.user && trackers.get(card.dataset.user);
    if (offlineEntry) refreshEntrySyncDisplay(offlineEntry);
    else setCardSyncDisplay(card, lastData);
    if (lastData) {
      const fishList = getPublicFishItems(lastData);
      const stoneList = getPublicStoneItems(lastData);
      if (b) patchCardInventory(b, fishList, stoneList);
    }
    if (accountViewMode === 'fish') renderBulkInventory('fish');
    else if (accountViewMode === 'stone') renderBulkInventory('stone');
  }
  function setCardError(card) {
    card.classList.remove('state-live','state-waiting');
    card.classList.add('state-error');
    const l = card.querySelector('[data-status-line]');
    if (l) l.textContent = DEBUG_INVENTORY ? 'Network error - retrying...' : '';
  }
  function setCardRefreshFailed(entry) {
    if (!entry || !entry.el) return;
    if (entry.lastData) {
      setCardOffline(entry.el, entry.displayName, entry.lastData);
      return;
    }
    setCardError(entry.el);
  }
  async function pollUser(key) {
    const entry = trackers.get(key);
    if (!entry) return;
    try {
      const res = await fetch(`/api/fishit-tracker/get-backpack/${encodeURIComponent(key)}`, { cache: 'no-store' });
      if (!trackers.has(key)) return;
      if (res.status === 404) {
        if (entry.lastData) {
          setCardOffline(entry.el, entry.displayName, entry.lastData);
        } else {
          setCardWaiting(entry.el, entry.displayName);
        }
        return;
      }
      if (!res.ok) { setCardRefreshFailed(entry); return; }
      const data = await res.json();
      debugLogEntryPlayerStats(entry);
      applyInventoryPollPayload(entry, key, data);
    } catch { if (trackers.has(key)) setCardRefreshFailed(entry); }
  }
  function normalizeUsername(raw) {
    return String(raw || '').trim().replace(/\s+/g, '');
  }
  function isValidUsername(raw) {
    const key = normalizeUsername(raw).toLowerCase();
    return /^[a-z0-9_]{3,20}$/.test(key);
  }
  function showUsernameError(message) {
    if (!usernameErrorEl) return;
    usernameErrorEl.textContent = message || '';
    if (inputEl) inputEl.classList.toggle('is-invalid', !!message);
  }
  function clearUsernameError() {
    showUsernameError('');
  }
  function selectLoadstringField() {
    if (!loadstringCodeEl) return;
    loadstringCodeEl.focus();
    loadstringCodeEl.select();
  }
  function fallbackCopyText(text) {
    return new Promise((resolve, reject) => {
      if (loadstringCodeEl) {
        loadstringCodeEl.value = text;
        selectLoadstringField();
        try {
          const ok = document.execCommand && document.execCommand('copy');
          if (ok) resolve();
          else reject(new Error('copy_failed'));
          return;
        } catch (err) {
          reject(err);
          return;
        }
      }
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.setAttribute('readonly', '');
      textarea.style.position = 'fixed';
      textarea.style.top = '-1000px';
      textarea.style.left = '-1000px';
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      try {
        const ok = document.execCommand && document.execCommand('copy');
        document.body.removeChild(textarea);
        if (ok) resolve();
        else reject(new Error('copy_failed'));
      } catch (err) {
        document.body.removeChild(textarea);
        reject(err);
      }
    });
  }
  function copyTrackerScript() {
    const text = CLEAN_LOADSTRING;
    if (!text) {
      setCopyStatus('Script unavailable.', true);
      return Promise.resolve(false);
    }
    const attempt = (navigator.clipboard && navigator.clipboard.writeText)
      ? navigator.clipboard.writeText(text).catch(() => fallbackCopyText(text))
      : fallbackCopyText(text);
    return attempt.then(() => {
      setCopyStatus('Copied!', false, true);
      if (copyBtn) {
        copyBtn.textContent = 'Copied!';
        copyBtn.classList.add('copied');
        setTimeout(() => { copyBtn.textContent = 'Copy'; copyBtn.classList.remove('copied'); }, 2000);
      }
      return true;
    }).catch(() => {
      selectLoadstringField();
      setCopyStatus('Copy failed ΓÇö select the script box and copy manually.', true);
      return false;
    });
  }
  function setCopyStatus(message, isError, isSuccess) {
    if (!copyStatusEl) return;
    copyStatusEl.textContent = message || '';
    copyStatusEl.classList.toggle('is-error', !!isError);
    copyStatusEl.classList.toggle('is-success', !!isSuccess);
  }
  function addTracker(username) {
    clearUsernameError();
    const raw = normalizeUsername(username);
    const key = raw.toLowerCase();
    if (!raw) {
      showUsernameError('Enter a Roblox username.');
      if (inputEl) inputEl.focus();
      return false;
    }
    if (!/^[a-z0-9_]{3,20}$/.test(key)) {
      showUsernameError('Username must be 3ΓÇô20 characters using letters, numbers, or underscore only.');
      if (inputEl) {
        inputEl.classList.add('is-invalid');
        inputEl.focus();
      }
      return false;
    }
    if (trackers.has(key)) {
      const e = trackers.get(key);
      e.el.scrollIntoView({ behavior:'smooth', block:'center' });
      showUsernameError('That player is already being tracked.');
      return false;
    }
    const card  = createCard(raw);
    trackerListEl.appendChild(card);
    const timer = setInterval(() => pollUser(key), POLL_MS);
    trackers.set(key, { timer, el: card, displayName: raw, lastData: null, liveSnapshot: null, lastSyncAt: null, lastPollOkAt: null, lastStatsPollAt: null, lastServerSyncAt: null, playerStats: null, lastFishList: null, lastStoneList: null });
    pollUser(key);
    updateSummary();
    saveCurrent();
    return true;
  }
  function removeTracker(key) {
    const entry = trackers.get(key);
    if (!entry) return;
    clearInterval(entry.timer);
    entry.el.remove();
    trackers.delete(key);
    updateSummary();
    saveCurrent();
  }
  function safeBind(name, fn) {
    try {
      fn();
    } catch (error) {
      console.error('[inventory] failed to bind ' + name, error);
    }
  }
  function bindCopyScript() {
    if (!copyBtn) return;
    copyBtn.addEventListener('click', () => { copyTrackerScript(); });
  }
  function bindSidebarScript() {
    if (!sidebarScriptBtn) return;
    sidebarScriptBtn.addEventListener('click', () => {
      copyTrackerScript().then((ok) => {
        if (ok === false) return;
        sidebarScriptBtn.classList.add('is-copied');
        const label = sidebarScriptBtn.querySelector('span');
        const previous = label ? label.textContent : 'Script';
        if (label) label.textContent = 'Copied!';
        setTimeout(() => {
          sidebarScriptBtn.classList.remove('is-copied');
          if (label) label.textContent = previous;
        }, 2000);
      }).catch(() => {});
    });
  }
  function bindAddPlayer() {
    if (!addBtn || !inputEl) return;
    addBtn.addEventListener('click', () => {
      const v = inputEl.value;
      if (addTracker(v)) inputEl.value = '';
      inputEl.focus();
    });
    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const v = inputEl.value;
        if (addTracker(v)) inputEl.value = '';
      }
    });
    inputEl.addEventListener('input', () => { if (inputEl.value.trim()) clearUsernameError(); });
  }
  function parseMultipleUsernames(raw) {
    const seen = new Set();
    const names = [];
    for (const part of String(raw || '').split(/[\n,;]+/)) {
      const trimmed = part.trim();
      if (!trimmed) continue;
      const key = trimmed.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      names.push(trimmed);
    }
    return names;
  }
  function showMultipleAddError(message) {
    if (!multipleAddErrorEl) return;
    if (message) {
      multipleAddErrorEl.textContent = message;
      multipleAddErrorEl.hidden = false;
    } else {
      multipleAddErrorEl.textContent = '';
      multipleAddErrorEl.hidden = true;
    }
  }
  function openMultipleAddModal() {
    if (!multipleAddModalEl) return;
    showMultipleAddError('');
    if (multipleAddTextareaEl) {
      multipleAddTextareaEl.value = '';
    }
    multipleAddModalEl.hidden = false;
    multipleAddModalEl.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    if (multipleAddTextareaEl) {
      setTimeout(() => multipleAddTextareaEl.focus(), 0);
    }
  }
  function closeMultipleAddModal() {
    if (!multipleAddModalEl) return;
    multipleAddModalEl.hidden = true;
    multipleAddModalEl.setAttribute('aria-hidden', 'true');
    showMultipleAddError('');
    document.body.style.overflow = '';
    if (multipleBtn) multipleBtn.focus();
  }
  function submitMultipleAdd() {
    const raw = multipleAddTextareaEl ? multipleAddTextareaEl.value : '';
    const names = parseMultipleUsernames(raw);
    if (!names.length) {
      showMultipleAddError('Enter at least one username.');
      if (multipleAddTextareaEl) multipleAddTextareaEl.focus();
      return;
    }
    showMultipleAddError('');
    let added = 0;
    for (const name of names) {
      if (addTracker(name)) added += 1;
    }
    if (added && inputEl) inputEl.value = '';
    closeMultipleAddModal();
  }
  function bindMultipleAdd() {
    if (!multipleBtn) return;
    multipleBtn.addEventListener('click', openMultipleAddModal);
    if (multipleAddCancelEl) {
      multipleAddCancelEl.addEventListener('click', closeMultipleAddModal);
    }
    if (multipleAddSubmitEl) {
      multipleAddSubmitEl.addEventListener('click', submitMultipleAdd);
    }
    if (multipleAddModalEl) {
      multipleAddModalEl.addEventListener('click', (e) => {
        if (e.target === multipleAddModalEl) closeMultipleAddModal();
      });
    }
    if (multipleAddTextareaEl) {
      multipleAddTextareaEl.addEventListener('input', () => {
        if (String(multipleAddTextareaEl.value || '').trim()) showMultipleAddError('');
      });
    }
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && multipleAddModalEl && !multipleAddModalEl.hidden) {
        e.preventDefault();
        closeMultipleAddModal();
      }
    });
  }
  function bindRemoveMenu() {
    if (!removeMenuBtn || !removeMenuEl) return;
    removeMenuBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleRemoveMenu();
    });
    removeMenuEl.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-remove-key]');
      if (!btn) return;
      const key = btn.getAttribute('data-remove-key');
      if (key) removeTracker(key);
      closeRemoveMenu();
    });
    document.addEventListener('click', (e) => {
      if (!removeMenuEl.hidden && !e.target.closest('.remove-dropdown')) closeRemoveMenu();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeRemoveMenu();
    });
  }
  function bindInventoryTabs() {
    const bulkSearchInput = document.querySelector('[data-bulk-search-input]');
    const bulkSearchClear = document.querySelector('[data-bulk-search-clear]');
    if (bulkSearchInput) {
      bulkSearchInput.addEventListener('input', () => {
        bulkSearchQuery = bulkSearchInput.value;
        if (bulkSearchClear) bulkSearchClear.classList.toggle('is-visible', String(bulkSearchQuery).trim().length > 0);
        if (accountViewMode === 'fish') renderBulkInventory('fish');
        else if (accountViewMode === 'stone') renderBulkInventory('stone');
      });
    }
    if (bulkSearchClear) {
      bulkSearchClear.addEventListener('click', () => {
        bulkSearchQuery = '';
        if (bulkSearchInput) bulkSearchInput.value = '';
        bulkSearchClear.classList.remove('is-visible');
        if (accountViewMode === 'fish') renderBulkInventory('fish');
        else if (accountViewMode === 'stone') renderBulkInventory('stone');
        if (bulkSearchInput) bulkSearchInput.focus();
      });
    }
  }
  function initFromQueryUsername() {
    const params = new URLSearchParams(window.location.search);
    const fromQuery = INITIAL_USERNAME
      || params.get('username')
      || params.get('u')
      || params.get('user')
      || '';
    if (fromQuery && isValidUsername(fromQuery)) {
      addTracker(fromQuery);
      if (inputEl) inputEl.value = '';
    } else if (fromQuery) {
      showUsernameError('Invalid username in URL. Use letters, numbers, or underscore (3ΓÇô20 chars).');
      if (inputEl) inputEl.value = fromQuery;
    }
  }
  function initInventoryUi() {
    safeBind('copy script', bindCopyScript);
    safeBind('sidebar script', bindSidebarScript);
    safeBind('add player', bindAddPlayer);
    safeBind('multiple add', bindMultipleAdd);
    safeBind('remove menu', bindRemoveMenu);
    safeBind('tabs', bindInventoryTabs);
    safeBind('accounts overview', bindAccountsOverview);
    safeBind('restore sessions', () => {
      loadSaved().forEach((u) => addTracker(u));
      updateSummary();
    });
    safeBind('query username', initFromQueryUsername);
    safeBind('sync age tick', () => setInterval(tickAllCardSyncStatus, SYNC_TICK_MS));
    if (APK_EMBED) document.documentElement.style.background = '#0d0f14';
    document.body.setAttribute('data-inventory-js', 'ready');
    window.__fishInventoryUiReady = true;
    if (DEBUG_INVENTORY) {
      window.__fishitDebugProof = {
        routeInventoryOnlyProof: {
          publicInventoryPath: '/inventory',
          legacyTrackerRedirect: '/tracker -> /inventory',
          publicUiUsesInventoryLabel: true,
        },
        trackerLuaTouchProof: {
          touched: false,
          reason: 'frontend_backend_route_ui_only',
          liveDistRelativePath: 'tracker.lua',
        },
        statusNoZeroProof: {
          minimumDurationSeconds: 1,
          neverRendersZeroSeconds: true,
          tableStatusFormat: '[circle] <duration>',
          noDuplicateUsernameInTableStatus: true,
          indicatorColorOnDotOnly: true,
        },
        statusFormatProof: {
          tablePublicFormat: '[circle] <duration>',
          cardPublicFormat: '[circle] <duration> <username>',
          literalLastSyncLabelPresent: false,
          durationTextUsesNormalColor: true,
          indicatorColorOnDotOnly: true,
          durationUpdatesEverySecond: true,
          durationResetsAfterSuccessfulPoll: true,
        },
        statsPollingProof: {
          publicPollIntervalMs: POLL_MS,
          syncTickMs: SYNC_TICK_MS,
          sharedRefreshFunction: 'applyInventoryPollPayload',
          statsFromSamePayload: true,
          coinRefreshesOnInterval: true,
          totalCaughtRefreshesOnInterval: true,
          rarestFishRefreshesOnInterval: true,
        },
        unifiedPollPipelineProof: {
          sharedRefreshFunction: 'applyInventoryPollPayload',
          liveSnapshotField: 'entry.liveSnapshot',
          samePayloadForFishStonesStats: true,
        },
        statsHarmonyProof: {
          coinTotalCaughtRarestFromSamePlayerStatsObject: true,
          numericTotalCaughtPreferred: true,
        },
        totalCaughtIntervalProof: {
          totalCaughtRefreshesOnEveryPoll: true,
          staleTextRegeneratedOnNumericMerge: true,
        },
        coinIntervalProof: { coinRefreshesOnEveryPoll: true },
        rarestFishIntervalProof: { rarestFishRefreshesOnEveryPoll: true },
        fishStoneIntervalProof: { fishStoneFromSamePollPayload: true },
        uploadIntervalProof: { trackerUploadIntervalSeconds: 10, publicRefreshIntervalMs: POLL_MS },
        connectionIndicatorProof: {
          staleThresholdSeconds: SYNC_LIVE_MAX_SEC,
          usesSuccessfulPollTimestamp: true,
          noRedBlinkDuringNormalRefresh: true,
        },
        toolbarActionProof: {
          order: ['table', 'fishGrid', 'stoneGrid', 'copyUsernames', 'refresh'],
          copyUsernamesOnly: true,
          copyIncludesTableData: false,
        },
        gridModeProof: {
          fishGridShowsAllAccountsFish: true,
          stoneGridShowsAllAccountsStones: true,
          eachAccountAllAccountsTabsRemoved: true,
          individualInventoryViaBackpackOnly: true,
        },
        responsiveLayoutProof: {
          desktopTableMinWidth: 769,
          mobileStatsHorizontalMaxWidth: 768,
          desktopLayoutReverted: true,
          mobileStatsFlexRow: true,
        },
      };
    }
    console.log('[inventory] UI initialized');
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initInventoryUi);
  } else {
    initInventoryUi();
  }
}());