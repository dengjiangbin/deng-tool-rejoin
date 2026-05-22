'use strict';
/**
 * Linkvertise Target-Link Anti-Bypass provider helper.
 *
 * Reference (publisher docs):
 *   POST https://publisher.linkvertise.com/api/v1/anti_bypassing
 *        ?token=<LINKVERTISE_ANTI_BYPASS_TOKEN>&hash=<hash>
 *
 * Linkvertise appends `hash=<64-char>` to the configured Target-Link callback
 * URL after a visitor completes the ad. The hash lives ~10 seconds and is
 * deleted on first successful verify (one-time use).
 *
 * Anti-Bypass works for TARGET LINKS only. We always treat the configured
 * link-hub.net URL as a target-link, and we always pass the secret token in
 * the request body — never in the redirect URL, frontend, logs, or git.
 */
const axios = require('axios');

const DEFAULT_VERIFY_URL = 'https://publisher.linkvertise.com/api/v1/anti_bypassing';

const VERIFY_TIMEOUT_MS = parseInt(process.env.LINKVERTISE_VERIFY_TIMEOUT_MS || '8000', 10);

const REASON_CODES = Object.freeze({
  NOT_CONFIGURED: 'linkvertise_not_configured',
  MISSING_HASH: 'missing_hash',
  BAD_HASH_FORMAT: 'bad_hash_format',
  API_TIMEOUT: 'api_timeout',
  API_ERROR: 'api_error',
  API_FALSE: 'api_false',
  API_INVALID_TOKEN: 'api_invalid_token',
  API_INVALID_RESPONSE: 'api_invalid_response',
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

/** True when Linkvertise can be used: enabled, target link set, anti-bypass token set. */
function isLinkvertiseConfigured() {
  return getLinkvertiseUnavailableReason() === null;
}

/** Human-friendly explanation when Linkvertise cannot be used (or null). */
function getLinkvertiseUnavailableReason() {
  if (!envEnabled('LINKVERTISE_ENABLED', 'false')) return 'LINKVERTISE_ENABLED is not true';
  if (!getLinkvertiseTargetLinkUrl()) return 'LINKVERTISE_TARGET_LINK_URL is not set';
  if (!getLinkvertiseAntiBypassToken()) return 'LINKVERTISE_ANTI_BYPASS_TOKEN is not set';
  return null;
}

function getLinkvertiseTargetLinkUrl() {
  return cleanEnvValue(
    'LINKVERTISE_TARGET_LINK_URL',
    cleanEnvValue('LINKVERTISE_MONETIZED_URL', ''),
  );
}

function getLinkvertiseCallbackUrl() {
  return cleanEnvValue(
    'LINKVERTISE_CALLBACK_URL',
    cleanEnvValue('LINKVERTISE_COMPLETE_URL', ''),
  );
}

function getLinkvertiseVerifyUrl() {
  return cleanEnvValue('LINKVERTISE_VERIFY_URL', DEFAULT_VERIFY_URL);
}

/** Read the anti-bypass token from env only. NEVER pass it to logs or frontend. */
function getLinkvertiseAntiBypassToken() {
  return cleanEnvValue('LINKVERTISE_ANTI_BYPASS_TOKEN', '');
}

/**
 * Validate the format of a Linkvertise hash query parameter.
 * Linkvertise documents the hash as a 64-character string. To stay robust
 * across small format changes we accept exactly 64 chars limited to
 * url-safe alphanumerics and a small set of safe punctuation.
 */
function isValidHashFormat(hash) {
  if (typeof hash !== 'string') return false;
  if (hash.length !== 64) return false;
  return /^[A-Za-z0-9_-]{64}$/.test(hash);
}

/** Public alias kept for clarity at call sites. */
function isHashShapedLikeLinkvertise(hash) {
  return isValidHashFormat(hash);
}

function safeHashPrefix(hash) {
  if (typeof hash !== 'string' || hash.length < 8) return '';
  return hash.slice(0, 8);
}

function classifyApiResponse(payload) {
  if (payload === true) return { ok: true, reason: REASON_CODES.SUCCESS };
  if (payload === false) return { ok: false, reason: REASON_CODES.API_FALSE };
  if (payload === null || typeof payload === 'undefined') {
    return { ok: false, reason: REASON_CODES.API_INVALID_RESPONSE };
  }

  if (typeof payload === 'string') {
    const trimmed = payload.trim().toLowerCase();
    if (trimmed === 'true') return { ok: true, reason: REASON_CODES.SUCCESS };
    if (trimmed === 'false') return { ok: false, reason: REASON_CODES.API_FALSE };
    if (trimmed.includes('invalid token') || trimmed.includes('invalid_token')) {
      return { ok: false, reason: REASON_CODES.API_INVALID_TOKEN };
    }
    return { ok: false, reason: REASON_CODES.API_INVALID_RESPONSE };
  }

  if (typeof payload === 'object') {
    if (payload.error) {
      const errStr = String(payload.error).toLowerCase();
      if (errStr.includes('invalid') && errStr.includes('token')) {
        return { ok: false, reason: REASON_CODES.API_INVALID_TOKEN };
      }
      return { ok: false, reason: REASON_CODES.API_ERROR };
    }
    if (payload.message) {
      const msgStr = String(payload.message).toLowerCase();
      if (msgStr.includes('invalid') && msgStr.includes('token')) {
        return { ok: false, reason: REASON_CODES.API_INVALID_TOKEN };
      }
    }
    // Linkvertise live API returns { "status": true|false } in JSON. We also
    // accept the documented synonyms `success` / `valid` for forward compat.
    if (payload.status === true || payload.success === true || payload.valid === true) {
      return { ok: true, reason: REASON_CODES.SUCCESS };
    }
    if (payload.status === false || payload.success === false || payload.valid === false) {
      return { ok: false, reason: REASON_CODES.API_FALSE };
    }
  }

  return { ok: false, reason: REASON_CODES.API_INVALID_RESPONSE };
}

/**
 * Verify a Linkvertise hash with the Anti-Bypass API.
 *
 * SECURITY:
 *  - The token is read from env only and sent in the POST body, never logged.
 *  - Hash is validated locally (length + char set) before any network call.
 *  - We fail closed on timeout / network / API errors.
 *  - Only an unambiguous TRUE response is treated as success.
 *
 * @param {{ hash: string, requestId?: string }} args
 * @returns {Promise<{ ok: boolean, reason: string, statusCode?: number }>}
 */
async function verifyLinkvertiseAntiBypass({ hash, requestId }) {
  const safePrefix = safeHashPrefix(hash);
  const rid = String(requestId || '').slice(0, 16);

  if (!isLinkvertiseConfigured()) {
    return { ok: false, reason: REASON_CODES.NOT_CONFIGURED };
  }
  if (!hash || typeof hash !== 'string') {
    return { ok: false, reason: REASON_CODES.MISSING_HASH };
  }
  if (!isValidHashFormat(hash)) {
    return { ok: false, reason: REASON_CODES.BAD_HASH_FORMAT };
  }

  const token = getLinkvertiseAntiBypassToken();
  const verifyUrl = getLinkvertiseVerifyUrl();

  // POST body — token never appears in URL/logs.
  const form = new URLSearchParams();
  form.set('token', token);
  form.set('hash', hash);

  try {
    const response = await axios.post(verifyUrl, form.toString(), {
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json,text/plain,*/*',
        'User-Agent': 'deng-tool-site/linkvertise-anti-bypass',
      },
      timeout: VERIFY_TIMEOUT_MS,
      validateStatus: () => true,
    });

    const status = response.status;
    const body = response.data;

    if (status === 401 || status === 403) {
      if (process.env.NODE_ENV !== 'test') {
        console.warn(
          '[linkvertise/verify] result=invalid_token rid=%s status=%d hash_prefix=%s',
          rid, status, safePrefix,
        );
      }
      return { ok: false, reason: REASON_CODES.API_INVALID_TOKEN, statusCode: status };
    }

    if (status < 200 || status >= 300) {
      if (process.env.NODE_ENV !== 'test') {
        console.warn(
          '[linkvertise/verify] result=api_error rid=%s status=%d hash_prefix=%s',
          rid, status, safePrefix,
        );
      }
      return { ok: false, reason: REASON_CODES.API_ERROR, statusCode: status };
    }

    const classified = classifyApiResponse(body);
    if (process.env.NODE_ENV !== 'test') {
      console.log(
        '[linkvertise/verify] result=%s rid=%s status=%d hash_prefix=%s',
        classified.reason, rid, status, safePrefix,
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
        '[linkvertise/verify] result=%s rid=%s error=%s hash_prefix=%s',
        reason, rid,
        (err && err.code) || (err && err.message ? err.message.slice(0, 64) : 'unknown'),
        safePrefix,
      );
    }
    return { ok: false, reason };
  }
}

module.exports = {
  REASON_CODES,
  isLinkvertiseConfigured,
  getLinkvertiseUnavailableReason,
  getLinkvertiseTargetLinkUrl,
  getLinkvertiseCallbackUrl,
  getLinkvertiseVerifyUrl,
  isValidHashFormat,
  isHashShapedLikeLinkvertise,
  safeHashPrefix,
  classifyApiResponse,
  verifyLinkvertiseAntiBypass,
};
