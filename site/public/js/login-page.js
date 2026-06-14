(function initLoginPageOnlinePill() {
  'use strict';

  var pill = document.querySelector('[data-login-online-pill]');
  var text = document.querySelector('[data-login-online-text]');
  if (!pill || !text) return;

  function fmt(value) {
    var n = Number(value);
    if (!Number.isFinite(n) || n < 0) return null;
    return Math.round(n);
  }

  function updatePill(count) {
    pill.hidden = false;
    var formatted = fmt(count);
    if (formatted == null) {
      text.textContent = 'Live network';
      pill.classList.remove('login-page__online-pill--offline');
      return;
    }
    if (formatted === 0) {
      text.textContent = 'No One Online';
      pill.classList.add('login-page__online-pill--offline');
      return;
    }
    pill.classList.remove('login-page__online-pill--offline');
    text.textContent = formatted.toLocaleString('en-US') + ' Online Now';
  }

  fetch('/api/fishit-tracker/public-network', {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  })
    .then(function(res) { return res.ok ? res.json() : null; })
    .then(function(data) {
      if (data && data.available) updatePill(data.onlineUsernames);
      else updatePill(null);
    })
    .catch(function() { updatePill(null); });
}());
