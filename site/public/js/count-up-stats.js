'use strict';

function createCountUpStats() {
  var DEFAULT_DURATION = 750;
  var observers = new WeakMap();

  function prefersReducedMotion() {
    return !!(typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
  }

  function parseRawNumber(value) {
    if (value == null || value === '') return null;
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    var text = String(value).trim();
    if (!text || text === '—' || text === '-') return null;
    var compact = text.match(/^(-?\d+(?:\.\d+)?)([KMB])$/i);
    if (compact) {
      var base = parseFloat(compact[1]);
      if (!Number.isFinite(base)) return null;
      var mult = compact[2].toUpperCase() === 'K' ? 1e3 : compact[2].toUpperCase() === 'M' ? 1e6 : 1e9;
      return base * mult;
    }
    var cleaned = text.replace(/,/g, '').replace(/[^\d.-]/g, '');
    if (!cleaned) return null;
    var n = parseFloat(cleaned);
    return Number.isFinite(n) ? n : null;
  }

  function formatInteger(value) {
    var n = parseRawNumber(value);
    if (n == null) return '0';
    return Math.round(n).toLocaleString('en-US');
  }

  function formatDecimal(value, decimals) {
    var n = parseRawNumber(value);
    var d = Number.isFinite(decimals) ? Math.max(0, Math.min(6, decimals)) : 0;
    if (n == null) n = 0;
    return n.toLocaleString('en-US', {
      minimumFractionDigits: d,
      maximumFractionDigits: d,
    });
  }

  function formatPercent(value, decimals) {
    var d = Number.isFinite(decimals) ? Math.max(0, Math.min(3, decimals)) : 1;
    return formatDecimal(value, d) + '%';
  }

  function formatRatio(numerator, denominator, opts) {
    opts = opts || {};
    var num = parseRawNumber(numerator);
    var den = parseRawNumber(denominator);
    if (num == null) num = 0;
    if (den == null) den = 0;
    var left = opts.compact ? formatCompact(num) : formatInteger(num);
    var right = opts.compact ? formatCompact(den) : formatInteger(den);
    return left + ' / ' + right;
  }

  function formatCompact(value) {
    var n = parseRawNumber(value);
    if (n == null) return '0';
    var abs = Math.abs(n);
    if (abs >= 1e9) return (n / 1e9).toFixed(abs >= 1e10 ? 0 : 1).replace(/\.0$/, '') + 'B';
    if (abs >= 1e6) return (n / 1e6).toFixed(abs >= 1e7 ? 0 : 1).replace(/\.0$/, '') + 'M';
    if (abs >= 1e3) return (n / 1e3).toFixed(abs >= 1e4 ? 0 : 1).replace(/\.0$/, '') + 'K';
    return formatInteger(n);
  }

  function placeholder(format, decimals) {
    switch (format) {
      case 'percent':
        return formatPercent(0, decimals);
      case 'decimal':
        return formatDecimal(0, decimals);
      case 'ratio':
        return '0 / 0';
      case 'compact':
        return '0';
      default:
        return '0';
    }
  }

  function readDecimals(el) {
    var d = parseInt(el.getAttribute('data-count-decimals') || '', 10);
    return Number.isFinite(d) ? d : (el.getAttribute('data-count-format') === 'percent' ? 1 : 0);
  }

  function readDuration(el, override) {
    if (Number.isFinite(override)) return Math.max(0, override);
    var d = parseInt(el.getAttribute('data-count-duration') || '', 10);
    return Number.isFinite(d) ? Math.max(0, d) : DEFAULT_DURATION;
  }

  function readFormat(el) {
    return el.getAttribute('data-count-format') || 'integer';
  }

  function readPrefix(el) {
    return el.getAttribute('data-count-prefix') || '';
  }

  function readSuffix(el) {
    return el.getAttribute('data-count-suffix') || '';
  }

  function composeText(el, value, total, format, decimals) {
    var prefix = readPrefix(el);
    var suffix = readSuffix(el);
    var body;
    if (format === 'ratio') {
      body = formatRatio(value, total, { compact: el.hasAttribute('data-count-compact') });
    } else if (format === 'percent') {
      body = formatPercent(value, decimals);
    } else if (format === 'decimal') {
      body = formatDecimal(value, decimals);
    } else if (format === 'compact') {
      body = formatCompact(value);
    } else {
      body = formatInteger(value);
    }
    return prefix + body + suffix;
  }

  function getState(el) {
    if (!el._dengCountUp) {
      el._dengCountUp = {
        rafId: 0,
        current: 0,
        currentTotal: 0,
        running: false,
      };
    }
    return el._dengCountUp;
  }

  function cancelAnimation(el) {
    var state = getState(el);
    if (state.rafId) {
      cancelAnimationFrame(state.rafId);
      state.rafId = 0;
    }
    state.running = false;
  }

  function parseDisplayedValue(el, format) {
    var text = el.textContent || '';
    if (format === 'ratio') {
      var parts = text.split('/');
      return {
        value: parseRawNumber(parts[0]) || 0,
        total: parseRawNumber(parts[1]) || 0,
      };
    }
    if (format === 'percent') {
      return { value: parseRawNumber(text.replace(/%/g, '')) || 0, total: 0 };
    }
    var prefix = readPrefix(el);
    var suffix = readSuffix(el);
    var core = text;
    if (prefix && core.indexOf(prefix) === 0) core = core.slice(prefix.length);
    if (suffix && core.slice(-suffix.length) === suffix) core = core.slice(0, -suffix.length);
    return { value: parseRawNumber(core) || 0, total: 0 };
  }

  function applyFinal(el, value, total, format, decimals) {
    var state = getState(el);
    state.current = value;
    state.currentTotal = total;
    el.textContent = composeText(el, value, total, format, decimals);
  }

  function animateTo(el, targetValue, targetTotal, opts) {
    opts = opts || {};
    var format = opts.format || readFormat(el);
    var decimals = Number.isFinite(opts.decimals) ? opts.decimals : readDecimals(el);
    var duration = readDuration(el, opts.duration);
    var state = getState(el);
    cancelAnimation(el);

    var target = parseRawNumber(targetValue);
    if (target == null) target = 0;
    var totalTarget = parseRawNumber(targetTotal);
    if (format === 'ratio' && totalTarget == null) totalTarget = 0;

    if (prefersReducedMotion() || duration === 0) {
      applyFinal(el, target, totalTarget == null ? 0 : totalTarget, format, decimals);
      return;
    }

    var parsed = parseDisplayedValue(el, format);
    var fromValue = Number.isFinite(opts.from) ? opts.from : parsed.value;
    var fromTotal = Number.isFinite(opts.fromTotal) ? opts.fromTotal : parsed.total;
    var start = performance.now();
    state.running = true;

    function frame(now) {
      var t = Math.min(1, (now - start) / duration);
      var eased = easeOutCubic(t);
      var nextValue = fromValue + (target - fromValue) * eased;
      var nextTotal = fromTotal + ((totalTarget || 0) - fromTotal) * eased;
      if (format === 'ratio') {
        applyFinal(el, Math.round(nextValue), Math.round(nextTotal), format, decimals);
      } else if (format === 'percent' || format === 'decimal') {
        applyFinal(el, nextValue, 0, format, decimals);
      } else if (format === 'compact') {
        applyFinal(el, nextValue, 0, format, decimals);
      } else {
        applyFinal(el, Math.round(nextValue), 0, format, decimals);
      }
      if (t < 1) {
        state.rafId = requestAnimationFrame(frame);
      } else {
        state.rafId = 0;
        state.running = false;
        applyFinal(el, target, totalTarget || 0, format, decimals);
      }
    }

    state.rafId = requestAnimationFrame(frame);
  }

  function shouldAnimateNow(el) {
    if (!('IntersectionObserver' in window)) return true;
    if (el.hasAttribute('data-count-immediate')) return true;
    var rect = el.getBoundingClientRect();
    return rect.top < window.innerHeight && rect.bottom > 0;
  }

  function observeElement(el) {
    if (!('IntersectionObserver' in window) || el.hasAttribute('data-count-immediate')) {
      runElement(el);
      return;
    }
    if (observers.has(el)) return;
    var io = new IntersectionObserver(function(entries) {
      entries.forEach(function(entry) {
        if (!entry.isIntersecting) return;
        io.unobserve(el);
        observers.delete(el);
        runElement(el);
      });
    }, { root: null, threshold: 0.1 });
    io.observe(el);
    observers.set(el, io);
  }

  function runElement(el) {
    if (!el || !el.classList.contains('js-count-up')) return;
    var format = readFormat(el);
    var decimals = readDecimals(el);
    var to = el.getAttribute('data-count-to');
    var total = el.getAttribute('data-count-total');
    if (to == null || to === '') {
      if (el.textContent.trim() === '' || el.textContent.trim() === '—') {
        el.textContent = placeholder(format, decimals);
      }
      return;
    }
    animateTo(el, to, total, { format: format, decimals: decimals });
  }

  function refresh(container) {
    var root = container && container.querySelectorAll ? container : document;
    var nodes = root.querySelectorAll ? root.querySelectorAll('.js-count-up') : [];
    Array.prototype.forEach.call(nodes, function(el) {
      if (shouldAnimateNow(el)) runElement(el);
      else observeElement(el);
    });
  }

  function set(el, opts) {
    if (!el) return;
    opts = opts || {};
    if (opts.to != null) el.setAttribute('data-count-to', String(opts.to));
    if (opts.total != null) el.setAttribute('data-count-total', String(opts.total));
    if (opts.format) el.setAttribute('data-count-format', opts.format);
    if (opts.decimals != null) el.setAttribute('data-count-decimals', String(opts.decimals));
    if (opts.duration != null) el.setAttribute('data-count-duration', String(opts.duration));
    if (opts.prefix != null) el.setAttribute('data-count-prefix', opts.prefix);
    if (opts.suffix != null) el.setAttribute('data-count-suffix', opts.suffix);
    el.classList.add('js-count-up');
    animateTo(el, opts.to != null ? opts.to : el.getAttribute('data-count-to'), opts.total != null ? opts.total : el.getAttribute('data-count-total'), opts);
  }

  function init() {
    refresh(document);
  }

  return {
    init: init,
    refresh: refresh,
    set: set,
    animateTo: animateTo,
    formatInteger: formatInteger,
    formatDecimal: formatDecimal,
    formatPercent: formatPercent,
    formatRatio: formatRatio,
    formatCompact: formatCompact,
    placeholder: placeholder,
    parseRawNumber: parseRawNumber,
    prefersReducedMotion: prefersReducedMotion,
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = createCountUpStats();
} else {
  (function boot() {
    var api = createCountUpStats();
    window.DengCountUpStats = api;
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', api.init);
    } else {
      api.init();
    }
  }());
}
