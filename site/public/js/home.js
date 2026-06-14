(function initHomeLanding() {
  'use strict';

  var NAV_OFFSET = (function readNavOffset() {
    var root = document.querySelector('.deng-home');
    if (!root || !window.getComputedStyle) return 108;
    var raw = getComputedStyle(root).getPropertyValue('--deng-home-nav-offset').trim();
    var n = parseFloat(raw);
    return Number.isFinite(n) && n > 0 ? n : 108;
  }());
  var countUp = function() { return window.DengCountUpStats; };

  function fmt(value) {
    var n = Number(value);
    if (!Number.isFinite(n) || n < 0) return null;
    return Math.round(n);
  }

  function showCard(key) {
    var card = document.querySelector('[data-home-stat-card="' + key + '"]');
    if (card) card.hidden = false;
  }

  function statEl(key) {
    return document.querySelector('[data-home-stat-value="' + key + '"]');
  }

  function metaEl(key) {
    return document.querySelector('[data-home-stat-meta="' + key + '"]');
  }

  function setStat(key, value) {
    var n = fmt(value);
    if (n == null) return false;
    var el = statEl(key);
    if (!el) return false;
    showCard(key);
    if (countUp()) countUp().set(el, { to: n, format: 'integer' });
    else el.textContent = n.toLocaleString('en-US');
    return true;
  }

  function setSplitDevices(active, total) {
    var activeN = fmt(active);
    var totalN = fmt(total);
    if (activeN == null || totalN == null) return false;
    var activeEl = statEl('rejoinActiveDevices');
    var totalEl = statEl('rejoinTotalDevices');
    if (!activeEl || !totalEl) return false;
    showCard('rejoinActiveDevices');
    if (countUp()) {
      countUp().set(activeEl, { to: activeN, format: 'integer' });
      countUp().set(totalEl, { to: totalN, format: 'integer' });
    } else {
      activeEl.textContent = activeN.toLocaleString('en-US');
      totalEl.textContent = totalN.toLocaleString('en-US');
    }
    return true;
  }

  function setOnlineMeta(online, total) {
    var meta = metaEl('onlineNow');
    if (!meta) return;
    var onlineN = fmt(online);
    var totalN = fmt(total);
    if (onlineN == null || totalN == null) {
      meta.textContent = 'Tracked usernames online';
      return;
    }
    var offline = Math.max(0, totalN - onlineN);
    meta.textContent = offline.toLocaleString('en-US') + ' Offline';
  }

  function updateOnlinePill(count) {
    var pill = document.querySelector('[data-home-online-pill]');
    var text = document.querySelector('[data-home-online-text]');
    if (!pill || !text) return;
    var formatted = fmt(count);
    pill.hidden = false;
    if (formatted == null) {
      text.classList.remove('js-count-up');
      text.removeAttribute('data-count-to');
      text.removeAttribute('data-count-suffix');
      text.removeAttribute('data-count-format');
      text.removeAttribute('data-count-duration');
      text.textContent = 'Live network online';
      pill.classList.remove('deng-home-hero__pill--offline');
      return;
    }
    if (formatted === 0) {
      text.classList.remove('js-count-up');
      text.removeAttribute('data-count-to');
      text.removeAttribute('data-count-suffix');
      text.removeAttribute('data-count-format');
      text.removeAttribute('data-count-duration');
      text.textContent = 'No One Online';
      pill.classList.add('deng-home-hero__pill--offline');
      return;
    }
    pill.classList.remove('deng-home-hero__pill--offline');
    text.classList.add('js-count-up');
    if (countUp()) {
      countUp().set(text, { to: formatted, format: 'integer', suffix: ' Online Now' });
    } else {
      text.textContent = formatted.toLocaleString('en-US') + ' Online Now';
    }
  }

  function markEmpty(selector, visibleCount) {
    var empty = document.querySelector(selector);
    if (empty) empty.hidden = visibleCount > 0;
  }

  function setActiveNav(section) {
    document.querySelectorAll('.deng-home-nav__link[data-nav-section]').forEach(function(link) {
      var isActive = link.getAttribute('data-nav-section') === section;
      link.classList.toggle('is-active', isActive);
    });
  }

  function scrollToSection(target) {
    if (!target) return;
    var top = target.getBoundingClientRect().top + window.scrollY - NAV_OFFSET;
    window.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  }

  function bindSmoothScroll() {
    document.querySelectorAll('.deng-home-nav__link[href^="#"], .deng-home-footer__links a[href^="#"]').forEach(function(link) {
      link.addEventListener('click', function(event) {
        var id = link.getAttribute('href');
        if (!id || id === '#') return;
        var target = document.querySelector(id);
        if (!target) return;
        event.preventDefault();
        scrollToSection(target);
        var section = link.getAttribute('data-nav-section') || id.replace('#', '');
        if (section) setActiveNav(section);
      });
    });
  }

  function bindNavScrollSpy() {
    var sections = Array.prototype.slice.call(document.querySelectorAll('[data-home-section]'));
    if (!sections.length) return;

    function updateFromScroll() {
      var marker = window.scrollY + NAV_OFFSET + 8;
      var current = 'home';
      sections.forEach(function(section) {
        if (section.offsetTop <= marker) {
          current = section.getAttribute('data-home-section') || current;
        }
      });
      setActiveNav(current);
    }

    window.addEventListener('scroll', updateFromScroll, { passive: true });
    updateFromScroll();
  }

  function bindWordmark() {
    var root = document.querySelector('[data-hero-wordmark]');
    if (!root) return;
    var words = Array.prototype.slice.call(root.querySelectorAll('[data-hero-word]'));

    function activate(word) {
      words.forEach(function(el) {
        el.classList.toggle('is-active', el === word);
      });
    }

    words.forEach(function(word) {
      word.addEventListener('mouseenter', function() { activate(word); });
      word.addEventListener('focus', function() { activate(word); });
    });

    root.addEventListener('mouseleave', function() {
      activate(words[0]);
    });

    root.addEventListener('focusout', function(event) {
      if (!root.contains(event.relatedTarget)) activate(words[0]);
    });
  }

  function loadPublicStats() {
    return fetch('/api/public-stats', {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store',
    })
      .then(function(res) { return res.ok ? res.json() : null; })
      .catch(function() { return null; });
  }

  function loadTrackerNetwork() {
    return fetch('/api/public/tracker-stats', {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    })
      .then(function(res) {
        if (!res.ok) {
          console.warn('[home] tracker stats fetch failed status=' + res.status);
          return null;
        }
        return res.json();
      })
      .then(function(data) {
        if (!data || !Number.isFinite(Number(data.trackedCount))) return null;
        return {
          available: true,
          trackedUsernames: Number(data.trackedCount),
          onlineUsernames: Number(data.onlineCount) || 0,
        };
      })
      .catch(function(err) {
        console.warn('[home] tracker stats fetch error', err && err.message ? err.message : err);
        return null;
      });
  }

  function loadFishitSummary() {
    return fetch('/api/fishit/public-summary', {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    })
      .then(function(res) { return res.ok ? res.json() : null; })
      .catch(function() { return null; });
  }

  function applyStats(publicStats, trackerNetwork, fishitSummary) {
    var liveVisible = 0;
    var platformVisible = 0;
    var fishitVisible = 0;
    var trackedTotal = null;
    var onlineTotal = null;

    if (trackerNetwork && trackerNetwork.available) {
      trackedTotal = trackerNetwork.trackedUsernames;
      onlineTotal = trackerNetwork.onlineUsernames;
      if (setStat('trackedPlayers', trackedTotal)) liveVisible += 1;
      if (setStat('onlineNow', onlineTotal)) liveVisible += 1;
      setOnlineMeta(onlineTotal, trackedTotal);
      updateOnlinePill(onlineTotal);
    } else {
      updateOnlinePill(null);
      setOnlineMeta(null, null);
    }

    if (publicStats) {
      if (setStat('discordUsers', publicStats.uniqueUsers)) platformVisible += 1;
      if (setStat('generatedKeys', publicStats.generatedKeys)) platformVisible += 1;
      if (setStat('redeemedKeys', publicStats.redeemedKeys)) platformVisible += 1;
      if (setSplitDevices(publicStats.activeDevices, publicStats.totalDevices)) liveVisible += 1;
    }

    if (fishitSummary && fishitSummary.available) {
      if (setStat('caught24Hours', fishitSummary.caught24Hours)) fishitVisible += 1;
      if (setStat('totalSecret', fishitSummary.totalSecret)) fishitVisible += 1;
      if (setStat('totalForgotten', fishitSummary.totalForgotten)) fishitVisible += 1;
      if (setStat('ghostfinnRod', fishitSummary.ghostfinnRod)) fishitVisible += 1;
      if (setStat('elementRod', fishitSummary.elementRod)) fishitVisible += 1;
      if (setStat('diamondRod', fishitSummary.diamondRod)) fishitVisible += 1;
    }

    markEmpty('[data-home-live-stats-empty]', liveVisible);
    markEmpty('[data-home-platform-stats-empty]', platformVisible);
    markEmpty('[data-home-fishit-stats-empty]', fishitVisible);
  }

  bindSmoothScroll();
  bindNavScrollSpy();
  bindWordmark();

  function readInitialHomeStats() {
    var root = document.getElementById('top');
    if (!root) return null;
    var tracked = Number(root.getAttribute('data-home-initial-tracked'));
    var online = Number(root.getAttribute('data-home-initial-online'));
    if (!Number.isFinite(tracked) && !Number.isFinite(online)) return null;
    return {
      available: true,
      trackedUsernames: Number.isFinite(tracked) ? tracked : 0,
      onlineUsernames: Number.isFinite(online) ? online : 0,
    };
  }

  function refreshHomeStats() {
    Promise.all([loadPublicStats(), loadTrackerNetwork(), loadFishitSummary()])
      .then(function(results) {
        applyStats(results[0], results[1], results[2]);
      })
      .catch(function() {
        updateOnlinePill(null);
        markEmpty('[data-home-live-stats-empty]', 0);
        markEmpty('[data-home-platform-stats-empty]', 0);
        markEmpty('[data-home-fishit-stats-empty]', 0);
      });
  }

  var initialStats = readInitialHomeStats();
  if (initialStats) {
    applyStats(null, initialStats, null);
  }
  refreshHomeStats();
  window.setInterval(refreshHomeStats, 10000);
}());
