(function () {
  'use strict';
  var root = document.querySelector('[data-fishit-page]');
  if (!root) return;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmt(n) {
    var v = Number(n);
    if (!isFinite(v)) return '0';
    return Math.round(v).toLocaleString('en-US');
  }
  function relTime(iso) {
    if (!iso) return '';
    var t = Date.parse(iso);
    if (!isFinite(t)) return '';
    var s = Math.max(0, Math.floor((Date.now() - t) / 1000));
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }
  function api(path) {
    return fetch(path, { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then(function (r) {
        if (r.status === 401) { var e = new Error('auth'); e.code = 401; throw e; }
        if (!r.ok) throw new Error('http_' + r.status);
        return r.json();
      });
  }
  function emptyHtml(msg) { return '<div class="fishit-empty">' + esc(msg) + '</div>'; }
  function errorHtml(target) {
    return '<div class="fishit-error"><p>Could not load Fish It stats.</p>' +
      '<button type="button" class="btn btn-ghost" data-retry="' + target + '">Retry</button></div>';
  }
  function badge(rarity) {
    var r = (rarity || 'secret').toLowerCase();
    var label = r.charAt(0).toUpperCase() + r.slice(1);
    return '<span class="rarity-badge rarity-' + esc(r) + '">' + esc(label) + '</span>';
  }
  function imageUrl(item) {
    return item && (item.imageUrl || item.image_url || item.thumbnailUrl || item.thumbnail || item.image || null);
  }
  // Re-apply username masking after dynamic render (app.js owns the state).
  function remask() { if (window.DengPrivacy && window.DengPrivacy.apply) window.DengPrivacy.apply(); }
  function countUpStatHtml(value, extraAttrs) {
    return '<strong class="stat-card-value js-count-up"' + (extraAttrs || '') +
      ' data-count-to="' + esc(String(value == null ? 0 : value)) +
      '" data-count-format="integer" data-count-duration="750">0</strong>';
  }
  function countUpAmountHtml(value) {
    return '<strong class="fishit-stat-amount js-count-up" data-count-to="' + esc(String(value == null ? 0 : value)) +
      '" data-count-format="integer" data-count-duration="750">0</strong>';
  }
  function refreshCountUp(container) {
    if (window.DengCountUpStats) window.DengCountUpStats.refresh(container);
  }

  // ── Tabs ──────────────────────────────────────────────────────────────────
  var loaded = { daily: false, stats: false, fish: false };
  root.querySelectorAll('[data-fishit-tab]').forEach(function (tab) {
    tab.addEventListener('click', function () {
      var name = tab.getAttribute('data-fishit-tab');
      root.querySelectorAll('[data-fishit-tab]').forEach(function (t) {
        var on = t === tab;
        t.classList.toggle('active', on);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      root.querySelectorAll('[data-fishit-panel]').forEach(function (p) {
        p.hidden = p.getAttribute('data-fishit-panel') !== name;
      });
      ensureLoaded(name);
    });
  });

  function ensureLoaded(name) {
    if (name === 'daily') loadDaily(currentPeriod);
    else if (name === 'stats') { if (!loaded.stats) loadStats(); }
    else if (name === 'fish') { if (!loaded.fish) loadFish(true); }
  }

  // ── Daily ───────────────────────────────────────────────────────────────────
  var currentPeriod = 'today';
  var dailyBody = root.querySelector('[data-daily-body]');
  root.querySelectorAll('[data-period]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      currentPeriod = chip.getAttribute('data-period');
      root.querySelectorAll('[data-period]').forEach(function (c) { c.classList.toggle('active', c === chip); });
      loadDaily(currentPeriod);
    });
  });

  function dailyCardHtml(c) {
    var meta = [];
    if (c.maxWeight) meta.push('Wt ' + esc(c.maxWeight));
    return '<article class="fish-card" tabindex="0">' +
      '<div class="fish-card-img">' + imgTag(imageUrl(c), c.fallbackUrl, c.name) + badge(c.rarity) + '</div>' +
      '<div class="fish-card-body"><span class="fish-card-name">' + esc(c.name) + '</span>' +
      '<div class="fish-card-stats"><span class="fish-card-amount">x' + fmt(c.count) + '</span>' +
      (meta.length ? '<span class="fish-card-meta">' + meta.join(' · ') + '</span>' : '') + '</div></div></article>';
  }
  function loadDaily(period) {
    dailyBody.innerHTML = '<div class="fishit-skeleton-grid"><div class="skeleton-fish"></div><div class="skeleton-fish"></div><div class="skeleton-fish"></div></div>';
    api('/api/fishit/me/daily?period=' + encodeURIComponent(period))
      .then(function (d) {
        var summary = d.summary || { totalFish: 0, secretFish: 0, forgottenFish: 0 };
        var cards = [
          { label: 'Total Fish', value: summary.totalFish },
          { label: 'Secret', value: summary.secretFish },
          { label: 'Forgotten', value: summary.forgottenFish },
        ];
        var html = '<div class="stat-card-row">' + cards.map(function (c) {
          return '<article class="stat-card"><span class="stat-card-label">' + esc(c.label) +
            '</span>' + countUpStatHtml(c.value) + '</article>';
        }).join('') + '</div>';
        if (!d.cards || !d.cards.length) {
          html += emptyHtml(d.emptyMessage || 'No catches found for this period.');
        } else {
          html += '<div class="fishit-card-grid">' + d.cards.map(dailyCardHtml).join('') + '</div>';
        }
        if (d.lastUpdated) html += '<p class="fishit-updated">Updated ' + esc(relTime(d.lastUpdated)) + '</p>';
        dailyBody.innerHTML = html;
        refreshCountUp(dailyBody);
      })
      .catch(function (e) { dailyBody.innerHTML = e.code === 401 ? emptyHtml('Sign in with Discord to view your Fish It stats.') : errorHtml('daily'); });
  }

  // ── Stats ─────────────────────────────────────────────────────────────────
  var statsBody = root.querySelector('[data-stats-body]');
  function statCard(card) {
    var img = imgTag(imageUrl(card), card.fallbackUrl, card.label);
    return '<article class="fishit-stat-card">' +
      '<div class="fishit-stat-img">' + img + '</div>' +
      '<span class="fishit-stat-label">' + esc(card.label) + '</span>' +
      countUpAmountHtml(card.amount != null ? card.amount : card.count) + '</article>';
  }
  function loadStats() {
    statsBody.innerHTML = '<div class="fishit-skeleton-grid"><div class="skeleton-fish"></div><div class="skeleton-fish"></div></div>';
    api('/api/fishit/me/stats')
      .then(function (s) {
        loaded.stats = true;
        if (!s.hasData) { statsBody.innerHTML = emptyHtml('You do not have Fish It stats yet.'); return; }
        var html = '<div class="stat-card-row"><article class="stat-card highlight"><span class="stat-card-label">Total Fish Caught</span>' +
          countUpStatHtml(s.totalFish) +
          (s.rank ? '<span class="stat-card-sub">Rank #' + s.rank.rank + ' of ' + s.rank.of + '</span>' : '') + '</article></div>';
        html += '<h2 class="fishit-section-title">Rarity</h2><div class="fishit-card-grid">' + (s.rarityCards || []).map(statCard).join('') + '</div>';
        html += '<h2 class="fishit-section-title">Rods</h2><div class="fishit-card-grid">' + (s.rodCards || []).map(statCard).join('') + '</div>';
        statsBody.innerHTML = html;
        refreshCountUp(statsBody);
        remask();
      })
      .catch(function (e) { statsBody.innerHTML = e.code === 401 ? emptyHtml('Sign in with Discord to view your Fish It stats.') : errorHtml('stats'); });
  }

  // ── Fish grid ───────────────────────────────────────────────────────────────
  var grid = root.querySelector('[data-fish-grid]');
  var moreWrap = root.querySelector('[data-fish-more]');
  var loadMoreBtn = root.querySelector('[data-fish-loadmore]');
  var searchEl = root.querySelector('[data-fish-search]');
  var rarityEl = root.querySelector('[data-fish-rarity]');
  var sortEl = root.querySelector('[data-fish-sort]');
  var fishPage = 1, fishPages = 1, searchTimer = null;

  function imgTag(src, fallback, alt) {
    var fb = esc(fallback || '/public/img/fishit/fallback-fish.svg');
    if (!src) return '<img class="lazy-img" loading="lazy" src="' + fb + '" alt="' + esc(alt) + '">';
    return '<img class="lazy-img" loading="lazy" src="' + esc(src) + '" alt="' + esc(alt) +
      '" onerror="this.onerror=null;this.src=\'' + fb + '\'">';
  }
  function fishCard(f) {
    var meta = [];
    if (f.maxWeight) meta.push('Wt ' + esc(f.maxWeight));
    if (f.mutation) meta.push(esc(f.mutation));
    return '<article class="fish-card" tabindex="0">' +
      '<div class="fish-card-img">' + imgTag(imageUrl(f), f.fallbackUrl, f.name) + badge(f.rarity) + '</div>' +
      '<div class="fish-card-body"><span class="fish-card-name">' + esc(f.name) + '</span>' +
      '<div class="fish-card-stats"><span class="fish-card-amount">x' + fmt(f.count) + '</span>' +
      (meta.length ? '<span class="fish-card-meta">' + meta.join(' · ') + '</span>' : '') + '</div></div></article>';
  }
  function fishQuery() {
    var p = new URLSearchParams();
    if (searchEl.value.trim()) p.set('search', searchEl.value.trim());
    if (rarityEl.value) p.set('rarity', rarityEl.value);
    p.set('sort', sortEl.value);
    p.set('page', String(fishPage));
    p.set('limit', '24');
    return '/api/fishit/me/fish?' + p.toString();
  }
  function loadFish(reset) {
    if (reset) { fishPage = 1; grid.innerHTML = '<div class="fishit-skeleton-grid"><div class="skeleton-fish"></div><div class="skeleton-fish"></div><div class="skeleton-fish"></div></div>'; }
    api(fishQuery())
      .then(function (res) {
        loaded.fish = true;
        fishPages = res.pages || 1;
        if (reset) grid.innerHTML = '';
        var items = res.items || res.fish || [];
        if (!items.length) {
          if (reset) grid.innerHTML = emptyHtml(searchEl.value || rarityEl.value ? 'No fish match your filters.' : 'You do not have any tracked fish yet.');
        } else {
          grid.insertAdjacentHTML('beforeend', items.map(fishCard).join(''));
          remask();
        }
        moreWrap.hidden = fishPage >= fishPages;
      })
      .catch(function (e) { if (reset) grid.innerHTML = e.code === 401 ? emptyHtml('Sign in with Discord to view your Fish It stats.') : errorHtml('fish'); });
  }
  if (searchEl) searchEl.addEventListener('input', function () { clearTimeout(searchTimer); searchTimer = setTimeout(function () { loadFish(true); }, 300); });
  if (rarityEl) rarityEl.addEventListener('change', function () { loadFish(true); });
  if (sortEl) sortEl.addEventListener('change', function () { loadFish(true); });
  if (loadMoreBtn) loadMoreBtn.addEventListener('click', function () { if (fishPage < fishPages) { fishPage++; loadFish(false); } });

  // Retry delegation
  root.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-retry]');
    if (!btn) return;
    var t = btn.getAttribute('data-retry');
    if (t === 'daily') loadDaily(currentPeriod);
    else if (t === 'stats') loadStats();
    else if (t === 'fish') loadFish(true);
  });

  var refreshBtn = root.querySelector('[data-fishit-refresh]');
  if (refreshBtn) refreshBtn.addEventListener('click', function () {
    loaded.stats = false; loaded.fish = false;
    var active = root.querySelector('[data-fishit-tab].active');
    ensureLoaded(active ? active.getAttribute('data-fishit-tab') : 'daily');
  });

  // Initial load (Daily tab)
  loadDaily('today');
}());
