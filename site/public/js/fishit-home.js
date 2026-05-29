(function () {
  'use strict';

  function fmt(n) {
    var v = Number(n);
    if (!isFinite(v)) return '0';
    return Math.round(v).toLocaleString('en-US');
  }
  function rodImg(url, label) {
    if (!url) return '<span class="mini-stat-icon" aria-hidden="true">\u{1F3A3}</span>';
    var fb = '/public/img/fishit/fallback-rod.svg';
    return '<img class="mini-stat-img" loading="lazy" src="' + url + '" alt="' + label +
      '" onerror="this.onerror=null;this.src=\'' + fb + '\'">';
  }

  var section = document.querySelector('[data-fishit-global]');
  if (!section) return;

  fetch('/api/fishit/global', { headers: { Accept: 'application/json' } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) {
      if (!data || !data.available) return; // stays hidden — clean empty state
      section.hidden = false;
      section.querySelectorAll('[data-fishit-stat]').forEach(function (el) {
        var key = el.getAttribute('data-fishit-stat');
        el.textContent = fmt(data[key]);
      });
      var rodWrap = section.querySelector('[data-fishit-rods]');
      if (rodWrap) {
        var cards = (data.rod_cards && data.rod_cards.length)
          ? data.rod_cards
          : (data.rods ? [
            { label: 'Ghostfinn Rod', amount: data.rods.ghostfinn, imageUrl: null, cls: 'rod-ghostfinn' },
            { label: 'Element Rod', amount: data.rods.element, imageUrl: null, cls: 'rod-element' },
            { label: 'Diamond Rod', amount: data.rods.diamond, imageUrl: null, cls: 'rod-diamond' },
          ] : []);
        rodWrap.innerHTML = cards.map(function (rod) {
          var cls = rod.cls || ('rod-' + String(rod.key || rod.label || '').toLowerCase().replace(/\s+/g, '-'));
          return '<article class="mini-stat-card ' + cls + '">' +
            rodImg(rod.imageUrl, rod.label) +
            '<span class="mini-stat-label">' + rod.label + '</span>' +
            '<strong class="mini-stat-value">' + fmt(rod.amount != null ? rod.amount : rod.value) + '</strong>' +
            '</article>';
        }).join('');
      }
    })
    .catch(function () { /* leave hidden */ });
}());
