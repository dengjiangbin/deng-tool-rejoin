'use strict';

const ID_MONTHS = [
  'Januari',
  'Februari',
  'Maret',
  'April',
  'Mei',
  'Juni',
  'Juli',
  'Agustus',
  'September',
  'Oktober',
  'November',
  'Desember',
];

function jakartaDateParts(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const offsetMs = 7 * 60 * 60 * 1000;
  const local = new Date(date.getTime() + offsetMs);
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'UTC',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  }).formatToParts(local);
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return {
    day: Number(byType.day),
    month: Number(byType.month),
    year: Number(byType.year),
    hour: Number(byType.hour),
    minute: byType.minute,
    second: byType.second,
    dayPeriod: String(byType.dayPeriod || '').toUpperCase(),
  };
}

function formatWibTimestamp(value) {
  if (!value) return 'None';
  const part = jakartaDateParts(value);
  if (!part) return 'None';
  return `${part.day} ${ID_MONTHS[part.month - 1]} ${part.year}, ${part.hour}:${part.minute}:${part.second} ${part.dayPeriod}`;
}

function formatWibDate(value = new Date()) {
  const part = jakartaDateParts(value);
  if (!part) return 'None';
  return `${part.day} ${ID_MONTHS[part.month - 1]} ${part.year}`;
}

function sanitizeFilenameUsername(value, fallbackId = '') {
  const cleaned = String(value || '')
    .replace(/[\/\\:*?"<>|]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (cleaned) return cleaned;
  const fallback = String(fallbackId || '').trim();
  return fallback ? `user-${fallback}` : 'user';
}

function licenseExportFilename(username, discordUserId, generatedAt = new Date()) {
  const safeUser = sanitizeFilenameUsername(username, discordUserId);
  return `${safeUser} - DENG Tool Rejoin License Keys - ${formatWibDate(generatedAt)}.txt`;
}

module.exports = {
  ID_MONTHS,
  formatWibDate,
  formatWibTimestamp,
  licenseExportFilename,
  sanitizeFilenameUsername,
};
