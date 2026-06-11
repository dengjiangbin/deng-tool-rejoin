(function initHomeLanding() {
  'use strict';

  var NAV_OFFSET = 96;

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

  function setDevicePair(active, total) {
    var activeFmt = fmt(active);
    var totalFmt = fmt(total);
    if (activeFmt == null || totalFmt == null) return false;
    var el = document.querySelector('[data-home-stat-value="activeDevices"]');
    if (!el) return false;
    el.textContent = activeFmt + ' / ' + totalFmt;
    showCard('activeDevices');
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
    return fetch('/api/fishit-tracker/public-network', {
      headers: { Accept: 'application/json' },
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

  function applyStats(publicStats, trackerNetwork, fishitGlobal) {
    var liveVisible = 0;
    var platformVisible = 0;

    if (trackerNetwork && trackerNetwork.available) {
      if (setStat('trackedUsernames', trackerNetwork.trackedUsernames)) liveVisible += 1;
      if (setStat('onlineUsernames', trackerNetwork.onlineUsernames)) liveVisible += 1;
      updateOnlinePill(trackerNetwork.onlineUsernames);
    } else {
      updateOnlinePill(null);
    }

    if (publicStats) {
      if (setDevicePair(publicStats.activeDevices, publicStats.totalDevices)) liveVisible += 1;
      if (setStat('discordUsers', publicStats.uniqueUsers)) platformVisible += 1;
      if (setStat('generatedKeys', publicStats.generatedKeys)) platformVisible += 1;
      if (setStat('redeemedKeys', publicStats.redeemedKeys)) platformVisible += 1;
    } else if (!trackerNetwork || !trackerNetwork.available) {
      updateOnlinePill(null);
    }

    if (fishitGlobal && fishitGlobal.available) {
      if (setStat('totalFish', fishitGlobal.total_fish)) liveVisible += 1;
    }

    markEmpty('[data-home-live-stats-empty]', liveVisible);
    markEmpty('[data-home-platform-stats-empty]', platformVisible);
  }

  bindSmoothScroll();
  bindNavScrollSpy();
  bindWordmark();

  Promise.all([loadPublicStats(), loadTrackerNetwork(), loadFishitGlobal()])
    .then(function(results) {
      applyStats(results[0], results[1], results[2]);
    })
    .catch(function() {
      updateOnlinePill(null);
      markEmpty('[data-home-live-stats-empty]', 0);
      markEmpty('[data-home-platform-stats-empty]', 0);
    });
}());
