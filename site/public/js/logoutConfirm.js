'use strict';
/* global document, window */

(function initLogoutConfirmModal() {
  var overlay = null;
  var modal = null;
  var submitBtn = null;
  var cancelBtn = null;
  var pendingAction = null;
  var processing = false;

  var TRIGGER_SELECTOR = [
    '[data-logout-confirm]',
    '.logout-link',
    '.logout-button',
    '.inventory-action-btn--logout',
    'form[action="/auth/logout"] button',
    'form[action$="/logout"] button',
    'a[href="/logout"]',
    'a[href="/auth/logout"]',
  ].join(', ');

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, function(ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function ensureModal() {
    if (overlay) return;
    overlay = document.getElementById('logoutConfirmOverlay');
    if (overlay) {
      modal = overlay.querySelector('.logout-confirm-modal');
      submitBtn = overlay.querySelector('.logout-confirm-submit');
      cancelBtn = overlay.querySelector('.logout-confirm-cancel');
      bindModalEvents();
      return;
    }

    overlay = document.createElement('div');
    overlay.id = 'logoutConfirmOverlay';
    overlay.className = 'logout-confirm-overlay';
    overlay.hidden = true;
    overlay.innerHTML =
      '<div class="logout-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="logoutConfirmTitle">' +
        '<div class="logout-confirm-icon" aria-hidden="true">!</div>' +
        '<h2 id="logoutConfirmTitle">Logout</h2>' +
        '<p>Are you sure you want to logout?</p>' +
        '<div class="logout-confirm-actions">' +
          '<button type="button" class="logout-confirm-cancel">Cancel</button>' +
          '<button type="button" class="logout-confirm-submit">Yes, logout</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    modal = overlay.querySelector('.logout-confirm-modal');
    submitBtn = overlay.querySelector('.logout-confirm-submit');
    cancelBtn = overlay.querySelector('.logout-confirm-cancel');
    bindModalEvents();
  }

  function resetSubmitButton() {
    if (!submitBtn) return;
    submitBtn.disabled = false;
    submitBtn.textContent = 'Yes, logout';
  }

  function closeLogoutConfirmModal() {
    if (!overlay) return;
    overlay.hidden = true;
    document.body.classList.remove('logout-confirm-open');
    pendingAction = null;
    processing = false;
    resetSubmitButton();
  }

  function performPendingLogout() {
    if (!pendingAction || processing) return;
    processing = true;
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Logging out...';
    }
    if (pendingAction.type === 'form' && pendingAction.form) {
      pendingAction.form.submit();
      return;
    }
    if (pendingAction.type === 'href' && pendingAction.href) {
      window.location.href = pendingAction.href;
      return;
    }
    if (typeof pendingAction.callback === 'function') {
      pendingAction.callback();
    }
  }

  function openLogoutConfirmModal(action) {
    if (typeof action === 'string') {
      action = { type: 'href', href: action };
    } else if (typeof action === 'function') {
      action = { callback: action };
    }
    ensureModal();
    pendingAction = action || null;
    processing = false;
    resetSubmitButton();
    overlay.hidden = false;
    document.body.classList.add('logout-confirm-open');
    if (cancelBtn) cancelBtn.focus();
  }

  function resolveLogoutActionFromElement(el) {
    if (!el) return null;
    var form = el.closest('form');
    if (form) {
      var action = form.getAttribute('action') || '';
      if (action.indexOf('logout') !== -1) {
        return { type: 'form', form: form };
      }
    }
    if (el.tagName === 'A') {
      var href = el.getAttribute('href') || '';
      if (href.indexOf('logout') !== -1) {
        return { type: 'href', href: href };
      }
    }
    return null;
  }

  function bindTrigger(el) {
    if (!el || el.dataset.logoutConfirmBound === '1') return;
    var action = resolveLogoutActionFromElement(el);
    if (!action && !el.hasAttribute('data-logout-confirm')) return;
    el.dataset.logoutConfirmBound = '1';
    if (el.type === 'submit') el.type = 'button';
    el.addEventListener('click', function(event) {
      event.preventDefault();
      event.stopPropagation();
      var resolved = resolveLogoutActionFromElement(el) || action;
      if (typeof resolved === 'function') {
        openLogoutConfirmModal({ callback: resolved });
        return;
      }
      if (resolved) {
        openLogoutConfirmModal(resolved);
      }
    });
  }

  function scanLogoutTriggers(root) {
    var scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll(TRIGGER_SELECTOR).forEach(bindTrigger);
  }

  function bindModalEvents() {
    if (!overlay || overlay.dataset.logoutConfirmReady === '1') return;
    overlay.dataset.logoutConfirmReady = '1';

    overlay.addEventListener('click', function(event) {
      if (event.target === overlay) closeLogoutConfirmModal();
    });

    if (cancelBtn) {
      cancelBtn.addEventListener('click', function(event) {
        event.preventDefault();
        closeLogoutConfirmModal();
      });
    }

    if (submitBtn) {
      submitBtn.addEventListener('click', function(event) {
        event.preventDefault();
        performPendingLogout();
      });
    }

    document.addEventListener('keydown', function(event) {
      if (overlay.hidden) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        closeLogoutConfirmModal();
      }
    });

    document.addEventListener('submit', function(event) {
      var form = event.target;
      if (!form || form.tagName !== 'FORM') return;
      var action = form.getAttribute('action') || '';
      if (action.indexOf('logout') === -1) return;
      event.preventDefault();
      openLogoutConfirmModal({ type: 'form', form: form });
    }, true);
  }

  window.openLogoutConfirmModal = openLogoutConfirmModal;
  window.closeLogoutConfirmModal = closeLogoutConfirmModal;

  function init() {
    ensureModal();
    scanLogoutTriggers(document);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}());
