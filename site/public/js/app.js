'use strict';
/* global document, fetch, localStorage, navigator, window */

(function initThemeToggle() {
  var STORAGE_KEY = 'deng_tool_theme';
  var root = document.documentElement;
  var toggles = Array.prototype.slice.call(document.querySelectorAll('[data-theme-toggle]'));

  function systemTheme() {
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) return 'light';
    return 'dark';
  }

  function savedTheme() {
    try {
      var saved = localStorage.getItem(STORAGE_KEY);
      return saved === 'light' || saved === 'dark' ? saved : '';
    } catch {
      return '';
    }
  }

  function applyTheme(theme, persist) {
    var next = theme === 'light' ? 'light' : 'dark';
    root.dataset.theme = next;
    toggles.forEach(function(toggle) {
      toggle.setAttribute('aria-label', 'Switch to ' + (next === 'light' ? 'dark' : 'light') + ' mode');
      toggle.setAttribute('title', 'Switch to ' + (next === 'light' ? 'Dark' : 'Light') + ' mode');
      toggle.setAttribute('aria-pressed', next === 'dark' ? 'true' : 'false');
    });
    if (persist) {
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // Theme still applies for this page even when storage is blocked.
      }
    }
  }

  applyTheme(savedTheme() || root.dataset.theme || systemTheme(), false);
  toggles.forEach(function(toggle) {
    toggle.addEventListener('click', function(event) {
      event.preventDefault();
      event.stopPropagation();
      applyTheme(root.dataset.theme === 'light' ? 'dark' : 'light', true);
    });
  });
}());

(function initHideUsername() {
  // UI-only privacy mask. Never changes the real identity sent to the server;
  // it only obscures the displayed Discord username (e.g. dengjiangbin -> d*********n).
  var STORAGE_KEY = 'deng_tool_hide_username';
  var toggles = Array.prototype.slice.call(document.querySelectorAll('[data-hide-username-toggle]'));

  function isHidden() {
    try { return localStorage.getItem(STORAGE_KEY) === '1'; } catch (e) { return false; }
  }
  function mask(name) {
    var s = String(name || '');
    if (s.length <= 1) return s ? s[0] + '*' : '*';
    if (s.length === 2) return s[0] + '*';
    return s[0] + new Array(s.length - 1).join('*') + s[s.length - 1];
  }
  function apply() {
    var hidden = isHidden();
    Array.prototype.slice.call(document.querySelectorAll('[data-username]')).forEach(function (el) {
      if (!el.hasAttribute('data-username-original')) {
        el.setAttribute('data-username-original', el.textContent.trim());
      }
      var original = el.getAttribute('data-username-original') || '';
      el.textContent = hidden ? mask(original) : original;
    });
    toggles.forEach(function (t) {
      t.setAttribute('aria-pressed', hidden ? 'true' : 'false');
      t.classList.toggle('active', hidden);
    });
  }
  function setHidden(next) {
    try { localStorage.setItem(STORAGE_KEY, next ? '1' : '0'); } catch (e) { /* still applies this page */ }
    apply();
  }

  // Expose for dynamically rendered content (e.g. Fish It pages).
  window.DengPrivacy = { apply: apply, isHidden: isHidden, mask: mask };

  toggles.forEach(function (t) {
    t.addEventListener('click', function (e) {
      e.preventDefault();
      setHidden(!isHidden());
    });
  });
  apply();
}());

(function initProgressMemory() {
  try {
    var stepEl = document.querySelector('[data-portal-step]');
    if (!stepEl) return;
    var state = {
      step: stepEl.dataset.portalStep || '',
      provider: stepEl.dataset.provider || '',
      updatedAt: Date.now()
    };
    localStorage.setItem('deng_tool_key_progress', JSON.stringify(state));
  } catch {
    // Local storage is only a harmless UX hint. Server state is authoritative.
  }
}());

(function initProviderReturnResume() {
  try {
    var path = window.location.pathname || '';
    if (path !== '/license' && path !== '/license/') return;
    var params = new URLSearchParams(window.location.search || '');
    var hash = params.get('hash');
    var state = params.get('s');
    if (hash) {
      window.location.replace('/unlock/linkvertise/complete?hash=' + encodeURIComponent(hash));
      return;
    }
    if (state) {
      window.location.replace('/unlock/lootlabs/complete?s=' + encodeURIComponent(state));
    }
  } catch (e) {
    // Server-side completion routes remain authoritative.
  }
}());

(function initCooldown() {
  var notice = document.querySelector('.cooldown-notice');
  if (!notice) return;
  var seconds = parseInt(notice.dataset.seconds || '0', 10);
  // If the cooldown has already expired (server rendered a stale state), hide immediately.
  if (!Number.isFinite(seconds) || seconds <= 0) {
    notice.style.display = 'none';
    return;
  }

  var counter = notice.querySelector('.countdown');
  var btn = document.getElementById('btn-generate');
  if (btn) btn.disabled = true;

  var remaining = seconds;
  function tick() {
    if (!counter) return;
    var m = Math.floor(remaining / 60);
    var s = remaining % 60;
    counter.textContent = m > 0 ? m + 'm ' + String(s).padStart(2, '0') + 's' : s + 's';
    if (remaining <= 0) {
      notice.style.display = 'none';
      if (btn) btn.disabled = false;
      return;
    }
    remaining -= 1;
    setTimeout(tick, 1000);
  }
  tick();
}());

(function initLicenseEligibilityRefresh() {
  var root = document.querySelector('[data-eligibility-refresh]');
  if (!root || !window.fetch) return;

  var btn = document.getElementById('btn-generate');
  var notice = document.querySelector('[data-eligibility-notice]');
  var serverNotice = document.querySelector('[data-server-license-notice]');

  function formatBlockMessage(body) {
    if (!body || body.canGenerate) return '';
    var reason = body.blockReason || '';
    var msg = body.message || '';
    if (reason === 'cooldown_active' && body.remainingSeconds > 0) {
      return msg || ('Please wait before generating another key. Try again in ' + body.remainingSeconds + 's.');
    }
    if (reason === 'active_unredeemed_key' && body.remainingSeconds > 0) {
      var mins = Math.ceil(body.remainingSeconds / 60);
      return msg || ('You already have an unused key. Expires in ' + mins + ' min.');
    }
    return msg || 'Key generation is temporarily unavailable.';
  }

  fetch('/api/license/eligibility', { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
    .then(function(res) { return res.json(); })
    .then(function(body) {
      try { localStorage.removeItem('deng_tool_key_blocked'); } catch (e) { /* ignore */ }

      if (body && body.canGenerate) {
        if (notice) notice.hidden = true;
        if (serverNotice) serverNotice.hidden = true;
        if (btn && !document.querySelector('.unused-key-recovery')) btn.disabled = false;
        return;
      }

      var text = formatBlockMessage(body);
      // The server rendered a fresh eligibility block for the same reason.
      // Keep that one authoritative notice instead of adding a second client
      // copy after the eligibility refresh completes.
      if (serverNotice && (
        serverNotice.dataset.blockReason === (body.blockReason || '')
        || serverNotice.dataset.blockReason === 'server_error'
      )) {
        serverNotice.textContent = text;
        serverNotice.hidden = !text;
        if (notice) notice.hidden = true;
        if (btn && body.blockReason === 'cooldown_active') btn.disabled = true;
        return;
      }
      if (document.querySelector('.unused-key-recovery') && body.blockReason === 'active_unredeemed_key') {
        if (notice) notice.hidden = true;
        if (btn) btn.disabled = true;
        return;
      }
      if (notice && text) {
        notice.textContent = text;
        notice.dataset.blockReason = body.blockReason || '';
        notice.hidden = false;
      }
      if (btn && body.blockReason === 'cooldown_active') btn.disabled = true;
    })
    .catch(function() {
      // Backend remains authoritative; ignore refresh failures.
    });
}());

(function initAlerts() {
  var alerts = document.querySelectorAll('.alert');
  alerts.forEach(function(el) {
    setTimeout(function() {
      el.style.transition = 'opacity 0.4s';
      el.style.opacity = '0';
      setTimeout(function() { el.remove(); }, 400);
    }, 5000);
  });
}());

(function initPublicStats() {
  var root = document.querySelector('[data-public-stats]');
  if (!root || !window.fetch) return;

  var keys = ['generatedKeys', 'uniqueUsers', 'redeemedKeys', 'activeDevices'];
  var values = {};
  keys.forEach(function(key) {
    values[key] = root.querySelector('[data-public-stat="' + key + '"]');
  });

  function safeNumber(value) {
    var n = Number(value);
    return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
  }

  function formatNumber(value) {
    var n = safeNumber(value);
    if (n === null) return '—';
    return n.toLocaleString('en-US');
  }

  function setValue(key, value) {
    var el = values[key];
    if (!el) return;
    if (window.DengCountUpStats) {
      window.DengCountUpStats.set(el, { to: value, format: 'integer' });
      return;
    }
    var next = formatNumber(value);
    if (el.textContent === next) return;
    el.classList.add('is-updating');
    el.textContent = next;
    window.setTimeout(function() {
      el.classList.remove('is-updating');
    }, 180);
  }

  function applyStats(stats) {
    keys.forEach(function(key) {
      setValue(key, stats && stats[key]);
    });
  }

  function loadStats() {
    fetch('/api/public-stats', {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store'
    })
      .then(function(res) {
        if (!res.ok) throw new Error('public_stats_failed');
        return res.json();
      })
      .then(applyStats)
      .catch(function() {
        // Keep the last good values (or the initial dash skeleton) and retry later.
      });
  }

  loadStats();
  window.setInterval(loadStats, 10000);
}());

(function initCopyButtons() {
  function fallbackCopy(text) {
    return new Promise(function(resolve, reject) {
      var textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.setAttribute('readonly', '');
      textarea.style.position = 'fixed';
      textarea.style.top = '-1000px';
      textarea.style.left = '-1000px';
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      try {
        var ok = document.execCommand && document.execCommand('copy');
        document.body.removeChild(textarea);
        if (ok) resolve();
        else reject(new Error('copy_failed'));
      } catch (err) {
        document.body.removeChild(textarea);
        reject(err);
      }
    });
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(function() {
        return fallbackCopy(text);
      });
    }
    return fallbackCopy(text);
  }

  document.querySelectorAll('[data-copy-key]').forEach(function(button) {
    button.addEventListener('click', function() {
      var key = button.dataset.key || '';
      if (!key) return;
      return copyText(key).then(function() {
        button.textContent = 'Copied';
        button.classList.add('copied');
        clearTimeout(button._copyTimer);
        button._copyTimer = setTimeout(function() {
          button.textContent = 'Copy';
          button.classList.remove('copied');
        }, 2200);
      }).catch(function() {
        button.textContent = 'Copy manually';
      });
    });
  });
}());

(function initLicenseActions() {
  var root = document.querySelector('[data-license-actions]');
  if (!root) return;

  var pageMessage = document.querySelector('[data-license-message]');
  var downloadButton = document.querySelector('[data-download-keys]');

  function showMessage(el, text, type) {
    if (!el) return;
    el.textContent = text || '';
    el.className = 'license-message ' + (type === 'success' ? 'license-message-success' : 'license-message-error');
    el.hidden = !text;
  }

  function setBusy(button, busy, label) {
    if (!button) return;
    if (busy) {
      button.dataset.originalText = button.textContent;
      button.textContent = label || 'Loading...';
      button.disabled = true;
    } else {
      button.textContent = button.dataset.originalText || button.textContent;
      button.disabled = false;
    }
  }

  var generateForm = root.querySelector('.license-action-form');
  if (generateForm) {
    generateForm.addEventListener('submit', function() {
      // Clear a client-only notice before the browser follows the server-owned
      // generation flow. Any current block will be rendered exactly once by
      // the destination page.
      if (pageMessage) showMessage(pageMessage, '', 'success');
    });
  }

  if (downloadButton) {
    downloadButton.addEventListener('click', function() {
      setBusy(downloadButton, true, 'Downloading...');
      showMessage(pageMessage, '', 'success');
      window.location.href = '/api/license/download';
      setTimeout(function() { setBusy(downloadButton, false); }, 1200);
    });
  }
}());
