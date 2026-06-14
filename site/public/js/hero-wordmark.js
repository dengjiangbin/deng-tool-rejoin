(function () {
  'use strict';

  function bindOne(root) {
    if (!root) return;
    var words = Array.prototype.slice.call(root.querySelectorAll('[data-hero-word]'));
    if (!words.length) return;

    function activate(word) {
      words.forEach(function (el) {
        el.classList.toggle('is-active', el === word);
      });
    }

    words.forEach(function (word) {
      word.addEventListener('mouseenter', function () { activate(word); });
      word.addEventListener('focus', function () { activate(word); });
    });

    root.addEventListener('mouseleave', function () {
      activate(words[0]);
    });

    root.addEventListener('focusout', function (event) {
      if (!root.contains(event.relatedTarget)) activate(words[0]);
    });
  }

  function bindHeroWordmark() {
    // Bind every wordmark on the page (e.g. the login page renders both a
    // mobile and a desktop hero wordmark) so each matches homepage behaviour.
    var roots = Array.prototype.slice.call(document.querySelectorAll('[data-hero-wordmark]'));
    roots.forEach(bindOne);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindHeroWordmark);
  } else {
    bindHeroWordmark();
  }
}());
