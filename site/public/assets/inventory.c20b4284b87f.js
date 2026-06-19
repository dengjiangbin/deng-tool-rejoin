(function(){'use strict';function readInventoryCfg(){const el=document.getElementById('inventory-runtime');if(!el)return{};try{return JSON.parse(el.textContent||'{}');}catch(_){return{};}}const __CFG__=readInventoryCfg();
const LS_KEY        = 'fishit_tracked_users';
  const LS_BULK_CACHE = 'fishit_bulk_inventory_cache_v1';
  const TRACKER_POLL_INTERVAL_MS = 4_000;
  const POLL_MS = TRACKER_POLL_INTERVAL_MS;
  const SYNC_TICK_MS  = 1000;
  const PERF_STARTED_AT = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
  let perfFirstApiMs = null;
  let perfApiPayloadBytes = 0;
  let perfInitialRequests = 0;
  const DEBUG_INVENTORY = !!__CFG__.debugInventory;
  const APK_EMBED = !!__CFG__.apkEmbed;
  const DEBUG_GLOBAL  = DEBUG_INVENTORY && /(?:^|[?&])debug=global(?:&|$)/.test(window.location.search);
  const FT_DEBUG_METADATA = /(?:^|[?&])debug=metadata(?:&|$)/i.test(window.location.search);
  function ftRenderMetadataDebug(key, info) {
    if (!FT_DEBUG_METADATA) return;
    let el = document.getElementById('ftMetadataDebug');
    if (!el) {
      el = document.createElement('pre');
      el.id = 'ftMetadataDebug';
      el.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:99999;max-width:46vw;max-height:60vh;overflow:auto;margin:0;padding:10px 12px;background:rgba(2,6,23,.94);color:#a7f3d0;border:1px solid #10b981;border-radius:8px;font:11px/1.4 ui-monospace,monospace;white-space:pre-wrap;';
      document.body.appendChild(el);
    }
    const lines = [
      `# debug=metadata  ${new Date().toLocaleTimeString()}`,
      `user=${key}`,
      `apiUrl=${info.url}`,
      `httpStatus=${info.status}`,
    ];
    if (info.empty) lines.push(`EMPTY reason=${info.reason || 'unknown'}`);
    const m = info.metadataDebug;
    if (m) {
      lines.push(`fish=${m.publicFishCount} stones=${m.publicStoneCount} totems=${m.publicTotemCount}`);
      lines.push(`ownedInstances=${m.ownedInstanceCount} withMutation=${m.instancesWithMutation} withWeight=${m.instancesWithWeight}`);
      lines.push(`nilMutationStr=${m.instancesWithNilMutationString} missingWeight=${m.instancesMissingWeight}`);
      lines.push(`leaderstats=${m.hasLeaderstats} build=${m.trackerBuild}`);
      lines.push(`lane=${JSON.stringify(m.laneMergeState)}`);
      lines.push(`source=${m.dataSourcePath}`);
    }
    el.textContent = lines.join('\n');
  }
  const TRACKER_UI_DEPLOY = __CFG__.trackerUiDeployMarker || '';
  const INITIAL_USERNAME = __CFG__.initialUsername || '';
  const CSRF_CFG = __CFG__.csrfToken || '';
  function readRuntimeCsrfToken(){if(CSRF_CFG)return CSRF_CFG;try{const hidden=document.querySelector('input[name=\"_csrf\"]');if(hidden&&hidden.value)return hidden.value;}catch(_){}return '';}
  const CSRF_TOKEN = readRuntimeCsrfToken();
  const LS_MIGRATED_KEY = 'fishit_tracked_users_migrated_v1';
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
  const removeOfflineBtn = document.getElementById('removeOfflineBtn');
  const removeNoDataBtn  = document.getElementById('removeNoDataBtn');
  const removeAllBtn     = document.getElementById('removeAllBtn');
  const removeAllModalEl = document.getElementById('removeAllModal');
  const removeAllCancelEl = document.getElementById('removeAllCancel');
  const removeAllConfirmEl = document.getElementById('removeAllConfirm');
  const removeAllErrorEl = document.getElementById('removeAllError');
  const summaryBarEl   = document.getElementById('summaryBar');
  const trackerListEl  = document.getElementById('trackerList');
  const bulkPanelEl    = document.getElementById('bulkInventoryPanel');
  const bulkBodyEl     = document.getElementById('bulkInventoryBody');
  const summaryTextEl  = document.getElementById('summaryText');
  const statOnlineAccountsEl = document.getElementById('statOnlineAccounts');
  const statEvolvedStonesEl = document.getElementById('statEvolvedStones');
  const statSecretFishEl = document.getElementById('statSecretFish');
  const statForgottenFishEl = document.getElementById('statForgottenFish');
  const statRubyGemstoneEl = document.getElementById('statRubyGemstone');
  const statRunicStoneEl = document.getElementById('statRunicStone');
  const accountsOverviewEl = document.getElementById('accountsOverview');
  const accountsSearchInputEl = document.getElementById('accountsSearchInput');
  const accountsTableBodyEl = document.getElementById('accountsTableBody');
  const accountsMobileListEl = document.getElementById('accountsMobileList');
  const accountsPaginationEl = document.getElementById('accountsPagination');
  const accountsPaginationRangeEl = document.getElementById('accountsPaginationRange');
  const accountsPaginationPageEl = document.getElementById('accountsPaginationPage');
  const accountsPageFirstBtn = document.getElementById('accountsPageFirst');
  const accountsPagePrevBtn = document.getElementById('accountsPagePrev');
  const accountsPageNextBtn = document.getElementById('accountsPageNext');
  const accountsPageLastBtn = document.getElementById('accountsPageLast');
  const accountsPageSizeBtn = document.getElementById('accountsPageSizeBtn');
  const pageSizeModalEl = document.getElementById('pageSizeModal');
  const pageSizeCancelBtn = document.getElementById('pageSizeCancel');
  const PAGE_SIZE_OPTIONS = [20, 50, 100, 1000];
  const ACCOUNTS_DEFAULT_PAGE_SIZE = 20;
  let accountsPageSize = ACCOUNTS_DEFAULT_PAGE_SIZE;
  let accountsCurrentPage = 1;
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
  function loadLegacyLocalUsernames() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || '[]'); } catch { return []; }
  }
  function serverAccountDisplayName(acct) {
    if (!acct) return '';
    return acct.robloxUsername || acct.displayName || '';
  }
  function formatInventoryAccountsError(res, fallback) {
    if (!res) return fallback || 'Request failed.';
    if (res.status === 401) return 'Login with Discord first, then try again.';
    if (res.status === 403) return (res.data && res.data.message) || 'Session expired. Refresh the page and try again.';
    if (res.status === 503) return (res.data && res.data.message) || 'Saved account storage is not ready yet.';
    const parts = [];
    if (res.data && res.data.message) parts.push(res.data.message);
    else if (fallback) parts.push(fallback);
    if (DEBUG_INVENTORY) {
      if (res.data && res.data.error) parts.push(`(${res.data.error})`);
      if (res.data && res.data.detail) parts.push(String(res.data.detail));
      if (res.status) parts.push(`HTTP ${res.status}`);
    }
    return parts.filter(Boolean).join(' ') || fallback || 'Request failed.';
  }
  function syncTrackersWithServerAccounts(accounts) {
    const list = Array.isArray(accounts) ? accounts : [];
    list.forEach((acct) => {
      const name = serverAccountDisplayName(acct);
      if (name) addTrackerLocal(name);
    });
    updateSummary();
  }
  async function inventoryAccountsRequest(path, opts) {
    const options = opts || {};
    const headers = Object.assign({
      Accept: 'application/json',
      'X-CSRF-Token': CSRF_TOKEN,
    }, options.headers || {});
    if (options.body != null && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }
    const res = await fetch('/api/inventory/accounts' + (path || ''), Object.assign({}, options, {
      credentials: 'same-origin',
      headers,
    }));
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    return { ok: res.ok, status: res.status, data };
  }
  async function migrateLegacyLocalUsernamesIfNeeded() {
    try {
      if (localStorage.getItem(LS_MIGRATED_KEY) === '1') return;
    } catch {}
    const legacy = loadLegacyLocalUsernames().filter(Boolean);
    if (!legacy.length) {
      try { localStorage.setItem(LS_MIGRATED_KEY, '1'); } catch {}
      return;
    }
    const res = await inventoryAccountsRequest('/migrate', {
      method: 'POST',
      body: JSON.stringify({ usernames: legacy }),
    });
    if (res.ok) {
      try {
        localStorage.removeItem(LS_KEY);
        localStorage.setItem(LS_MIGRATED_KEY, '1');
      } catch {}
    }
  }
  async function restoreTrackedAccountsFromServer() {
    await migrateLegacyLocalUsernamesIfNeeded();
    const res = await inventoryAccountsRequest('', { method: 'GET' });
    if (!res.ok) {
      console.error('[inventory] restore tracked accounts failed', res.status, res.data);
      return;
    }
    const accounts = Array.isArray(res.data && res.data.accounts) ? res.data.accounts : [];
    syncTrackersWithServerAccounts(accounts);
  }
  async function persistTrackerAdd(username) {
    const res = await inventoryAccountsRequest('', {
      method: 'POST',
      body: JSON.stringify({ username }),
    });
    if (res.ok && res.data) {
      if (Array.isArray(res.data.accounts)) syncTrackersWithServerAccounts(res.data.accounts);
      return true;
    }
    if (res.status === 409) {
      if (Array.isArray(res.data && res.data.accounts)) syncTrackersWithServerAccounts(res.data.accounts);
      showUsernameError((res.data && res.data.message) || 'That player is already being tracked.');
      return false;
    }
    showUsernameError(formatInventoryAccountsError(res, 'Could not save tracked account.'));
    return false;
  }
  async function persistTrackerAddMany(usernames) {
    const res = await inventoryAccountsRequest('', {
      method: 'POST',
      body: JSON.stringify({ usernames }),
    });
    if (res.ok && res.data) {
      if (Array.isArray(res.data.accounts)) syncTrackersWithServerAccounts(res.data.accounts);
      return res.data;
    }
    if (res.status === 409 && res.data) {
      if (Array.isArray(res.data.accounts)) syncTrackersWithServerAccounts(res.data.accounts);
      return res.data;
    }
    showMultipleAddError(formatInventoryAccountsError(res, 'Could not save tracked accounts.'));
    return null;
  }
  async function persistTrackerRemove(key) {
    const normalizedKey = String(key || '').toLowerCase();
    const res = await inventoryAccountsRequest('/' + encodeURIComponent(normalizedKey), {
      method: 'DELETE',
    });
    if (!res.ok) return false;
    if (res.data && Array.isArray(res.data.accounts)) {
      const serverKeys = new Set(res.data.accounts.map((acct) => String(acct.robloxUsernameKey || '').toLowerCase()));
      [...trackers.keys()].forEach((localKey) => {
        if (!serverKeys.has(localKey)) {
          const entry = trackers.get(localKey);
          if (entry && entry.timer) clearInterval(entry.timer);
          if (entry && entry.el) entry.el.remove();
          trackers.delete(localKey);
        }
      });
      syncTrackersWithServerAccounts(res.data.accounts);
      return true;
    }
    const entry = trackers.get(normalizedKey);
    if (entry) {
      if (entry.timer) clearInterval(entry.timer);
      if (entry.el) entry.el.remove();
      trackers.delete(normalizedKey);
      updateSummary();
    }
    return true;
  }
  async function persistTrackerRemoveAll() {
    const res = await inventoryAccountsRequest('', { method: 'DELETE' });
    if (!res.ok) return false;
    [...trackers.keys()].forEach((localKey) => {
      const entry = trackers.get(localKey);
      if (entry && entry.timer) clearInterval(entry.timer);
      if (entry && entry.el) entry.el.remove();
      trackers.delete(localKey);
    });
    if (res.data && Array.isArray(res.data.accounts)) {
      syncTrackersWithServerAccounts(res.data.accounts);
    } else {
      updateSummary();
    }
    return true;
  }
  const EMPTY_STAT = '\u2014';
  const TRACKER_READ_API = '/api/tracker';
  const EN_DASH = '\u2013';
  const EM_DASH = '\u2014';
  const TRACKER_BUILD_MARKER = 'LANE_SESSION_SYNC_P0_2026_06_19';
  try {
    window.__TRACKER_BUILD_MARKER = TRACKER_BUILD_MARKER;
    console.log('[tracker] build ' + TRACKER_BUILD_MARKER);
  } catch (_) {  }
  function trackerReadPath(pathOrUrl) {
    const value = String(pathOrUrl || '');
    if (value.startsWith('/api/fishit-tracker/')) {
      return `${TRACKER_READ_API}/${value.slice('/api/fishit-tracker/'.length)}`;
    }
    return value;
  }
  function isEmptyStatValue(value) {
    return value === EMPTY_STAT || value === '-' || value === '\u2014';
  }
  function markCardEnterAnimation(card) {
    if (!card || card.dataset.enterBound === '1') return;
    card.dataset.enterBound = '1';
    card.classList.add('ft-card--enter');
    card.addEventListener('animationend', () => {
      card.classList.remove('ft-card--enter');
    }, { once: true });
  }
  function placeGridCardAtIndex(grid, card, idx) {
    if (!grid || !card) return;
    if (grid.children[idx] === card) return;
    grid.insertBefore(card, grid.children[idx] || null);
  }
  function setElementTextIfChanged(el, text, title) {
    if (!el) return;
    const next = String(text == null ? '' : text);
    if (el.textContent !== next) el.textContent = next;
    if (title != null) {
      const t = String(title);
      if (el.title !== t) el.title = t;
    }
  }
  function patchHtmlIfChanged(el, html) {
    if (!el) return;
    const next = String(html || '');
    if (el.getAttribute('data-render-sig') === next) return;
    el.innerHTML = next;
    el.setAttribute('data-render-sig', next);
  }
  function patchCardImage(img, item, imgSrc, isFish, eagerLoad) {
    if (!img) return;
    const src = imgSrc || ITEM_IMAGES.Default;
    const isPlaceholder = src === ITEM_IMAGES.Default;
    const current = img.getAttribute('src') || '';
    if (current !== src) {
      if (isFish) img.onerror = () => onFishImageError(img, item);
      else img.onerror = null;
      img.src = src;
    }
    img.decoding = 'async';
    img.loading = eagerLoad ? 'eager' : 'lazy';
    img.width = 54;
    img.height = 54;
    const alt = item && (item.name || item.displayName) ? String(item.name || item.displayName) : (img.alt || '');
    if (img.alt !== alt) img.alt = alt;
    if (img.title !== alt) img.title = alt;
    if (isPlaceholder) img.setAttribute('data-placeholder', 'true');
    else img.removeAttribute('data-placeholder');
    const itemId = item && item.itemId != null ? String(item.itemId) : '';
    if (itemId) img.setAttribute('data-item-id', itemId);
    else img.removeAttribute('data-item-id');
    const assetId = item && item.imageAssetId != null ? String(item.imageAssetId) : '';
    if (assetId) img.setAttribute('data-asset-id', assetId);
    else img.removeAttribute('data-asset-id');
  }
  function patchFishCardDom(card, item, opts) {
    opts = opts || {};
    const cardKeyVal = card && card.getAttribute('data-card-key');
    const cardRoot = card && card.closest('.tracker-card');
    const entryKey = cardRoot && cardRoot.dataset.user;
    const entry = entryKey && trackers.get(entryKey);
    let mergedItem = item;
    if (entry && cardKeyVal) {
      const prevList = entry.lastFishList || [];
      const prev = prevList.find((row) => cardKey(row) === cardKeyVal);
      if (prev) mergedItem = preferFishCardImageFields(prev, item);
    }
    const title = cardTitle(mergedItem);
    const imgSrc = itemImageSrc(mergedItem) || ITEM_IMAGES.Default;
    const icon = card.querySelector('.ft-card-icon');
    let img = icon && icon.querySelector('img');
    if (!img && icon) {
      img = document.createElement('img');
      icon.textContent = '';
      icon.appendChild(img);
    }
    patchCardImage(img, mergedItem, imgSrc, true, !!(opts && opts.eagerImage));
    setElementTextIfChanged(card.querySelector('.ft-card-name'), title, title);
    const statsHtml = buildCardBadgesHtml(mergedItem, opts);
    patchHtmlIfChanged(card.querySelector('.ft-card-stats'), statsHtml);
    const weight = formatCardWeight(item);
    let weightEl = card.querySelector('.ft-card-weight');
    if (weight) {
      if (!weightEl) {
        weightEl = document.createElement('div');
        weightEl.className = 'ft-card-weight';
        const main = card.querySelector('.ft-card-main');
        if (main) main.appendChild(weightEl);
      }
      setElementTextIfChanged(weightEl, weight);
    } else if (weightEl) {
      weightEl.remove();
    }
  }
  function patchStoneCardDom(card, item, opts) {
    opts = opts || {};
    const title = stoneDisplayName(item);
    const imgSrc = itemImageSrc(item);
    const icon = card.querySelector('.ft-card-icon');
    if (imgSrc) {
      let img = icon && icon.querySelector('img');
      if (!img && icon) {
        img = document.createElement('img');
        icon.textContent = '';
        icon.appendChild(img);
      }
      patchCardImage(img, item, imgSrc, false, !!(opts && opts.eagerImage));
    } else if (icon && icon.textContent !== '\u{1F48E}') {
      icon.textContent = '\u{1F48E}';
    }
    setElementTextIfChanged(card.querySelector('.ft-card-name'), title, title);
    patchHtmlIfChanged(card.querySelector('.ft-card-stats'), buildStoneStatsHtml(item, opts));
  }
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
  const SYNC_LIVE_MAX_SEC = 75;
  const DEFAULT_UPLOAD_INTERVAL_SEC = 60;
  let accountStatusServerNowMs = Date.now();
  let lastValidTrackerSummary = null;
  function entryUploadStatus(entry) {
    return entry && entry.uploadStatus ? entry.uploadStatus : null;
  }
  function statusColorToFreshness(color) {
    if (color === 'green') return 'live';
    if (color === 'yellow') return 'stale';
    return 'dead';
  }
  function mergeUploadStatusOntoEntry(entry, statusRow, serverNow) {
    if (!entry || !statusRow) return;
    entry.uploadStatus = statusRow;
    entry._uploadStatusFetchedAtMs = serverNow
      ? new Date(serverNow).getTime()
      : Date.now();
  }
  function liveDriftedSeconds(st, secondsField, fetchedAtMs) {
    if (!st || st[secondsField] == null) return null;
    if (!fetchedAtMs) return st[secondsField];
    const drift = Math.floor((Date.now() - fetchedAtMs) / 1000);
    return st[secondsField] + Math.max(0, drift);
  }
  function liveSecondsSinceLastSuccess(entry) {
    return liveSecondsSinceStatusSuccess(entry);
  }
  function entryStatusSuccessTimestamp(entry) {
    const st = entryUploadStatus(entry);
    if (st && st.statusLastSuccessAt) return st.statusLastSuccessAt;
    const data = entry && entry.lastData;
    if (data && data.statusLastSuccessAt) return data.statusLastSuccessAt;
    if (st && st.lastSuccessfulHeartbeatAt) return st.lastSuccessfulHeartbeatAt;
    return (data && (data.lastAccountSeenAt || data.lastHeartbeatAt || data.lastSeenAt)) || null;
  }
  function liveSecondsSinceStatusSuccess(entry) {
    const st = entryUploadStatus(entry);
    if (st && st.secondsSinceLastStatusSuccess != null) {
      return liveDriftedSeconds(st, 'secondsSinceLastStatusSuccess', entry._uploadStatusFetchedAtMs);
    }
    return syncAgeSeconds(entryStatusSuccessTimestamp(entry));
  }
  function entryStatsUploadSuccessTimestamp(entry) {
    const st = entryUploadStatus(entry);
    if (st && st.leaderstatsLastSuccessAt) return st.leaderstatsLastSuccessAt;
    if (st && st.lastStatsUploadAt) return st.lastStatsUploadAt;
    const data = entry && entry.lastData;
    return (data && (data.leaderstatsLastSuccessAt || data.lastStatsUploadAt)) || entryStatsUploadTimestamp(entry);
  }
  function liveSecondsSinceStatsSuccess(entry) {
    const st = entryUploadStatus(entry);
    if (st && st.secondsSinceLastLeaderstatsSuccess != null) {
      return liveDriftedSeconds(st, 'secondsSinceLastLeaderstatsSuccess', entry._uploadStatusFetchedAtMs);
    }
    return syncAgeSeconds(entryStatsUploadSuccessTimestamp(entry));
  }
  function entryInventorySuccessTimestamp(entry) {
    const st = entryUploadStatus(entry);
    if (st && st.inventoryLastSuccessAt) return st.inventoryLastSuccessAt;
    return entryInventoryUploadTimestamp(entry);
  }
  function liveSecondsSinceInventorySuccess(entry) {
    const st = entryUploadStatus(entry);
    if (st && st.secondsSinceLastInventorySuccess != null) {
      return liveDriftedSeconds(st, 'secondsSinceLastInventorySuccess', entry._uploadStatusFetchedAtMs);
    }
    const fresh = isInventoryUploadFresh(entry);
    const ts = fresh ? entryInventorySuccessTimestamp(entry) : entryInventoryRedSince(entry);
    return syncAgeSeconds(ts);
  }
  function applyTrackerSummaryFields(payload) {
    if (!payload || !Number.isFinite(Number(payload.trackedCount))) return;
    lastValidTrackerSummary = {
      trackedCount: Number(payload.trackedCount),
      onlineCount: Number(payload.onlineCount) || 0,
      offlineCount: Number(payload.offlineCount) || 0,
      generatedAt: payload.generatedAt || payload.serverNow || null,
      freshnessWindowMs: payload.freshnessWindowMs || null,
    };
  }
  async function pollAccountStatuses(forceFresh) {
    try {
      const qs = `?_=${Date.now()}`;
      const res = await fetch(`/api/tracker/account-status${qs}`, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'Cache-Control': 'no-cache', Pragma: 'no-cache' },
      });
      if (!res.ok) return;
      const payload = await res.json();
      applyAccountStatusPayload(payload);
    } catch {}
  }
  function applyAccountStatusPayload(payload) {
    if (!payload || !Array.isArray(payload.accounts)) return;
    applyTrackerSummaryFields(payload);
    accountStatusServerNowMs = payload.serverNow
      ? new Date(payload.serverNow).getTime()
      : Date.now();
    const byKey = new Map();
    payload.accounts.forEach((acct) => {
      const userKey = acct.username ? String(acct.username).toLowerCase() : null;
      if (userKey) byKey.set(userKey, acct);
      if (acct.robloxUserId) byKey.set('uid:' + acct.robloxUserId, acct);
      if (acct.canonicalKey) byKey.set(String(acct.canonicalKey).toLowerCase(), acct);
    });
    trackers.forEach((entry, key) => {
      const uid = entry.lastData && entry.lastData.userId ? String(entry.lastData.userId) : null;
      const st = byKey.get(key) || (uid ? byKey.get('uid:' + uid) : null);
      if (st) {
        mergeUploadStatusOntoEntry(entry, st, payload.serverNow);
        applyAccountStatusStatsToEntry(entry, st);
        entry.lastData = mergePreservedInventorySnapshot(entry.lastData, {
          ...(entry.lastData || {}),
          ...st,
          liveAccountStats: st.liveAccountStats || {
            coin: st.coin,
            coins: st.coins,
            coinsText: st.coinsText,
            totalCaught: st.totalCaught,
            totalCaughtText: st.totalCaughtText,
            rarestFish: st.rarestFish,
            rarestFishChance: st.rarestFishChance,
            statsSource: st.statsSource,
            statsProven: st.statsProven === true,
            emptyReason: st.emptyReason || st.statsEmptyReason || null,
            statsAt: st.statsAt || null,
            trackerBuild: st.trackerBuild || null,
            lastSuccessfulUploadAt: st.lastSuccessfulUploadAt || null,
          },
          statsProven: st.statsProven === true,
          playerStatsProven: st.playerStatsProven === true || st.statsProven === true,
          username: st.username || (entry.lastData && entry.lastData.username) || entry.displayName,
          inventoryDisplayState: st.inventoryDisplayState
            || (entry.lastData && entry.lastData.inventoryDisplayState)
            || null,
          statsEmptyReason: st.emptyReason || st.statsEmptyReason || null,
        });
        reconcileEntryPresence(entry, st, accountStatusServerNowMs);
        refreshLiveSnapshotInventoryFromEntry(entry, entry.lastData);
        applyLiveSnapshotToPublicUi(entry, key, entry.lastData);
        maybeResetSectionTimers(entry);
      }
    });
    patchAllVisibleAccountStats();
    updateInventoryStats();
  }
  function entrySnapshotData(entry) {
    return entry && entry.lastData ? entry.lastData : null;
  }
  function inventoryDisplayState(data) {
    if (!data) return 'waiting';
    if (data.inventoryDisplayState) return data.inventoryDisplayState;
    if (data.snapshotComplete === true) return data.provenEmptyInventory ? 'empty' : 'ready';
    if (data.inventoryReady === true) return data.provenEmptyInventory ? 'empty' : 'ready';
    if (data.statsProven === true && (data.lastInventoryAt || data.lastStatsUploadAt)) {
      return data.provenEmptyInventory ? 'empty' : 'ready';
    }
    if (data.lastSuccessfulHeartbeatAt || data.lastHeartbeatAt) return 'syncing';
    return 'waiting';
  }
  function isProvenEmptyInventory(data) {
    return inventoryDisplayState(data) === 'empty';
  }
  const INVENTORY_PRESERVE_KEYS = [
    'fishItems', 'stoneItems', 'totemItems', 'publicItems', 'publicFishItems',
    'fishInventory', 'stoneInventory', 'totemInventory',
    'lastGoodPublicFishItems', 'lastGoodPublicStoneItems', 'lastGoodPublicTotemItems',
    'lastGoodPublicFishCount', 'items', 'inventory', 'counts', 'fishCounts', 'publicCounts',
    'lastInventoryAt', 'lastSnapshotUploadAt', 'lastGoodFishPreserved',
  ];
  const STATS_PRESERVE_KEYS = [
    'playerStats', 'liveAccountStats', 'lastStatsUploadAt', 'playerStatsUpdatedAt',
    'leaderstatsLastSuccessAt', 'hasLeaderstatsSnapshot', 'statsProven', 'playerStatsProven',
  ];
  function getSnapshotTime(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') return NaN;
    const fields = [
      snapshot.lastInventoryAt,
      snapshot.lastSnapshotUploadAt,
      snapshot.serverReceivedAt,
      snapshot.updatedAt,
      snapshot.playerStatsUpdatedAt,
      snapshot.lastStatsUploadAt,
      snapshot.payloadAt,
    ];
    for (let i = 0; i < fields.length; i += 1) {
      const ms = new Date(fields[i]).getTime();
      if (Number.isFinite(ms)) return ms;
    }
    return NaN;
  }
  function isNewerSnapshot(candidate, current) {
    const candidateTime = getSnapshotTime(candidate);
    const currentTime = getSnapshotTime(current);
    if (!Number.isFinite(candidateTime)) return false;
    if (!Number.isFinite(currentTime)) return true;
    return candidateTime >= currentTime;
  }
  function hasLeaderstats(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') return false;
    const ps = snapshot.playerStats;
    if (ps && typeof ps === 'object') {
      if (ps.coins != null || ps.totalCaught != null || ps.coinsText || ps.totalCaughtText || ps.rarestFishChance) {
        return true;
      }
    }
    const live = snapshot.liveAccountStats;
    if (live && typeof live === 'object' && !live.emptyReason) {
      if (live.coins != null || live.totalCaught != null || live.coinsText || live.coin || live.totalCaughtText) {
        return true;
      }
    }
    return snapshot.hasLeaderstatsSnapshot === true;
  }
  function payloadHasLeaderstats(src) {
    if (!src || typeof src !== 'object') return false;
    if (hasLeaderstats(src)) return true;
    const live = (src.liveAccountStats && typeof src.liveAccountStats === 'object') ? src.liveAccountStats : src;
    if (live && !live.emptyReason && (
      live.coins != null || live.coin != null || live.totalCaught != null
      || live.coinsText || live.totalCaughtText
    )) {
      return true;
    }
    return src.statsProven === true || src.hasLeaderstatsSnapshot === true;
  }
  function getFishRows(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') return [];
    if (Array.isArray(snapshot.fishItems) && snapshot.fishItems.length) return snapshot.fishItems;
    if (Array.isArray(snapshot.publicFishItems) && snapshot.publicFishItems.length) return snapshot.publicFishItems;
    if (snapshot.fishInventory && Array.isArray(snapshot.fishInventory.fish) && snapshot.fishInventory.fish.length) {
      return snapshot.fishInventory.fish;
    }
    if (Array.isArray(snapshot.lastGoodPublicFishItems) && snapshot.lastGoodPublicFishItems.length) {
      return snapshot.lastGoodPublicFishItems;
    }
    return [];
  }
  function getItemRows(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') return [];
    const stones = Array.isArray(snapshot.stoneItems) ? snapshot.stoneItems : [];
    const totems = Array.isArray(snapshot.totemItems) ? snapshot.totemItems : [];
    const publicItems = Array.isArray(snapshot.publicItems) ? snapshot.publicItems : [];
    const legacyStones = Array.isArray(snapshot.lastGoodPublicStoneItems) ? snapshot.lastGoodPublicStoneItems : [];
    const legacyTotems = Array.isArray(snapshot.lastGoodPublicTotemItems) ? snapshot.lastGoodPublicTotemItems : [];
    return stones.concat(totems, publicItems, legacyStones, legacyTotems);
  }
  function entryHasInventoryRows(data) {
    if (!data || typeof data !== 'object') return false;
    return getFishRows(data).length > 0 || getItemRows(data).length > 0;
  }
  function hasRenderableTrackerData(snapshot) {
    return Boolean(snapshot && (
      hasLeaderstats(snapshot)
      || getFishRows(snapshot).length > 0
      || getItemRows(snapshot).length > 0
    ));
  }
  function shouldReplaceCurrentSnapshot(candidate, current) {
    if (!hasRenderableTrackerData(candidate)) return false;
    if (!current || !hasRenderableTrackerData(current)) return true;
    return isNewerSnapshot(candidate, current);
  }
  function mergePreservedInventorySnapshot(previous, incoming) {
    if (!incoming || typeof incoming !== 'object') return previous || null;
    if (!previous) return incoming;
    if (entryHasInventoryRows(incoming)) {
      const merged = { ...previous, ...incoming, provenEmptyInventory: false };
      if (entryHasInventoryRows(previous) && !shouldReplaceCurrentSnapshot(incoming, previous)) {
        for (let i = 0; i < INVENTORY_PRESERVE_KEYS.length; i += 1) {
          const key = INVENTORY_PRESERVE_KEYS[i];
          if (previous[key] != null) merged[key] = previous[key];
        }
      }
      return merged;
    }
    if (incoming.provenEmptyInventory === true && !entryHasInventoryRows(previous)) {
      return { ...previous, ...incoming };
    }
    const merged = { ...previous, ...incoming };
    if (entryHasInventoryRows(previous) && !entryHasInventoryRows(incoming)) {
      for (let i = 0; i < INVENTORY_PRESERVE_KEYS.length; i += 1) {
        const key = INVENTORY_PRESERVE_KEYS[i];
        if (previous[key] != null) merged[key] = previous[key];
      }
      merged.provenEmptyInventory = false;
      if (previous.lastInventoryAt) merged.lastInventoryAt = previous.lastInventoryAt;
      if (previous.lastSnapshotUploadAt) merged.lastSnapshotUploadAt = previous.lastSnapshotUploadAt;
      if (!hasLeaderstats(incoming)) {
        for (let j = 0; j < STATS_PRESERVE_KEYS.length; j += 1) {
          const skey = STATS_PRESERVE_KEYS[j];
          if (previous[skey] != null) merged[skey] = previous[skey];
        }
      }
      if (previous.snapshotComplete === true && incoming.snapshotComplete !== true) {
        merged.snapshotComplete = true;
      }
      if (previous.inventoryReady === true && incoming.inventoryReady !== true) {
        merged.inventoryReady = true;
      }
      if (previous.inventoryDisplayState === 'ready' && incoming.inventoryDisplayState !== 'ready') {
        merged.inventoryDisplayState = previous.inventoryDisplayState;
      }
      return merged;
    }
    if (hasRenderableTrackerData(previous) && !shouldReplaceCurrentSnapshot(incoming, previous)) {
      for (let i = 0; i < INVENTORY_PRESERVE_KEYS.length; i += 1) {
        const key = INVENTORY_PRESERVE_KEYS[i];
        if (previous[key] != null) merged[key] = previous[key];
      }
      if (!hasLeaderstats(incoming)) {
        for (let j = 0; j < STATS_PRESERVE_KEYS.length; j += 1) {
          const skey = STATS_PRESERVE_KEYS[j];
          if (previous[skey] != null) merged[skey] = previous[skey];
        }
      }
      if (!isNewerSnapshot(incoming, previous)) {
        if (previous.lastInventoryAt) merged.lastInventoryAt = previous.lastInventoryAt;
        if (previous.lastSnapshotUploadAt) merged.lastSnapshotUploadAt = previous.lastSnapshotUploadAt;
        if (previous.playerStatsUpdatedAt) merged.playerStatsUpdatedAt = previous.playerStatsUpdatedAt;
        if (previous.lastStatsUploadAt) merged.lastStatsUploadAt = previous.lastStatsUploadAt;
      }
      if (previous.snapshotComplete === true && incoming.snapshotComplete !== true) {
        merged.snapshotComplete = true;
      }
      if (previous.inventoryReady === true && incoming.inventoryReady !== true) {
        merged.inventoryReady = true;
      }
      if (previous.inventoryDisplayState === 'ready' && incoming.inventoryDisplayState !== 'ready') {
        merged.inventoryDisplayState = previous.inventoryDisplayState;
      }
      return merged;
    }
    return merged;
  }
  function resolveEntryPublicSnapshot(entry, dataOverride) {
    const data = dataOverride || (entry && entry.lastData) || null;
    if (entryHasInventoryRows(data)) return data;
    const snap = entry && entry.liveSnapshot;
    const fish = (snap && snap.fishList) || (entry && entry.lastFishList) || [];
    const stones = (snap && snap.stoneList) || (entry && entry.lastStoneList) || [];
    if (!fish.length && !stones.length) return data;
    return {
      ...(data || {}),
      fishItems: fish.length ? fish : (data && data.fishItems),
      stoneItems: stones.length ? stones : (data && data.stoneItems),
    };
  }
  function refreshLiveSnapshotInventoryFromEntry(entry, data) {
    if (!entry) return;
    const snapData = resolveEntryPublicSnapshot(entry, data);
    const fishList = getPublicFishItems(snapData);
    const stoneList = getPublicStoneItems(snapData);
    if (!fishList.length && !stoneList.length) return;
    const pollAt = new Date().toISOString();
    if (!entry.liveSnapshot) {
      entry.liveSnapshot = {
        pollAt,
        pollCount: 0,
        payloadAt: (snapData && (snapData.lastInventoryAt || snapData.updatedAt)) || pollAt,
        playerStats: extractPlayerStatsFromPayload(snapData),
        fishList,
        stoneList,
        fishCount: fishList.length,
        stoneCount: stoneList.length,
      };
    } else {
      entry.liveSnapshot.fishList = fishList;
      entry.liveSnapshot.stoneList = stoneList;
      entry.liveSnapshot.fishCount = fishList.length;
      entry.liveSnapshot.stoneCount = stoneList.length;
    }
    syncEntryFromLiveSnapshot(entry);
  }
  function statsSnapshotReady(data) {
    if (!data) return false;
    if (data.hasLeaderstatsSnapshot === true) return true;
    if (data.snapshotComplete === true) return true;
    if (data.inventoryReady === true) return true;
    if (data.statsProven === true) return true;
    if (data.liveAccountStats && !data.liveAccountStats.emptyReason) return true;
    if (data.playerStatsProven === true && data.playerStats) return true;
    if (data.lastStatsUploadAt || data.playerStatsUpdatedAt) return true;
    if (data.lastInventoryAt && (data.lastGoodPublicFishCount > 0 || data.fishItems?.length)) return true;
    return false;
  }
  function inventoryUploadGraceSeconds(intervalSeconds) {
    const interval = Number(intervalSeconds) > 0 ? Number(intervalSeconds) : DEFAULT_UPLOAD_INTERVAL_SEC;
    return Math.max(20, Math.ceil(interval * 1.5));
  }
  function inventoryUploadStaleAfterSeconds(intervalSeconds) {
    const interval = Number(intervalSeconds) > 0 ? Number(intervalSeconds) : DEFAULT_UPLOAD_INTERVAL_SEC;
    return Math.max(interval + inventoryUploadGraceSeconds(interval), interval * 2.5);
  }
  var ACCOUNT_PRESENCE_GRACE_MS = 150 * 1000;
  function presenceContactMsFrom(src) {
    if (!src || typeof src !== 'object') return 0;
    var ts = src.statusLastSuccessAt || src.lastAccountSeenAt || src.lastSuccessfulHeartbeatAt
      || src.lastHeartbeatAt || src.serverReceivedAt || src.lastSeenAt || null;
    if (!ts) return 0;
    var ms = new Date(ts).getTime();
    return Number.isFinite(ms) ? ms : 0;
  }
  function reconcileEntryPresence(entry, incoming, serverNowMs) {
    if (!entry || !incoming || typeof incoming !== 'object') return;
    if (typeof incoming.accountPresenceLive !== 'boolean') return;
    var incomingLive = incoming.accountPresenceLive;
    var incomingMs = presenceContactMsFrom(incoming);
    var prevMs = Number(entry._presenceContactMs) || 0;
    var nowMs = Number(serverNowMs) || Date.now();
    var serverHardRed = incoming.accountPresenceReason === 'client_offline'
      || incoming.accountStatusReason === 'client_offline'
      || incoming.accountPresenceReason === 'outdated_loader'
      || incoming.accountPresenceStatus === 'error';
    var withinGrace = incomingMs > 0 && (nowMs - incomingMs) < ACCOUNT_PRESENCE_GRACE_MS;
    if (incomingLive === true) {
      entry._presenceLive = true;
      entry._presenceContactMs = Math.max(prevMs, incomingMs);
    } else if (withinGrace && !serverHardRed) {
      entry._presenceLive = true;
      entry._presenceContactMs = Math.max(prevMs, incomingMs);
    } else if (incomingMs >= prevMs) {
      entry._presenceLive = false;
      entry._presenceContactMs = Math.max(prevMs, incomingMs);
    }
    var live = entry._presenceLive === true;
    if (entry.uploadStatus && typeof entry.uploadStatus === 'object') entry.uploadStatus.accountPresenceLive = live;
    if (entry.lastData && typeof entry.lastData === 'object') entry.lastData.accountPresenceLive = live;
  }
  function isAccountPresent(entry) {
    if (entry && entry._auth && typeof entry._auth.isOnline === 'boolean') return entry._auth.isOnline;
    if (entry && typeof entry._presenceLive === 'boolean') return entry._presenceLive;
    const st = entryUploadStatus(entry);
    if (st && typeof st.accountPresenceLive === 'boolean') return st.accountPresenceLive;
    const data = entry && entry.lastData;
    if (data && typeof data.accountPresenceLive === 'boolean') return data.accountPresenceLive;
    return false;
  }
  function isEntryStatusGreen(entry) {
    return isTrackerAccountOnline(entry, Date.now());
  }
  function trackerPresenceContactMs(entry) {
    if (!entry) return 0;
    let best = Number(entry._presenceContactMs) || 0;
    const srcs = [entryUploadStatus(entry), entry.lastData, entry];
    for (const s of srcs) {
      const ms = presenceContactMsFrom(s);
      if (ms > best) best = ms;
    }
    return best;
  }
  function isTrackerAccountOnline(entry, nowMs) {
    if (!entry) return false;
    if (entry._auth && typeof entry._auth.isOnline === 'boolean') return entry._auth.isOnline;
    const now = Number(nowMs) || Date.now();
    const st = entryUploadStatus(entry);
    const data = entry.lastData;
    const liveFlags = [
      entry._presenceLive,
      st && st.accountPresenceLive,
      data && data.accountPresenceLive,
    ];
    if (liveFlags.some((v) => v === true)) return true;
    const hardRed = (st && (st.accountPresenceReason === 'client_offline'
      || st.accountStatusReason === 'client_offline'
      || st.accountPresenceReason === 'outdated_loader'
      || st.accountPresenceStatus === 'error'))
      || (data && data.accountPresenceReason === 'client_offline');
    if (hardRed) return false;
    const contactMs = trackerPresenceContactMs(entry);
    return contactMs > 0 && (now - contactMs) < ACCOUNT_PRESENCE_GRACE_MS;
  }
  function entryStatsUploadTimestamp(entry) {
    if (!entry) return null;
    const data = entry.lastData;
    const stats = getEntryPlayerStats(entry);
    return (data && (data.lastStatsUploadAt || data.playerStatsUpdatedAt))
      || (stats && stats.statsAt)
      || null;
  }
  function entryInventoryUploadTimestamp(entry) {
    const st = entryUploadStatus(entry);
    if (st && st.lastSnapshotUploadAt) return st.lastSnapshotUploadAt;
    const data = entry && entry.lastData;
    if (!data) return null;
    return data.lastSnapshotUploadAt || data.lastInventoryAt || null;
  }
  function entryInventoryRedSince(entry) {
    const st = entryUploadStatus(entry);
    if (st && typeof st.inventoryUploadFresh === 'boolean') {
      if (st.inventoryUploadFresh) return null;
      if (st.inventoryRedSince) return st.inventoryRedSince;
    }
    const data = entry && entry.lastData;
    if (data && data.inventoryUploadFresh === true) return null;
    if (data && data.inventoryRedSince) return data.inventoryRedSince;
    return null;
  }
  function isStatsUploadFresh(entry) {
    const data = entry && entry.lastData;
    if (data && data.statsUploadFresh === true) return true;
    if (data && data.statsUploadFresh === false) return false;
    return syncFreshnessFromTimestamp(entryStatsUploadTimestamp(entry)) === 'live';
  }
  function isInventoryUploadFresh(entry) {
    const st = entryUploadStatus(entry);
    if (st && typeof st.inventoryUploadFresh === 'boolean') return st.inventoryUploadFresh;
    const data = entry && entry.lastData;
    if (data && data.inventoryUploadFresh === true) return true;
    if (data && data.inventoryUploadFresh === false) return false;
    return false;
  }
  function inventoryUploadFreshness(entry) {
    return isInventoryUploadFresh(entry) ? 'live' : 'dead';
  }
  function entryRedSince(entry) {
    return entryInventoryRedSince(entry);
  }
  function statsSyncTimestamp(data) {
    if (!data) return null;
    const fields = [
      data.lastStatsUploadAt,
      data.playerStatsUpdatedAt,
      data.playerStats && data.playerStats.statsAt,
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
  function formatAgeAgo(ms) {
    const totalSecs = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
    if (totalSecs < 60) {
      return `${Math.max(1, totalSecs)}s ago`;
    }
    if (totalSecs < 3600) {
      const m = Math.floor(totalSecs / 60);
      const s = totalSecs % 60;
      return s > 0 ? `${m}m ${s}s ago` : `${m}m ago`;
    }
    if (totalSecs < 86400) {
      const h = Math.floor(totalSecs / 3600);
      const m = Math.floor((totalSecs % 3600) / 60);
      return m > 0 ? `${h}H ${m}m ago` : `${h}H ago`;
    }
    const d = Math.floor(totalSecs / 86400);
    return `${d}D ago`;
  }
  function formatAgeAgoSeconds(secs) {
    if (secs == null || secs === '') return '';
    const n = Number(secs);
    if (!Number.isFinite(n) || n < 0) return '';
    return formatAgeAgo(n * 1000);
  }
  function formatCompactAgeAgoSeconds(secs) {
    if (secs == null || secs === '') return '';
    const t = Math.floor(Number(secs));
    if (!Number.isFinite(t) || t < 0) return '';
    const n = Math.max(1, t);
    if (n < 60) return `${n}s ago`;
    if (n < 3600) return `${Math.floor(n / 60)}m ago`;
    if (n < 86400) return `${Math.floor(n / 3600)}h ago`;
    return `${Math.floor(n / 86400)}d ago`;
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
    return isTrackerAccountOnline(entry, Date.now());
  }
  function tableSyncFreshness(entryOrTimestamp) {
    if (entryOrTimestamp && typeof entryOrTimestamp === 'object' && entryOrTimestamp.displayName != null) {
      return isStatsUploadFresh(entryOrTimestamp) ? 'live' : 'dead';
    }
    return syncFreshnessFromTimestamp(entryOrTimestamp);
  }
  function formatTableSyncAge(timestamp) {
    const label = formatExactSyncAge(timestamp);
    if (!label || label === 'no sync') return 'no sync';
    if (label.endsWith('D')) return `${label.slice(0, -1)}d`;
    return label;
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
    if (!s) return EMPTY_STAT;
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
    if (entry.lastData) {
      const fromData = extractPlayerStatsFromPayload(entry.lastData);
      if (fromData) return { stats: fromData, source: 'lastData.playerStats' };
    }
    return null;
  }
  const TRUSTED_PLAYERSTATS_BUILD_MARKS = ['INVENTORY_SNAPSHOT_NIL_FIX_METADATA_SCAN', 'INSTANCE_MUTATION_WEIGHT_DETAIL', 'METADATA_PROBE_DEEP_SCAN', 'UPLOAD_HTML_530_GATEWAY_DIAG', 'BLOCKER10ZT5', 'BLOCKER10ZT4', 'BLOCKER10ZT3', 'BLOCKER10ZW'];
  function isTrustedPlayerStatsBuild(build) {
    const s = String(build || '');
    if (!s) return false;
    if (s.includes('LOADER_REGISTER_LIMIT_FIX')) return true;
    if (s.includes('TOTEM_UNSTACKED_ROW_AGG')) return true;
    if (s.includes('TOTEM_EVIDENCE_GROUP_MATCH')) return true;
    if (s.includes('TOTEM_MULTI_SOURCE_CLASSIFY')) return true;
    return TRUSTED_PLAYERSTATS_BUILD_MARKS.some((mark) => s.includes(mark));
  }
  function isTrustedPlayerStats(stats) {
    if (!stats || typeof stats !== 'object') return false;
    const build = stats.build || '';
    const source = stats.source || '';
    if (!isTrustedPlayerStatsBuild(build)) return false;
    return source === 'replion' || source === 'leaderstats' || source === 'missing';
  }
  function displayableEntryPlayerStats(stats) {
    if (!stats || typeof stats !== 'object') return null;
    if (stats.__fromApi === true) {
      const hasValues = stats.coins != null || stats.totalCaught != null
        || stats.coinsText || stats.totalCaughtText || stats.rarestFishChance;
      return hasValues ? stats : null;
    }
    if (!isTrustedPlayerStats(stats)) return null;
    if (stats.source === 'missing') return stats;
    const hasValues = stats.coins != null || stats.totalCaught != null
      || stats.coinsText || stats.totalCaughtText || stats.rarestFishChance;
    return hasValues ? stats : null;
  }
  function getEntryPlayerStats(entry) {
    if (!entry) return null;
    if (entry.liveSnapshot && entry.liveSnapshot.playerStats) {
      return entry.liveSnapshot.playerStats;
    }
    if (entry.lastData) {
      const fromData = extractPlayerStatsFromPayload(entry.lastData);
      if (fromData) return fromData;
    }
    return null;
  }
  const HIDE_USERNAME_EYE_SVG = '<svg viewBox="0 0 24 24" focusable="false"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"></path><circle cx="12" cy="12" r="3"></circle></svg>';
  const HIDE_USERNAME_EYE_OFF_SVG = '<svg viewBox="0 0 24 24" focusable="false"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-5 0-9.27-3.11-11-7 1.09-2.28 2.94-4.12 5.18-5.18"></path><path d="M1 1l22 22"></path><path d="M9.88 9.88a3 3 0 0 0 4.24 4.24"></path><path d="M10.73 5.08A10.94 10.94 0 0 1 12 4c5 0 9.27 3.11 11 7a11.8 11.8 0 0 1-2.16 3.19"></path></svg>';
  function normalizePollPlayerStats(raw) {
    if (!raw || typeof raw !== 'object') return null;
    if (raw.__fromApi === true) {
      const out = { ...raw };
      if (out.coins != null && !out.coinsText) out.coinsText = formatCompactStatNumber(out.coins);
      if (out.totalCaught != null && !out.totalCaughtText) out.totalCaughtText = formatGroupedCaughtNumber(out.totalCaught);
      return out;
    }
    const stats = displayableEntryPlayerStats(raw);
    if (!stats) return null;
    const out = { ...stats };
    if (out.coins != null) out.coinsText = formatCompactStatNumber(out.coins);
    if (out.totalCaught != null) out.totalCaughtText = formatGroupedCaughtNumber(out.totalCaught);
    return out;
  }
  function liveAccountStatsToPlayerStats(live, data) {
    if (!live || typeof live !== 'object') return null;
    const hasValues = live.coins != null || live.totalCaught != null
      || live.coinsText || live.coin || live.totalCaughtText
      || live.rarestFish || live.rarestFishChance;
    if (!hasValues) return null;
    return {
      __fromApi: true,
      coins: live.coins != null ? live.coins : null,
      coinsText: live.coinsText || live.coin || null,
      totalCaught: live.totalCaught != null ? live.totalCaught : null,
      totalCaughtText: live.totalCaughtText || null,
      rarestFishChance: live.rarestFish || live.rarestFishChance || null,
      source: live.statsSource || 'leaderstats',
      build: live.trackerBuild || (data && data.trackerBuild) || '',
      statsAt: live.statsAt || live.lastSuccessfulUploadAt || (data && data.playerStatsUpdatedAt) || null,
    };
  }
  function extractPlayerStatsFromPayload(data) {
    if (!data) return null;
    const fromLive = liveAccountStatsToPlayerStats(data.liveAccountStats, data)
      || (data.statsProven === true ? liveAccountStatsToPlayerStats(data, data) : null);
    if (fromLive) return normalizePollPlayerStats(fromLive);
    if (!statsSnapshotReady(data)) return null;
    const raw = data.playerStats;
    if (!raw || typeof raw !== 'object') return null;
    return normalizePollPlayerStats({ ...raw, __fromApi: true });
  }
  function buildLiveSnapshotFromPayload(entry, data, pollAt) {
    const snapData = resolveEntryPublicSnapshot(entry, data);
    const fishList = getPublicFishItems(snapData);
    const stoneList = getPublicStoneItems(snapData);
    const prevCount = entry && entry.liveSnapshot && entry.liveSnapshot.pollCount || 0;
    let playerStats = extractPlayerStatsFromPayload(snapData);
    if (!playerStats && entry && entry.liveSnapshot && entry.liveSnapshot.playerStats) {
      playerStats = entry.liveSnapshot.playerStats;
    }
    return {
      pollAt,
      payloadAt: (snapData && (
        snapData.lastInventoryAt
        || snapData.lastSnapshotUploadAt
        || snapData.playerStatsUpdatedAt
        || snapData.lastStatsUploadAt
        || snapData.updatedAt
      )) || pollAt,
      pollCount: prevCount + 1,
      playerStats,
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
  function entryPresenceTimestamp(entry) {
    return entryStatusSuccessTimestamp(entry);
  }
  function normalizeTotalCaughtValue(stats) {
    const s = displayableEntryPlayerStats(stats);
    if (!s) return null;
    if (s.totalCaught != null && Number.isFinite(Number(s.totalCaught))) {
      return Math.max(0, Math.floor(Number(s.totalCaught)));
    }
    if (s.totalCaughtText) {
      const parsed = Number(String(s.totalCaughtText).replace(/\./g, '').replace(/,/g, ''));
      if (Number.isFinite(parsed)) return Math.max(0, Math.floor(parsed));
    }
    return null;
  }
  function statsActivitySignature(stats) {
    const s = displayableEntryPlayerStats(stats);
    if (!s) return null;
    const caught = s.totalCaught != null ? String(s.totalCaught) : (s.totalCaughtText || '');
    const coins = s.coins != null ? String(s.coins) : (s.coinsText || '');
    const rare = s.rarestFishChance || '';
    return `${caught}|${coins}|${rare}`;
  }
  function touchCaughtActivityState(entry, stats, nowIso) {
    if (!entry) return;
    const sig = statsActivitySignature(stats);
    if (sig == null) return;
    const now = nowIso || new Date().toISOString();
    if (entry._lastStatsSignature == null) {
      entry._lastStatsSignature = sig;
      if (!entry._lastCaughtIncreaseAt) entry._lastCaughtIncreaseAt = now;
      return;
    }
    if (sig !== entry._lastStatsSignature) {
      entry._lastStatsSignature = sig;
      entry._lastCaughtIncreaseAt = now;
    }
  }
  function entryStatsChangeAt(entry) {
    const data = entry && entry.lastData;
    if (data && data.lastStatsChangeAt) return data.lastStatsChangeAt;
    const st = entryUploadStatus(entry);
    if (st && st.lastStatsChangeAt) return st.lastStatsChangeAt;
    return entry && entry._lastCaughtIncreaseAt ? entry._lastCaughtIncreaseAt : null;
  }
  function formatPresenceDurationLabel(secs) {
    if (secs == null) return '';
    if (secs < 60) return `${Math.max(1, secs)}s`;
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
  function markEntryFrontendRefreshed(entry) {
    if (!entry) return;
    entry._frontendRefreshAt = Date.now();
  }
  function getEntryFrontendRefreshAgeMs(entry) {
    if (!entry || entry._frontendRefreshAt == null) return null;
    return Math.max(0, Date.now() - entry._frontendRefreshAt);
  }
  function formatFrontendRefreshAgeText(entry) {
    const ageMs = getEntryFrontendRefreshAgeMs(entry);
    if (ageMs == null) return '';
    return formatAgeAgoSeconds(Math.max(1, Math.floor(ageMs / 1000)));
  }
  function markEntryLeaderstatsRefreshed(entry) {
    if (!entry) return;
    entry._leaderstatsFrontendRefreshAt = Date.now();
  }
  function getEntryLeaderstatsRefreshAgeMs(entry) {
    if (!entry || entry._leaderstatsFrontendRefreshAt == null) return null;
    return Math.max(0, Date.now() - entry._leaderstatsFrontendRefreshAt);
  }
  function formatLeaderstatsRefreshAgeText(entry) {
    const ageMs = getEntryLeaderstatsRefreshAgeMs(entry);
    if (ageMs == null) return '';
    return formatAgeAgoSeconds(Math.max(1, Math.floor(ageMs / 1000)));
  }
  function markEntryInventoryRefreshed(entry) {
    if (!entry) return;
    entry._inventoryFrontendRefreshAt = Date.now();
  }
  function getEntryInventoryRefreshAgeMs(entry) {
    if (!entry || entry._inventoryFrontendRefreshAt == null) return null;
    return Math.max(0, Date.now() - entry._inventoryFrontendRefreshAt);
  }
  function formatInventoryRefreshAgeText(entry) {
    const ageMs = getEntryInventoryRefreshAgeMs(entry);
    if (ageMs == null) return '';
    return formatAgeAgoSeconds(Math.max(1, Math.floor(ageMs / 1000)));
  }
  function stableStringify(value) {
    if (value === null || typeof value !== 'object') return JSON.stringify(value);
    if (Array.isArray(value)) return '[' + value.map(stableStringify).join(',') + ']';
    const keys = Object.keys(value).sort();
    return '{' + keys.map((k) => JSON.stringify(k) + ':' + stableStringify(value[k])).join(',') + '}';
  }
  function fishRowSignature(row) {
    const instances = Array.isArray(row && row.ownedInstances) ? row.ownedInstances : [];
    return {
      name: normalizeToken((row && (row.cleanName || row.baseFishName || row.name)) || ''),
      mutation: normalizeToken((row && (row.mutation || row.mutationName)) || ''),
      amount: Math.max(0, Math.floor(Number(resolveItemAmount(row)) || 0)),
      instances: instances.length,
    };
  }
  function buildLeaderstatsSignature(entry) {
    const stats = getEntryPlayerStats(entry);
    const data = entry && entry.lastData;
    return stableStringify({
      coins: normalizeToken(displayCoinsStat(stats, data, entry)),
      totalCaught: normalizeToken(displayTotalCaughtStat(stats, data, entry)),
      rarest: normalizeToken(displayRarestFishStat(stats, data, entry)),
    });
  }
  function buildInventorySignature(data) {
    if (!data || typeof data !== 'object') return stableStringify(null);
    return stableStringify({
      fish: (getPublicFishItems(data) || []).map(fishRowSignature),
      stones: (getPublicStoneItems(data) || []).map(fishRowSignature),
      totems: (getPublicTotemItems(data) || []).map(fishRowSignature),
      ruby: getRubyGemstoneTopCardCount(data),
    });
  }
  function buildDisplayedDatasetSignature(entry) {
    const data = entry && entry.lastData;
    return stableStringify({
      username: normalizeToken((data && data.username) || (entry && entry.displayName) || ''),
      leaderstats: buildLeaderstatsSignature(entry),
      inventory: buildInventorySignature(data),
    });
  }
  function maybeResetSectionTimers(_entry) {  }
  function authLaneTimestamp(entry, lane) {
    const auth = entry && entry._auth;
    if (!auth) return null;
    if (lane === 'status') return auth.lastRealStatusAt || null;
    if (lane === 'leaderstats') return auth.lastRealLeaderstatsAt || null;
    if (lane === 'inventory') return auth.lastRealInventoryAt || null;
    return null;
  }
  function authLaneRevision(entry, lane) {
    const auth = entry && entry._auth;
    if (!auth) return null;
    if (lane === 'status') return auth.statusRevision;
    if (lane === 'leaderstats') return auth.leaderstatsRevision;
    if (lane === 'inventory') return auth.inventoryRevision;
    return null;
  }
  function laneTimestampAdvanced(prevAuth, nextAuth, lane) {
    if (!nextAuth) return false;
    const nextTs = lane === 'status' ? nextAuth.lastRealStatusAt
      : lane === 'leaderstats' ? nextAuth.lastRealLeaderstatsAt
      : nextAuth.lastRealInventoryAt;
    if (!nextTs) return false;
    const prevTs = prevAuth && (lane === 'status' ? prevAuth.lastRealStatusAt
      : lane === 'leaderstats' ? prevAuth.lastRealLeaderstatsAt
      : prevAuth.lastRealInventoryAt);
    if (!prevTs) return true;
    const nextMs = Date.parse(nextTs);
    const prevMs = Date.parse(prevTs);
    if (Number.isFinite(nextMs) && Number.isFinite(prevMs) && nextMs > prevMs) return true;
    const nextRev = lane === 'status' ? nextAuth.statusRevision
      : lane === 'leaderstats' ? nextAuth.leaderstatsRevision
      : nextAuth.inventoryRevision;
    const prevRev = prevAuth && (lane === 'status' ? prevAuth.statusRevision
      : lane === 'leaderstats' ? prevAuth.leaderstatsRevision
      : prevAuth.inventoryRevision);
    return nextRev != null && prevRev != null && nextRev > prevRev;
  }
  function authAgeSecondsFromTs(ts) {
    if (!ts) return null;
    const ms = Date.parse(ts);
    if (!Number.isFinite(ms)) return null;
    return Math.max(0, Math.floor((Date.now() - ms) / 1000));
  }
  function backendPresenceAgeSeconds(entry) {
    if (entry && entry._auth && entry._auth.lastRealStatusAt) {
      const a = authAgeSecondsFromTs(entry._auth.lastRealStatusAt);
      if (a != null) return a;
    }
    const secs = liveSecondsSinceStatusSuccess(entry);
    if (secs != null) return secs;
    return syncAgeSeconds(entryStatusSuccessTimestamp(entry));
  }
  function backendStatsAgeSeconds(entry) {
    if (entry && entry._auth && entry._auth.lastRealLeaderstatsAt) {
      const a = authAgeSecondsFromTs(entry._auth.lastRealLeaderstatsAt);
      if (a != null) return a;
    }
    return liveSecondsSinceStatsSuccess(entry);
  }
  function backendInventoryAgeSeconds(entry) {
    if (entry && entry._auth && entry._auth.lastRealInventoryAt) {
      const a = authAgeSecondsFromTs(entry._auth.lastRealInventoryAt);
      if (a != null) return a;
    }
    return liveSecondsSinceInventorySuccess(entry);
  }
  function formatBackendAgeText(ageSeconds) {
    if (ageSeconds == null || !Number.isFinite(ageSeconds)) return '';
    return formatAgeAgoSeconds(Math.max(1, Math.floor(ageSeconds)));
  }
  function formatPresenceStatusText(entry) {
    return formatBackendAgeText(backendPresenceAgeSeconds(entry));
  }
  function formatStatsUploadDurationText(entry) {
    return formatBackendAgeText(backendStatsAgeSeconds(entry));
  }
  function formatCaughtActivitySub(entry) {
    return formatStatsUploadDurationText(entry);
  }
  function seedTimersFromBackend(_entry) {  }
  function seedOfflineTimersFromBackend(_entry) {  }
  function formatMinimalSyncDuration(timestamp) {
    const label = formatTableSyncAge(timestamp);
    if (!label || label === 'no sync') return '';
    if (label.endsWith('d')) return `${label.slice(0, -1)}D`;
    return label;
  }
  function formatSyncDurationLabel(timestamp) {
    return formatMinimalSyncDuration(timestamp);
  }
  function formatTableSyncStatusText(entry) {
    return formatStatsSyncAgeSub(entry);
  }
  function formatEntrySyncStatusText(entry) {
    if (!entry) return '';
    return formatBackendAgeText(backendInventoryAgeSeconds(entry));
  }
  function formatInventoryUploadLabel(entry) {
    if (!entry) return '';
    return formatCompactAgeAgoSeconds(backendInventoryAgeSeconds(entry));
  }
  function formatEntrySyncStatusLine(entry) {
    return formatEntrySyncStatusText(entry);
  }
  function entryConnectionFreshness(entry) {
    return isTrackerAccountOnline(entry, Date.now()) ? 'live' : 'dead';
  }
  function formatStatsSyncAgeSub(entry) {
    return formatCaughtActivitySub(entry);
  }
  function buildTotalCaughtCellHtml(stats, entry, rowData) {
    const caughtText = displayTotalCaughtStat(stats, rowData, entry);
    const sub = formatCaughtActivitySub(entry);
    return `<span class="accounts-table__stat-stack"><span class="accounts-table__stat-main total-caught-value${caughtText === EMPTY_STAT ? ' is-muted' : ''}">${escHtml(caughtText)}</span><span class="accounts-table__stat-sub" data-stats-sync-sub data-caught-activity-sub>${escHtml(sub)}</span></span>`;
  }
  function buildMobileCaughtCellHtml(stats, entry, rowData) {
    const caughtText = displayTotalCaughtStat(stats, rowData, entry);
    const sub = formatCaughtActivitySub(entry);
    return `<span class="accounts-mobile-card__row-value-stack"><span class="accounts-mobile-card__row-value total-caught-value${caughtText === EMPTY_STAT ? ' is-muted' : ''}">${escHtml(caughtText)}</span><span class="accounts-mobile-card__stat-sub" data-stats-sync-sub data-caught-activity-sub>${escHtml(sub)}</span></span>`;
  }
  function ensureSinglePresenceSyncEl(root) {
    if (!root) return null;
    const allSync = root.querySelectorAll('[data-table-status-sync]');
    if (allSync.length > 1) {
      for (let i = 1; i < allSync.length; i += 1) allSync[i].remove();
    }
    let syncEl = root.querySelector('[data-table-status-sync]');
    if (!syncEl) {
      const statusWrap = root.querySelector('.accounts-status') || root;
      syncEl = document.createElement('span');
      syncEl.className = 'accounts-status__text';
      syncEl.setAttribute('data-table-status-sync', '');
      statusWrap.appendChild(syncEl);
    }
    return syncEl;
  }
  function patchAccountStatusDom(root, entry) {
    if (!root || !entry) return;
    const statusEl = root.querySelector('[data-table-status-dot]');
    const syncEl = ensureSinglePresenceSyncEl(root);
    const fresh = entryConnectionFreshness(entry);
    if (statusEl) {
      const want = fresh === 'live' ? 'live' : 'dead';
      if (!statusEl.classList.contains(want)) {
        statusEl.classList.remove('live', 'stale', 'dead');
        statusEl.classList.add(want);
      }
      const label = fresh === 'live' ? 'Online' : 'Offline';
      if (statusEl.getAttribute('title') !== label) {
        statusEl.setAttribute('title', label);
        statusEl.setAttribute('aria-label', label);
      }
    }
    if (syncEl) {
      const txt = formatPresenceStatusText(entry);
      if (syncEl.textContent !== txt) syncEl.textContent = txt;
      const backendSecs = backendPresenceAgeSeconds(entry);
      if (backendSecs != null) syncEl.setAttribute('data-backend-presence-age', String(backendSecs));
    }
  }
  function ensureSingleStatsSyncSub(root) {
    if (!root) return null;
    const allSubs = root.querySelectorAll('[data-stats-sync-sub]');
    if (allSubs.length > 1) {
      for (let i = 1; i < allSubs.length; i += 1) allSubs[i].remove();
    }
    return root.querySelector('[data-stats-sync-sub]');
  }
  function patchAccountStatsSyncSub(root, entry) {
    if (!root || !entry) return;
    const subEl = ensureSingleStatsSyncSub(root);
    if (!subEl) return;
    subEl.textContent = formatCaughtActivitySub(entry) || '';
    subEl.classList.toggle('is-stale', !isStatsUploadFresh(entry));
  }
  function refreshEntryTableSyncDisplay(entry, key) {
    if (!entry || !key) return;
    if (accountsTableBodyEl) {
      const row = accountsTableBodyEl.querySelector(`[data-account-row-key="${CSS.escape(key)}"]`);
      if (row) {
        patchAccountStatusDom(row, entry);
        patchAccountStatsSyncSub(row, entry);
      }
    }
    if (accountsMobileListEl) {
      const mobile = accountsMobileListEl.querySelector(`[data-account-mobile-key="${CSS.escape(key)}"]`);
      if (mobile) {
        patchAccountStatusDom(mobile.querySelector('.accounts-mobile-card__account'), entry);
        patchAccountStatsSyncSub(mobile, entry);
      }
    }
  }
  function applyAccountStatusStatsToEntry(entry, st) {
    if (!entry || !st) return;
    const stats = liveAccountStatsToPlayerStats(st, entry.lastData);
    if (!stats) return;
    const normalized = normalizePollPlayerStats(stats);
    if (!normalized) return;
    if (!entry.liveSnapshot) {
      entry.liveSnapshot = {
        pollAt: new Date().toISOString(),
        pollCount: 0,
        payloadAt: st.lastSuccessfulUploadAt || null,
        playerStats: normalized,
        fishList: entry.lastFishList || [],
        stoneList: entry.lastStoneList || [],
        fishCount: (entry.lastFishList || []).length,
        stoneCount: (entry.lastStoneList || []).length,
      };
    } else {
      entry.liveSnapshot.playerStats = normalized;
    }
    entry.playerStats = normalized;
  }
  function patchAllVisibleAccountStats() {
    trackers.forEach((entry, key) => {
      if (!accountMatchesStatusFilter(entry)) return;
      if (!accountMatchesSearch(entry)) return;
      patchAccountStatsRow(entry, key);
      refreshEntryTableSyncDisplay(entry, key);
    });
  }
  function patchAccountStatsRow(entry, key) {
    if (!entry || !key) return;
    const stats = getEntryPlayerStats(entry);
    const rowData = entry.lastData || entrySnapshotData(entry) || entry.uploadStatus || null;
    const waitingStats = statsAwaitingInventorySnapshot(rowData, entry);
    const row = accountsTableBodyEl && accountsTableBodyEl.querySelector(`[data-account-row-key="${CSS.escape(key)}"]`);
    if (row) {
      const coinsEl = row.querySelector('.col-coins');
      const caughtEl = row.querySelector('.col-caught');
      const rareEl = row.querySelector('.col-rare');
      const coinsText = displayCoinsStat(stats, rowData, entry);
      const caughtText = displayTotalCaughtStat(stats, rowData, entry);
      const rareText = displayRarestFishStat(stats, rowData, entry);
      if (coinsEl) {
        coinsEl.textContent = coinsText;
        coinsEl.classList.toggle('is-muted', coinsText === '—' || waitingStats);
      }
      if (caughtEl) {
        const mainEl = caughtEl.querySelector('.total-caught-value') || caughtEl;
        mainEl.textContent = caughtText;
        mainEl.classList.toggle('is-muted', caughtText === '—' || waitingStats);
        caughtEl.classList.toggle('is-muted', caughtText === '—' || waitingStats);
        patchAccountStatsSyncSub(row, entry);
      }
      if (rareEl) {
        rareEl.textContent = rareText;
        rareEl.classList.toggle('is-muted', rareText === '—' || waitingStats);
      }
      refreshEntryTableSyncDisplay(entry, key);
    }
    const mobile = accountsMobileListEl && accountsMobileListEl.querySelector(`[data-account-mobile-key="${CSS.escape(key)}"]`);
    if (mobile) {
      const coinsText = displayCoinsStat(stats, rowData, entry);
      const caughtText = displayTotalCaughtStat(stats, rowData, entry);
      const rareText = displayRarestFishStat(stats, rowData, entry);
      const coinEl = mobile.querySelector('[data-col="coin"] .accounts-mobile-card__row-value');
      const caughtEl = mobile.querySelector('[data-col="total-caught"] .total-caught-value');
      const rareEl = mobile.querySelector('[data-col="rarest-fish"] .accounts-mobile-card__row-value');
      if (coinEl) {
        coinEl.textContent = coinsText;
        coinEl.classList.toggle('is-muted', coinsText === '—' || waitingStats);
      }
      if (caughtEl) {
        caughtEl.textContent = caughtText;
        caughtEl.classList.toggle('is-muted', caughtText === '—' || waitingStats);
        patchAccountStatsSyncSub(mobile, entry);
      }
      if (rareEl) {
        rareEl.textContent = rareText;
        rareEl.classList.toggle('is-muted', rareText === '—' || waitingStats);
      }
      refreshEntryTableSyncDisplay(entry, key);
    }
  }
  function applyLiveSnapshotToPublicUi(entry, key, data) {
    const snap = entry.liveSnapshot;
    const invData = resolveEntryPublicSnapshot(entry, data);
    const fishList = getPublicFishItems(invData);
    const stoneList = getPublicStoneItems(invData);
    if (snap && (fishList.length || stoneList.length)) {
      snap.fishList = fishList;
      snap.stoneList = stoneList;
      snap.fishCount = fishList.length;
      snap.stoneCount = stoneList.length;
      syncEntryFromLiveSnapshot(entry);
    }
    const hasInventory = fishList.length > 0 || stoneList.length > 0;
    const hasRenderable = hasRenderableTrackerData(invData || data);
    if (!snap && !hasRenderable) return;
    const live = isAccountPresent(entry);
    const invState = inventoryDisplayState(invData || data);
    if (!live) {
      setCardOffline(entry.el, entry.displayName, invData || data);
    } else if (hasInventory) {
      updateCard(entry.el, invData || data);
    } else if (invState === 'syncing' || invState === 'waiting') {
      setCardRunning(entry.el, entry.displayName, data);
    } else if (invState === 'empty') {
      setCardRunning(entry.el, entry.displayName, data);
    } else {
      setCardRunning(entry.el, entry.displayName, data);
    }
    patchAccountStatsRow(entry, key);
    refreshEntryTableSyncDisplay(entry, key);
    updateInventoryStats();
    if (accountViewMode === 'fish') renderBulkInventory('fish');
    else if (accountViewMode === 'stone') renderBulkInventory('stone');
    else if (accountViewMode === 'account' && key === activeAccountKey && entry.el) {
      const body = entry.el.querySelector('[data-card-body]');
      if (body) patchCardInventory(body, fishList, stoneList);
    }
  }
  function applyInventoryPollPayload(entry, key, data) {
    entry.lastData = mergePreservedInventorySnapshot(entry.lastData, data);
    data = entry.lastData;
    if (data && (data.statusColor || data.currentStatus)) {
      mergeUploadStatusOntoEntry(entry, {
        username: data.username || entry.displayName,
        robloxUserId: data.userId != null ? String(data.userId) : null,
        status: data.status || data.accountOnlineStatus,
        statusColor: data.statusColor || data.currentStatus,
        lastStatus: data.lastStatus || null,
        lastStatusAt: data.lastStatusAt || null,
        redSince: data.redSince || null,
        lastSuccessfulUploadAt: data.lastSuccessfulUploadAt || null,
        secondsSinceLastSuccess: data.secondsSinceLastSuccess,
        uploadIntervalSeconds: data.uploadIntervalSeconds || data.intervalSeconds,
        onlineThresholdSeconds: data.onlineThresholdSeconds,
        offlineThresholdSeconds: data.offlineThresholdSeconds,
        statusDecisionReason: data.statusDecisionReason || data.accountStatusReason,
        accountPresenceLive: typeof data.accountPresenceLive === 'boolean' ? data.accountPresenceLive : undefined,
        uploadWarningReason: data.uploadWarningReason || null,
        inventoryUploadFresh: typeof data.inventoryUploadFresh === 'boolean' ? data.inventoryUploadFresh : undefined,
        inventoryRedSince: data.inventoryRedSince || null,
        lastSnapshotUploadAt: data.lastSnapshotUploadAt || data.lastInventoryAt || null,
        lastStatsChangeAt: data.lastStatsChangeAt || null,
        statusLastSuccessAt: data.statusLastSuccessAt || null,
        leaderstatsLastSuccessAt: data.leaderstatsLastSuccessAt || data.lastStatsUploadAt || null,
        inventoryLastSuccessAt: data.inventoryLastSuccessAt || data.lastSnapshotUploadAt || data.lastInventoryAt || null,
        secondsSinceLastStatusSuccess: data.secondsSinceLastStatusSuccess,
        secondsSinceLastLeaderstatsSuccess: data.secondsSinceLastLeaderstatsSuccess,
        secondsSinceLastInventorySuccess: data.secondsSinceLastInventorySuccess,
        statsUploadFresh: typeof data.statsUploadFresh === 'boolean' ? data.statsUploadFresh : undefined,
        lastStatsUploadAt: data.lastStatsUploadAt || null,
        runId: data.runId,
        uploadSeq: data.uploadSeq,
        trackerBuild: data.trackerBuild,
        loaderBuild: data.loaderBuild,
        serverReceivedAt: data.serverReceivedAt,
        latestPayloadAccepted: data.latestPayloadAccepted,
        rejectReason: data.rejectReason,
        snapshotComplete: data.snapshotComplete === true,
        inventoryReady: data.inventoryReady === true,
        snapshotCompletenessReason: data.snapshotCompletenessReason || data.accountStatusReason,
        hasLeaderstatsSnapshot: data.hasLeaderstatsSnapshot === true,
        inventoryDisplayState: data.inventoryDisplayState || null,
        blankPayloadRejected: data.blankPayloadRejected === true,
        provenEmptyInventory: data.provenEmptyInventory === true,
      }, data.serverNow || null);
    }
    reconcileEntryPresence(entry, data, data && data.serverNow ? new Date(data.serverNow).getTime() : Date.now());
    const pollAt = new Date().toISOString();
    entry.lastPollOkAt = pollAt;
    entry.lastSyncAt = pollAt;
    entry.lastStatsPollAt = pollAt;
    entry.lastServerSyncAt = data ? bestSyncTimestamp(data) : null;
    entry.liveSnapshot = buildLiveSnapshotFromPayload(entry, data, pollAt);
    syncEntryFromLiveSnapshot(entry);
    touchCaughtActivityState(entry, getEntryPlayerStats(entry), pollAt);
    captureEntrySync(entry, data);
    applyLiveSnapshotToPublicUi(entry, key, data);
    maybeResetSectionTimers(entry);
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
    if (DEBUG_INVENTORY && isAccountPresent(entry) && !getEntryPlayerStats(entry)) {
      console.debug('[fishit] missing account stat fields', {
        username: entry.displayName,
        statsEmptyReason: data.statsEmptyReason || (data.liveAccountStats && data.liveAccountStats.emptyReason),
        hasPlayerStats: !!data.playerStats,
        playerStatsProven: data.playerStatsProven,
        snapshotComplete: data.snapshotComplete,
        hasLeaderstatsSnapshot: data.hasLeaderstatsSnapshot,
      });
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
  function statsAwaitingInventorySnapshot(data, entry) {
    if (!data) return false;
    if (data.snapshotComplete === true || data.inventoryReady === true) return false;
    if (data.statsProven === true || data.playerStatsProven === true) return false;
    if (statsSnapshotReady(data)) return false;
    if (data.inventoryDisplayState === 'ready' || data.inventoryDisplayState === 'empty') return false;
    if (data.statsEmptyReason === 'awaiting_inventory_snapshot') return true;
    if (data.liveAccountStats && data.liveAccountStats.emptyReason === 'awaiting_inventory_snapshot') return true;
    if (entry && isAccountPresent(entry) && !statsSnapshotReady(data)) return true;
    if (data.inventoryDisplayState === 'syncing') return true;
    return !!(data.lastSuccessfulHeartbeatAt || data.lastHeartbeatAt);
  }
  function displayCoinsStat(stats, data, entry) {
    const s = displayableEntryPlayerStats(stats);
    if (!s) return EMPTY_STAT;
    if (s.source === 'missing' && s.coins == null && !s.coinsText) return EMPTY_STAT;
    if (s.coins != null) {
      const compact = formatCompactStatNumber(s.coins);
      if (compact != null) return compact;
    }
    if (s.coinsText) return s.coinsText;
    return EMPTY_STAT;
  }
  function displayTotalCaughtStat(stats, data, entry) {
    const s = displayableEntryPlayerStats(stats);
    if (!s) return EMPTY_STAT;
    if (s.source === 'missing' && s.totalCaught == null && !s.totalCaughtText) return EMPTY_STAT;
    if (s.totalCaught != null) {
      const grouped = formatGroupedCaughtNumber(s.totalCaught);
      if (grouped != null) return grouped;
    }
    if (s.totalCaughtText) return s.totalCaughtText;
    return EMPTY_STAT;
  }
  function displayRarestFishStat(stats, data, entry) {
    const s = displayableEntryPlayerStats(stats);
    if (!s || !s.rarestFishChance) return EMPTY_STAT;
    if (s.source === 'missing' && !s.rarestFishChance) return EMPTY_STAT;
    return String(s.rarestFishChance);
  }
  function formatStatsAgeSub(stats) {
    const ts = stats && (stats.statsAt || null);
    if (!ts) return '';
    const secs = syncAgeSeconds(ts);
    return secs == null ? '' : formatAgeAgo(secs * 1000);
  }
  function displayTableUsername(entry) {
    if (!entry) return EMPTY_STAT;
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
    const dotClass = freshness === 'live' ? 'live' : 'dead';
    const syncText = formatPresenceStatusText(entry);
    return `<span class="accounts-status"><span class="status-dot ${dotClass}" data-table-status-dot aria-hidden="true"></span><span class="accounts-status__text" data-table-status-sync>${escHtml(syncText)}</span></span>`;
  }
  function buildAccountMobileCardHtml(row) {
    const { key, entry } = row;
    const stats = getEntryPlayerStats(entry);
    const rowData = entry.lastData || entrySnapshotData(entry) || null;
    const inventoryLabel = `Open inventory for ${entry.displayName}`;
    const username = displayTableUsername(entry);
    return `<article class="accounts-mobile-card" data-account-mobile-key="${escHtml(key)}">
  <div class="accounts-mobile-card__top">
    <div class="accounts-mobile-card__account">
      ${buildAccountStatusHtml(entry)}
      <span class="accounts-mobile-card__username" title="${hideUsernames ? 'Username hidden' : escHtml(entry.displayName)}">${escHtml(username)}</span>
    </div>
    <div class="accounts-mobile-card__actions">
      <button type="button" class="accounts-table__icon-btn" data-open-backpack="${escHtml(key)}" aria-label="${escHtml(inventoryLabel)}" title="${escHtml(inventoryLabel)}">${BACKPACK_NAV_ICON}</button>
    </div>
  </div>
  <div class="accounts-mobile-card__grid accounts-mobile-card__grid--stats">
    <div class="accounts-mobile-card__row col-coin" data-col="coin"><span class="accounts-mobile-card__row-label">Coin</span><span class="accounts-mobile-card__row-value coin-value${displayCoinsStat(stats, rowData, entry) === EMPTY_STAT ? ' is-muted' : ''}">${escHtml(displayCoinsStat(stats, rowData, entry))}</span></div>
    <div class="accounts-mobile-card__row col-total-caught" data-col="total-caught"><span class="accounts-mobile-card__row-label">Caught</span>${buildMobileCaughtCellHtml(stats, entry, rowData)}</div>
    <div class="accounts-mobile-card__row col-rarest-fish" data-col="rarest-fish"><span class="accounts-mobile-card__row-label">Rare</span><span class="accounts-mobile-card__row-value rarest-fish-value${displayRarestFishStat(stats, rowData, entry) === EMPTY_STAT ? ' is-muted' : ''}">${escHtml(displayRarestFishStat(stats, rowData, entry))}</span></div>
  </div>
</article>`;
  }
  function buildAccountRowHtml(row, index) {
    const { key, entry } = row;
    const stats = getEntryPlayerStats(entry);
    const rowData = entry.lastData || entrySnapshotData(entry) || null;
    const inventoryLabel = `Open inventory for ${entry.displayName}`;
    return `<tr data-account-row-key="${escHtml(key)}">
  <td class="accounts-table__index col-index">${index + 1}</td>
  <td class="accounts-table__status col-status">${buildAccountStatusHtml(entry)}</td>
  <td class="accounts-table__username col-username" title="${hideUsernames ? 'Username hidden' : escHtml(entry.displayName)}">${escHtml(displayTableUsername(entry))}</td>
  <td class="accounts-table__stat col-coins col-coin coin-value${displayCoinsStat(stats, rowData, entry) === EMPTY_STAT ? ' is-muted' : ''}" data-col="coin">${escHtml(displayCoinsStat(stats, rowData, entry))}</td>
  <td class="accounts-table__stat col-caught col-total-caught${displayTotalCaughtStat(stats, rowData, entry) === EMPTY_STAT ? ' is-muted' : ''}" data-col="total-caught">${buildTotalCaughtCellHtml(stats, entry, rowData)}</td>
  <td class="accounts-table__stat col-rare col-rarest-fish rarest-fish-value${displayRarestFishStat(stats, rowData, entry) === EMPTY_STAT ? ' is-muted' : ''}" data-col="rarest-fish">${escHtml(displayRarestFishStat(stats, rowData, entry))}</td>
  <td class="col-backpack"><button type="button" class="accounts-table__icon-btn" data-open-backpack="${escHtml(key)}" aria-label="${escHtml(inventoryLabel)}" title="${escHtml(inventoryLabel)}">${BACKPACK_NAV_ICON}</button></td>
  <td class="col-actions"><button type="button" class="accounts-table__icon-btn accounts-table__icon-btn--danger" data-remove-account="${escHtml(key)}" aria-label="Remove ${escHtml(entry.displayName)}" title="Remove account"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16" aria-hidden="true"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path></svg></button></td>
</tr>`;
  }
  function accountsTotalPages(totalRows) {
    return Math.max(1, Math.ceil((totalRows || 0) / accountsPageSize));
  }
  function clampAccountsPage(totalRows) {
    const totalPages = accountsTotalPages(totalRows);
    if (accountsCurrentPage > totalPages) accountsCurrentPage = totalPages;
    if (accountsCurrentPage < 1) accountsCurrentPage = 1;
    return totalPages;
  }
  function renderAccountsPagination(totalRows, startIndex, pageCount) {
    if (!accountsPaginationEl) return;
    const totalPages = accountsTotalPages(totalRows);
    accountsPaginationEl.hidden = totalRows <= 0;
    if (accountsPaginationRangeEl) {
      if (totalRows <= 0) {
        accountsPaginationRangeEl.textContent = 'Showing 0 of 0 players';
      } else {
        const from = startIndex + 1;
        const to = startIndex + pageCount;
        accountsPaginationRangeEl.textContent = 'Showing ' + from + '\u2013' + to + ' of ' + totalRows + ' players';
      }
    }
    if (accountsPaginationPageEl) {
      accountsPaginationPageEl.textContent = 'Page ' + accountsCurrentPage + ' of ' + totalPages;
    }
    if (accountsPageSizeBtn) accountsPageSizeBtn.textContent = accountsPageSize + ' / page';
    const onFirst = accountsCurrentPage <= 1;
    const onLast = accountsCurrentPage >= totalPages;
    if (accountsPageFirstBtn) accountsPageFirstBtn.disabled = onFirst;
    if (accountsPagePrevBtn) accountsPagePrevBtn.disabled = onFirst;
    if (accountsPageNextBtn) accountsPageNextBtn.disabled = onLast;
    if (accountsPageLastBtn) accountsPageLastBtn.disabled = onLast;
  }
  function renderAccountsTable() {
    const rows = getFilteredAccountEntries();
    const totalRows = rows.length;
    clampAccountsPage(totalRows);
    const startIndex = (accountsCurrentPage - 1) * accountsPageSize;
    const pageRows = rows.slice(startIndex, startIndex + accountsPageSize);
    if (accountsTableBodyEl) {
      if (!totalRows) {
        accountsTableBodyEl.innerHTML = '<tr><td colspan="8" class="accounts-table__empty">No matching accounts yet.</td></tr>';
      } else {
        accountsTableBodyEl.innerHTML = pageRows.map((row, idx) => buildAccountRowHtml(row, startIndex + idx)).join('');
      }
    }
    if (accountsMobileListEl) {
      if (!totalRows) {
        accountsMobileListEl.innerHTML = '<div class="accounts-mobile-card__empty">No matching accounts yet.</div>';
      } else {
        accountsMobileListEl.innerHTML = pageRows.map((row) => buildAccountMobileCardHtml(row)).join('');
      }
    }
    renderAccountsPagination(totalRows, startIndex, pageRows.length);
    pageRows.forEach(({ key, entry }) => refreshEntryTableSyncDisplay(entry, key));
    updateInventoryUploadIndicator();
  }
  function goToAccountsPage(page) {
    const totalRows = getFilteredAccountEntries().length;
    const totalPages = accountsTotalPages(totalRows);
    let next = page;
    if (next === 'first') next = 1;
    else if (next === 'last') next = totalPages;
    else if (next === 'prev') next = accountsCurrentPage - 1;
    else if (next === 'next') next = accountsCurrentPage + 1;
    next = Math.min(totalPages, Math.max(1, Number(next) || 1));
    if (next === accountsCurrentPage) return;
    accountsCurrentPage = next;
    renderAccountsTable();
  }
  function setAccountsPageSize(size) {
    const n = Number(size);
    if (!PAGE_SIZE_OPTIONS.includes(n)) return;
    const firstVisible = (accountsCurrentPage - 1) * accountsPageSize;
    accountsPageSize = n;
    accountsCurrentPage = Math.floor(firstVisible / accountsPageSize) + 1;
    renderAccountsTable();
  }
  function openPageSizeModal() {
    if (!pageSizeModalEl) return;
    pageSizeModalEl.querySelectorAll('[data-page-size]').forEach((btn) => {
      btn.classList.toggle('is-active', Number(btn.getAttribute('data-page-size')) === accountsPageSize);
    });
    pageSizeModalEl.hidden = false;
    pageSizeModalEl.setAttribute('aria-hidden', 'false');
  }
  function closePageSizeModal() {
    if (!pageSizeModalEl) return;
    pageSizeModalEl.hidden = true;
    pageSizeModalEl.setAttribute('aria-hidden', 'true');
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
        const snapData = resolveEntryPublicSnapshot(entry);
        if (snapData) {
          const body = entry.el.querySelector('[data-card-body]');
          if (body) {
            patchCardInventory(
              body,
              getPublicFishItems(snapData),
              getPublicStoneItems(snapData),
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
    clearInlineDetailState('set-view-mode:' + String(mode));
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
    refetchAllAccountStatus(true);
    renderAccountsTable();
  }
  function refetchAllAccountStatus(forceFresh) {
    pollAccountStatuses(forceFresh === true);
    trackers.forEach((_, key) => pollUser(key, { forceFresh: forceFresh === true }));
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
        accountsCurrentPage = 1;
        renderAccountsTable();
      });
    }
    document.querySelectorAll('[data-account-filter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        accountStatusFilter = btn.getAttribute('data-account-filter') || 'all';
        document.querySelectorAll('[data-account-filter]').forEach((el) => {
          el.classList.toggle('is-active', el.getAttribute('data-account-filter') === accountStatusFilter);
        });
        accountsCurrentPage = 1;
        renderAccountsTable();
      });
    });
    if (accountsPageFirstBtn) accountsPageFirstBtn.addEventListener('click', () => goToAccountsPage('first'));
    if (accountsPagePrevBtn) accountsPagePrevBtn.addEventListener('click', () => goToAccountsPage('prev'));
    if (accountsPageNextBtn) accountsPageNextBtn.addEventListener('click', () => goToAccountsPage('next'));
    if (accountsPageLastBtn) accountsPageLastBtn.addEventListener('click', () => goToAccountsPage('last'));
    if (accountsPageSizeBtn) accountsPageSizeBtn.addEventListener('click', openPageSizeModal);
    if (pageSizeCancelBtn) pageSizeCancelBtn.addEventListener('click', closePageSizeModal);
    if (pageSizeModalEl) {
      pageSizeModalEl.addEventListener('click', (e) => {
        if (e.target === pageSizeModalEl) { closePageSizeModal(); return; }
        const opt = e.target.closest('[data-page-size]');
        if (opt) {
          setAccountsPageSize(opt.getAttribute('data-page-size'));
          closePageSizeModal();
        }
      });
    }
    if (viewTableBtn) viewTableBtn.addEventListener('click', () => setAccountViewMode('table'));
    if (viewFishGridBtn) viewFishGridBtn.addEventListener('click', () => setAccountViewMode('fish'));
    if (viewStoneGridBtn) viewStoneGridBtn.addEventListener('click', () => setAccountViewMode('stone'));
    if (refreshAccountsBtn) refreshAccountsBtn.addEventListener('click', refreshAllAccounts);
    if (copyUsernamesBtn) copyUsernamesBtn.addEventListener('click', copyAllUsernames);
    function handleAccountsActionClick(e) {
      const backpackBtn = e.target.closest('[data-open-backpack]');
      if (backpackBtn) {
        e.preventDefault();
        openAccountInventory(backpackBtn.getAttribute('data-open-backpack'));
        return;
      }
      const removeBtn = e.target.closest('[data-remove-account]');
      if (removeBtn) {
        e.preventDefault();
        removeTracker(removeBtn.getAttribute('data-remove-account'));
      }
    }
    if (accountsOverviewEl) accountsOverviewEl.addEventListener('click', handleAccountsActionClick);
    setAccountViewMode('table');
  }
  const FT_MUTATION_COLORS = {
    gold: '#fbbf24', golden: '#fbbf24',
    albino: '#ffffff',
    gemstone: '#34d399',
    stone: '#c8a06b',
    sandy: '#e3c879', sand: '#e3c879',
    ruby: '#f87171', diamond: '#7dd3fc', emerald: '#34d399', sapphire: '#60a5fa',
    shiny: '#fde68a', glowing: '#fde68a', radiant: '#fde68a',
    big: '#a3e635', 'big shiny': '#d9f99d',
    ghost: '#d8d8ff', spectral: '#d8d8ff',
    holographic: '#a5f3fc', holo: '#a5f3fc',
    darkened: '#7c83a3',
    electric: '#38bdf8', shocked: '#38bdf8',
    mythic: '#c084fc', mythical: '#c084fc',
    celestial: '#93c5fd', heavenly: '#93c5fd',
    frozen: '#67e8f9', ice: '#67e8f9', icy: '#67e8f9', glacial: '#67e8f9',
    corrupt: '#c084fc', corrupted: '#c084fc', void: '#c084fc', dark: '#c084fc',
    rainbow: '#f0abfc', galaxy: '#a78bfa', cosmic: '#a78bfa',
    fire: '#fb923c', molten: '#fb923c', lava: '#fb923c',
    normal: '#94a3b8',
  };
  function ftMutationHashColor(name) {
    const s = String(name || '');
    let h = 0;
    for (let i = 0; i < s.length; i += 1) { h = (h * 31 + s.charCodeAt(i)) >>> 0; }
    const hue = h % 360;
    const sat = 60 + (h >> 9) % 22;   // 60-81%
    const light = 62 + (h >> 17) % 10; // 62-71% — readable on dark neutral card
    return `hsl(${hue},${sat}%,${light}%)`;
  }
  function ftMutationColor(mut) {
    const key = String(mut || '').toLowerCase().trim();
    if (!key) return '';
    if (FT_MUTATION_COLORS[key]) return FT_MUTATION_COLORS[key];
    for (const k in FT_MUTATION_COLORS) {
      if (k.indexOf(' ') === -1 && key.indexOf(k) !== -1) return FT_MUTATION_COLORS[k];
    }
    return ftMutationHashColor(key);
  }
  function ftMutationStyle(mut) {
    const key = String(mut || '').toLowerCase().trim();
    if (!key) return '';
    if (key.indexOf('gemstone') !== -1) {
      return 'color:#34d399;background:linear-gradient(90deg,#34d399 0%,#f87171 100%);'
        + '-webkit-background-clip:text;background-clip:text;'
        + '-webkit-text-fill-color:transparent;';
    }
    if (key === 'albino') {
      return 'color:#ffffff;text-shadow:0 0 4px rgba(255,255,255,.55),0 1px 2px rgba(0,0,0,.65);';
    }
    return `color:${ftMutationColor(key)};`;
  }
  function ftNormalizeNonNil(value) {
    if (value == null) return '';
    const raw = String(value).trim();
    if (!raw) return '';
    if (/^(nil|null|undefined|none|normal|default|no\s*mutation|n\/a)$/i.test(raw)) return '';
    return raw;
  }
  function normalizeMutation(value) {
    return ftNormalizeNonNil(value);
  }
  const MUTATION_SORT_ORDER = ['Gold', 'Gemstone', 'Albino', 'Stone', 'Sandy'];
  const FT_RARITY_ACCENT_HEX = {
    common: '#9ca3af', uncommon: '#84cc16', rare: '#60a5fa', epic: '#a855f7',
    legendary: '#ff8c00', mythic: '#ef4444', secret: '#00ff7f', forgotten: '#e5e7eb',
  };
  function parseHexColor(hex) {
    const h = String(hex || '').replace('#', '').trim();
    if (h.length === 3) {
      return [
        parseInt(h[0] + h[0], 16),
        parseInt(h[1] + h[1], 16),
        parseInt(h[2] + h[2], 16),
      ];
    }
    if (h.length >= 6) {
      return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
    }
    return [23, 23, 29];
  }
  function rgbToHex(r, g, b) {
    const clamp = (n) => Math.max(0, Math.min(255, Math.round(n)));
    return `#${[clamp(r), clamp(g), clamp(b)].map((x) => x.toString(16).padStart(2, '0')).join('')}`;
  }
  function colorDistance(hexA, hexB) {
    const [r1, g1, b1] = parseHexColor(hexA);
    const [r2, g2, b2] = parseHexColor(hexB);
    return Math.sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2);
  }
  function getReadableTextColor(hexBackground) {
    const [r, g, b] = parseHexColor(hexBackground);
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.62 ? '#111827' : '#ffffff';
  }
  function ftRarityAccentHex(rarityKey) {
    const key = String(rarityKey || 'common').toLowerCase();
    if (key === 'legend') return FT_RARITY_ACCENT_HEX.legendary;
    return FT_RARITY_ACCENT_HEX[key] || FT_RARITY_ACCENT_HEX.common;
  }
  function nudgeHexAwayFromRarity(hex, rarityKey, minDistance) {
    let out = String(hex || '#888888');
    const target = ftRarityAccentHex(rarityKey);
    const need = Number(minDistance) > 0 ? Number(minDistance) : 72;
    if (colorDistance(out, target) >= need) return out;
    const [r, g, b] = parseHexColor(out);
    for (let i = 0; i < 12 && colorDistance(out, target) < need; i += 1) {
      out = rgbToHex(r + (i % 2 ? 28 : -28), g + (i % 3 ? 18 : -12), b + (i % 2 ? -16 : 22));
    }
    return out;
  }
  function getMutationSortRank(mutation) {
    const m = String(mutation || '').trim();
    if (!m) return 99999;
    const idx = MUTATION_SORT_ORDER.findIndex((x) => x.toLowerCase() === m.toLowerCase());
    if (idx >= 0) return idx;
    return 1000;
  }
  function parseFishWeight(rawWeight) {
    if (rawWeight == null || rawWeight === '') return NaN;
    if (typeof rawWeight === 'number') return Number.isFinite(rawWeight) ? rawWeight : NaN;
    const s = String(rawWeight).trim();
    const kMatch = s.match(/^([\d,.]+)\s*[kK]\b/);
    if (kMatch) {
      const n = parseFloat(kMatch[1].replace(/,/g, ''));
      return Number.isFinite(n) ? n * 1000 : NaN;
    }
    const mMatch = s.match(/^([\d,.]+)\s*[mM]\b/);
    if (mMatch) {
      const n = parseFloat(mMatch[1].replace(/,/g, ''));
      return Number.isFinite(n) ? n * 1000000 : NaN;
    }
    const numMatch = s.replace(/,/g, '').match(/[\d.]+/);
    return numMatch ? parseFloat(numMatch[0]) : NaN;
  }
  function formatFishWeight(rawWeight) {
    const parsed = parseFishWeight(rawWeight);
    if (!Number.isFinite(parsed)) {
      return rawWeight ? String(rawWeight) : '';
    }
    return `${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(parsed)} kg`;
  }
  function hslToHex(h, s, l) {
    const sat = s / 100;
    const lig = l / 100;
    const c = (1 - Math.abs(2 * lig - 1)) * sat;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = lig - c / 2;
    let r = 0; let g = 0; let b = 0;
    if (h < 60) { r = c; g = x; }
    else if (h < 120) { r = x; g = c; }
    else if (h < 180) { g = c; b = x; }
    else if (h < 240) { g = x; b = c; }
    else if (h < 300) { r = x; b = c; }
    else { r = c; b = x; }
    return rgbToHex((r + m) * 255, (g + m) * 255, (b + m) * 255);
  }
  function colorToHex(color) {
    const raw = String(color || '').trim();
    if (!raw) return '#888888';
    if (raw.startsWith('#')) return raw.length === 4
      ? `#${raw[1]}${raw[1]}${raw[2]}${raw[2]}${raw[3]}${raw[3]}`
      : raw.slice(0, 7);
    const hsl = raw.match(/^hsl\(\s*(\d+)\s*,\s*(\d+)%\s*,\s*(\d+)%\s*\)$/i);
    if (hsl) return hslToHex(Number(hsl[1]), Number(hsl[2]), Number(hsl[3]));
    return '#888888';
  }
  function ensureMutationColorDiffersFromRarity(palette, rarityKey, minDistance) {
    const out = Object.assign({}, palette);
    const need = Number(minDistance) > 0 ? Number(minDistance) : 72;
    const rarityHex = ftRarityAccentHex(rarityKey);
    let accent = colorToHex(out.accent);
    if (colorDistance(accent, rarityHex) < need) {
      accent = nudgeHexAwayFromRarity(accent, rarityKey, need);
      out.accent = accent;
      const [r, g, b] = parseHexColor(accent);
      out.bg = `linear-gradient(145deg, ${rgbToHex(r * 0.35, g * 0.35, b * 0.35)}, ${rgbToHex(r * 0.12, g * 0.12, b * 0.12)})`;
      out.border = accent;
      out.text = getReadableTextColor(rgbToHex(r * 0.35, g * 0.35, b * 0.35));
      out.pillBg = nudgeHexAwayFromRarity(accent, rarityKey, need + 8);
      out.pillText = getReadableTextColor(out.pillBg);
      out.muted = out.text === '#111827' ? '#475569' : '#cbd5e1';
      out.owner = out.text === '#111827' ? '#1e3a8a' : '#93c5fd';
    }
    return out;
  }
  function ftDetailMutationSemanticPalette(mutation) {
    let key = String(mutation || '').toLowerCase().trim();
    if (!key) return null;
    if (key === 'fairy dust') key = 'midnight';
    else if (key === 'midnight') key = 'fairy dust';
    const mk = (slug, accent, bgA, bgB, border, text, pillBg, pillText, muted, owner) => ({
      slug, accent, bg: `linear-gradient(145deg, ${bgA}, ${bgB})`, border, text, pillBg, pillText,
      muted: muted || (text === '#111827' ? '#475569' : '#cbd5e1'),
      owner: owner || (text === '#111827' ? '#1e3a8a' : '#93c5fd'),
    });
    const rules = [
      [/gold|golden/, mk('gold', '#fbbf24', '#4a3410', '#19130a', 'rgba(255,204,72,0.65)', '#fff7e6', '#ffd166', '#1a1200')],
      [/gemstone|ruby|emerald|sapphire|diamond/, mk('gemstone', '#34d399', '#123c2c', '#3b1020', 'rgba(76,255,170,0.55)', '#eafff5', '#d7ffe8', '#102018')],
      [/^albino$|albino/, mk('albino', '#e8e4d9', '#f5f3e8', '#b8b8b8', 'rgba(255,255,255,0.85)', '#111827', '#111827', '#ffffff', '#475569', '#1e3a8a')],
      [/pearl/, mk('pearl', '#e8e8f0', '#f8f8ff', '#c8c8d8', 'rgba(255,255,255,0.8)', '#111827', '#334155', '#ffffff', '#475569', '#1e3a8a')],
      [/runic|stone|rock/, mk('stone', '#c8a06b', '#5a3a24', '#21150e', 'rgba(210,154,96,0.62)', '#fff4e8', '#f0c08c', '#241205')],
      [/sand|sandy/, mk('sandy', '#e3c879', '#6f5a2f', '#241d10', 'rgba(242,210,128,0.65)', '#fff8e8', '#ffe3a3', '#221700')],
      [/ghost|spectral/, mk('ghost', '#b8d4f0', '#2a3a4a', '#151c24', 'rgba(184,212,240,0.55)', '#eef6ff', '#d8e8ff', '#102030')],
      [/holographic|holo/, mk('holographic', '#67e8f9', '#1e1038', '#0a1828', 'rgba(167,139,250,0.55)', '#f5f3ff', 'linear-gradient(90deg,#67e8f9,#c084fc)', '#111827')],
      [/shiny|glowing|radiant|sparkle/, mk('shiny', '#e2e8f0', '#3a4250', '#181c22', 'rgba(226,232,240,0.55)', '#f8fafc', '#f1f5f9', '#0f172a')],
      [/^big\b|big shiny/, mk('big', '#2563eb', '#0f1f3d', '#081018', 'rgba(96,165,250,0.55)', '#eff6ff', '#93c5fd', '#0f172a')],
      [/dark|shadow|void|obsidian/, mk('dark', '#7c3aed', '#120818', '#080510', 'rgba(124,58,237,0.55)', '#f5f3ff', '#c4b5fd', '#1e1038')],
      [/frozen|ice|icy|glacial|frost/, mk('frozen', '#67e8f9', '#0c2a38', '#061018', 'rgba(103,232,249,0.55)', '#ecfeff', '#a5f3fc', '#0c4a6e')],
      [/lava|molten|fire/, mk('lava', '#fb923c', '#4a1808', '#180804', 'rgba(251,146,60,0.62)', '#fff7ed', '#fdba74', '#431407')],
      [/moss|mossy/, mk('mossy', '#65a30d', '#1a2a10', '#0a1208', 'rgba(132,204,22,0.55)', '#f7fee7', '#bef264', '#1a2e05')],
      [/electric|shocked|lightning|volt/, mk('electric', '#facc15', '#2a2408', '#121004', 'rgba(250,204,21,0.62)', '#fefce8', '#fde047', '#422006')],
      [/crystal/, mk('crystal', '#22d3ee', '#102038', '#081018', 'rgba(34,211,238,0.55)', '#ecfeff', '#a5f3fc', '#083344')],
      [/blood|crimson/, mk('blood', '#dc2626', '#3a0a0a', '#140404', 'rgba(220,38,38,0.58)', '#fef2f2', '#fca5a5', '#450a0a')],
      [/toxic|poison|venom/, mk('toxic', '#84cc16', '#142008', '#081004', 'rgba(132,204,22,0.58)', '#f7fee7', '#d9f99d', '#1a2e05')],
      [/coral/, mk('coral', '#fb7185', '#3a1018', '#140808', 'rgba(251,113,133,0.58)', '#fff1f2', '#fda4af', '#4c0519')],
      [/neon/, mk('neon', '#22d3ee', '#101828', '#080510', 'rgba(236,72,153,0.55)', '#fdf4ff', 'linear-gradient(90deg,#22d3ee,#ec4899)', '#111827')],
      [/rainbow|galaxy|cosmic/, mk('cosmic', '#a78bfa', '#1a1030', '#080510', 'rgba(167,139,250,0.55)', '#faf5ff', '#ddd6fe', '#2e1065')],
      [/mythic|mythical|celestial/, mk('mythic', '#c084fc', '#201030', '#0a0810', 'rgba(192,132,252,0.55)', '#faf5ff', '#e9d5ff', '#3b0764')],
      [/corrupt|corrupted/, mk('corrupt', '#a855f7', '#180818', '#080408', 'rgba(168,85,247,0.55)', '#faf5ff', '#d8b4fe', '#3b0764')],
    ];
    for (let i = 0; i < rules.length; i += 1) {
      if (rules[i][0].test(key)) return rules[i][1];
    }
    const accent = ftMutationHashColor(key);
    const accentHex = colorToHex(accent);
    const [r, g, b] = parseHexColor(accentHex);
    return mk(
      'custom',
      accentHex,
      rgbToHex(r * 0.28, g * 0.28, b * 0.28),
      rgbToHex(r * 0.10, g * 0.10, b * 0.10),
      accentHex,
      getReadableTextColor(rgbToHex(r * 0.28, g * 0.28, b * 0.28)),
      nudgeHexAwayFromRarity(accentHex, 'common', 48),
      getReadableTextColor(nudgeHexAwayFromRarity(accentHex, 'common', 48)),
    );
  }
  function ftDetailMutationSlug(mutation) {
    const palette = ftDetailMutationSemanticPalette(mutation);
    return palette ? palette.slug : '';
  }
  function ftDetailMutationThemeVars(mutation, rarityKey) {
    const base = ftDetailMutationSemanticPalette(mutation);
    if (!base) return '';
    const palette = ensureMutationColorDiffersFromRarity(base, rarityKey, 72);
    const pillBg = String(palette.pillBg).startsWith('linear-gradient')
      ? palette.pillBg
      : palette.pillBg;
    return `--tdf-bg:${palette.bg};--tdf-border:${palette.border};--tdf-text:${palette.text};`
      + `--tdf-pill-bg:${pillBg};--tdf-pill-text:${palette.pillText};`
      + `--tdf-muted:${palette.muted};--tdf-owner:${palette.owner};`;
  }
  function sortFishDetailInstances(a, b) {
    const aMutation = normalizeMutation(a.mutation);
    const bMutation = normalizeMutation(b.mutation);
    const aHasMutation = !!aMutation;
    const bHasMutation = !!bMutation;
    if (aHasMutation !== bHasMutation) return aHasMutation ? -1 : 1;
    const aWeight = parseFishWeight(a.weightRaw != null ? a.weightRaw : a.weight);
    const bWeight = parseFishWeight(b.weightRaw != null ? b.weightRaw : b.weight);
    if (Number.isFinite(aWeight) && Number.isFinite(bWeight) && aWeight !== bWeight) {
      return bWeight - aWeight;
    }
    if (Number.isFinite(aWeight) !== Number.isFinite(bWeight)) {
      return Number.isFinite(aWeight) ? -1 : 1;
    }
    if (aHasMutation && bHasMutation) {
      const rankDiff = getMutationSortRank(aMutation) - getMutationSortRank(bMutation);
      if (rankDiff !== 0) return rankDiff;
      const nameDiff = aMutation.localeCompare(bMutation);
      if (nameDiff !== 0) return nameDiff;
    }
    return String(a.cleanName || a.name || '').localeCompare(String(b.cleanName || b.name || ''));
  }
  function ftRarityKey(item) {
    const r = String((item && (item.rarity || item.Rarity || item.tierName)) || '').toLowerCase().trim();
    if (!r) return 'common';
    if (r === 'legend') return 'legendary';
    return r;
  }
  function ftBracketToken(rawName) {
    const m = String(rawName || '').match(/^\s*\[([^\]]+)\]/);
    return m ? m[1].trim() : '';
  }
  function ftExtractMutation(item) {
    if (!item || typeof item !== 'object') return '';
    let m = String(item.mutation || item.Mutation || '').trim();
    if (!m && Array.isArray(item.mutationTags) && item.mutationTags.length) {
      const tag = item.mutationTags
        .map((t) => String(t || '').trim())
        .find((t) => t && !/gemstone/i.test(t));
      if (tag) m = tag;
    }
    if (!m) {
      const tok = ftBracketToken(item.name || item.displayName || item.baseFishName || item.Name);
      if (tok && !/gemstone/i.test(tok)) m = tok;
    }
    if (/^(normal|none|default|no\s*mutation)$/i.test(m)) m = '';
    return m;
  }
  function ftExtractBaseName(item) {
    let name = String(
      (item && (item.baseFishName || item.cardName || item.name || item.displayName || item.Name)) || '',
    ).trim();
    name = name.replace(/^\[[^\]]*\]\s*/, '').trim();
    name = name.replace(/\b([\p{L}\p{N}]+)(\s+\1\b)+/giu, '$1');
    const m = ftExtractMutation(item);
    if (m && name.toLowerCase().startsWith(`${m.toLowerCase()} `) && name.length > m.length + 1) {
      name = name.slice(m.length).trim();
    }
    return name || String((item && (item.name || item.displayName)) || 'Unknown').trim();
  }
  function ftItemWeightText(item) {
    return (typeof formatCardWeight === 'function') ? formatCardWeight(item) : '';
  }
  function normalizeToken(value) {
    return String(value == null ? '' : value)
      .trim()
      .toLowerCase()
      .replace(/\s+/g, ' ');
  }
  const RUBY_FISH_NAME_ALIASES = new Set(['ruby']);
  const GEMSTONE_MUTATION_ALIASES = new Set(['gemstone', 'gem stone', 'ruby gemstone']);
  function isRubyGemstoneFishInstance(row) {
    if (!row || typeof row !== 'object') return false;
    const nameCandidates = [
      row.cleanName,
      row.baseFishName,
      row.fishName,
      row.name,
      row.displayName,
      row.itemName,
    ].map(normalizeToken);
    const mutationCandidates = [
      row.mutation,
      row.mutationName,
      row.mutationType,
      row.metadataMutation,
      row.modifier,
    ].map(normalizeToken);
    const isRubyName = nameCandidates.some((name) => RUBY_FISH_NAME_ALIASES.has(name));
    const isGemstoneMutation = mutationCandidates.some((mutation) => GEMSTONE_MUTATION_ALIASES.has(mutation));
    return isRubyName && isGemstoneMutation;
  }
  const RUBY_GEMSTONE_ALIASES = ['Ruby', 'Ruby Gemstone', 'Ruby gemstone', 'ruby', 'ruby gemstone'];
  const RUBY_GEMSTONE_ALIAS_SET = new Set(RUBY_GEMSTONE_ALIASES.map((a) => String(a).trim().toLowerCase()));
  function isRubyGemstoneMutationName(value) {
    return RUBY_GEMSTONE_ALIAS_SET.has(String(value || '').trim().toLowerCase());
  }
  function isRubyGemstoneItem(item) {
    if (!item || typeof item !== 'object') return false;
    if (isRubyGemstoneMutationName(ftExtractMutation(item))) return true;
    const rawName = String(item.name || item.displayName || item.baseFishName || item.Name || '');
    const bracket = ftBracketToken(rawName).toLowerCase();
    const nameStripped = rawName.toLowerCase().replace(/\[[^\]]*\]/g, ' ').replace(/\s+/g, ' ').trim();
    const cat = String(item.category || item.type || item.Category || '').toLowerCase();
    const mut = String(item.mutation || item.Mutation || '').toLowerCase();
    const tags = Array.isArray(item.mutationTags) ? item.mutationTags.map((t) => String(t).toLowerCase()) : [];
    const hasGemstone = cat.includes('gemstone') || mut.includes('gemstone')
      || tags.some((t) => t.includes('gemstone')) || nameStripped.includes('gemstone')
      || bracket.includes('gemstone');
    const hasRuby = nameStripped.includes('ruby') || mut.includes('ruby')
      || tags.some((t) => t.includes('ruby')) || bracket.includes('ruby');
    if (!hasRuby) return false;
    if (nameStripped === 'ruby mutation gemstone' || nameStripped === 'ruby gemstone') return true;
    return hasGemstone;
  }
  function rubyGemstoneCountForItem(item) {
    if (!item || typeof item !== 'object') return 0;
    const list = Array.isArray(item.ownedInstances) ? item.ownedInstances : null;
    if (list && list.length) {
      let n = 0;
      for (const inst of list) {
        if (!inst || typeof inst !== 'object') continue;
        const merged = {
          cleanName: inst.cleanName != null ? inst.cleanName : item.cleanName,
          baseFishName: inst.baseFishName != null ? inst.baseFishName : item.baseFishName,
          fishName: inst.fishName != null ? inst.fishName : item.fishName,
          name: inst.name != null ? inst.name : item.name,
          displayName: inst.displayName != null ? inst.displayName : item.displayName,
          itemName: inst.itemName != null ? inst.itemName : item.itemName,
          mutation: inst.mutation != null ? inst.mutation : item.mutation,
          mutationName: inst.mutationName != null ? inst.mutationName : item.mutationName,
          mutationType: inst.mutationType != null ? inst.mutationType : item.mutationType,
          metadataMutation: inst.metadataMutation != null ? inst.metadataMutation : item.metadataMutation,
          modifier: inst.modifier != null ? inst.modifier : item.modifier,
        };
        if (isRubyGemstoneFishInstance(merged)) {
          const qty = Number(inst.quantity != null ? inst.quantity : (inst.amount != null ? inst.amount : (inst.count != null ? inst.count : 1)));
          n += Number.isFinite(qty) && qty > 0 ? Math.floor(qty) : 1;
        }
      }
      if (n > 0) return n;
    }
    if (isRubyGemstoneFishInstance(item)) {
      const amount = Number(resolveItemAmount(item));
      return Number.isFinite(amount) && amount > 0 ? Math.floor(amount) : 1;
    }
    if (isRubyGemstoneItem(item)) {
      return Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0)) || 1;
    }
    return 0;
  }
  function getRubyGemstoneTopCardCount(snapshotOrState) {
    if (!snapshotOrState || typeof snapshotOrState !== 'object') return 0;
    const rows = []
      .concat(getPublicFishItems(snapshotOrState) || [])
      .concat(getPublicStoneItems(snapshotOrState) || [])
      .concat(getPublicTotemItems(snapshotOrState) || []);
    const proofRows = [];
    let total = 0;
    for (const row of rows) {
      const c = rubyGemstoneCountForItem(row);
      if (c > 0) {
        total += c;
        if (DEBUG_INVENTORY) {
          proofRows.push({
            name: row && row.name,
            cleanName: row && row.cleanName,
            mutation: (row && (row.mutation || row.mutationName)) || (Array.isArray(row && row.ownedInstances) && row.ownedInstances[0] ? (row.ownedInstances[0].mutationName || row.ownedInstances[0].mutation) : null),
            weight: row && (row.weightKg != null ? row.weightKg : row.weight),
            count: c,
          });
        }
      }
    }
    if (DEBUG_INVENTORY) {
      window.__rubyGemstoneProof = { topCount: total, matchedRows: proofRows, finalTopCardValue: total };
    }
    return total;
  }
  function computeInventoryStats() {
    let onlineCount = 0;
    let evolvedStones = 0;
    let secretFish = 0;
    let forgottenFish = 0;
    let rubyGemstone = 0;
    let runicStone = 0;
    const countRuby = (item) => {
      rubyGemstone += rubyGemstoneCountForItem(item);
    };
    trackers.forEach((entry) => {
      if (isTrackerOnline(entry)) onlineCount += 1;
      const data = resolveEntryPublicSnapshot(entry);
      if (!data) return;
      const fishList = getPublicFishItems(data);
      const stoneList = getPublicStoneItems(data);
      const totemList = getPublicTotemItems(data);
      for (const item of fishList) {
        const rarity = normalizeRarityLabel(item);
        const amount = Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0));
        if (rarity === 'Secret') secretFish += amount;
        else if (rarity === 'Forgotten') forgottenFish += amount;
        countRuby(item);
      }
      for (const item of stoneList) {
        const stoneType = String(item?.stoneType || item?.StoneType || '').trim();
        if (stoneType === 'Evolved') {
          evolvedStones += Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0));
        } else if (stoneType === 'Runic') {
          runicStone += Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0));
        }
        countRuby(item);
      }
      for (const item of totemList) {
        countRuby(item);
      }
    });
    return {
      totalAccounts: trackers.size,
      onlineCount,
      evolvedStones,
      secretFish,
      forgottenFish,
      rubyGemstone,
      runicStone,
    };
  }
  function updateInventoryStats() {
    const localStats = computeInventoryStats();
    const stats = lastValidTrackerSummary
      ? {
        totalAccounts: localStats.totalAccounts,
        onlineCount: localStats.onlineCount,
        evolvedStones: localStats.evolvedStones,
        secretFish: localStats.secretFish,
        forgottenFish: localStats.forgottenFish,
        rubyGemstone: localStats.rubyGemstone,
        runicStone: localStats.runicStone,
      }
      : localStats;
    const countUp = window.DengCountUpStats;
    if (statOnlineAccountsEl) {
      statOnlineAccountsEl.innerHTML = `<span class="online-count">${escHtml(formatQuantity(stats.onlineCount))}</span><span class="separator"> / </span><span class="total-count">${escHtml(formatQuantity(stats.totalAccounts))}</span>`;
    }
    if (statEvolvedStonesEl) {
      if (countUp) countUp.set(statEvolvedStonesEl, { to: stats.evolvedStones, format: 'integer' });
      else statEvolvedStonesEl.textContent = formatQuantity(stats.evolvedStones);
    }
    if (statSecretFishEl) {
      if (countUp) countUp.set(statSecretFishEl, { to: stats.secretFish, format: 'integer' });
      else statSecretFishEl.textContent = formatQuantity(stats.secretFish);
    }
    if (statForgottenFishEl) {
      if (countUp) countUp.set(statForgottenFishEl, { to: stats.forgottenFish, format: 'integer' });
      else statForgottenFishEl.textContent = formatQuantity(stats.forgottenFish);
    }
    if (statRubyGemstoneEl) {
      if (countUp) countUp.set(statRubyGemstoneEl, { to: stats.rubyGemstone || 0, format: 'integer' });
      else statRubyGemstoneEl.textContent = formatQuantity(stats.rubyGemstone || 0);
    }
    if (statRunicStoneEl) {
      if (countUp) countUp.set(statRunicStoneEl, { to: stats.runicStone || 0, format: 'integer' });
      else statRunicStoneEl.textContent = formatQuantity(stats.runicStone || 0);
    }
  }
  const INDIVIDUAL_BACKPACK_CARD_OPTS = Object.freeze({ includeOwnerChip: false, includeRarity: true });
  function buildCardBadgesHtml(item, opts) {
    opts = opts || {};
    const amount = formatAmountLabel(resolveItemAmount(item));
    const rarity = opts.rarity != null ? opts.rarity : publicRarity(item);
    const parts = [];
    if (opts.includeOwnerChip !== false) {
      parts.push(ownersChipHtml(opts.accountCount || 1));
    }
    parts.push(`<span class="ft-chip ft-chip-qty">${escHtml(amount)}</span>`);
    if (opts.includeRarity !== false && rarity && rarity !== 'Unknown') {
      parts.push(`<span class="ft-chip ft-chip-rarity">${escHtml(rarity)}</span>`);
    }
    return `<div class="ft-card-stats">${parts.join('')}</div>`;
  }
  function buildStoneStatsHtml(item, opts) {
    opts = opts || {};
    const amount = formatAmountLabel(resolveItemAmount(item));
    const ownerChip = opts.includeOwnerChip !== false ? ownersChipHtml(opts.accountCount || 1) : '';
    return `<div class="ft-card-stats">${ownerChip}<span class="ft-chip ft-chip-qty">${escHtml(amount)}</span></div>`;
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
  function inventoryItemQuantity(item) {
    return Number(item?.count ?? item?.amount ?? item?.quantity ?? 0) || 0;
  }
  function sortInventoryFish(items) {
    if (!Array.isArray(items)) return [];
    return [...items].sort((a, b) => {
      const rarityDiff = rarityRank(b) - rarityRank(a);
      if (rarityDiff) return rarityDiff;
      const qtyDiff = inventoryItemQuantity(b) - inventoryItemQuantity(a);
      if (qtyDiff) return qtyDiff;
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
  function publicLiveEmptyHtml(data) {
    const state = inventoryDisplayState(data);
    if (state === 'empty') {
      return '<div class="card-empty">&#x1F9F3; Inventory is empty.</div>';
    }
    if (state === 'syncing') {
      return '<div class="card-empty">Waiting for inventory snapshot<span class="card-empty-sub">Account is online. Stats, fish, and stones will appear after the first full sync.</span></div>';
    }
    if (state === 'waiting') {
      return '<div class="card-empty">Waiting for inventory snapshot<span class="card-empty-sub">Run the tracker script in-game to start syncing.</span></div>';
    }
    return '<div class="card-empty">&#x1F9F3; Inventory is empty.</div>';
  }
  function refreshEntrySyncDisplay(entry) {
    if (!entry) return;
    updateInventoryUploadIndicator(entry);
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
  function tickIndicator1Presence(entry, key) {
    if (!entry || !key) return;
    if (accountsTableBodyEl) {
      const row = accountsTableBodyEl.querySelector(`[data-account-row-key="${CSS.escape(key)}"]`);
      if (row) patchAccountStatusDom(row, entry);
    }
    if (accountsMobileListEl) {
      const mobile = accountsMobileListEl.querySelector(`[data-account-mobile-key="${CSS.escape(key)}"]`);
      if (mobile) patchAccountStatusDom(mobile.querySelector('.accounts-mobile-card__account'), entry);
    }
  }
  function tickIndicator2Stats(entry, key) {
    if (!entry || !key) return;
    if (accountsTableBodyEl) {
      const row = accountsTableBodyEl.querySelector(`[data-account-row-key="${CSS.escape(key)}"]`);
      if (row) patchAccountStatsSyncSub(row, entry);
    }
    if (accountsMobileListEl) {
      const mobile = accountsMobileListEl.querySelector(`[data-account-mobile-key="${CSS.escape(key)}"]`);
      if (mobile) patchAccountStatsSyncSub(mobile, entry);
    }
  }
  function tickIndicator3Inventory(entry) {
    if (!entry) return;
    refreshEntrySyncDisplay(entry);
  }
  function tickAllCardSyncStatus() {
    trackers.forEach((entry, key) => {
      tickIndicator1Presence(entry, key);
      tickIndicator2Stats(entry, key);
      tickIndicator3Inventory(entry);
    });
    updateInventoryUploadIndicator();
    updateInventoryStats();
  }
  function resolveInventoryIndicatorEntry(preferredEntry) {
    if (preferredEntry) return preferredEntry;
    if (accountViewMode === 'account' && activeAccountKey) {
      const active = trackers.get(activeAccountKey);
      if (active) return active;
    }
    return null;
  }
  function setInventoryIndicatorHidden(root, hidden) {
    const wrap = root && (root.closest('[data-inventory-upload-indicator]') || root);
    if (!wrap) return;
    wrap.style.display = hidden ? 'none' : '';
  }
  function ensureSingleInventoryUploadText(root) {
    if (!root) return null;
    const scope = root.closest('.inventory-upload-indicator') || root;
    const allText = scope.querySelectorAll('[data-inventory-upload-text]');
    if (allText.length > 1) {
      for (let i = 1; i < allText.length; i += 1) allText[i].remove();
    }
    return scope.querySelector('[data-inventory-upload-text]');
  }
  function patchInventoryUploadIndicatorDom(root, entry) {
    if (!root) return;
    const dotEl = root.querySelector('[data-inventory-upload-dot]');
    if (dotEl) dotEl.remove();
    const textEl = ensureSingleInventoryUploadText(root);
    const wrapEl = root.closest('.inventory-upload-indicator') || root;
    const label = entry ? formatInventoryUploadLabel(entry) : '';
    if (textEl && textEl.textContent !== (label || '')) textEl.textContent = label || '';
    if (wrapEl && wrapEl.classList.contains('inventory-upload-indicator')) {
      wrapEl.classList.remove('is-live', 'is-stale', 'live', 'dead');
      if (!wrapEl.classList.contains('is-neutral')) wrapEl.classList.add('is-neutral');
      if (wrapEl.getAttribute('title') !== (label || '')) wrapEl.setAttribute('title', label || '');
    }
  }
  function buildSectionUploadIndicatorHtml(scopeAttr, ariaLabel) {
    return (
      '<div class="inventory-upload-indicator is-neutral" ' + scopeAttr +
      ' aria-label="' + escHtml(ariaLabel) + '">' +
      '<span class="inventory-upload-indicator__text" data-inventory-upload-text></span></div>'
    );
  }
  function ensureSectionUploadIndicator(parent, scopeAttr, headClass, titleClass, titleText, ariaLabel) {
    if (!parent) return null;
    let head = parent.querySelector('.' + headClass);
    if (!head) {
      const legacyTitle = parent.querySelector(':scope > .' + titleClass);
      head = document.createElement('div');
      head.className = headClass;
      head.innerHTML =
        '<div class="' + titleClass + '"></div>' +
        buildSectionUploadIndicatorHtml(scopeAttr, ariaLabel);
      if (legacyTitle) parent.replaceChild(head, legacyTitle);
      else parent.insertBefore(head, parent.firstChild);
    }
    const titleEl = head.querySelector('.' + titleClass);
    if (titleEl && titleEl.textContent !== titleText) titleEl.textContent = titleText;
    let indicator = head.querySelector('[' + scopeAttr + ']');
    if (!indicator) {
      head.insertAdjacentHTML('beforeend', buildSectionUploadIndicatorHtml(scopeAttr, ariaLabel));
      indicator = head.querySelector('[' + scopeAttr + ']');
    }
    return indicator;
  }
  function ensureFishGridUploadIndicator(fishHost, titleText) {
    return ensureSectionUploadIndicator(
      fishHost,
      'data-fish-grid-upload-indicator',
      'inventory-section-head',
      'fish-section__title',
      titleText,
      'Fish grid upload status',
    );
  }
  function ensureItemGridUploadIndicator(itemHost, titleText) {
    return ensureSectionUploadIndicator(
      itemHost,
      'data-item-grid-upload-indicator',
      'inventory-section-head',
      'items-section-head__title',
      titleText,
      'Item grid upload status',
    );
  }
  function ensureDetailUploadIndicator(panel) {
    if (!panel) return null;
    let indicator = panel.querySelector('[data-detail-upload-indicator]');
    if (!indicator) {
      const head = panel.querySelector('.ft-detail-panel__head');
      if (!head) return null;
      head.insertAdjacentHTML(
        'beforeend',
        buildSectionUploadIndicatorHtml('data-detail-upload-indicator', 'Backpack detail upload status'),
      );
      indicator = head.querySelector('[data-detail-upload-indicator]');
    }
    return indicator;
  }
  function updateInventoryUploadIndicator(preferredEntry) {
    const entry = resolveInventoryIndicatorEntry(preferredEntry);
    if (!(accountViewMode === 'account' && activeAccountKey)) return;
    const active = trackers.get(activeAccountKey);
    const body = active && active.el && active.el.querySelector('[data-card-body]');
    if (body) {
      const fishHost = body.querySelector('[data-fish-grid-host]');
      const itemHost = body.querySelector('[data-stones-section]');
      const fishTitleEl = fishHost && fishHost.querySelector('.fish-section__title');
      const itemTitleEl = itemHost && itemHost.querySelector('.items-section-head__title');
      if (fishHost) {
        const fishTitle = fishTitleEl ? fishTitleEl.textContent : 'Fishes';
        patchInventoryUploadIndicatorDom(ensureFishGridUploadIndicator(fishHost, fishTitle), entry || active);
      }
      if (itemHost) {
        const itemTitle = itemTitleEl ? itemTitleEl.textContent : 'Items';
        patchInventoryUploadIndicatorDom(ensureItemGridUploadIndicator(itemHost, itemTitle), entry || active);
      }
    }
    if (ftDetailPanelEl && !ftDetailPanelEl.hidden) {
      patchInventoryUploadIndicatorDom(ensureDetailUploadIndicator(ftDetailPanelEl), entry || active);
    }
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
    if (cat === 'totem') {
      const stable = String(item?.itemId || '').trim().toLowerCase();
      return stable ? `${cat}:${name.toLowerCase()}:${stable}` : `${cat}:${name.toLowerCase()}`;
    }
    return `${cat}:${name.toLowerCase()}:${normalizeRarityLabel(item).toLowerCase()}`;
  }
  function mergeBulkItem(existing, item, username, category) {
    const amount = Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0));
    const ownerAmounts = Object.assign({}, existing.ownerAmounts);
    const ownerKey = String(username || '').trim();
    if (ownerKey) {
      ownerAmounts[ownerKey] = Math.max(Number(ownerAmounts[ownerKey]) || 0, amount);
    }
    const owners = Object.keys(ownerAmounts);
    const totalAmount = owners.reduce((sum, k) => sum + (Number(ownerAmounts[k]) || 0), 0);
    const candidate = item.imageUrl || null;
    const imageUrl = isUsableImageUrl(candidate)
      ? candidate
      : (isUsableImageUrl(existing.imageUrl) ? existing.imageUrl : null);
    const imageAssetId = item.imageAssetId || item.iconAssetId || existing.imageAssetId || existing.iconAssetId || null;
    return {
      ...existing,
      name: existing.name || canonicalBulkName(item),
      category: category || existing.category,
      rarity: existing.rarity || normalizeRarityLabel(item),
      stoneType: existing.stoneType || item.stoneType || item.StoneType || null,
      itemId: existing.itemId || inventoryItemId(item) || null,
      imageUrl,
      imageAssetId,
      iconAssetId: item.iconAssetId || existing.iconAssetId || imageAssetId,
      iconSource: item.iconSource || existing.iconSource || null,
      imageSource: item.imageSource || existing.imageSource || null,
      amount: totalAmount,
      accountCount: owners.length,
      owners,
      ownerAmounts,
      dataSource: 'bulk_playerdata_gameitemdb',
      groupKey: existing.groupKey,
    };
  }
  function aggregateBulkInventory(sessions) {
    const fishMap = new Map();
    const stoneMap = new Map();
    const totemMap = new Map();
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
      for (const item of session?.totemList || []) {
        const key = bulkGroupKey('totem', item);
        totemMap.set(key, mergeBulkItem(totemMap.get(key) || {
          groupKey: key, name: canonicalBulkName(item), category: 'totem',
          rarity: normalizeRarityLabel(item), amount: 0, accountCount: 0, owners: [], imageUrl: null,
        }, item, username, 'totem'));
      }
    }
    return {
      fish: sortInventoryFish([...fishMap.values()]),
      stones: sortInventoryStones([...stoneMap.values()]),
      totems: sortInventoryTotems([...totemMap.values()]),
      accountCount: accountSet.size,
      fishTypeCount: fishMap.size,
      stoneTypeCount: stoneMap.size,
      totemTypeCount: totemMap.size,
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
      const data = resolveEntryPublicSnapshot(entry);
      if (!data) return;
      const fishList = getPublicFishItems(data);
      const stoneList = getPublicStoneItems(data);
      const totemList = getPublicTotemItems(data);
      if (!fishList.length && !stoneList.length && !totemList.length) return;
      sessions.push({ username: entry.displayName, fishList, stoneList, totemList });
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
    <img src="${escHtml(imgSrc)}" alt="${escHtml(title)}" title="${escHtml(title)}" decoding="async" loading="lazy" width="54" height="54"${isPlaceholder ? ' data-placeholder="true"' : ''} data-item-id="${escHtml(item.itemId || '')}">
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
  function patchBulkItemGrid(container, stoneItems, totemItems, opts) {
    patchItemGrid(container, stoneItems, totemItems, opts);
  }
  function patchBulkStoneGrid(container, items, opts) {
    patchBulkItemGrid(container, items, [], opts);
  }
  function renderBulkInventory(showCategory) {
    if (!bulkBodyEl) return;
    if (ftDetailPanelEl && !ftDetailPanelEl.hidden && ftDetailHostEl === bulkBodyEl) return;
    const sessions = collectBulkSessions();
    const bulk = aggregateBulkInventory(sessions);
    const fish = filterBulkItems(bulk.fish, bulkSearchQuery);
    const stones = filterBulkItems(bulk.stones, bulkSearchQuery);
    const totems = filterBulkItems(bulk.totems, bulkSearchQuery);
    if (!bulk.accountCount) {
      if (!bulkBodyEl.querySelector('.card-empty')) {
        bulkBodyEl.innerHTML = '<div class="card-empty">No inventory data yet.</div>';
      }
      return;
    }
    bulkBodyEl.querySelectorAll('.card-empty').forEach((el) => el.remove());
    if (showCategory === 'fish') {
      if (!fish.length) {
        bulkBodyEl.innerHTML = '<div class="inventory-search-empty">No inventory items found</div>';
        return;
      }
      let host = bulkBodyEl.querySelector('[data-bulk-fish-host]');
      if (!host) {
        bulkBodyEl.innerHTML = '<div data-bulk-fish-host></div>';
        host = bulkBodyEl.querySelector('[data-bulk-fish-host]');
      }
      bulkBodyEl.querySelectorAll('.inventory-search-empty').forEach((el) => el.remove());
      patchItemsGrid(host, fish, {
        keyFn: bulkCardKey,
        buildOpts: (item) => ({ accountCount: item.accountCount || 1, includeRarity: true }),
      });
      return;
    }
    if (showCategory === 'stone') {
      if (!stones.length && !totems.length) {
        bulkBodyEl.innerHTML = '<div class="inventory-search-empty">No inventory items found</div>';
        return;
      }
      let host = bulkBodyEl.querySelector('[data-bulk-stone-host]');
      if (!host) {
        bulkBodyEl.innerHTML = '<div data-bulk-stone-host></div>';
        host = bulkBodyEl.querySelector('[data-bulk-stone-host]');
      }
      bulkBodyEl.querySelectorAll('.inventory-search-empty').forEach((el) => el.remove());
      patchBulkItemGrid(host, stones, totems, {
        buildOpts: (item) => ({ accountCount: item.accountCount || 1 }),
      });
    }
    updateInventoryUploadIndicator();
  }
  function isTrackerOfflineForRemoval(entry) {
    return entryConnectionFreshness(entry) === 'dead';
  }
  function isTrackerNoDataForRemoval(entry) {
    const snap = resolveEntryPublicSnapshot(entry);
    return !hasRenderableTrackerData(snap);
  }
  function collectTrackerRemovalKeys(predicate) {
    const keys = [];
    trackers.forEach((entry, key) => {
      if (predicate(entry, key)) keys.push(key);
    });
    return keys;
  }
  function reconcileActiveAccountAfterRemoval() {
    if (activeAccountKey && trackers.has(activeAccountKey)) return;
    if (!activeAccountKey) return;
    const firstKey = trackers.keys().next().value;
    if (accountViewMode === 'account') {
      if (firstKey) setAccountViewMode('account', { key: firstKey });
      else setAccountViewMode('table');
    } else {
      activeAccountKey = null;
    }
    clearInlineDetailState('bulk-remove');
  }
  function showUsernameActionNotice(message) {
    if (!usernameErrorEl) return;
    usernameErrorEl.textContent = message || '';
    usernameErrorEl.classList.add('tracker-username-action-notice');
    if (inputEl) inputEl.classList.remove('is-invalid');
  }
  async function removeTrackersByKeys(keys) {
    if (!keys.length) return true;
    let ok = true;
    for (let i = 0; i < keys.length; i += 1) {
      const removed = await persistTrackerRemove(keys[i]);
      if (!removed) ok = false;
    }
    reconcileActiveAccountAfterRemoval();
    return ok;
  }
  async function removeOfflineTrackers() {
    const keys = collectTrackerRemovalKeys((entry) => isTrackerOfflineForRemoval(entry));
    if (!keys.length) {
      showUsernameActionNotice('No offline usernames to remove.');
      return;
    }
    clearUsernameError();
    const ok = await removeTrackersByKeys(keys);
    if (!ok) showUsernameError('Could not remove all offline usernames.');
  }
  async function removeNoDataTrackers() {
    const keys = collectTrackerRemovalKeys((entry) => isTrackerNoDataForRemoval(entry));
    if (!keys.length) {
      showUsernameActionNotice('No no-data usernames to remove.');
      return;
    }
    clearUsernameError();
    const ok = await removeTrackersByKeys(keys);
    if (!ok) showUsernameError('Could not remove all no-data usernames.');
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
    if (entry && entry.uploadStatus && entry.uploadStatus.statusColor) {
      return entry.uploadStatus.statusColor === 'green' || entry.uploadStatus.statusColor === 'yellow';
    }
    if (!data) return false;
    const color = data.statusColor || data.currentStatus;
    return color === 'green' || color === 'yellow';
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
  function aggregateTotemItemsForDisplay(items) {
    if (!Array.isArray(items)) return [];
    const map = new Map();
    for (const item of items) {
      const name = String(item?.name || item?.displayName || 'Totem').trim();
      const key = `${name.toLowerCase()}|${item?.itemId || ''}`;
      const qty = Number(item?.quantity ?? item?.amount) >= 1
        ? Math.floor(Number(item.quantity ?? item.amount))
        : 1;
      const prev = map.get(key);
      if (prev) {
        prev.quantity = (Number(prev.quantity) || 0) + qty;
        prev.amount = prev.quantity;
      } else {
        map.set(key, { ...item, quantity: qty, amount: qty });
      }
    }
    return [...map.values()];
  }
  function getPublicTotemItems(data) {
    if (!data) return [];
    let items = [];
    if (Array.isArray(data.totemItems)) items = data.totemItems;
    else if (Array.isArray(data.totemInventory)) items = data.totemInventory;
    if (!items.length && Array.isArray(data.lastGoodPublicTotemItems) && data.lastGoodPublicTotemItems.length) {
      items = data.lastGoodPublicTotemItems;
    }
    return sortInventoryTotems(aggregateTotemItemsForDisplay(items));
  }
  function sortInventoryTotems(items) {
    if (!Array.isArray(items)) return [];
    return [...items].sort((a, b) => {
      const nameA = String(a?.name || a?.displayName || '').toLowerCase();
      const nameB = String(b?.name || b?.displayName || '').toLowerCase();
      if (nameA !== nameB) return nameA.localeCompare(nameB);
      const idA = String(a?.uuid || a?.itemId || '');
      const idB = String(b?.uuid || b?.itemId || '');
      return idA.localeCompare(idB);
    });
  }
  function getPublicStoneItems(data) {
    if (!data) return [];
    let items = [];
    if (Array.isArray(data.stoneItems)) items = data.stoneItems;
    else if (Array.isArray(data.stoneInventory)) items = data.stoneInventory;
    if (!items.length && Array.isArray(data.lastGoodPublicStoneItems) && data.lastGoodPublicStoneItems.length) {
      items = data.lastGoodPublicStoneItems;
    }
    return sortInventoryStones(items);
  }
  function totemCardKey(item) {
    if (item && item.groupKey) return String(item.groupKey).toLowerCase();
    return `totem|${String(item.uuid || item.itemId || item.name || 'unknown').toLowerCase()}`;
  }
  function buildTotemCardInnerHtml(item, opts) {
    opts = opts || {};
    const title = String(item.name || item.displayName || 'Totem');
    const imgSrc = itemImageSrc(item);
    const imageHtml = imgSrc
      ? `<img src="${escHtml(imgSrc)}" alt="${escHtml(title)}" title="${escHtml(title)}" decoding="async" data-item-id="${escHtml(item.itemId || '')}">`
      : '&#x1F9FF;';
    return `
  <div class="ft-card-icon">${imageHtml}</div>
  <div class="ft-card-main">
    <div class="ft-card-name" title="${escHtml(title)}">${escHtml(title)}</div>
    ${buildStoneStatsHtml(item, opts)}
  </div>`;
  }
  function buildTotemCardElement(item, opts) {
    const card = document.createElement('div');
    card.className = 'ft-card ft-card--totem';
    card.setAttribute('data-card-key', totemCardKey(item));
    card.setAttribute('data-kind', 'totem');
    card.innerHTML = buildTotemCardInnerHtml(item, opts);
    markCardEnterAnimation(card);
    attachFtCardItem(card, item, 'totem');
    return card;
  }
  function patchTotemCardElement(card, item, opts) {
    card.setAttribute('data-card-key', totemCardKey(item));
    card.className = 'ft-card ft-card--totem';
    patchStoneCardDom(card, item, opts);
    attachFtCardItem(card, item, 'totem');
  }
  function patchStonesSubgrid(host, items, opts) {
    opts = opts || {};
    if (!host) return;
    const stoneTotal = (items || []).reduce((sum, item) => sum + Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0)), 0);
    let title = host.querySelector('.items-subsection__title, .stones-section__title');
    if (!title) {
      host.innerHTML = '<div class="items-subsection__title stones-section__title"></div><div class="items-grid inventory-grid stones-grid stone-grid"></div>';
      title = host.querySelector('.items-subsection__title, .stones-section__title');
    }
    const titleText = `Enchant Stones (${formatQuantity(stoneTotal)})`;
    if (title) title.textContent = titleText;
    host.style.display = items && items.length ? '' : 'none';
    let grid = host.querySelector('.stones-grid, .stone-grid');
    if (!grid) {
      grid = document.createElement('div');
      grid.className = 'items-grid inventory-grid stones-grid stone-grid';
      host.appendChild(grid);
    }
    if (!items || !items.length) {
      grid.innerHTML = '';
      return;
    }
    const nextKeys = new Set();
    const existing = new Map();
    grid.querySelectorAll('.ft-card--stone[data-card-key]').forEach((el) => {
      existing.set(el.getAttribute('data-card-key'), el);
    });
    items.forEach((item, idx) => {
      const key = stoneCardKey(item);
      const itemOpts = typeof opts.buildOpts === 'function'
        ? Object.assign({}, opts, opts.buildOpts(item, idx))
        : opts;
      nextKeys.add(key);
      let card = existing.get(key);
      if (!card) {
        card = buildStoneCardElement(item, itemOpts);
        placeGridCardAtIndex(grid, card, idx);
      } else {
        patchStoneCardElement(card, item, itemOpts);
        placeGridCardAtIndex(grid, card, idx);
      }
    });
    existing.forEach((el, key) => { if (!nextKeys.has(key)) el.remove(); });
  }
  function patchTotemsSubgrid(host, items, opts) {
    opts = opts || {};
    if (!host) return;
    const totemTotal = (items || []).reduce((sum, item) => sum + Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0)), 0);
    let title = host.querySelector('.items-subsection__title, .totems-section__title');
    if (!title) {
      host.innerHTML = '<div class="items-subsection__title totems-section__title"></div><div class="items-grid inventory-grid totems-grid"></div>';
      title = host.querySelector('.items-subsection__title, .totems-section__title');
    }
    const titleText = `Totems (${formatQuantity(totemTotal)})`;
    if (title) title.textContent = titleText;
    host.style.display = items && items.length ? '' : (DEBUG_GLOBAL ? '' : 'none');
    let grid = host.querySelector('.totems-grid');
    if (!grid) {
      grid = document.createElement('div');
      grid.className = 'items-grid inventory-grid totems-grid';
      host.appendChild(grid);
    }
    if (!items || !items.length) {
      grid.innerHTML = '';
      return;
    }
    const nextKeys = new Set();
    const existing = new Map();
    grid.querySelectorAll('.ft-card--totem[data-card-key]').forEach((el) => {
      existing.set(el.getAttribute('data-card-key'), el);
    });
    items.forEach((item, idx) => {
      const key = totemCardKey(item);
      const itemOpts = typeof opts.buildOpts === 'function'
        ? Object.assign({}, opts, opts.buildOpts(item, idx))
        : opts;
      nextKeys.add(key);
      let card = existing.get(key);
      if (!card) {
        card = buildTotemCardElement(item, itemOpts);
        placeGridCardAtIndex(grid, card, idx);
      } else {
        patchTotemCardElement(card, item, itemOpts);
        placeGridCardAtIndex(grid, card, idx);
      }
    });
    existing.forEach((el, key) => { if (!nextKeys.has(key)) el.remove(); });
  }
  function patchItemGrid(container, stoneItems, totemItems, opts) {
    opts = opts || {};
    if (!container) return;
    const stones = Array.isArray(stoneItems) ? stoneItems : [];
    const totems = Array.isArray(totemItems) ? totemItems : [];
    if (!stones.length && !totems.length && !DEBUG_GLOBAL) {
      container.innerHTML = '';
      container.style.display = 'none';
      return;
    }
    container.style.display = '';
    const stoneTotal = stones.reduce((sum, item) => sum + Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0)), 0);
    const totemTotal = totems.reduce((sum, item) => sum + Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0)), 0);
    if (!container.querySelector('[data-stones-subsection]')) {
      container.innerHTML = [
        '<div class="items-subsection" data-stones-subsection>',
        '  <div class="items-subsection__title stones-section__title"></div>',
        '  <div class="items-grid inventory-grid stones-grid stone-grid"></div>',
        '</div>',
        '<div class="items-subsection" data-totems-subsection>',
        '  <div class="items-subsection__title totems-section__title"></div>',
        '  <div class="items-grid inventory-grid totems-grid"></div>',
        '</div>',
      ].join('');
    }
    ensureItemGridUploadIndicator(container, `Items (${formatQuantity(stoneTotal + totemTotal)})`);
    patchStonesSubgrid(container.querySelector('[data-stones-subsection]'), stones, opts);
    if (totems.length || DEBUG_GLOBAL) {
      patchTotemsSubgrid(container.querySelector('[data-totems-subsection]'), totems, opts);
    } else {
      const totemHost = container.querySelector('[data-totems-subsection]');
      if (totemHost) totemHost.style.display = 'none';
    }
  }
  function patchStonesGrid(container, items, opts) {
    patchItemGrid(container, items, [], opts);
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
  function buildStoneCardElement(item, opts) {
    const card = document.createElement('div');
    card.className = 'ft-card ft-card--stone';
    card.setAttribute('data-card-key', stoneCardKey(item));
    card.setAttribute('data-kind', 'stone');
    card.innerHTML = buildStoneCardInnerHtml(item, opts);
    markCardEnterAnimation(card);
    attachFtCardItem(card, item, 'stone');
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
  function patchStoneCardElement(card, item, opts) {
    card.setAttribute('data-card-key', stoneCardKey(item));
    card.className = 'ft-card ft-card--stone';
    patchStoneCardDom(card, item, opts);
    attachFtCardItem(card, item, 'stone');
  }
  function clearCardWaitingPanels(cardBody) {
    if (!cardBody) return;
    cardBody.querySelectorAll('.card-empty').forEach((el) => el.remove());
  }
  function patchCardInventory(cardBody, fishList, stoneList, totemList) {
    if (!cardBody) return;
    const card = cardBody.closest('.tracker-card');
    const key = card && card.dataset.user;
    const entry = key && trackers.get(key);
    const data = entry && entry.lastData;
    if (!totemList && data) totemList = getPublicTotemItems(data);
    if (entry) {
      entry.lastFishList = Array.isArray(fishList) ? fishList.map((item, idx) => {
        const prev = (entry.lastFishList || [])[idx];
        const byKey = (entry.lastFishList || []).find((row) => cardKey(row) === cardKey(item));
        return preferFishCardImageFields(byKey || prev, item);
      }) : [];
      entry.lastStoneList = Array.isArray(stoneList) ? stoneList : [];
      entry.lastTotemList = Array.isArray(totemList) ? totemList : [];
    }
    const hasItems = (fishList && fishList.length) || (stoneList && stoneList.length) || (totemList && totemList.length);
    if (!hasItems && data && !isProvenEmptyInventory(data)) {
      clearCardWaitingPanels(cardBody);
      cardBody.innerHTML = publicLiveEmptyHtml(data);
      return;
    }
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
    if (showFish) patchItemsGrid(fishHost, fishList || [], { cardOpts: INDIVIDUAL_BACKPACK_CARD_OPTS });
    else fishHost.innerHTML = '';
    if (showStones) patchItemGrid(stoneHost, stoneList || [], totemList || [], INDIVIDUAL_BACKPACK_CARD_OPTS);
    else stoneHost.innerHTML = '';
    if (stoneHost) stoneHost.style.display = showStones ? '' : 'none';
    if (fishHost) fishHost.style.display = showFish ? '' : 'none';
    updateInventoryUploadIndicator(entry);
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
  function parseIconAssetId(item) {
    if (!item) return null;
    const direct = item.iconAssetId || item.imageAssetId;
    if (direct && /^\d{10,22}$/.test(String(direct))) return String(direct);
    const icon = item.icon || item.debugIcon;
    if (icon && typeof icon === 'string') {
      const m = icon.match(/^rbxassetid:\/\/(\d+)$/i);
      if (m && m[1] !== '0' && /^\d{10,22}$/.test(m[1])) return m[1];
    }
    return null;
  }
  function isTrackerBackedImageItem(item) {
    if (!item) return false;
    const src = item.iconSource || item.imageSource || item.dataImageSource;
    return src === 'tracker_lua_game_asset'
      || src === 'gameitemdb_icon'
      || src === 'game_fish_icon_catalog';
  }
  function preferFishCardImageFields(existing, incoming) {
    const base = { ...(existing || {}), ...(incoming || {}) };
    const existingUrlOk = isUsableImageUrl(existing && existing.imageUrl);
    const incomingUrlOk = isUsableImageUrl(incoming && incoming.imageUrl);
    const incomingTracker = isTrackerBackedImageItem(incoming);
    const existingTracker = isTrackerBackedImageItem(existing);
    if (incomingUrlOk && (incomingTracker || !existingUrlOk || isPlaceholderImageUrl(existing && existing.imageUrl))) {
      return base;
    }
    if (existingUrlOk && !incomingUrlOk && !isPlaceholderImageUrl(existing && existing.imageUrl)) {
      return {
        ...base,
        imageUrl: existing.imageUrl,
        imageAssetId: existing.imageAssetId || base.imageAssetId,
        iconAssetId: existing.iconAssetId || base.iconAssetId,
        iconSource: existing.iconSource || base.iconSource,
        imageSource: existing.imageSource || base.imageSource,
        imageResolved: existing.imageResolved != null ? existing.imageResolved : base.imageResolved,
      };
    }
    return base;
  }
  function itemImageSrc(item) {
    if (isUsableImageUrl(item.imageUrl)) return trackerReadPath(item.imageUrl);
    if (isUsableImageUrl(item.cachedImageUrl)) return trackerReadPath(item.cachedImageUrl);
    if (isUsableImageUrl(item.localImageUrl)) return trackerReadPath(item.localImageUrl);
    const assetId = parseIconAssetId(item);
    if (assetId) return `${TRACKER_READ_API}/image/${assetId}`;
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
  function isPlaceholderImageUrl(url) {
    if (!url || typeof url !== 'string') return true;
    const u = url.trim().toLowerCase();
    if (!u) return true;
    if (/fallback|placeholder|default-fish|no-image|missing/i.test(u)) return true;
    if (u.includes('/assets/img/fishit/fallback')) return true;
    return false;
  }
  function isUsableImageUrl(url) {
    if (!url || typeof url !== 'string') return false;
    const u = url.trim();
    if (!u || isPlaceholderImageUrl(u)) return false;
    if (/^\d{10,22}$/.test(u)) return false;
    if (/thumbnails\.roblox\.com/i.test(u)) return false;
    if (/create\.roblox\.com\/store\/asset\//i.test(u)) return false;
    if (u.startsWith('/api/tracker/image/')) return true;
    if (u.startsWith('/api/tracker/assets/fish/')) return true;
    if (u.startsWith('/api/tracker/assets/stones/')) return true;
    if (u.startsWith('/api/tracker/assets/totems/')) return true;
    if (u.startsWith('/api/tracker/assets/')) return true;
    if (u.startsWith('/api/fishit-tracker/image/')) return true;
    if (u.startsWith('/api/fishit-tracker/assets/fish/')) return true;
    if (u.startsWith('/api/fishit-tracker/assets/stones/')) return true;
    if (u.startsWith('/api/fishit-tracker/assets/totems/')) return true;
    if (u.startsWith('/api/fishit-tracker/assets/')) return true;
    if (u.startsWith('/api/fishit/assets/stats-fish/')) return true;
    if (u.startsWith('http')) return true;
    if (u.startsWith('/assets/')) return true;
    return false;
  }
  function onFishImageError(img, item) {
    const attempted = img && img.src ? img.src : '';
    const assetId = parseIconAssetId(item) || (img && img.getAttribute('data-asset-id'));
    const trackerImagePath = `${TRACKER_READ_API}/image/${assetId}`;
    if (assetId && !attempted.includes(trackerImagePath)) {
      img.onerror = () => {
        img.onerror = null;
        img.src = ITEM_IMAGES.Default;
        img.setAttribute('data-placeholder', 'true');
      };
      img.src = trackerImagePath;
      return;
    }
    console.warn('[fishit-tracker-ui] image load failed', {
      name: item && item.name,
      itemId: item && item.itemId,
      imageAssetId: item && item.imageAssetId,
      iconAssetId: item && item.iconAssetId,
      imageUrl: item && item.imageUrl,
      imageResolved: item && item.imageResolved,
      imageStatus: item && item.imageStatus,
      attempted,
    });
    img.onerror = null;
    img.src = ITEM_IMAGES.Default;
    img.setAttribute('data-placeholder', 'true');
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
    reconcileActiveAccountAfterRemoval();
    renderAccountsTable();
    syncViewModeUi();
  }
  function cardTitle(item) {
    return item.cardName || item.baseFishName || item.name || 'Unknown';
  }
  function attachFtCardItem(card, item, kind) {
    if (!card) return;
    try { card.__ftItem = item; card.__ftKind = kind; } catch (_) {}
    card.classList.add('ft-card--interactive');
    if (!card.hasAttribute('tabindex')) card.setAttribute('tabindex', '0');
    card.setAttribute('role', 'button');
  }
  function ftCleanDetailName(rawName, tag) {
    let name = String(rawName || '').trim();
    name = name.replace(/^\[[^\]]*\]\s*/, '').trim();
    const t = String(tag || '').trim();
    if (t && name.toLowerCase().startsWith(`${t.toLowerCase()} `) && name.length > t.length + 1) {
      name = name.slice(t.length).trim();
    }
    name = name.replace(/\b([\p{L}\p{N}]+)(\s+\1\b)+/giu, '$1');
    return name || String(rawName || '').trim();
  }
  function ftDetailMeta(item, kind) {
    const k = String(kind || item.category || 'fish').toLowerCase();
    let rawName;
    let tag;
    if (k === 'stone') {
      rawName = (typeof stoneDisplayName === 'function') ? stoneDisplayName(item) : (item.name || 'Enchant Stone');
      tag = String(item.stoneType || item.StoneType || '').trim();
    } else if (k === 'totem') {
      rawName = String(item.name || item.displayName || 'Totem');
      tag = ftExtractMutation(item);
    } else {
      rawName = (typeof cardTitle === 'function') ? cardTitle(item) : (item.name || item.baseFishName || 'Fish');
      tag = ftExtractMutation(item);
    }
    return {
      tag: String(tag || '').trim(),
      name: ftCleanDetailName(rawName, tag),
      rarity: (typeof publicRarity === 'function') ? publicRarity(item) : '',
    };
  }
  function ftDetailOwnerRows(item) {
    const rows = [];
    const amounts = item && item.ownerAmounts && typeof item.ownerAmounts === 'object' ? item.ownerAmounts : null;
    if (amounts) {
      for (const username of Object.keys(amounts)) {
        rows.push({ username: String(username || ''), amount: Math.max(0, Math.floor(Number(amounts[username]) || 0)) });
      }
    } else if (Array.isArray(item && item.owners) && item.owners.length) {
      const per = Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0));
      const each = item.owners.length ? Math.floor(per / item.owners.length) : per;
      item.owners.forEach((username) => rows.push({ username: String(username || ''), amount: each }));
    }
    return rows.sort((a, b) => {
      const cmp = a.username.toLowerCase().localeCompare(b.username.toLowerCase());
      if (cmp) return cmp;
      return a.username.localeCompare(b.username);
    });
  }
  function collectGroupInstances(item, kind) {
    const k = String(kind || (item && item.category) || 'fish').toLowerCase();
    let targetKey;
    try { targetKey = (item && item.groupKey) || bulkGroupKey(k, item || {}); } catch (_) { targetKey = item && item.groupKey; }
    const out = [];
    let sessions = [];
    try { sessions = collectBulkSessions(); } catch (_) { sessions = []; }
    for (const s of sessions) {
      const list = k === 'stone' ? s.stoneList : (k === 'totem' ? s.totemList : s.fishList);
      for (const row of list || []) {
        let key;
        try { key = bulkGroupKey(k, row); } catch (_) { key = null; }
        if (key && key === targetKey) out.push({ owner: String(s.username || ''), row });
      }
    }
    return out;
  }
  function ftWeightNum(weight) {
    const parsed = parseFishWeight(weight);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  const FT_MAX_INSTANCE_CARDS = 800;
  function ftWeightKgText(kg) {
    return formatFishWeight(kg);
  }
  function ftFishInstanceCards(instances) {
    const cards = [];
    const pushCard = (card) => {
      cards.push(card);
      return cards.length < FT_MAX_INSTANCE_CARDS;
    };
    for (const { owner, row } of instances) {
      const imgSrc = (typeof itemImageSrc === 'function') ? (itemImageSrc(row) || '') : '';
      const baseName = ftExtractBaseName(row);
      const rarity = ftRarityKey(row);
      const list = Array.isArray(row && row.ownedInstances) ? row.ownedInstances : null;
      if (list && list.length) {
        for (const inst of list) {
          const amount = Math.max(1, Math.floor(Number(inst.quantity) || 1));
          const mutation = ftNormalizeNonNil(inst && (inst.mutationName || inst.mutation || inst.metadataMutation));
          const name = (inst && (inst.cleanName || inst.baseFishName) && String(inst.cleanName || inst.baseFishName).trim()) || baseName;
          const weightRaw = (inst && (inst.weightKg != null ? inst.weightKg
            : (inst.metadataWeightKg != null ? inst.metadataWeightKg : inst.weight)));
          const weight = formatFishWeight(weightRaw);
          for (let i = 0; i < amount; i += 1) {
            if (!pushCard({
              owner, mutation, name, weight, weightRaw, imgSrc, rarity, cleanName: name,
            })) return cards.sort(sortFishDetailInstances);
          }
        }
      } else {
        const amount = Math.max(1, Math.floor(Number(resolveItemAmount(row)) || 1));
        const mutation = ftNormalizeNonNil(ftExtractMutation(row));
        const weightRaw = row && (row.weightKg != null ? row.weightKg : row.weight);
        const weight = weightRaw != null ? formatFishWeight(weightRaw) : ftItemWeightText(row);
        for (let i = 0; i < amount; i += 1) {
          if (!pushCard({
            owner, mutation, name: baseName, weight, weightRaw, imgSrc, rarity, cleanName: baseName,
          })) return cards.sort(sortFishDetailInstances);
        }
      }
    }
    return cards.sort(sortFishDetailInstances);
  }
  function ftFilterInstanceCards(cards, query) {
    const q = String(query || '').toLowerCase().trim();
    if (!q) return cards;
    const terms = q.split(/\s+/).filter(Boolean);
    return cards.filter((c) => {
      const name = String(c.name || '').toLowerCase();
      const mut = String(c.mutation || '').toLowerCase();
      const combined = `${mut} ${name}`.trim();
      return terms.every((t) => name.includes(t) || mut.includes(t) || combined.includes(t));
    });
  }
  function ftSortFishCards(cards) {
    return cards.sort(sortFishDetailInstances);
  }
  function renderFishInstanceCard(card) {
    const realMut = ftNormalizeNonNil(card.mutation);
    const slug = realMut ? ftDetailMutationSlug(realMut) : '';
    const classes = ['tracker-detail-fish-card'];
    let styleAttr = '';
    if (realMut) {
      classes.push('has-mutation');
      if (slug) classes.push(`mutation-${slug}`);
      styleAttr = ` style="${escHtml(ftDetailMutationThemeVars(realMut, card.rarity))}"`;
    }
    const img = card.imgSrc
      ? `<div class="tracker-detail-fish-card__img"><img src="${escHtml(card.imgSrc)}" alt="${escHtml(card.name)}" decoding="async" loading="lazy"></div>`
      : '<div class="tracker-detail-fish-card__img" aria-hidden="true">&#x1F41F;</div>';
    const weight = card.weight
      ? `<div class="tracker-detail-fish-weight">${escHtml(card.weight)}</div>`
      : '<div class="tracker-detail-fish-weight tracker-detail-fish-weight--unknown">Weight unknown</div>';
    const mut = realMut
      ? `<div class="tracker-detail-fish-mutation">${escHtml(realMut)}</div>`
      : '';
    return `<div class="${escHtml(classes.join(' '))}"${styleAttr}>${img}<div class="tracker-detail-fish-card__body">${mut}<div class="tracker-detail-fish-name">${escHtml(card.name)}</div>${weight}<div class="tracker-detail-fish-owner">${escHtml(card.owner || '-')}</div></div></div>`;
  }
  function ftBreakdownRows(instances, item) {
    const map = new Map();
    for (const { owner, row } of instances) {
      const mutation = ftExtractMutation(row);
      const name = ftExtractBaseName(row);
      const amount = Math.max(1, Math.floor(Number(resolveItemAmount(row)) || 1));
      const key = `${owner.toLowerCase()}|${mutation.toLowerCase()}|${name.toLowerCase()}`;
      const prev = map.get(key);
      if (prev) prev.amount += amount;
      else map.set(key, { owner, mutation, name, amount });
    }
    let rows = [...map.values()];
    if (!rows.length) {
      const fallbackMut = ftExtractMutation(item);
      rows = ftDetailOwnerRows(item).map((r) => ({ owner: r.username, mutation: fallbackMut, name: '', amount: r.amount }));
    }
    return rows.sort((a, b) => {
      const c1 = a.owner.toLowerCase().localeCompare(b.owner.toLowerCase());
      if (c1) return c1;
      return String(a.mutation || '').toLowerCase().localeCompare(String(b.mutation || '').toLowerCase());
    });
  }
  function renderBreakdownRow(row) {
    const realMut = ftNormalizeNonNil(row.mutation);
    const mut = realMut
      ? `<span class="ft-detail-owner__mut" style="${escHtml(ftMutationStyle(realMut))}">${escHtml(realMut)}</span>`
      : '';
    return `<div class="ft-detail-owner"><span class="ft-detail-owner__main">${mut}<span class="ft-detail-owner__name">${escHtml(row.owner || '-')}</span></span><span class="ft-detail-owner__qty">x${formatQuantity(row.amount)}</span></div>`;
  }
  let ftDetailPanelEl = null;
  let ftDetailHostEl = null;
  function ftDetailHost(sourceCard) {
    const bulk = document.getElementById('bulkInventoryBody');
    if (sourceCard && bulk && bulk.contains(sourceCard)) return bulk;
    const body = sourceCard && sourceCard.closest && sourceCard.closest('[data-card-body]');
    if (body) return body;
    return bulk || document.getElementById('bulkInventoryPanel');
  }
  function ensureFtDetailPanel() {
    if (ftDetailPanelEl) return ftDetailPanelEl;
    const panel = document.createElement('section');
    panel.className = 'ft-detail-panel';
    panel.id = 'ftInlineDetail';
    panel.hidden = true;
    panel.innerHTML = [
      '<div class="ft-detail-panel__head">',
      '  <button type="button" class="ft-detail-back" data-ft-detail-back aria-label="Back to grid">',
      '    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><polyline points="15 18 9 12 15 6"></polyline></svg>Back',
      '  </button>',
      '  <div class="ft-detail-icon" data-ft-detail-icon></div>',
      '  <div class="ft-detail-headtext">',
      '    <div class="ft-detail-tag" data-ft-detail-tag></div>',
      '    <div class="ft-detail-name" data-ft-detail-name></div>',
      '    <div class="ft-detail-count" data-ft-detail-count></div>',
      '  </div>',
      buildSectionUploadIndicatorHtml('data-detail-upload-indicator', 'Backpack detail upload status'),
      '</div>',
      '<div class="ft-detail-search" data-ft-detail-search-wrap hidden>',
      '  <input type="search" class="ft-detail-search__input" data-ft-detail-search placeholder="Search by name or mutation..." autocomplete="off" spellcheck="false" aria-label="Search fish instances by name or mutation">',
      '</div>',
      '<div data-ft-detail-body></div>',
    ].join('');
    panel.addEventListener('click', (e) => {
      if (e.target.closest && e.target.closest('[data-ft-detail-back]')) {
        e.preventDefault();
        closeFtDetail();
      }
    });
    const searchInput = panel.querySelector('[data-ft-detail-search]');
    if (searchInput) {
      searchInput.addEventListener('input', () => {
        if (Array.isArray(panel.__ftCards)) ftRenderFishInstances(panel, panel.__ftCards, searchInput.value);
      });
    }
    ftDetailPanelEl = panel;
    return panel;
  }
  function ftRenderFishInstances(panel, cards, query) {
    const bodyEl = panel.querySelector('[data-ft-detail-body]');
    if (!bodyEl) return;
    bodyEl.className = 'ft-detail-instances';
    const filtered = ftFilterInstanceCards(cards, query);
    if (!filtered.length) {
      bodyEl.classList.add('is-empty');
      bodyEl.innerHTML = cards.length
        ? '<div class="ft-detail-owner ft-detail-owner--empty">No matching fish instances</div>'
        : '<div class="ft-detail-owner ft-detail-owner--empty">No fish data</div>';
      return;
    }
    bodyEl.classList.remove('is-empty');
    bodyEl.innerHTML = filtered.map(renderFishInstanceCard).join('');
  }
  function closeFtDetail() {
    if (!ftDetailPanelEl) return;
    ftDetailPanelEl.hidden = true;
    if (ftDetailPanelEl.parentElement) ftDetailPanelEl.parentElement.removeChild(ftDetailPanelEl);
    if (ftDetailHostEl) {
      Array.from(ftDetailHostEl.children).forEach((el) => {
        if (el.getAttribute && el.getAttribute('data-ft-hidden') === '1') el.removeAttribute('data-ft-hidden');
      });
    }
    document.querySelectorAll('[data-ft-hidden="1"]').forEach((el) => el.removeAttribute('data-ft-hidden'));
    ftDetailHostEl = null;
  }
  function clearInlineDetailState(reason) {
    if (ftDetailPanelEl) ftDetailPanelEl.__ftCards = null;
    closeFtDetail();
  }
  function populateFtDetail(panel, item, kind) {
    const k = String(kind || (item && item.category) || 'fish').toLowerCase();
    const meta = ftDetailMeta(item, k);
    const iconEl = panel.querySelector('[data-ft-detail-icon]');
    const tagEl = panel.querySelector('[data-ft-detail-tag]');
    const nameEl = panel.querySelector('[data-ft-detail-name]');
    const countEl = panel.querySelector('[data-ft-detail-count]');
    const bodyEl = panel.querySelector('[data-ft-detail-body]');
    const imgSrc = (typeof itemImageSrc === 'function') ? itemImageSrc(item) : null;
    if (iconEl) {
      iconEl.innerHTML = imgSrc
        ? `<img src="${escHtml(imgSrc)}" alt="${escHtml(meta.name)}" decoding="async">`
        : '<span class="ft-detail-icon__fallback">&#x1F4E6;</span>';
    }
    if (tagEl) {
      if (meta.tag) { tagEl.textContent = meta.tag; tagEl.hidden = false; tagEl.style.color = ftMutationColor(meta.tag) || '#93c5fd'; }
      else { tagEl.textContent = ''; tagEl.hidden = true; }
    }
    if (nameEl) nameEl.textContent = meta.name;
    const instances = collectGroupInstances(item, k);
    const searchWrap = panel.querySelector('[data-ft-detail-search-wrap]');
    const searchInput = panel.querySelector('[data-ft-detail-search]');
    if (k === 'fish') {
      const cards = ftFishInstanceCards(instances);
      panel.__ftCards = cards;
      if (countEl) countEl.textContent = `${cards.length} ${cards.length === 1 ? 'fish' : 'fish'}`;
      if (searchInput) searchInput.value = '';
      if (searchWrap) searchWrap.hidden = false;
      ftRenderFishInstances(panel, cards, '');
    } else {
      panel.__ftCards = null;
      if (searchWrap) searchWrap.hidden = true;
      const rows = ftBreakdownRows(instances, item);
      const total = rows.reduce((s, r) => s + r.amount, 0) || Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0));
      if (countEl) countEl.textContent = `Total x${formatQuantity(total)}`;
      if (bodyEl) {
        bodyEl.className = 'ft-detail-owners';
        bodyEl.innerHTML = rows.length
          ? rows.map(renderBreakdownRow).join('')
          : '<div class="ft-detail-owner ft-detail-owner--empty">No ownership data</div>';
      }
    }
  }
  function openFtDetail(item, kind, sourceCard) {
    if (!item) return;
    const host = ftDetailHost(sourceCard);
    if (!host) return;
    closeFtDetail();
    ftDetailHostEl = host;
    const panel = ensureFtDetailPanel();
    Array.from(host.children).forEach((el) => {
      if (el !== panel) el.setAttribute('data-ft-hidden', '1');
    });
    const searchRow = document.querySelector('#bulkInventoryPanel [data-bulk-search-row]');
    if (searchRow) searchRow.setAttribute('data-ft-hidden', '1');
    host.appendChild(panel);
    populateFtDetail(panel, item, kind);
    panel.hidden = false;
    updateInventoryUploadIndicator();
    const back = panel.querySelector('[data-ft-detail-back]');
    if (back) setTimeout(() => { try { back.focus(); } catch (_) {} }, 0);
    try { panel.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (_) {}
  }
  function bindFtDetail() {
    document.addEventListener('click', (e) => {
      if (e.target.closest && e.target.closest('[data-ft-detail-back]')) return;
      const card = e.target.closest && e.target.closest('.ft-card--interactive');
      if (!card || !card.__ftItem) return;
      openFtDetail(card.__ftItem, card.__ftKind, card);
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && ftDetailPanelEl && !ftDetailPanelEl.hidden) {
        e.preventDefault();
        closeFtDetail();
        return;
      }
      if ((e.key === 'Enter' || e.key === ' ') && e.target && e.target.classList && e.target.classList.contains('ft-card--interactive') && e.target.__ftItem) {
        e.preventDefault();
        openFtDetail(e.target.__ftItem, e.target.__ftKind, e.target);
      }
    });
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
    <img src="${escHtml(imgSrc)}" alt="${escHtml(title)}" title="${escHtml(title)}" decoding="async" loading="lazy" width="54" height="54"${isPlaceholder ? ' data-placeholder="true"' : ''} data-item-id="${escHtml(item.itemId || '')}" data-asset-id="${escHtml(item.imageAssetId || '')}">
  </div>
  <div class="ft-card-main">
    <div class="ft-card-name" title="${escHtml(title)}">${escHtml(title)}</div>
    ${statsHtml}
    ${weightHtml}
  </div>`;
  }
  function buildItemCardElement(item, opts) {
    const card = document.createElement('div');
    card.className = fishCardClassList(item).join(' ');
    card.setAttribute('data-card-key', (opts && opts.keyFn ? opts.keyFn(item) : cardKey(item)));
    if (item.dataSource) card.setAttribute('data-source', item.dataSource);
    if (item.dataImageSource) card.setAttribute('data-image-source', item.dataImageSource);
    if (item.dataRaritySource) card.setAttribute('data-rarity-source', item.dataRaritySource);
    if (item.mutation) card.setAttribute('data-mutation', item.mutation);
    if (item.shiny === true) card.setAttribute('data-shiny', 'true');
    const rarityLow = publicRarity(item);
    if (rarityLow) card.setAttribute('data-rarity', rarityLow.toLowerCase());
    card.innerHTML = buildFishCardInnerHtml(item, opts);
    markCardEnterAnimation(card);
    const img = card.querySelector('.ft-card-icon img');
    if (img) img.onerror = () => onFishImageError(img, item);
    attachFtCardItem(card, item, 'fish');
    return card;
  }
  function patchItemCardElement(card, item, opts) {
    const rarity = publicRarity(item);
    const rarityLow = rarity ? rarity.toLowerCase() : '';
    const keyFn = opts && opts.keyFn ? opts.keyFn : cardKey;
    card.setAttribute('data-card-key', keyFn(item));
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
    patchFishCardDom(card, item, opts);
    attachFtCardItem(card, item, 'fish');
  }
  function patchItemsGrid(container, items, opts) {
    opts = opts || {};
    const keyFn = opts.keyFn || cardKey;
    const buildOpts = opts.buildOpts;
    if (!items || items.length === 0) {
      if (!container.querySelector('.card-empty')) {
        container.innerHTML = '<div class="card-empty">&#x1F9F3; Inventory is empty.</div>';
      }
      return;
    }
    const fishTotal = items.reduce((sum, item) => sum + Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0)), 0);
    const titleText = `Fishes (${formatQuantity(fishTotal)})`;
    ensureFishGridUploadIndicator(container, titleText);
    let grid = container.querySelector('.items-grid');
    if (!grid) {
      grid = document.createElement('div');
      grid.className = 'items-grid inventory-grid fish-grid';
      container.appendChild(grid);
    }
    const empty = container.querySelector('.card-empty');
    if (empty) empty.remove();
    const nextKeys = new Set();
    const existing = new Map();
    grid.querySelectorAll('.ft-card--fish[data-card-key]').forEach((el) => {
      existing.set(el.getAttribute('data-card-key'), el);
    });
    items.forEach((item, idx) => {
      const key = keyFn(item);
      const itemOpts = typeof buildOpts === 'function' ? buildOpts(item, idx) : (opts.cardOpts || {});
      const cardOpts = { ...itemOpts, eagerImage: idx < 16 };
      nextKeys.add(key);
      let card = existing.get(key);
      if (!card) {
        card = buildItemCardElement(item, { ...cardOpts, keyFn });
        placeGridCardAtIndex(grid, card, idx);
      } else {
        patchItemCardElement(card, item, { ...cardOpts, keyFn });
        placeGridCardAtIndex(grid, card, idx);
      }
    });
    existing.forEach((el, key) => {
      if (!nextKeys.has(key)) el.remove();
    });
    wireFishImageErrors(grid);
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
    el.className = 'tracker-card tracker-card--account-inventory';
    el.dataset.user = username.toLowerCase();
    el.innerHTML = `
${DEBUG_INVENTORY ? '<div data-global-db-proof></div>' : ''}
<div class="card-body" data-card-body></div>`;
    el.style.display = 'none';
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
    if (l) l.textContent = DEBUG_INVENTORY ? `Live ${EM_DASH} User ID: ${data.userId || '-'}` : '';
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
        return `Player data found ${EM_DASH} reading inventory...`;
      case 'startup':
      case 'live':
      default:
        return `Script running ${EM_DASH} locating Replion data...`;
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
    if (b) b.innerHTML = publicLiveEmptyHtml(data);
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
    const snapData = offlineEntry ? resolveEntryPublicSnapshot(offlineEntry, lastData) : lastData;
    if (snapData) {
      const fishList = getPublicFishItems(snapData);
      const stoneList = getPublicStoneItems(snapData);
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
    if (!entry.lastData) setCardError(entry.el);
  }
  function backpackQuerySuffix(forceFresh) {
    const useLite = !(DEBUG_INVENTORY && DEBUG_GLOBAL);
    const params = new URLSearchParams();
    if (useLite) params.set('lite', '1');
    if (FT_DEBUG_METADATA) params.set('debug', 'metadata');
    params.set('_', String(Date.now()));
    if (forceFresh) params.set('fresh', '1');
    const qs = params.toString();
    return qs ? `?${qs}` : '';
  }
  function notePerfFetch(responseText) {
    perfInitialRequests += 1;
    if (responseText) perfApiPayloadBytes += responseText.length;
    if (perfFirstApiMs == null) {
      perfFirstApiMs = Math.round(((typeof performance !== 'undefined' && performance.now)
        ? performance.now()
        : Date.now()) - PERF_STARTED_AT);
    }
  }
  function readReadApiPresence(res) {
    if (!res || !res.headers || !res.headers.get) return null;
    const g = (n) => res.headers.get(n);
    const ps = g('X-DENG-Presence-State');
    if (!ps) return null;
    const numOrNull = (v) => {
      if (v == null || v === '') return null;
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    };
    return {
      presenceState: ps,
      isOnline: g('X-DENG-Is-Online') === '1',
      statusAgeSeconds: numOrNull(g('X-DENG-Status-Age')),
      inventoryAgeSeconds: numOrNull(g('X-DENG-Inventory-Age')),
      leaderstatsAgeSeconds: numOrNull(g('X-DENG-Leaderstats-Age')),
      lastRealStatusAt: g('X-DENG-Last-Real-Status-At') || null,
      lastRealInventoryAt: g('X-DENG-Last-Real-Inventory-At') || null,
      lastRealLeaderstatsAt: g('X-DENG-Last-Real-Leaderstats-At') || null,
      statusRevision: numOrNull(g('X-DENG-Status-Revision')),
      statusReportId: g('X-DENG-Status-Report-Id') || null,
      statusSeq: numOrNull(g('X-DENG-Status-Seq')),
      statusDecisionReason: g('X-DENG-Status-Decision') || null,
      missedStatusReports: numOrNull(g('X-DENG-Missed-Status-Reports')),
      isStatusStale: g('X-DENG-Status-Stale') === '1',
      leaderstatsRevision: numOrNull(g('X-DENG-Leaderstats-Revision')),
      inventoryRevision: numOrNull(g('X-DENG-Inventory-Revision')),
      preservedDataReason: g('X-DENG-Preserved-Data-Reason') || null,
      snapshotSource: g('X-DENG-Snapshot-Source') || 'precomputed',
      hasRenderableData: g('X-DENG-Has-Renderable') === '1',
      snapshotHash: g('X-DENG-Snapshot-Hash') || '',
      unchanged: g('X-DENG-Unchanged') === '1',
    };
  }
  function applyAuthPresence(entry, key, contract) {
    if (!entry || !contract) return;
    const prevAuth = entry._auth || null;
    entry._auth = contract;
    entry._presenceLive = contract.isOnline === true;
    refreshEntryTableSyncDisplay(entry, key);
    refreshEntrySyncDisplay(entry);
    entry._laneTimerProof = {
      statusAdvanced: laneTimestampAdvanced(prevAuth, contract, 'status'),
      leaderstatsAdvanced: laneTimestampAdvanced(prevAuth, contract, 'leaderstats'),
      inventoryAdvanced: laneTimestampAdvanced(prevAuth, contract, 'inventory'),
      lastRealStatusAt: contract.lastRealStatusAt || null,
      lastRealLeaderstatsAt: contract.lastRealLeaderstatsAt || null,
      lastRealInventoryAt: contract.lastRealInventoryAt || null,
    };
  }
  async function pollUser(key, opts) {
    const entry = trackers.get(key);
    if (!entry) return;
    const forceFresh = opts && opts.forceFresh === true;
    const requestId = (entry._pollReqSeq = (entry._pollReqSeq || 0) + 1);
    const isStaleResponse = () => !trackers.has(key) || entry._pollReqSeq !== requestId;
    const hashSuffix = entry._snapshotHash ? `&h=${encodeURIComponent(entry._snapshotHash)}` : '';
    const reqUrl = `${TRACKER_READ_API}/get-backpack/${encodeURIComponent(key)}${backpackQuerySuffix(forceFresh)}${hashSuffix}`;
    try {
      const res = await fetch(reqUrl, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'Cache-Control': 'no-cache', Pragma: 'no-cache' },
      });
      if (isStaleResponse()) return;
      if (res.status === 404) {
        ftRenderMetadataDebug(key, { url: reqUrl, status: 404, empty: true, reason: 'no_tracking_session (404)' });
        if (entry.lastData) {
          setCardOffline(entry.el, entry.displayName, entry.lastData);
        } else {
          setCardWaiting(entry.el, entry.displayName);
        }
        return;
      }
      if (!res.ok) {
        ftRenderMetadataDebug(key, { url: reqUrl, status: res.status, empty: true, reason: `http_${res.status}` });
        if (!entry.lastData) setCardRefreshFailed(entry);
        return;
      }
      const contract = readReadApiPresence(res);
      if (contract && contract.unchanged) {
        applyAuthPresence(entry, key, contract);
        return;
      }
      const raw = await res.text();
      if (isStaleResponse()) return;
      notePerfFetch(raw);
      const data = JSON.parse(raw);
      if (contract && contract.snapshotHash) entry._snapshotHash = contract.snapshotHash;
      if (FT_DEBUG_METADATA) {
        const fishLen = (data.fishItems || data.publicFishItems || []).length;
        const stoneLen = (data.stoneItems || []).length;
        ftRenderMetadataDebug(key, {
          url: reqUrl,
          status: res.status,
          empty: !fishLen && !stoneLen && !data.playerStats,
          reason: (!fishLen && !stoneLen) ? 'no_inventory_rows_in_payload' : '',
          metadataDebug: data.metadataDebug || null,
        });
      }
      debugLogEntryPlayerStats(entry);
      applyInventoryPollPayload(entry, key, data);
      if (contract) applyAuthPresence(entry, key, contract);
    } catch (_) { if (trackers.has(key) && !entry.lastData) setCardRefreshFailed(entry); }
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
    usernameErrorEl.classList.remove('tracker-username-action-notice');
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
      setCopyStatus(`Copy failed ${EM_DASH} select the script box and copy manually.`, true);
      return false;
    });
  }
  function setCopyStatus(message, isError, isSuccess) {
    if (!copyStatusEl) return;
    copyStatusEl.textContent = message || '';
    copyStatusEl.classList.toggle('is-error', !!isError);
    copyStatusEl.classList.toggle('is-success', !!isSuccess);
  }
  let liveTrackerPollingActive = false;
  let globalStatusPollTimer = null;
  let dashboardPollTimer = null;
  let syncTickTimer = null;
  function startTrackerPollForKey(key) {
    const entry = trackers.get(key);
    if (!entry || entry.timer) return;
    entry.timer = setInterval(() => pollUser(key), POLL_MS);
    pollUser(key);
  }
  function stopTrackerPollForKey(key) {
    const entry = trackers.get(key);
    if (!entry || !entry.timer) return;
    clearInterval(entry.timer);
    entry.timer = null;
  }
  function startLiveTrackerPolling() {
    if (liveTrackerPollingActive) return;
    liveTrackerPollingActive = true;
    trackers.forEach((_, key) => startTrackerPollForKey(key));
    if (globalStatusPollTimer) {
      clearInterval(globalStatusPollTimer);
      globalStatusPollTimer = null;
    }
    globalStatusPollTimer = setInterval(() => pollAccountStatuses(false), POLL_MS);
    requestAnimationFrame(() => pollAccountStatuses(true));
  }
  function stopLiveTrackerPolling() {
    liveTrackerPollingActive = false;
    trackers.forEach((_, key) => stopTrackerPollForKey(key));
    if (globalStatusPollTimer) {
      clearInterval(globalStatusPollTimer);
      globalStatusPollTimer = null;
    }
  }
  function startDashboardPolling() {
    if (dashboardPollTimer) return;
    dashboardPollTimer = setInterval(() => {
      if (activeInventorySection === 'dashboard') loadDashboardRange(false);
    }, POLL_MS);
  }
  function ensureBackgroundPolling() {
    if (trackers.size > 0) startLiveTrackerPolling();
    else if (!globalStatusPollTimer) {
      globalStatusPollTimer = setInterval(() => pollAccountStatuses(false), POLL_MS);
      requestAnimationFrame(() => pollAccountStatuses(true));
    }
    startDashboardPolling();
  }
  function addTrackerLocal(username) {
    clearUsernameError();
    const raw = normalizeUsername(username);
    const key = raw.toLowerCase();
    if (!raw || !/^[a-z0-9_]{3,20}$/.test(key)) return false;
    if (trackers.has(key)) return false;
    const card  = createCard(raw);
    trackerListEl.appendChild(card);
    trackers.set(key, { timer: null, el: card, displayName: raw, lastData: null, liveSnapshot: null, lastSyncAt: null, lastPollOkAt: null, lastStatsPollAt: null, lastServerSyncAt: null, playerStats: null, lastFishList: null, lastStoneList: null, uploadStatus: null, _uploadStatusFetchedAtMs: null });
    startTrackerPollForKey(key);
    return true;
  }
  async function addTracker(username) {
    clearUsernameError();
    const raw = normalizeUsername(username);
    const key = raw.toLowerCase();
    if (!raw) {
      showUsernameError('Enter a Roblox username.');
      if (inputEl) inputEl.focus();
      return false;
    }
    if (!/^[a-z0-9_]{3,20}$/.test(key)) {
      showUsernameError(`Username must be 3${EN_DASH}20 characters using letters, numbers, or underscore only.`);
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
    const saved = await persistTrackerAdd(raw);
    if (!saved) return false;
    clearUsernameError();
    if (inputEl) inputEl.value = '';
    return true;
  }
  async function removeTracker(key) {
    if (!trackers.has(key)) return;
    const removed = await persistTrackerRemove(key);
    if (!removed) {
      showUsernameError('Could not remove tracked account.');
      return;
    }
    reconcileActiveAccountAfterRemoval();
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
  function bindSidebarProfileControls() {
    if (hideUsernamesBtn) {
      hideUsernamesBtn.addEventListener('click', () => {
        hideUsernames = !hideUsernames;
        try { localStorage.setItem(LS_HIDE_USERNAMES, hideUsernames ? '1' : '0'); } catch {}
        updateHideUsernamesUi();
        refreshAllUsernameDisplays();
        renderAccountsTable();
      });
    }
    try {
      hideUsernames = localStorage.getItem(LS_HIDE_USERNAMES) === '1';
    } catch {}
    updateHideUsernamesUi();
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
    addBtn.addEventListener('click', (e) => {
      e.preventDefault();
      const v = inputEl.value;
      addTracker(v).then((ok) => { if (ok && inputEl) inputEl.value = ''; }).catch(() => {
        showUsernameError('Could not save tracked account.');
      });
      inputEl.focus();
    });
    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const v = inputEl.value;
        addTracker(v).then((ok) => { if (ok) inputEl.value = ''; }).catch((err) => {
          showUsernameError(DEBUG_INVENTORY && err && err.message ? err.message : 'Could not save tracked account.');
        });
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
  async function submitMultipleAdd() {
    const raw = multipleAddTextareaEl ? multipleAddTextareaEl.value : '';
    const names = parseMultipleUsernames(raw);
    if (!names.length) {
      showMultipleAddError('Enter at least one username.');
      if (multipleAddTextareaEl) multipleAddTextareaEl.focus();
      return;
    }
    showMultipleAddError('');
    const pending = names.filter((name) => !trackers.has(String(name || '').trim().toLowerCase()));
    if (pending.length) {
      const result = await persistTrackerAddMany(pending);
      if (!result) return;
    } else {
      showMultipleAddError('All entered usernames are already being tracked.');
      return;
    }
    if (inputEl) inputEl.value = '';
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
  function showRemoveAllError(message) {
    if (!removeAllErrorEl) return;
    if (message) {
      removeAllErrorEl.textContent = message;
      removeAllErrorEl.hidden = false;
    } else {
      removeAllErrorEl.textContent = '';
      removeAllErrorEl.hidden = true;
    }
  }
  function openRemoveAllModal() {
    if (!removeAllModalEl) return;
    showRemoveAllError('');
    removeAllModalEl.hidden = false;
    removeAllModalEl.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    if (removeAllCancelEl) setTimeout(() => removeAllCancelEl.focus(), 0);
  }
  function closeRemoveAllModal() {
    if (!removeAllModalEl) return;
    removeAllModalEl.hidden = true;
    removeAllModalEl.setAttribute('aria-hidden', 'true');
    showRemoveAllError('');
    document.body.style.overflow = '';
  }
  async function confirmRemoveAll() {
    if (!trackers.size) {
      closeRemoveAllModal();
      return;
    }
    if (removeAllConfirmEl) removeAllConfirmEl.disabled = true;
    try {
      const ok = await persistTrackerRemoveAll();
      if (!ok) {
        showRemoveAllError('Could not remove all usernames. Please try again.');
        return;
      }
      closeRemoveAllModal();
      reconcileActiveAccountAfterRemoval();
    } finally {
      if (removeAllConfirmEl) removeAllConfirmEl.disabled = false;
    }
  }
  function bindRemoveAllModal() {
    if (removeAllCancelEl) removeAllCancelEl.addEventListener('click', closeRemoveAllModal);
    if (removeAllConfirmEl) removeAllConfirmEl.addEventListener('click', confirmRemoveAll);
    if (removeAllModalEl) {
      removeAllModalEl.addEventListener('click', (e) => {
        if (e.target === removeAllModalEl) closeRemoveAllModal();
      });
    }
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && removeAllModalEl && !removeAllModalEl.hidden) {
        e.preventDefault();
        closeRemoveAllModal();
      }
    });
  }
  function bindUsernameBulkActions() {
    bindRemoveAllModal();
    if (removeOfflineBtn) {
      removeOfflineBtn.addEventListener('click', () => {
        removeOfflineTrackers().catch(() => {
          showUsernameError('Could not remove offline usernames.');
        });
      });
    }
    if (removeNoDataBtn) {
      removeNoDataBtn.addEventListener('click', () => {
        removeNoDataTrackers().catch(() => {
          showUsernameError('Could not remove no-data usernames.');
        });
      });
    }
    if (removeAllBtn) {
      removeAllBtn.addEventListener('click', () => {
        openRemoveAllModal();
      });
    }
  }
  const dashboardPanelEl = document.getElementById('inventoryDashboardPanel');
  const accountsPanelEl = document.getElementById('inventoryAccountsPanel');
  const dashboardToolbarEl = document.querySelector('.dashboard-toolbar');
  const dashboardPeriodBtns = document.querySelectorAll('[data-dashboard-period]');
  const dashboardCustomToggleEl = document.getElementById('dashboardCustomToggle');
  const dashboardCustomPanelEl = document.getElementById('dashboardCustomPanel');
  const dashboardCustomFromEl = document.getElementById('dashboardCustomFrom');
  const dashboardCustomToEl = document.getElementById('dashboardCustomTo');
  const dashboardCustomApplyEl = document.getElementById('dashboardCustomApply');
  const mainSectionNavTabs = document.querySelectorAll('[data-inventory-section]');
  let activeInventorySection = 'accounts';
  let dashboardPeriod = 'all';
  let dashboardCustomFrom = '';
  let dashboardCustomTo = '';
  const dashboardMemCache = new Map();
  const DASHBOARD_MEM_TTL_MS = 10_000;
  const DASHBOARD_SWR_STALE_MS = 10_000;
  const DASHBOARD_RANGE_DEBOUNCE_MS = 120;
  let dashboardFetchAbort = null;
  let dashboardFetchGen = 0;
  let dashboardRangeDebounceTimer = null;
  let dashboardInitialLoaded = false;
  function dashboardCountUp() {
    return window.DengCountUpStats || null;
  }
  function isDesktopSidebarNavLayout() {
    if (document.body.classList.contains('inventory-apk-embed')) return false;
    return window.matchMedia('(min-width: 769px)').matches;
  }
  let mobileTrackerNavLayoutTimer = null;
  function scheduleMobileTrackerNavLayoutSync() {
    if (mobileTrackerNavLayoutTimer) clearTimeout(mobileTrackerNavLayoutTimer);
    mobileTrackerNavLayoutTimer = setTimeout(syncMobileTrackerNavVisibility, 50);
  }
  function syncMobileTrackerNavVisibility() {
    const mobileNav = document.querySelector('[data-mobile-tracker-tabs]');
    if (!mobileNav) return;
    const hideMobileNav = isDesktopSidebarNavLayout();
    mobileNav.hidden = hideMobileNav;
    mobileNav.setAttribute('aria-hidden', hideMobileNav ? 'true' : 'false');
  }
  function setInventorySection(section) {
    activeInventorySection = section === 'dashboard' ? 'dashboard' : 'accounts';
    syncMobileTrackerNavVisibility();
    mainSectionNavTabs.forEach((tab) => {
      tab.classList.toggle('is-active', tab.getAttribute('data-inventory-section') === activeInventorySection);
    });
    if (dashboardPanelEl) dashboardPanelEl.hidden = activeInventorySection !== 'dashboard';
    if (accountsPanelEl) accountsPanelEl.hidden = activeInventorySection !== 'accounts';
    if (activeInventorySection === 'dashboard') {
      loadDashboardRange(false);
    }
    ensureBackgroundPolling();
  }
  function dashboardPeriodQuery() {
    const params = new URLSearchParams();
    if (dashboardPeriod === 'custom') {
      params.set('period', 'custom');
      if (dashboardCustomFrom) params.set('from', dashboardCustomFrom);
      if (dashboardCustomTo) params.set('to', dashboardCustomTo);
    } else {
      params.set('period', dashboardPeriod);
    }
    if (DEBUG_INVENTORY) params.set('debug', '1');
    return params.toString();
  }
  function dashboardStatsState(data) {
    if (!data || data.ok === false) return 'error';
    if (data.statsState === 'error' || data.available === false) return 'error';
    return data.statsState || 'ok';
  }
  const DASHBOARD_EMPTY_REASON_LABELS = {
    bot_db_not_connected: 'Catch stats unavailable — bot database is not connected.',
    fish_cache_missing_or_empty: 'Catch stats unavailable — bot fish cache is empty.',
    no_bot_user_for_discord_id: 'No catch profile found for your Discord account in the bot database.',
    no_catch_records_in_bot_db: 'No fish caught yet in the bot database.',
    date_range_filtered_all_rows: 'No catches in the selected date range.',
    missing_auth_discord_id: 'Could not resolve your Discord account for catch stats.',
    dashboard_unavailable: 'Catch stats are temporarily unavailable.',
  };
  function dashboardEmptyReasonMessage(reason, debug) {
    if (!reason) return '';
    if (DEBUG_INVENTORY && debug) {
      const proof = [
        `emptyReason=${reason}`,
        debug.authDiscordId ? `authDiscordId=${debug.authDiscordId}` : '',
        debug.matchedBotUserId ? `matchedBotUserId=${debug.matchedBotUserId}` : '',
        debug.identityMatchMode ? `identityMatchMode=${debug.identityMatchMode}` : '',
        debug.botDbPath ? `botDbPath=${debug.botDbPath}` : '',
        debug.allTimeCatchRows != null ? `allTimeCatchRows=${debug.allTimeCatchRows}` : '',
        debug.filteredCatchRows != null ? `filteredCatchRows=${debug.filteredCatchRows}` : '',
      ].filter(Boolean).join('\n');
      return proof || String(reason);
    }
    return DASHBOARD_EMPTY_REASON_LABELS[reason] || DASHBOARD_EMPTY_REASON_LABELS.dashboard_unavailable;
  }
  function renderDashboardStatusNotice(data) {
    const notice = document.getElementById('dashboardStatusNotice');
    if (!notice) return;
    notice.hidden = true;
    notice.textContent = '';
    notice.classList.remove('is-error', 'is-debug');
    if (!data) return;
    const state = dashboardStatsState(data);
    if (state !== 'error') return;
    const reason = data.emptyReason || 'dashboard_unavailable';
    notice.textContent = dashboardEmptyReasonMessage(reason, data.debug);
    notice.classList.add('is-error');
    notice.classList.toggle('is-debug', !!(DEBUG_INVENTORY && data.debug));
    notice.hidden = false;
  }
  function dashboardRangeKey() {
    if (dashboardPeriod === 'custom') {
      return `custom|${dashboardCustomFrom}|${dashboardCustomTo}`;
    }
    return dashboardPeriod || 'all';
  }
  function setDashboardToolbarLoading(loading) {
    if (dashboardToolbarEl) dashboardToolbarEl.classList.toggle('is-loading', !!loading);
  }
  function getDashboardMemEntry(key) {
    return dashboardMemCache.get(key) || null;
  }
  function isDashboardMemFresh(entry) {
    return !!(entry && (Date.now() - entry.at) < DASHBOARD_MEM_TTL_MS);
  }
  function isDashboardMemStale(entry) {
    return !entry || (Date.now() - entry.at) > DASHBOARD_SWR_STALE_MS;
  }
  function storeDashboardMemCache(key, data) {
    dashboardMemCache.set(key, { data, at: Date.now() });
  }
  function applyDashboardPresetActiveUI(period, btn) {
    clearDashboardPresetActive();
    if (period === 'custom') {
      if (dashboardCustomToggleEl) dashboardCustomToggleEl.classList.add('is-active');
      return;
    }
    if (btn) btn.classList.add('is-active');
    else {
      const match = document.querySelector(`[data-dashboard-period="${period}"]`);
      if (match) match.classList.add('is-active');
    }
  }
  function clearDashboardPresetActive() {
    dashboardPeriodBtns.forEach((item) => item.classList.remove('is-active'));
  }
  function scheduleDashboardRangeFetch(immediate) {
    if (dashboardRangeDebounceTimer) {
      clearTimeout(dashboardRangeDebounceTimer);
      dashboardRangeDebounceTimer = null;
    }
    const run = () => {
      dashboardRangeDebounceTimer = null;
      loadDashboardRange(false);
    };
    if (immediate) run();
    else dashboardRangeDebounceTimer = setTimeout(run, DASHBOARD_RANGE_DEBOUNCE_MS);
  }
  function initDashboardDefaultPeriod() {
    dashboardPeriod = 'all';
    dashboardCustomFrom = '';
    dashboardCustomTo = '';
    dashboardInitialLoaded = false;
    clearDashboardPresetActive();
    const allBtn = document.querySelector('[data-dashboard-period="all"]');
    if (allBtn) allBtn.classList.add('is-active');
    if (dashboardCustomToggleEl) dashboardCustomToggleEl.classList.remove('is-active');
    if (dashboardCustomPanelEl) {
      dashboardCustomPanelEl.hidden = true;
      dashboardCustomPanelEl.classList.remove('is-open');
    }
    if (dashboardCustomToggleEl) dashboardCustomToggleEl.setAttribute('aria-expanded', 'false');
  }
  function setDashboardPresetPeriod(period, btn) {
    dashboardPeriod = period || 'all';
    if (dashboardCustomToggleEl) dashboardCustomToggleEl.classList.remove('is-active');
    if (dashboardCustomPanelEl) {
      dashboardCustomPanelEl.hidden = true;
      dashboardCustomPanelEl.classList.remove('is-open');
    }
    if (dashboardCustomToggleEl) dashboardCustomToggleEl.setAttribute('aria-expanded', 'false');
    applyDashboardPresetActiveUI(period, btn);
    scheduleDashboardRangeFetch(true);
  }
  function setDashboardCustomPeriod(from, to) {
    dashboardPeriod = 'custom';
    dashboardCustomFrom = from || '';
    dashboardCustomTo = to || '';
    applyDashboardPresetActiveUI('custom');
    scheduleDashboardRangeFetch(true);
  }
  function renderDashboardChart(dailyCaught, opts) {
    opts = opts || {};
    const svg = document.getElementById('dashboardChart');
    const labelsEl = document.getElementById('dashboardChartLabels');
    const emptyEl = document.getElementById('dashboardChartEmpty');
    if (!svg || !labelsEl) return;
    const rows = Array.isArray(dailyCaught) ? dailyCaught : [];
    const hasData = rows.some((row) => Number(row.totalCaught) > 0);
    if (emptyEl) {
      if (opts.failed) {
        emptyEl.hidden = true;
      } else {
        emptyEl.hidden = hasData;
        emptyEl.textContent = hasData ? '' : 'No catch data for this period.';
      }
    }
    const width = 320;
    const height = 132;
    const padL = 8;
    const padR = 8;
    const padTop = 18;
    const padBottom = 8;
    const chartW = width - padL - padR;
    const chartH = height - padTop - padBottom;
    const baseY = padTop + chartH;
    const maxVal = Math.max(1, ...rows.map((row) => Number(row.totalCaught) || 0));
    if (!rows.length) {
      svg.innerHTML = '';
      svg.removeAttribute('data-chart-ready');
      svg.removeAttribute('data-bucket-count');
      labelsEl.innerHTML = '';
      return;
    }
    const points = rows.map((row, i) => {
      const val = Number(row.totalCaught) || 0;
      const x = padL + (rows.length <= 1 ? chartW / 2 : (i / (rows.length - 1)) * chartW);
      const y = padTop + chartH - Math.round((val / maxVal) * chartH);
      return { x, y, val };
    });
    let linePath = '';
    points.forEach((pt, i) => {
      linePath += `${i === 0 ? 'M' : 'L'} ${pt.x.toFixed(1)} ${pt.y.toFixed(1)} `;
    });
    const areaPath = `${linePath} L ${points[points.length - 1].x.toFixed(1)} ${baseY} L ${points[0].x.toFixed(1)} ${baseY} Z`;
    const bucketCount = String(rows.length);
    const canPatchChart = opts.patchChart !== false
      && svg.getAttribute('data-chart-ready') === '1'
      && svg.getAttribute('data-bucket-count') === bucketCount
      && svg.querySelector('.dashboard-chart-line');
    if (canPatchChart) {
      const lineEl = svg.querySelector('.dashboard-chart-line');
      const areaEl = svg.querySelector('.dashboard-chart-area');
      if (lineEl) lineEl.setAttribute('d', linePath.trim());
      if (areaEl) areaEl.setAttribute('d', areaPath);
      svg.querySelectorAll('.dashboard-chart-value, .dashboard-chart-zero').forEach((el) => el.remove());
      const valueEvery = rows.length > 12 ? Math.ceil(rows.length / 6) : 1;
      const pointEls = svg.querySelectorAll('.dashboard-chart-point');
      points.forEach((pt, i) => {
        const circle = pointEls[i];
        if (circle) {
          circle.setAttribute('cx', pt.x.toFixed(1));
          circle.setAttribute('cy', pt.y.toFixed(1));
          const title = circle.querySelector('title');
          if (title) title.textContent = `${pt.val} caught`;
        }
        const showVal = i % valueEvery === 0 || i === points.length - 1;
        if (showVal) {
          const labelY = pt.val > 0 ? Math.max(padTop + 2, pt.y - 8) : baseY - 10;
          const textEl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
          textEl.setAttribute('class', pt.val > 0 ? 'dashboard-chart-value' : 'dashboard-chart-zero');
          textEl.setAttribute('x', pt.x.toFixed(1));
          textEl.setAttribute('y', labelY.toFixed(1));
          textEl.textContent = String(pt.val);
          svg.appendChild(textEl);
        }
      });
    } else {
      const gridLines = [0.25, 0.5, 0.75].map((ratio) => {
        const y = padTop + chartH * (1 - ratio);
        return `<line class="dashboard-chart-grid" x1="${padL}" y1="${y.toFixed(1)}" x2="${width - padR}" y2="${y.toFixed(1)}"></line>`;
      }).join('');
      const labelEvery = rows.length > 14 ? Math.ceil(rows.length / 7) : rows.length > 7 ? 2 : 1;
      const valueEvery = rows.length > 12 ? Math.ceil(rows.length / 6) : 1;
      const markers = points.map((pt, i) => {
        const showVal = i % valueEvery === 0 || i === points.length - 1;
        const labelY = pt.val > 0 ? Math.max(padTop + 2, pt.y - 8) : baseY - 10;
        const valLabel = showVal
          ? `<text class="${pt.val > 0 ? 'dashboard-chart-value' : 'dashboard-chart-zero'}" x="${pt.x.toFixed(1)}" y="${labelY.toFixed(1)}">${pt.val}</text>`
          : '';
        return `<circle class="dashboard-chart-point" cx="${pt.x.toFixed(1)}" cy="${pt.y.toFixed(1)}" r="3.2"><title>${pt.val} caught</title></circle>${valLabel}`;
      }).join('');
      svg.innerHTML = `
        <defs>
          <linearGradient id="dashboardLineGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="#38bdf8"></stop>
            <stop offset="100%" stop-color="#6366f1"></stop>
          </linearGradient>
          <linearGradient id="dashboardAreaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="rgba(96,165,250,0.28)"></stop>
            <stop offset="100%" stop-color="rgba(96,165,250,0)"></stop>
          </linearGradient>
        </defs>
        ${gridLines}
        <path class="dashboard-chart-area" d="${areaPath}"></path>
        <path class="dashboard-chart-line" d="${linePath.trim()}"></path>
        ${markers}
      `;
      svg.setAttribute('data-chart-ready', '1');
      svg.setAttribute('data-bucket-count', bucketCount);
    }
    const useWeekly = rows.some((row) => row.bucket === 'week');
    const labelEvery = rows.length > 14 ? Math.ceil(rows.length / 7) : rows.length > 7 ? 2 : 1;
    labelsEl.innerHTML = rows.map((row, i) => {
      if (i % labelEvery !== 0 && i !== rows.length - 1) return '';
      const raw = String(row.date || '');
      const short = useWeekly ? raw.slice(5) : raw.slice(5);
      return `<span>${short}</span>`;
    }).join('');
  }
  function sortDashboardFishCards(cards) {
    return sortInventoryFish(Array.isArray(cards) ? cards : []);
  }
  function renderDashboardFishGrid(fishCards, opts) {
    opts = opts || {};
    const host = document.getElementById('dashboardFishGrid');
    const emptyEl = document.getElementById('dashboardFishEmpty');
    if (!host) return;
    const cards = sortDashboardFishCards(fishCards);
    if (!cards.length) {
      host.innerHTML = '';
      if (emptyEl) {
        emptyEl.hidden = !!opts.failed;
        if (!opts.failed) emptyEl.textContent = 'No fish caught in this period.';
      }
      return;
    }
    if (emptyEl) emptyEl.hidden = true;
    const items = cards.map((card) => ({
      name: card.name,
      cardName: card.name,
      rarity: card.rarity,
      amount: card.count || card.amount || 0,
      imageUrl: card.imageUrl,
      imageAssetId: card.imageAssetId || null,
      dataSource: 'dashboard_owner',
    }));
    patchItemsGrid(host, items, {
      keyFn: (item) => `${String(item.name || '').toLowerCase()}|${String(item.rarity || '').toLowerCase()}`,
      buildOpts: () => ({ includeOwnerChip: false, includeRarity: true }),
    });
  }
  function applyDashboardData(data, opts) {
    opts = opts || {};
    renderDashboardStatusNotice(data);
    const state = dashboardStatsState(data);
    const failed = state === 'error';
    const cards = (data && (data.cards || data.summary)) || {};
    const cu = dashboardCountUp();
    const secretEl = document.getElementById('dashboardSecretCaught');
    const forgottenEl = document.getElementById('dashboardForgottenCaught');
    const secretVal = failed ? null : Number(cards.secretCaught || 0);
    const forgottenVal = failed ? null : Number(cards.forgottenCaught || 0);
    const countDuration = opts.animate === false ? 180 : 1200;
    if (cu) {
      if (secretEl) {
        if (!failed) cu.set(secretEl, { to: secretVal, format: 'integer', duration: countDuration });
        else secretEl.textContent = '—';
      }
      if (forgottenEl) {
        if (!failed) cu.set(forgottenEl, { to: forgottenVal, format: 'integer', duration: countDuration });
        else forgottenEl.textContent = '—';
      }
    } else {
      if (secretEl) secretEl.textContent = failed ? '—' : String(secretVal);
      if (forgottenEl) forgottenEl.textContent = failed ? '—' : String(forgottenVal);
    }
    renderDashboardChart((data && data.dailyCaught) || [], { failed, patchChart: opts.patchChart });
    renderDashboardFishGrid((data && data.fishCards) || [], { failed });
  }
  async function loadDashboardRange(forceRefresh) {
    if (activeInventorySection !== 'dashboard') return;
    const rangeKey = dashboardRangeKey();
    const cachedEntry = getDashboardMemEntry(rangeKey);
    const hasFreshCache = isDashboardMemFresh(cachedEntry);
    const requestKey = rangeKey;
    if (cachedEntry && !forceRefresh) {
      applyDashboardData(cachedEntry.data, { animate: false, patchChart: true });
      if (!isDashboardMemStale(cachedEntry)) {
        dashboardInitialLoaded = true;
        return;
      }
    }
    if (dashboardFetchAbort) {
      dashboardFetchAbort.abort();
      dashboardFetchAbort = null;
    }
    const gen = ++dashboardFetchGen;
    const ac = new AbortController();
    dashboardFetchAbort = ac;
    setDashboardToolbarLoading(!cachedEntry);
    try {
      const res = await fetch(`/api/tracker/dashboard?${dashboardPeriodQuery()}`, {
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
        cache: 'no-store',
        signal: ac.signal,
      });
      if (gen !== dashboardFetchGen || requestKey !== dashboardRangeKey()) return;
      const raw = await res.text();
      notePerfFetch(raw);
      const data = res.ok ? JSON.parse(raw) : null;
      if (gen !== dashboardFetchGen || requestKey !== dashboardRangeKey()) return;
      if (!data || !data.ok) {
        console.error('[inventory] dashboard load failed', data && data.error ? data.error : res.status);
        throw new Error((data && data.error) || 'dashboard_load_failed');
      }
      if (data.debug) {
        console.info('[inventory] dashboard proof', data.debug);
      }
      storeDashboardMemCache(rangeKey, data);
      applyDashboardData(data, { animate: !hasFreshCache, patchChart: true });
      dashboardInitialLoaded = true;
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      if (gen !== dashboardFetchGen || requestKey !== dashboardRangeKey()) return;
      console.error('[inventory] dashboard load failed', err);
      if (!cachedEntry) {
        applyDashboardData({
          ok: false,
          statsState: 'error',
          available: false,
          emptyReason: 'dashboard_unavailable',
          period: dashboardPeriod,
          cards: { secretCaught: 0, forgottenCaught: 0 },
          fishCards: [],
          dailyCaught: [],
        }, { animate: false });
      }
    } finally {
      if (gen === dashboardFetchGen) {
        setDashboardToolbarLoading(false);
        dashboardFetchAbort = null;
      }
    }
  }
  async function loadDashboardData(force) {
    if (dashboardInitialLoaded && !force) return;
    await new Promise((resolve) => requestAnimationFrame(resolve));
    await loadDashboardRange(!!force);
  }
  function bindMainSectionNav() {
    mainSectionNavTabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        setInventorySection(tab.getAttribute('data-inventory-section'));
      });
    });
    syncMobileTrackerNavVisibility();
    window.addEventListener('resize', scheduleMobileTrackerNavLayoutSync, { passive: true });
    dashboardPeriodBtns.forEach((btn) => {
      btn.addEventListener('click', () => {
        setDashboardPresetPeriod(btn.getAttribute('data-dashboard-period') || 'all', btn);
      });
    });
    if (dashboardCustomToggleEl && dashboardCustomPanelEl) {
      dashboardCustomToggleEl.addEventListener('click', () => {
        const open = dashboardCustomPanelEl.hidden;
        dashboardCustomPanelEl.hidden = !open;
        dashboardCustomPanelEl.classList.toggle('is-open', open);
        dashboardCustomToggleEl.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open && dashboardCustomFromEl && !dashboardCustomFromEl.value && dashboardCustomFrom) {
          dashboardCustomFromEl.value = dashboardCustomFrom;
        }
        if (open && dashboardCustomToEl && !dashboardCustomToEl.value && dashboardCustomTo) {
          dashboardCustomToEl.value = dashboardCustomTo;
        }
      });
    }
    if (dashboardCustomApplyEl) {
      dashboardCustomApplyEl.addEventListener('click', () => {
        const from = dashboardCustomFromEl ? dashboardCustomFromEl.value : '';
        const to = dashboardCustomToEl ? dashboardCustomToEl.value : '';
        if (!from || !to) return;
        setDashboardCustomPeriod(from, to);
      });
    }
  }
  function initDashboardSectionFromQuery() {
    initDashboardDefaultPeriod();
    const params = new URLSearchParams(window.location.search);
    const section = String(params.get('section') || '').trim().toLowerCase();
    if (section === 'dashboard') {
      setInventorySection('dashboard');
      return;
    }
    setInventorySection('accounts');
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
      addTracker(fromQuery).then((ok) => {
        if (ok && inputEl) inputEl.value = '';
      }).catch((err) => {
        showUsernameError(DEBUG_INVENTORY && err && err.message ? err.message : 'Could not save tracked account.');
      });
    } else if (fromQuery) {
      showUsernameError(`Invalid username in URL. Use letters, numbers, or underscore (3${EN_DASH}20 chars).`);
      if (inputEl) inputEl.value = fromQuery;
    }
  }
  function initInventoryUi() {
    safeBind('main section nav', bindMainSectionNav);
    safeBind('dashboard section init', initDashboardSectionFromQuery);
    safeBind('sidebar profile controls', bindSidebarProfileControls);
    safeBind('copy script', bindCopyScript);
    safeBind('sidebar script', bindSidebarScript);
    safeBind('add player', bindAddPlayer);
    safeBind('multiple add', bindMultipleAdd);
    safeBind('username bulk actions', bindUsernameBulkActions);
    safeBind('card detail panel', bindFtDetail);
    safeBind('tabs', bindInventoryTabs);
    safeBind('accounts overview', bindAccountsOverview);
    safeBind('restore sessions', () => {
      restoreTrackedAccountsFromServer()
        .then(() => {
          ensureBackgroundPolling();
          if (activeInventorySection === 'dashboard') {
            requestAnimationFrame(() => loadDashboardData(false));
          }
        })
        .then(() => initFromQueryUsername())
        .catch((err) => {
          console.error('[inventory] restore tracked accounts failed', err);
          initFromQueryUsername();
        });
    });
    safeBind('sync age tick', () => {
      if (syncTickTimer) return;
      syncTickTimer = setInterval(tickAllCardSyncStatus, SYNC_TICK_MS);
      tickAllCardSyncStatus();
    });
    safeBind('visibility refetch', () => {
      let resumeRefreshAt = 0;
      const refreshTrackerNow = (reason) => {
        const now = Date.now();
        if (now - resumeRefreshAt < 700) return;
        resumeRefreshAt = now;
        if (DEBUG_INVENTORY) console.debug('[fishit] refreshTrackerNow', reason);
        refetchAllAccountStatus(true);
      };
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') refreshTrackerNow('tab-visible');
      });
      window.addEventListener('focus', () => refreshTrackerNow('window-focus'));
      window.addEventListener('pageshow', () => refreshTrackerNow('pageshow'));
    });
    if (APK_EMBED) document.documentElement.style.background = '#0d0f14';
    document.body.setAttribute('data-inventory-js', 'ready');
    window.__fishInventoryUiReady = true;
    if (DEBUG_INVENTORY) {
      window.__fishitDebugProof = {
        routeInventoryOnlyProof: {
          publicInventoryPath: '/tracker',
          legacyInventoryRedirect: '/inventory -> /tracker',
          publicUiUsesLiveTrackerLabel: true,
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
          publicPollIntervalMs: TRACKER_POLL_INTERVAL_MS,
          pollIntervalMs: POLL_MS,
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
          caughtActivityResetsOnValueIncrease: true,
          caughtActivityContinuesWhenValueUnchanged: true,
        },
        coinIntervalProof: { coinRefreshesOnEveryPoll: true },
        rarestFishIntervalProof: { rarestFishRefreshesOnEveryPoll: true },
        fishStoneIntervalProof: { fishStoneFromSamePollPayload: true },
        uploadIntervalProof: { trackerUploadIntervalSeconds: 10, publicRefreshIntervalMs: POLL_MS },
        accountUploadStatusProof: {
          canonicalEndpoint: '/api/tracker/account-status',
          sharedDesktopMobileApkLogic: true,
          serverTimeFromApi: true,
          statusColors: ['green', 'yellow', 'red'],
          pollFunction: 'pollAccountStatuses',
        },
        connectionIndicatorProof: {
          staleThresholdSeconds: null,
          usesSuccessfulPollTimestamp: true,
          usesServerUploadStatus: true,
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
    if (DEBUG_INVENTORY) {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const runtimeEl = document.getElementById('inventory-runtime');
          const cssLinks = [...document.querySelectorAll('link[rel="stylesheet"]')];
          const jsScripts = [...document.querySelectorAll('script[src]')];
          window.__fishitPerf = {
            htmlBytes: document.documentElement.outerHTML.length,
            runtimeJsonBytes: runtimeEl ? String(runtimeEl.textContent || '').length : 0,
            cssBytes: cssLinks.reduce((sum, el) => sum + String(el.href || '').length, 0),
            jsBytes: jsScripts.reduce((sum, el) => sum + String(el.src || '').length, 0),
            apiPayloadBytes: perfApiPayloadBytes,
            firstApiMs: perfFirstApiMs,
            totalInitialRequests: perfInitialRequests,
            firstRenderMs: Math.round(((typeof performance !== 'undefined' && performance.now)
              ? performance.now()
              : Date.now()) - PERF_STARTED_AT),
            imageRequests: document.querySelectorAll('img[src]').length,
            liveTrackerPollingActive,
            activeInventorySection,
          };
        });
      });
    }
    ftAuditTopSummaryImages();
    console.log('[inventory] UI initialized');
  }
  function ftAuditTopSummaryImages() {
    try {
      const imgs = document.querySelectorAll('.tracker-top-summary-card img');
      imgs.forEach((img) => {
        const report = () => {
          if (!img.getAttribute('src')) {
            console.error('[tracker-top-summary] missing image src for', img.alt || 'icon');
          } else if (img.complete && img.naturalWidth === 0) {
            console.error('[tracker-top-summary] BROKEN image (naturalWidth=0):', img.alt || 'icon', img.src);
          }
        };
        img.addEventListener('error', () => {
          console.error('[tracker-top-summary] image failed to load:', img.alt || 'icon', img.src);
        });
        if (img.complete) report();
        else img.addEventListener('load', report);
      });
    } catch (err) {
      console.error('[tracker-top-summary] image audit failed:', err && err.message ? err.message : err);
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initInventoryUi);
  } else {
    initInventoryUi();
  }
}());