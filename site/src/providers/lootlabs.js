'use strict';
/**
 * LootLabs Redirect API / Anti-Bypass provider helper.
 *
 * Reference (publisher docs):
 *   POST https://creators.lootlabs.gg/api/public/url_encryptor
 *        Authorization: Bearer <LOOTLABS_API_TOKEN>
 *        Content-Type: application/json
 *        body: { "destination_url": "<DENG callback URL>" }
 *   →    200 { "type": "success", "message": "<encrypted_data>" }
 *
 * The encrypted value MUST be appended to the LootLabs shortlink as
 * `&data=<encrypted_data>`. The shortlink ID (e.g. `?TqZQAW38`) is a
 * valueless query key so we MUST NOT use URLSearchParams (it would rewrite
 * `?TqZQAW38` into `?TqZQAW38=`, breaking the link).
 *
 * SECURITY:
 *  - LOOTLABS_API_TOKEN is read from env only and sent in the Authorization
 *    header. It is never logged, never echoed to clients, never embedded in
 *    redirect URLs, never written to the DB.
 *  - The signed state attached to the callback (?s=…) is HMAC-signed and
 *    one-time consumed by the challenge status machine.
 *  - We fail closed on timeout / network / non-2xx / missing-message /
 *    type=error responses.
 */
const axios = require('axios');

const DEFAULT_ENCRYPT_URL = 'https://creators.lootlabs.gg/api/public/url_encryptor';
const DEFAULT_BASE_LINK = ''; // No hardcoded fallback — env must provide it.

const ENCRYPT_TIMEOUT_MS = parseInt(process.env.LOOTLABS_ENCRYPT_TIMEOUT_MS || '8000', 10);

const REASON_CODES = Object.freeze({
  NOT_CONFIGURED: 'lootlabs_not_configured',
  MISSING_DESTINATION: 'missing_destination',
  API_TIMEOUT: 'api_timeout',
  API_ERROR: 'api_error',
  API_INVALID_TOKEN: 'api_invalid_token',
  API_INVALID_RESPONSE: 'api_invalid_response',
  API_TYPE_ERROR: 'api_type_error',
  SUCCESS: 'success',
});

function cleanEnvValue(name, fallback = '') {
  const raw = Object.prototype.hasOwnProperty.call(process.env, name) ? process.env[name] : fallback;
  const cleaned = String(raw || '').trim().replace(/^['"]|['"]$/g, '').trim();
  if (cleaned) return cleaned;
  return String(fallback || '').trim().replace(/^['"]|['"]$/g, '').trim();
}

function envEnabled(name, fallback = 'false') {
  return ['1', 'true', 'yes', 'on'].includes(cleanEnvValue(name, fallback).toLowerCase());
}

/**
 * Strip any pre-existing `&data=…` (or `?data=…`) suffix so we always start
 * from the canonical shortlink. The base link in the LootLabs dashboard is
 * just `https://lootdest.org/s?TqZQAW38`; older deployments may have left a
 * stale `&data=` from a previous encrypted redirect in the env value.
 */
function stripDataParam(baseLink) {
  if (!baseLink) return '';
  let s = String(baseLink);
  // Remove trailing `&data=…` or `?data=…` (whichever appears last).
  s = s.replace(/[?&]data=[^&]*$/i, '');
  return s;
}

function getLootLabsBaseLink() {
  const raw = cleanEnvValue(
    'LOOTLABS_BASE_LINK',
    cleanEnvValue('LOOTLABS_MONETIZED_URL', DEFAULT_BASE_LINK),
  );
  return stripDataParam(raw);
}

function getLootLabsApiToken() {
  return cleanEnvValue('LOOTLABS_API_TOKEN', cleanEnvValue('LOOTLABS_API_KEY', ''));
}

function getLootLabsEncryptUrl() {
  return cleanEnvValue('LOOTLABS_ENCRYPT_URL', DEFAULT_ENCRYPT_URL);
}

function getPublicUrl() {
  return cleanEnvValue('TOOL_SITE_PUBLIC_URL', 'https://tool.deng.my.id').replace(/\/+$/, '');
}

/** True when LootLabs Redirect API can be used. */
function isLootLabsConfigured() {
  return getLootLabsUnavailableReason() === null;
}

/** Human-friendly explanation when LootLabs cannot be used (or null). */
function getLootLabsUnavailableReason() {
  if (!envEnabled('LOOTLABS_ENABLED', 'false')) return 'LOOTLABS_ENABLED is not true';
  if (!getLootLabsBaseLink()) return 'LOOTLABS_BASE_LINK is not set';
  if (!getLootLabsApiToken()) return 'LOOTLABS_API_TOKEN is not set';
  if (!getLootLabsEncryptUrl()) return 'LOOTLABS_ENCRYPT_URL is not set';
  return null;
}

/**
 * Build the DENG callback URL that LootLabs will redirect to after the ad.
 * The signed state is the only client-visible identifier — it is HMAC-signed
 * and only resolves to a key on the server when paired with this user's
 * session, the matching pending challenge, and a valid challenge state.
 */
function buildLootLabsCallbackUrl({ signedState, publicUrl }) {
  if (!signedState || typeof signedState !== 'string') return '';
  const base = (publicUrl || getPublicUrl()).replace(/\/+$/, '');
  return `${base}/unlock/lootlabs/complete?s=${encodeURIComponent(signedState)}`;
}

/**
 * Append the encrypted data to the LootLabs shortlink WITHOUT touching the
 * shortlink id. We cannot use URLSearchParams because it would normalise the
 * valueless query key `?TqZQAW38` into `?TqZQAW38=`.
 *
 * LootLabs returns the encrypted blob already URL-ready (per their docs the
 * value is meant to be appended as `&data=<message>` directly). It contains
 * characters like `%2B`/`%2F`/`%3D` pre-encoded; double-encoding via
 * `encodeURIComponent` would mangle it into `%252B` etc. We therefore append
 * the message raw, only escaping the truly URL-unsafe bytes that LootLabs
 * itself would never emit (whitespace, control chars) using `encodeURI`.
 */
function buildLootLabsStartUrl({ encryptedData, baseLink }) {
  // Only fall back to env when the caller did not pass a baseLink at all.
  // An explicit empty string from a test/caller MUST be respected (and yield '').
  const raw = typeof baseLink === 'undefined' ? getLootLabsBaseLink() : baseLink;
  const base = stripDataParam(raw);
  if (!base) return '';
  if (!encryptedData || typeof encryptedData !== 'string') return '';
  const sep = base.includes('?') ? '&' : '?';
  // Per LootLabs docs, `&data=<message>` is appended to an already-formed
  // shortlink DIRECTLY. The encrypt API returns a value that is already
  // URL-ready (base64-style + selective pre-percent-encoding like `%2B`).
  // We MUST NOT call encodeURIComponent / encodeURI here — both would
  // double-encode existing `%`-escapes (e.g. `%2B` → `%252B`). We only
  // escape characters that would actively break the URL (whitespace, `#`,
  // `&`, `?`); none of those should ever appear in a base64-style payload.
  const safe = String(encryptedData).replace(/[\s#&?]/g, (ch) => (
    `%${ch.charCodeAt(0).toString(16).toUpperCase().padStart(2, '0')}`
  ));
  return `${base}${sep}data=${safe}`;
}

function safeSignedStatePrefix(signedState) {
  if (typeof signedState !== 'string' || signedState.length < 8) return '';
  return signedState.slice(0, 8);
}

function safeEncryptedPrefix(encrypted) {
  if (typeof encrypted !== 'string' || encrypted.length < 8) return '';
  return encrypted.slice(0, 8);
}

function classifyEncryptResponse(body) {
  if (body === null || typeof body === 'undefined') {
    return { ok: false, reason: REASON_CODES.API_INVALID_RESPONSE };
  }
  if (typeof body !== 'object') {
    return { ok: false, reason: REASON_CODES.API_INVALID_RESPONSE };
  }
  // Explicit error type
  if (body.type && String(body.type).toLowerCase() === 'error') {
    const msg = String(body.message || '').toLowerCase();
    if (msg.includes('invalid') && (msg.includes('token') || msg.includes('auth') || msg.includes('bearer'))) {
      return { ok: false, reason: REASON_CODES.API_INVALID_TOKEN };
    }
    return { ok: false, reason: REASON_CODES.API_TYPE_ERROR };
  }
  if (typeof body.message !== 'string' || body.message.length === 0) {
    return { ok: false, reason: REASON_CODES.API_INVALID_RESPONSE };
  }
  return { ok: true, reason: REASON_CODES.SUCCESS, encrypted: body.message };
}

/**
 * Encrypt the DENG callback URL via LootLabs Redirect API.
 * Returns `{ ok, reason, encrypted?, statusCode? }`. Fails closed.
 *
 * @param {{ destinationUrl: string, requestId?: string }} args
 */
async function encryptLootLabsDestination({ destinationUrl, requestId }) {
  const rid = String(requestId || '').slice(0, 16);

  if (!isLootLabsConfigured()) {
    return { ok: false, reason: REASON_CODES.NOT_CONFIGURED };
  }
  if (!destinationUrl || typeof destinationUrl !== 'string') {
    return { ok: false, reason: REASON_CODES.MISSING_DESTINATION };
  }

  const url = getLootLabsEncryptUrl();
  const token = getLootLabsApiToken();

  try {
    const response = await axios.post(
      url,
      { destination_url: destinationUrl },
      {
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'User-Agent': 'deng-tool-site/lootlabs-redirect-api',
        },
        timeout: ENCRYPT_TIMEOUT_MS,
        validateStatus: () => true,
      },
    );

    const status = response.status;
    const body = response.data;

    if (status === 401 || status === 403) {
      if (process.env.NODE_ENV !== 'test') {
        console.warn('[lootlabs/encrypt] result=invalid_token rid=%s status=%d', rid, status);
      }
      return { ok: false, reason: REASON_CODES.API_INVALID_TOKEN, statusCode: status };
    }
    if (status < 200 || status >= 300) {
      if (process.env.NODE_ENV !== 'test') {
        console.warn('[lootlabs/encrypt] result=api_error rid=%s status=%d', rid, status);
      }
      return { ok: false, reason: REASON_CODES.API_ERROR, statusCode: status };
    }

    const classified = classifyEncryptResponse(body);
    if (process.env.NODE_ENV !== 'test') {
      console.log(
        '[lootlabs/encrypt] result=%s rid=%s status=%d enc_prefix=%s',
        classified.reason, rid, status, safeEncryptedPrefix(classified.encrypted),
      );
    }
    return { ...classified, statusCode: status };
  } catch (err) {
    const isTimeout = err && (
      err.code === 'ECONNABORTED' ||
      err.code === 'ETIMEDOUT' ||
      /timeout/i.test(err.message || '')
    );
    const reason = isTimeout ? REASON_CODES.API_TIMEOUT : REASON_CODES.API_ERROR;
    if (process.env.NODE_ENV !== 'test') {
      console.warn(
        '[lootlabs/encrypt] result=%s rid=%s error=%s',
        reason, rid,
        (err && err.code) || (err && err.message ? err.message.slice(0, 64) : 'unknown'),
      );
    }
    return { ok: false, reason };
  }
}

module.exports = {
  REASON_CODES,
  isLootLabsConfigured,
  getLootLabsUnavailableReason,
  getLootLabsBaseLink,
  getLootLabsEncryptUrl,
  getPublicUrl,
  stripDataParam,
  classifyEncryptResponse,
  buildLootLabsCallbackUrl,
  buildLootLabsStartUrl,
  safeSignedStatePrefix,
  safeEncryptedPrefix,
  encryptLootLabsDestination,
};
