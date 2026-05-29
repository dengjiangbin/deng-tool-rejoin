(function () {
  'use strict';

  function fmt(n) {
    n = Number(n) || 0;
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(n % 1_000 === 0 ? 0 : 1) + 'K';
    return String(n);
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
      if (rodWrap && data.rods) {
        var rods = [
          { label: 'Ghostfinn Rod', value: data.rods.ghostfinn, cls: 'rod-ghostfinn' },
          { label: 'Element Rod', value: data.rods.element, cls: 'rod-element' },
          { label: 'Diamond Rod', value: data.rods.diamond, cls: 'rod-diamond' },
        ];
        rodWrap.innerHTML = rods.map(function (rod) {
          return '<article class="mini-stat-card ' + rod.cls + '">' +
            '<span class="mini-stat-icon" aria-hidden="true">\u{1F3A3}</span>' +
            '<span class="mini-stat-label">' + rod.label + '</span>' +
            '<strong class="mini-stat-value">' + fmt(rod.value) + '</strong>' +
            '</article>';
        }).join('');
      }
    })
    .catch(function () { /* leave hidden */ });
}());
