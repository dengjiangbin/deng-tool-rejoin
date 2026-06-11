(function initHomeLanding() {
  'use strict';

  function fmt(value) {
    var n = Number(value);
    if (!Number.isFinite(n) || n < 0) return null;
    return Math.round(n).toLocaleString('en-US');
  }

  function showCard(key) {
    var card = document.querySelector('[data-home-stat-card="' + key + '"]');
    if (card) card.hidden = false;
  }

  function setStat(key, value) {
    var formatted = fmt(value);
    if (formatted == null) return false;
    var el = document.querySelector('[data-home-stat-value="' + key + '"]');
    if (!el) return false;
    el.textContent = formatted;
    showCard(key);
    return true;
  }

  function updateOnlinePill(count) {
    var pill = document.querySelector('[data-home-online-pill]');
    var text = document.querySelector('[data-home-online-text]');
    if (!pill || !text) return;
    var formatted = fmt(count);
    if (formatted == null) {
      text.textContent = 'Live network online';
    } else {
      text.textContent = formatted + ' online now';
    }
    pill.hidden = false;
  }

  function markEmptyIfNeeded(visibleCount) {
    var empty = document.querySelector('[data-home-stats-empty]');
    if (empty) empty.hidden = visibleCount > 0;
  }

  function setActiveNav(section) {
    document.querySelectorAll('.deng-home-nav__link[data-nav-section]').forEach(function(link) {
      var isActive = link.getAttribute('data-nav-section') === section;
      link.classList.toggle('is-active', isActive);
    });
  }

  function bindSmoothScroll() {
    document.querySelectorAll('.deng-home-nav__link[href^="#"], .deng-home-footer__links a[href^="#"]').forEach(function(link) {
      link.addEventListener('click', function(event) {
        var id = link.getAttribute('href');
        if (!id || id === '#') return;
        var target = document.querySelector(id);
        if (!target) return;
        event.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        var section = link.getAttribute('data-nav-section');
        if (section) setActiveNav(section);
      });
    });
  }

  function bindNavScrollSpy() {
    var sections = Array.prototype.slice.call(document.querySelectorAll('[data-home-section]'));
    if (!sections.length) return;

    function updateFromScroll() {
      var marker = window.scrollY + 120;
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

  function loadPublicStats() {
    return fetch('/api/public-stats', {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store',
    })
      .then(function(res) { return res.ok ? res.json() : null; })
      .catch(function() { return null; });
  }

  function loadFishitGlobal() {
    return fetch('/api/fishit/global', {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    })
      .then(function(res) { return res.ok ? res.json() : null; })
      .catch(function() { return null; });
  }

  function applyStats(publicStats, fishitGlobal) {
    var visible = 0;

    if (publicStats) {
      if (setStat('generatedKeys', publicStats.generatedKeys)) visible += 1;
      if (setStat('uniqueUsers', publicStats.uniqueUsers)) visible += 1;
      if (setStat('redeemedKeys', publicStats.redeemedKeys)) visible += 1;
      if (setStat('onlineNow', publicStats.activeDevices)) visible += 1;
      updateOnlinePill(publicStats.activeDevices);
    } else {
      updateOnlinePill(null);
    }

    if (fishitGlobal && fishitGlobal.available) {
      if (setStat('trackedPlayers', fishitGlobal.total_players)) visible += 1;
      if (setStat('totalFish', fishitGlobal.total_fish)) visible += 1;
      if (setStat('secretFish', fishitGlobal.secret_fish)) visible += 1;
      if (setStat('forgottenFish', fishitGlobal.forgotten_fish)) visible += 1;
    }

    markEmptyIfNeeded(visible);
  }

  bindSmoothScroll();
  bindNavScrollSpy();

  Promise.all([loadPublicStats(), loadFishitGlobal()])
    .then(function(results) {
      applyStats(results[0], results[1]);
    })
    .catch(function() {
      updateOnlinePill(null);
      markEmptyIfNeeded(0);
    });
}());
