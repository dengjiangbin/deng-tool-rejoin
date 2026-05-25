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

(function initCooldown() {
  var notice = document.querySelector('.cooldown-notice');
  if (!notice) return;
  var seconds = parseInt(notice.dataset.seconds || '0', 10);
  // If the cooldown has already expired (server rendered a stale state), hide immediately.
  if (!seconds || seconds <= 0) {
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

  var csrf = root.dataset.csrf || '';
  var pageMessage = document.querySelector('[data-license-message]');
  var resetModal = document.querySelector('[data-license-modal="reset"]');
  var redeemModal = document.querySelector('[data-license-modal="redeem"]');
  var resetList = document.querySelector('[data-reset-key-list]');
  var resetMessage = document.querySelector('[data-reset-message]');
  var redeemMessage = document.querySelector('[data-redeem-message]');
  var redeemInput = document.querySelector('[data-redeem-key-input]');
  var resetButton = document.querySelector('[data-confirm-reset]');
  var redeemButton = document.querySelector('[data-confirm-redeem]');
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

  function openModal(modal) {
    if (!modal) return;
    modal.hidden = false;
    showMessage(pageMessage, '', 'success');
  }

  function closeModals() {
    [resetModal, redeemModal].forEach(function(modal) {
      if (modal) modal.hidden = true;
    });
  }

  function selectedResetKeyId() {
    var selected = resetList ? resetList.querySelector('input[name="reset_key_id"]:checked') : null;
    return selected ? selected.value : '';
  }

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, function(ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function loadResetKeys() {
    if (!resetList) return;
    resetList.innerHTML = '<p class="empty-text">Loading keys...</p>';
    showMessage(resetMessage, '', 'success');
    fetch('/api/license/resettable', { headers: { Accept: 'application/json' } })
      .then(function(res) { return res.json().then(function(body) { return { ok: res.ok, body: body }; }); })
      .then(function(result) {
        if (!result.ok) throw new Error(result.body.message || 'Could not load keys.');
        var resettable = (result.body.keys || []).filter(function(row) { return row.can_reset; });
        if (!resettable.length) {
          resetList.innerHTML = '<p class="empty-title">No Resettable Keys Found.</p>';
          return;
        }
        resetList.innerHTML = resettable.map(function(row, idx) {
          var device = row.device_label || 'Bound To A Device';
          var checked = idx === 0 ? ' checked' : '';
          return '<label class="license-key-option">' +
            '<input type="radio" name="reset_key_id" value="' + escapeHtml(row.id) + '"' + checked + '>' +
            '<span><strong>' + escapeHtml(row.key) + '</strong><small>' + escapeHtml(row.device_status) + ' · ' + escapeHtml(device) + '</small></span>' +
            '</label>';
        }).join('');
      })
      .catch(function(err) {
        resetList.innerHTML = '<p class="empty-title">No Resettable Keys Found.</p>';
        showMessage(resetMessage, err.message || 'Could not load keys.', 'error');
      });
  }

  document.querySelectorAll('[data-open-license-modal]').forEach(function(button) {
    button.addEventListener('click', function() {
      var target = button.dataset.openLicenseModal;
      if (target === 'reset') {
        openModal(resetModal);
        loadResetKeys();
      } else if (target === 'redeem') {
        openModal(redeemModal);
        if (redeemInput) redeemInput.focus();
      }
    });
  });

  document.querySelectorAll('[data-close-license-modal]').forEach(function(button) {
    button.addEventListener('click', closeModals);
  });

  [resetModal, redeemModal].forEach(function(modal) {
    if (!modal) return;
    modal.addEventListener('click', function(event) {
      if (event.target === modal) closeModals();
    });
  });

  if (resetButton) {
    resetButton.addEventListener('click', function() {
      var keyId = selectedResetKeyId();
      if (!keyId) {
        showMessage(resetMessage, 'No Resettable Keys Found.', 'error');
        return;
      }
      setBusy(resetButton, true, 'Resetting...');
      fetch('/api/license/reset-hwid', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, Accept: 'application/json' },
        body: JSON.stringify({ key_id: keyId })
      })
        .then(function(res) { return res.json().then(function(body) { return { ok: res.ok, body: body }; }); })
        .then(function(result) {
          if (!result.ok) throw new Error(result.body.message || 'Could not reset HWID.');
          showMessage(resetMessage, result.body.message || 'HWID Reset Successful. You Can Bind This Key On A New Device.', 'success');
          setTimeout(function() { window.location.reload(); }, 900);
        })
        .catch(function(err) {
          showMessage(resetMessage, err.message || 'Could not reset HWID.', 'error');
        })
        .finally(function() { setBusy(resetButton, false); });
    });
  }

  if (redeemButton) {
    redeemButton.addEventListener('click', function() {
      var key = redeemInput ? redeemInput.value.trim() : '';
      if (!key) {
        showMessage(redeemMessage, 'Enter License Key.', 'error');
        return;
      }
      setBusy(redeemButton, true, 'Redeeming...');
      fetch('/api/license/redeem', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, Accept: 'application/json' },
        body: JSON.stringify({ key: key })
      })
        .then(function(res) { return res.json().then(function(body) { return { ok: res.ok, body: body }; }); })
        .then(function(result) {
          if (!result.ok) throw new Error(result.body.message || 'Could not redeem key.');
          showMessage(redeemMessage, result.body.message || 'Key Redeemed Successfully.', 'success');
          setTimeout(function() { window.location.reload(); }, 900);
        })
        .catch(function(err) {
          showMessage(redeemMessage, err.message || 'Could not redeem key.', 'error');
        })
        .finally(function() { setBusy(redeemButton, false); });
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
