(function(){'use strict';function readInventoryCfg(){const el=document.getElementById('inventory-runtime');if(!el)return{};try{return JSON.parse(el.textContent||'{}');}catch(_){return{};}}const __CFG__=readInventoryCfg();
const LS_KEY        = 'fishit_tracked_users';
  const LS_BULK_CACHE = 'fishit_bulk_inventory_cache_v1';
  const TRACKER_POLL_INTERVAL_MS = 10_000;
  const POLL_MS = TRACKER_POLL_INTERVAL_MS;
  const SYNC_TICK_MS  = 1000;
  const PERF_STARTED_AT = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
  let perfFirstApiMs = null;
  let perfApiPayloadBytes = 0;
  let perfInitialRequests = 0;
  const DEBUG_INVENTORY = !!__CFG__.debugInventory;
  const APK_EMBED = !!__CFG__.apkEmbed;
  const DEBUG_GLOBAL  = DEBUG_INVENTORY && /(?:^|[?&])debug=global(?:&|$)/.test(window.location.search);
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
  const removeMenuBtn  = document.getElementById('removeMenuBtn');
  const removeMenuEl   = document.getElementById('removeMenu');
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
      const qs = forceFresh === true ? `?_=${Date.now()}` : '';
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
        entry.lastData = {
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
        };
        reconcileEntryPresence(entry, st, accountStatusServerNowMs);
        applyLiveSnapshotToPublicUi(entry, key, entry.lastData);
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
  var ACCOUNT_PRESENCE_GRACE_MS = 600 * 1000;
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
    return null;
  }
  const TRUSTED_PLAYERSTATS_BUILD_MARKS = ['BLOCKER10ZT5', 'BLOCKER10ZT4', 'BLOCKER10ZT3', 'BLOCKER10ZW'];
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
    if (!entry || !entry.liveSnapshot) return null;
    return entry.liveSnapshot.playerStats || null;
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
  function formatPresenceStatusText(entry) {
    const secs = liveSecondsSinceStatusSuccess(entry);
    if (secs != null) return formatPresenceDurationLabel(secs);
    const label = formatPresenceDurationLabel(syncAgeSeconds(entryStatusSuccessTimestamp(entry)));
    return label || '1s';
  }
  function formatStatsUploadDurationText(entry) {
    const secs = liveSecondsSinceStatsSuccess(entry);
    if (secs == null) return '';
    return formatPresenceDurationLabel(secs);
  }
  function formatCaughtActivitySub(entry) {
    return formatStatsUploadDurationText(entry);
  }
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
    const secs = liveSecondsSinceInventorySuccess(entry);
    if (secs == null) return '';
    return formatPresenceDurationLabel(secs);
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
      statusEl.classList.remove('live', 'stale', 'dead');
      statusEl.classList.add(fresh === 'live' ? 'live' : 'dead');
      const label = fresh === 'live' ? 'Online' : 'Offline';
      statusEl.setAttribute('title', label);
      statusEl.setAttribute('aria-label', label);
    }
    if (syncEl) syncEl.textContent = formatPresenceStatusText(entry);
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
    if (!snap) return;
    const fishList = snap.fishList || [];
    const stoneList = snap.stoneList || [];
    const live = isAccountPresent(entry);
    const invState = inventoryDisplayState(data);
    const hasInventory = fishList.length > 0 || stoneList.length > 0;
    if (!live) {
      setCardOffline(entry.el, entry.displayName, data);
    } else if (hasInventory) {
      updateCard(entry.el, data);
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
    entry.lastData = data;
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
    const label = formatTableSyncAge(ts);
    return label && label !== 'no sync' ? `(${label} ago)` : '';
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
    const syncText = formatPresenceStatusText(entry) || '1s';
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
      <span class="accounts-mobile-card__username">${escHtml(username)}</span>
    </div>
    <div class="accounts-mobile-card__actions">
      <button type="button" class="accounts-table__icon-btn" data-open-backpack="${escHtml(key)}" aria-label="${escHtml(inventoryLabel)}" title="${escHtml(inventoryLabel)}">${BACKPACK_NAV_ICON}</button>
      <button type="button" class="accounts-table__icon-btn accounts-table__icon-btn--danger" data-remove-account="${escHtml(key)}" aria-label="Remove ${escHtml(entry.displayName)}" title="Remove account"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16" aria-hidden="true"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path></svg></button>
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
    rows.forEach(({ key, entry }) => refreshEntryTableSyncDisplay(entry, key));
    updateInventoryUploadIndicator();
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
    ruby: '#f87171', diamond: '#7dd3fc', emerald: '#34d399', sapphire: '#60a5fa',
    shiny: '#fde68a', glowing: '#fde68a', radiant: '#fde68a',
    frozen: '#67e8f9', ice: '#67e8f9', icy: '#67e8f9', glacial: '#67e8f9',
    corrupt: '#c084fc', corrupted: '#c084fc', void: '#c084fc', dark: '#c084fc',
    rainbow: '#f0abfc', galaxy: '#a78bfa', cosmic: '#a78bfa',
    fire: '#fb923c', molten: '#fb923c', lava: '#fb923c',
    normal: '#94a3b8',
  };
  function ftMutationColor(mut) {
    const key = String(mut || '').toLowerCase().trim();
    if (!key) return '';
    return FT_MUTATION_COLORS[key] || '#cbd5e1';
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
  function isRubyGemstoneItem(item) {
    if (!item || typeof item !== 'object') return false;
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
  function computeInventoryStats() {
    let onlineCount = 0;
    let evolvedStones = 0;
    let secretFish = 0;
    let forgottenFish = 0;
    let rubyGemstone = 0;
    const countRuby = (item) => {
      if (isRubyGemstoneItem(item)) {
        rubyGemstone += Math.max(0, Math.floor(Number(resolveItemAmount(item)) || 0)) || 1;
      }
    };
    trackers.forEach((entry) => {
      if (isTrackerOnline(entry)) onlineCount += 1;
      const data = entry.lastData;
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
      }
      : localStats;
    const countUp = window.DengCountUpStats;
    if (statOnlineAccountsEl) {
      if (countUp) {
        countUp.set(statOnlineAccountsEl, { to: stats.onlineCount, total: stats.totalAccounts, format: 'ratio' });
      } else {
        statOnlineAccountsEl.textContent = `${formatQuantity(stats.onlineCount)} / ${formatQuantity(stats.totalAccounts)}`;
      }
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
  }
  function resolveInventoryIndicatorEntry(preferredEntry) {
    if (preferredEntry) return preferredEntry;
    if (accountViewMode === 'account' && activeAccountKey) {
      const active = trackers.get(activeAccountKey);
      if (active) return active;
    }
    let worst = null;
    let worstAge = -1;
    getFilteredAccountEntries().forEach(({ entry }) => {
      if (isInventoryUploadFresh(entry)) return;
      const age = syncAgeSeconds(entryInventoryUploadTimestamp(entry));
      const score = age == null ? Number.MAX_SAFE_INTEGER : age;
      if (score >= worstAge) {
        worstAge = score;
        worst = entry;
      }
    });
    if (worst) return worst;
    const rows = getFilteredAccountEntries();
    return rows.length ? rows[0].entry : null;
  }
  function ensureSingleInventoryUploadText(root) {
    if (!root) return null;
    const scope = root.closest('[data-inventory-upload-indicator]') || root;
    const allText = scope.querySelectorAll('[data-inventory-upload-text]');
    if (allText.length > 1) {
      for (let i = 1; i < allText.length; i += 1) allText[i].remove();
    }
    return scope.querySelector('[data-inventory-upload-text]');
  }
  function patchInventoryUploadIndicatorDom(root, entry) {
    if (!root) return;
    const dotEl = root.querySelector('[data-inventory-upload-dot]');
    const textEl = ensureSingleInventoryUploadText(root);
    const wrapEl = root.closest('[data-inventory-upload-indicator]') || root;
    const fresh = entry && isInventoryUploadFresh(entry);
    const label = entry ? formatEntrySyncStatusText(entry) : '';
    if (dotEl) {
      dotEl.classList.remove('live', 'stale', 'dead');
      dotEl.classList.add(fresh ? 'live' : 'dead');
    }
    if (textEl) textEl.textContent = label || '';
    if (wrapEl && wrapEl.matches('[data-inventory-upload-indicator]')) {
      wrapEl.classList.toggle('is-live', !!fresh);
      wrapEl.classList.toggle('is-stale', !fresh);
      wrapEl.setAttribute('title', fresh ? 'Fish and stone upload fresh' : 'Fish and stone upload stale');
    }
  }
  function ensureCardInventoryUploadBar(cardBody) {
    if (!cardBody) return null;
    const existingBars = cardBody.querySelectorAll('[data-inventory-upload-indicator]');
    if (existingBars.length > 1) {
      for (let i = 1; i < existingBars.length; i += 1) {
        const host = existingBars[i].closest('.inventory-grid-upload-bar');
        if (host) host.remove();
        else existingBars[i].remove();
      }
    }
    let bar = cardBody.querySelector('[data-inventory-upload-indicator]');
    if (bar) return bar;
    bar = document.createElement('div');
    bar.className = 'inventory-grid-upload-bar';
    bar.innerHTML = '<div class="inventory-upload-indicator is-stale" data-inventory-upload-indicator aria-label="Fish and stone upload status"><span class="status-dot dead" data-inventory-upload-dot aria-hidden="true"></span><span class="inventory-upload-indicator__text" data-inventory-upload-text></span></div>';
    cardBody.insertBefore(bar, cardBody.firstChild);
    return bar.querySelector('[data-inventory-upload-indicator]') || bar;
  }
  function updateInventoryUploadIndicator(preferredEntry) {
    const entry = resolveInventoryIndicatorEntry(preferredEntry);
    const bulkIndicator = document.querySelector('#bulkInventoryPanel [data-inventory-upload-indicator]');
    if (bulkIndicator) patchInventoryUploadIndicatorDom(bulkIndicator, entry);
    if (accountViewMode === 'account' && activeAccountKey) {
      const active = trackers.get(activeAccountKey);
      const body = active && active.el && active.el.querySelector('[data-card-body]');
      if (body) patchInventoryUploadIndicatorDom(ensureCardInventoryUploadBar(body), entry || active);
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
      const data = entry.lastData;
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
    const itemsHtml = keys.map((key) => {
      const entry = trackers.get(key);
      const name = entry && entry.displayName ? entry.displayName : key;
      return `<button type="button" class="remove-dropdown__item" role="menuitem" data-remove-key="${escHtml(key)}"><span>${escHtml(name)}</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16" aria-hidden="true"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path></svg></button>`;
    }).join('');
    const removeAllHtml = '<div class="remove-dropdown__divider" role="separator"></div>'
      + '<button type="button" class="remove-dropdown__item remove-dropdown__item--all" role="menuitem" data-remove-all="1"><span>Remove all usernames</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16" aria-hidden="true"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path></svg></button>';
    removeMenuEl.innerHTML = itemsHtml + removeAllHtml;
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
    updateRemoveMenu();
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
    const m = String(weight || '').match(/[\d.]+/);
    return m ? parseFloat(m[0]) : 0;
  }
  const FT_MAX_INSTANCE_CARDS = 800;
  function ftWeightKgText(kg) {
    const n = Number(kg);
    if (!Number.isFinite(n) || n <= 0) return '';
    let s;
    if (n >= 100) s = n.toFixed(0);
    else if (n >= 10) s = n.toFixed(1);
    else s = n.toFixed(2);
    s = s.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
    return `${s} kg`;
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
      const list = Array.isArray(row && row.ownedInstances) ? row.ownedInstances : null;
      if (list && list.length) {
        for (const inst of list) {
          const amount = Math.max(1, Math.floor(Number(inst.quantity) || 1));
          const mutation = String((inst && inst.mutation) || '').trim();
          const name = (inst && inst.baseFishName && String(inst.baseFishName).trim()) || baseName;
          const weight = ftWeightKgText(inst && inst.weightKg);
          for (let i = 0; i < amount; i += 1) {
            if (!pushCard({ owner, mutation, name, weight, imgSrc })) return ftSortFishCards(cards);
          }
        }
      } else {
        const amount = Math.max(1, Math.floor(Number(resolveItemAmount(row)) || 1));
        const mutation = ftExtractMutation(row);
        const weight = ftItemWeightText(row);
        for (let i = 0; i < amount; i += 1) {
          if (!pushCard({ owner, mutation, name: baseName, weight, imgSrc })) return ftSortFishCards(cards);
        }
      }
    }
    return ftSortFishCards(cards);
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
    return cards.sort((a, b) => {
      const c1 = String(a.owner).toLowerCase().localeCompare(String(b.owner).toLowerCase());
      if (c1) return c1;
      const c2 = String(a.mutation || '').toLowerCase().localeCompare(String(b.mutation || '').toLowerCase());
      if (c2) return c2;
      return ftWeightNum(b.weight) - ftWeightNum(a.weight);
    });
  }
  function renderFishInstanceCard(card) {
    const mutColor = ftMutationColor(card.mutation);
    const mut = card.mutation
      ? `<div class="ft-inst-card__mut" style="color:${escHtml(mutColor)}">${escHtml(card.mutation)}</div>`
      : '<div class="ft-inst-card__mut" aria-hidden="true"></div>';
    const img = card.imgSrc
      ? `<div class="ft-inst-card__img"><img src="${escHtml(card.imgSrc)}" alt="${escHtml(card.name)}" decoding="async" loading="lazy"></div>`
      : '<div class="ft-inst-card__img" aria-hidden="true">&#x1F41F;</div>';
    const weight = card.weight
      ? `<div class="ft-inst-card__weight">Weight: ${escHtml(card.weight)}</div>`
      : '<div class="ft-inst-card__weight ft-inst-card__weight--unknown">Weight unknown</div>';
    return `<div class="ft-inst-card">${img}<div class="ft-inst-card__body">${mut}<div class="ft-inst-card__name">${escHtml(card.name)}</div><div class="ft-inst-card__owner">${escHtml(card.owner || '-')}</div>${weight}</div></div>`;
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
    const mut = row.mutation
      ? `<span class="ft-detail-owner__mut" style="color:${escHtml(ftMutationColor(row.mutation))}">${escHtml(row.mutation)}</span>`
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
    let title = container.querySelector('.fish-section__title');
    if (!title) {
      container.innerHTML = '<div class="fish-section__title"></div><div class="items-grid inventory-grid fish-grid"></div>';
      title = container.querySelector('.fish-section__title');
    }
    const titleText = `Fishes (${formatQuantity(fishTotal)})`;
    if (title && title.textContent !== titleText) title.textContent = titleText;
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
    if (!entry.lastData) setCardError(entry.el);
  }
  function backpackQuerySuffix(forceFresh) {
    const useLite = !(DEBUG_INVENTORY && DEBUG_GLOBAL);
    const params = new URLSearchParams();
    if (useLite) params.set('lite', '1');
    if (forceFresh) params.set('_', String(Date.now()));
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
  async function pollUser(key, opts) {
    const entry = trackers.get(key);
    if (!entry) return;
    const forceFresh = opts && opts.forceFresh === true;
    try {
      const res = await fetch(`${TRACKER_READ_API}/get-backpack/${encodeURIComponent(key)}${backpackQuerySuffix(forceFresh)}`, {
        cache: 'no-store',
        headers: { 'Cache-Control': 'no-cache', Pragma: 'no-cache' },
      });
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
      const raw = await res.text();
      notePerfFetch(raw);
      const data = JSON.parse(raw);
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
    }
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
  function bindRemoveMenu() {
    if (!removeMenuBtn || !removeMenuEl) return;
    bindRemoveAllModal();
    removeMenuBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleRemoveMenu();
    });
    removeMenuEl.addEventListener('click', (e) => {
      const allBtn = e.target.closest('[data-remove-all]');
      if (allBtn) {
        closeRemoveMenu();
        openRemoveAllModal();
        return;
      }
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
    safeBind('remove menu', bindRemoveMenu);
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
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') refetchAllAccountStatus(true);
      });
      window.addEventListener('focus', () => refetchAllAccountStatus(true));
      window.addEventListener('pageshow', (ev) => {
        if (ev.persisted) refetchAllAccountStatus(true);
      });
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
    console.log('[inventory] UI initialized');
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initInventoryUi);
  } else {
    initInventoryUi();
  }
}());