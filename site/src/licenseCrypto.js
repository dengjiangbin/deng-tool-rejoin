'use strict';

const crypto = require('crypto');

const EXPORT_SECRET_ENV = 'LICENSE_KEY_EXPORT_SECRET';

function secretBytes() {
  const raw = String(process.env[EXPORT_SECRET_ENV] || '').trim();
  if (!raw) return null;
  return crypto.createHash('sha256').update(raw, 'utf8').digest();
}

function base64UrlEncode(buf) {
  return Buffer.from(buf).toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
}

function base64UrlDecode(token) {
  const text = String(token || '').trim().replace(/-/g, '+').replace(/_/g, '/');
  const padded = text + '='.repeat((4 - (text.length % 4)) % 4);
  return Buffer.from(padded, 'base64');
}

function isExportSecretConfigured() {
  return Boolean(secretBytes());
}

function encryptLicenseKeyPlaintext(plainKey) {
  const key = secretBytes();
  const raw = String(plainKey || '').trim();
  if (!key || !raw) return null;
  try {
    const signKey = key.subarray(0, 16);
    const encKey = key.subarray(16, 32);
    const iv = crypto.randomBytes(16);
    const ts = Buffer.alloc(8);
    ts.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000)), 0);
    const cipher = crypto.createCipheriv('aes-128-cbc', encKey, iv);
    const ciphertext = Buffer.concat([cipher.update(raw, 'utf8'), cipher.final()]);
    const body = Buffer.concat([Buffer.from([0x80]), ts, iv, ciphertext]);
    const mac = crypto.createHmac('sha256', signKey).update(body).digest();
    return base64UrlEncode(Buffer.concat([body, mac]));
  } catch {
    return null;
  }
}

function decryptLicenseKeyCiphertext(token) {
  const key = secretBytes();
  if (!key || !token) return null;
  try {
    const data = base64UrlDecode(token);
    if (data.length < 1 + 8 + 16 + 16 + 32 || data[0] !== 0x80) return null;
    const signKey = key.subarray(0, 16);
    const encKey = key.subarray(16, 32);
    const body = data.subarray(0, data.length - 32);
    const mac = data.subarray(data.length - 32);
    const expected = crypto.createHmac('sha256', signKey).update(body).digest();
    if (mac.length !== expected.length || !crypto.timingSafeEqual(mac, expected)) return null;
    const iv = data.subarray(9, 25);
    const ciphertext = data.subarray(25, data.length - 32);
    const decipher = crypto.createDecipheriv('aes-128-cbc', encKey, iv);
    return Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString('utf8');
  } catch {
    return null;
  }
}

module.exports = {
  decryptLicenseKeyCiphertext,
  encryptLicenseKeyPlaintext,
  isExportSecretConfigured,
};
