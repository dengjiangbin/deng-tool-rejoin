'use strict';
/* global document, localStorage, navigator */

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
