(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function imageUrl(item) {
    return item && (item.imageUrl || item.image_url || item.thumbnailUrl || item.thumbnail || item.image || null);
  }
  function rodImg(url, label) {
    if (!url) return '<span class="mini-stat-icon" aria-hidden="true">\u{1F3A3}</span>';
    var fb = '/public/img/fishit/fallback-rod.svg';
    return '<img class="mini-stat-img" loading="lazy" src="' + esc(url) + '" alt="' + esc(label) +
      '" onerror="this.onerror=null;this.src=\'' + fb + '\'">';
  }

  var section = document.querySelector('[data-fishit-global]');
  if (!section) return;

  fetch('/api/fishit/global', { headers: { Accept: 'application/json' } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) {
      if (!data || !data.available) return;
      section.hidden = false;
      section.querySelectorAll('[data-fishit-stat]').forEach(function (el) {
        var key = el.getAttribute('data-fishit-stat');
        if (window.DengCountUpStats) {
      window.DengCountUpStats.set(el, { to: data[key], format: 'integer' });
        } else {
          el.textContent = Math.round(Number(data[key]) || 0).toLocaleString('en-US');
        }
      });
      var rodWrap = section.querySelector('[data-fishit-rods]');
      if (rodWrap) {
        var apiCards = (data.rod_cards && data.rod_cards.length) ? data.rod_cards : data.rodCards;
        var cards = (apiCards && apiCards.length)
          ? apiCards
          : (data.rods ? [
            { label: 'Ghostfinn Rod', amount: data.rods.ghostfinn, imageUrl: null, cls: 'rod-ghostfinn' },
            { label: 'Element Rod', amount: data.rods.element, imageUrl: null, cls: 'rod-element' },
            { label: 'Diamond Rod', amount: data.rods.diamond, imageUrl: null, cls: 'rod-diamond' },
          ] : []);
        rodWrap.innerHTML = cards.map(function (rod) {
          var cls = rod.cls || ('rod-' + String(rod.key || rod.label || '').toLowerCase().replace(/[^a-z0-9]+/g, '-'));
          var amount = rod.amount != null ? rod.amount : rod.value;
          return '<article class="mini-stat-card ' + esc(cls) + '">' +
            rodImg(imageUrl(rod), rod.label) +
            '<span class="mini-stat-label">' + esc(rod.label) + '</span>' +
            '<strong class="mini-stat-value js-count-up" data-count-to="' + esc(String(amount == null ? 0 : amount)) +
            '" data-count-format="integer" data-count-duration="1200">0</strong>' +
            '</article>';
        }).join('');
        if (window.DengCountUpStats) window.DengCountUpStats.refresh(rodWrap);
      }
    })
    .catch(function () { /* leave hidden */ });
}());
