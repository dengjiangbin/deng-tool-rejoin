'use strict';

function formatQuantity(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return '0';
  return Math.max(0, Math.floor(n)).toLocaleString('en-US');
}

function formatAmountLabel(value) {
  return `x${formatQuantity(value)}`;
}

module.exports = {
  formatQuantity,
  formatAmountLabel,
};
