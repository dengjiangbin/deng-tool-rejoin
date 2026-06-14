'use strict';

function safeOptionalWeight(row) {
  if (!row || typeof row !== 'object') return null;
  if (row.weightKg != null && Number.isFinite(Number(row.weightKg))) return Number(row.weightKg);
  if (row.weight != null && Number.isFinite(Number(row.weight))) return Number(row.weight);
  if (row.metadataWeightKg != null && Number.isFinite(Number(row.metadataWeightKg))) {
    return Number(row.metadataWeightKg);
  }
  if (row.maxWeight != null && Number.isFinite(Number(row.maxWeight))) return Number(row.maxWeight);
  if (row.totalWeight != null && Number.isFinite(Number(row.totalWeight))) return Number(row.totalWeight);
  if (row.Weight != null && Number.isFinite(Number(row.Weight))) return Number(row.Weight);
  return null;
}

function isUsableUploadRow(row) {
  return Boolean(row && typeof row === 'object');
}

module.exports = {
  safeOptionalWeight,
  isUsableUploadRow,
};
