'use strict';
/**
 * DENG Tool: Rejoin APK — backend monitor routes.
 *
 * Provides three logical surfaces:
 *
 *   1. Termux agent bridge (Bearer monitor_bridge_token):
 *        POST /api/monitor/bridge/push       - status JSON (lightweight, every ~2s)
 *        POST /api/monitor/bridge/snapshot   - latest screenshot bytes
 *
 *   2. Android app (Bearer monitor_app_session_token):
 *        GET    /api/monitor/devices
 *        GET    /api/monitor/devices/:id/status
 *        GET    /api/monitor/devices/:id/snapshot/latest
 *        PATCH  /api/monitor/devices/:id/settings
 *
 *   3. Website pairing (logged-in Discord session):
 *        POST   /api/monitor/pairing/create   - issue 8-char one-time code
 *        POST   /api/monitor/pairing/redeem   - app posts code → app session token
 *        POST   /api/monitor/bridge/issue     - logged-in user mints bridge token
 *                                                (called by user from website
 *                                                 to seed Termux env var)
 *
 * Security contract:
 *   - All tokens are stored as SHA-256 hashes; raw tokens are returned exactly
 *     once at issue time.
 *   - All app/device endpoints require ownership match on
 *     monitor_devices.owner_discord_user_id.
 *   - No license keys, private URLs, HWIDs, cookies, secrets ever returned.
 *   - Snapshots are bounded to 1.5 MB; payloads bounded to 32 KB.
 *   - All write endpoints are rate-limited.
 */

const express = require('express');
const crypto = require('crypto');
const rateLimit = require('express-rate-limit');

const supabase = require('./db');
const { requireLogin } = require('./auth');

// ── Limits ──────────────────────────────────────────────────────────────────
const MAX_JSON_BYTES        = 32 * 1024;            // 32 KB
// v1.0.3: raised from 1.5 MB → 5 MB. Real Samsung cloud phones
// (A51 1080×2400) produce 1.8–2.5 MB PNG screencaps. Termux bridge
// caps its own uploads at 3 MB; this 5 MB server limit leaves
// headroom for higher-DPI devices without changing rate limits.
const MAX_SNAPSHOT_BYTES    = 5 * 1024 * 1024;      // 5 MB
const BRIDGE_TOKEN_TTL_SEC  = 12 * 60 * 60;         // 12h bridge token
const APP_SESSION_TTL_SEC   = 30 * 24 * 60 * 60;    // 30 day app session
const PAIRING_CODE_TTL_SEC  = 5 * 60;               // 5 min pairing code
const ALLOWED_SNAPSHOT_INTERVALS = new Set([0, 15, 30, 60, 300]);

// ── migration 010 resilience ─────────────────────────────────────────────────
// monitor_devices.last_bridge_status (migration 010) may not be applied yet on
// every deployment. If it is missing, naively SELECTing or UPDATEing it would
// (a) 400 every /status read and (b) — far worse — roll back the *entire*
// heartbeat UPDATE, so last_seen_at would never persist and every device would
// look permanently Disconnected under the v1.0.4 TTL logic.
//
// We treat the column as optional: detect a "column does not exist" failure
// once, latch a flag, and from then on skip the column entirely. When the
// migration is applied the flag simply never trips. No restart required to go
// from "missing" → "present": a fresh process re-probes; an already-running
// process that latched off keeps degrading gracefully until next deploy, which
// is the safe direction. Writes always split the core heartbeat from the
// optional diagnostics so the heartbeat can never be poisoned by the column.
let bridgeStatusColumnAvailable = true;

function isMissingBridgeStatusColumn(err) {
  if (!err) return false;
  // Supabase/PostgREST: code 42703 (undefined_column) or a message referencing
  // the column. Be liberal — false positives only cost us the diagnostics blob.
  const code = err.code || err?.details || '';
  const msg = `${err.message || ''} ${err.hint || ''} ${err.details || ''}`.toLowerCase();
  if (code === '42703') return true;
  return msg.includes('last_bridge_status') &&
    (msg.includes('does not exist') || msg.includes('could not find') || msg.includes('column'));
}

// ── Helpers ─────────────────────────────────────────────────────────────────
function sha256(input) {
  return crypto.createHash('sha256').update(String(input)).digest('hex');
}

function randomToken(bytes = 32) {
  return crypto.randomBytes(bytes).toString('base64url');
}

function randomPairingCode() {
  // 8-char base32 — readable + ~40 bits entropy
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const buf = crypto.randomBytes(8);
  let out = '';
  for (let i = 0; i < 8; i++) {
    out += alphabet[buf[i] % alphabet.length];
  }
  return out;
}

function nowIso(offsetSec = 0) {
  return new Date(Date.now() + offsetSec * 1000).toISOString();
}

function badRequest(res, message = 'invalid_request') {
  return res.status(400).json({ error: 'invalid_request', message });
}

function unauthorized(res, message = 'unauthorized') {
  return res.status(401).json({ error: 'unauthorized', message });
}

function notFound(res) {
  return res.status(404).json({ error: 'not_found' });
}

function serverError(res, code = 'server_error') {
  return res.status(500).json({ error: code });
}

function extractBearer(req) {
  const h = req.headers['authorization'] || '';
  if (typeof h !== 'string') return null;
  const m = h.match(/^Bearer\s+([A-Za-z0-9_\-+/=]+)$/);
  return m ? m[1] : null;
}

function safeOwnerOf(req) {
  return req.session && req.session.user && req.session.user.discord_user_id
    ? String(req.session.user.discord_user_id)
    : null;
}

function safePackageRowForApp(row) {
  // Drop everything we never want to leak to clients.
  return {
    package_name: row.package_name,
    display_name: row.display_name || null,
    username: row.username || null,
    state: row.state || 'Unknown',
    ram_mb: row.ram_mb || 0,
    runtime_seconds: row.runtime_seconds || 0,
    restart_count: row.restart_count || 0,
    private_url_configured: Boolean(row.private_url_configured),
    safe_error_reason: row.safe_error_reason || null,
    last_launch_at: row.last_launch_at || null,
    last_heartbeat_at: row.last_heartbeat_at || null,
    last_state_change_at: row.last_state_change_at || null,
    updated_at: row.updated_at || null,
  };
}

// v1.0.4: canonical 5 APK-visible states — Dead, Launching, Joining,
// Online, No Heartbeat. Older `Relaunching` rolls into `launching` for
// summary purposes (the Termux supervisor still emits it briefly).
// `In-Lobby` was removed per user requirement.
function summarizePackages(rows) {
  const out = {
    total: rows.length,
    online: 0,
    dead: 0,
    launching: 0,
    joining: 0,
    no_heartbeat: 0,
    relaunching: 0,  // legacy counter, kept for old APKs that still read it
    other: 0,
    total_ram_mb: 0,
    average_ram_mb: 0,
  };
  for (const r of rows) {
    out.total_ram_mb += Number(r.ram_mb) || 0;
    switch (r.state) {
      case 'Online':       out.online++; break;
      case 'Dead':         out.dead++; break;
      case 'Launching':    out.launching++; break;
      case 'Joining':      out.joining++; break;
      case 'No Heartbeat': out.no_heartbeat++; break;
      // Old supervisor vocabulary that may still arrive briefly.
      // Counted both into `launching` (new APK) and `relaunching`
      // (legacy field) so neither view goes blank.
      case 'Relaunching':  out.launching++; out.relaunching++; break;
      default:             out.other++;
    }
  }
  out.average_ram_mb = rows.length ? Math.round(out.total_ram_mb / rows.length) : 0;
  return out;
}

// v1.0.8: dashboard package summary. The dashboard's headline cards are
// PACKAGE counts (not device counts) — TOTAL configured packages, ONLINE
// (running/healthy) and DEAD (everything else: dead/launching/joining/no
// heartbeat/stale). Matches the user's spec exactly: 8 configured + all dead
// → TOTAL 8 / ONLINE 0 / DEAD 8; 8 with 3 online → TOTAL 8 / ONLINE 3 / DEAD 5.
// A snapshot failure never affects these counts (packages are tracked
// independently of the snapshot pipeline).
function aggregatePackageSummary(rows) {
  const list = Array.isArray(rows) ? rows : [];
  let online = 0;
  let totalRam = 0;
  let launching = 0;
  let joining = 0;
  let noHeartbeat = 0;
  for (const r of list) {
    totalRam += Number(r.ram_mb) || 0;
    switch (r.state) {
      case 'Online': online += 1; break;
      case 'Launching': case 'Relaunching': launching += 1; break;
      case 'Joining': joining += 1; break;
      case 'No Heartbeat': noHeartbeat += 1; break;
      default: break; // Dead / Unknown / stale all roll into "dead" below
    }
  }
  const total = list.length;
  return {
    total,
    online,
    dead: total - online, // everything not actively Online counts as dead
    launching,
    joining,
    no_heartbeat: noHeartbeat,
    total_ram_mb: totalRam,
  };
}

// v1.0.9: connection TTL scales with the device's configured refresh interval
// so a 30s app interval does not show Disconnected after ~30s of silence.
/** Legacy default TTL (5s interval); prefer connectionTtlSeconds(interval). */
const DEVICE_CONNECTION_TTL_SECONDS = 30;

function connectionThresholds(intervalSec) {
  const interval = Math.max(2, Math.min(300, Number(intervalSec) || 5));
  return {
    stale_after_seconds: Math.max(interval * 2, 60),
    disconnected_after_seconds: Math.max(interval * 3, 90),
  };
}

function connectionTtlSeconds(intervalSec) {
  return connectionThresholds(intervalSec).disconnected_after_seconds;
}

function normalizeMonitorSettings(row = {}) {
  return {
    snapshot_interval_seconds: Math.max(0, Math.min(3600,
      parseInt(row.snapshot_interval_seconds, 10) || 30)),
    monitor_enabled: row.monitor_enabled !== false,
    app_refresh_interval_seconds: Math.max(2, Math.min(60,
      parseInt(row.app_refresh_interval_seconds, 10) || 30)),
    app_display_name: typeof row.app_display_name === 'string' ? row.app_display_name : null,
  };
}

function computeConnectionState(lastSeenAt, intervalSec = 5) {
  const thresholds = connectionThresholds(intervalSec);
  const ttl = thresholds.disconnected_after_seconds;
  if (!lastSeenAt) {
    return {
      connected: false,
      connection_state: 'Disconnected',
      seconds_since_last_seen: null,
      connection_ttl_seconds: ttl,
      stale_after_seconds: thresholds.stale_after_seconds,
      disconnected_after_seconds: thresholds.disconnected_after_seconds,
      monitor_interval_seconds: intervalSec,
    };
  }
  const t = new Date(lastSeenAt).getTime();
  if (!Number.isFinite(t)) {
    return {
      connected: false,
      connection_state: 'Disconnected',
      seconds_since_last_seen: null,
      connection_ttl_seconds: ttl,
      stale_after_seconds: thresholds.stale_after_seconds,
      disconnected_after_seconds: thresholds.disconnected_after_seconds,
      monitor_interval_seconds: intervalSec,
    };
  }
  const age = Math.max(0, Math.floor((Date.now() - t) / 1000));
  const connected = age <= ttl;
  const state = age <= thresholds.stale_after_seconds
    ? 'Connected'
    : (age <= thresholds.disconnected_after_seconds ? 'Stale' : 'Disconnected');
  return {
    connected,
    connection_state: state,
    seconds_since_last_seen: age,
    connection_ttl_seconds: ttl,
    stale_after_seconds: thresholds.stale_after_seconds,
    disconnected_after_seconds: thresholds.disconnected_after_seconds,
    monitor_interval_seconds: intervalSec,
  };
}

async function loadSettingsByDeviceIds(deviceIds) {
  const map = new Map();
  if (!deviceIds.length) return map;
  try {
    const { data, error } = await supabase
      .from('monitor_settings')
      .select('monitor_device_id, app_refresh_interval_seconds, snapshot_interval_seconds, monitor_enabled')
      .in('monitor_device_id', deviceIds);
    if (error) throw error;
    for (const row of (data || [])) {
      map.set(row.monitor_device_id, normalizeMonitorSettings(row));
    }
  } catch (err) {
    console.warn('[monitor] settings batch load failed', err?.message || err);
  }
  return map;
}

// ── Auth middlewares ────────────────────────────────────────────────────────

/**
 * Authenticate a Termux bridge request and attach req.bridgeDevice.
 */
async function requireBridgeAuth(req, res, next) {
  const token = extractBearer(req);
  if (!token) return unauthorized(res, 'missing_bridge_token');
  const hash = sha256(token);
  try {
    const { data: tokenRow, error } = await supabase
      .from('monitor_bridge_tokens')
      .select('id, monitor_device_id, expires_at, revoked_at')
      .eq('token_hash', hash)
      .maybeSingle();
    if (error || !tokenRow) return unauthorized(res, 'invalid_bridge_token');
    if (tokenRow.revoked_at) return unauthorized(res, 'token_revoked');
    if (new Date(tokenRow.expires_at).getTime() < Date.now()) {
      return unauthorized(res, 'token_expired');
    }
    const { data: device } = await supabase
      .from('monitor_devices')
      .select('id, owner_discord_user_id, device_label, tool_version, channel')
      .eq('id', tokenRow.monitor_device_id)
      .maybeSingle();
    if (!device) return unauthorized(res, 'device_missing');
    req.bridgeDevice = device;
    req.bridgeTokenId = tokenRow.id;
    return next();
  } catch (err) {
    console.error('[monitor] bridge auth error', err?.message || err);
    return serverError(res, 'bridge_auth_failed');
  }
}

/**
 * Authenticate an Android app request and attach req.appOwner.
 */
async function requireAppAuth(req, res, next) {
  const token = extractBearer(req);
  if (!token) return unauthorized(res, 'missing_app_token');
  const hash = sha256(token);
  try {
    const { data: row, error } = await supabase
      .from('monitor_app_sessions')
      .select('id, owner_discord_user_id, expires_at, revoked_at')
      .eq('token_hash', hash)
      .maybeSingle();
    if (error || !row) return unauthorized(res, 'invalid_app_token');
    if (row.revoked_at) return unauthorized(res, 'token_revoked');
    if (new Date(row.expires_at).getTime() < Date.now()) {
      return unauthorized(res, 'token_expired');
    }
    // Refresh last_used_at (best-effort, do not await failures)
    supabase.from('monitor_app_sessions')
      .update({ last_used_at: new Date().toISOString() })
      .eq('id', row.id)
      .then(() => {}, () => {});
    req.appOwner = row.owner_discord_user_id;
    req.appSessionId = row.id;
    return next();
  } catch (err) {
    console.error('[monitor] app auth error', err?.message || err);
    return serverError(res, 'app_auth_failed');
  }
}

// ── Routers ─────────────────────────────────────────────────────────────────
const router = express.Router();

// Skip rate limits in test, like the rest of the site.
const isTest = () => process.env.NODE_ENV === 'test';

// Hard JSON body limit (overrides app-level 16kb for monitor bridge only).
const monitorJsonParser = express.json({ limit: MAX_JSON_BYTES });

// Raw byte parser for snapshots, with content-type allow-list.
const snapshotParser = express.raw({
  type: ['image/png', 'image/jpeg', 'image/webp'],
  limit: MAX_SNAPSHOT_BYTES,
});

const bridgePushLimiter = rateLimit({
  windowMs: 60_000,
  max: 240,                     // supports 5s interval + retries per device
  skip: isTest,
  keyGenerator: (req) => `bridge:${req.bridgeDevice?.id || req.bridgeTokenId || 'unknown'}`,
  standardHeaders: true,
  legacyHeaders: false,
  handler: (req, res, _next, options) => {
    const retryAfter = Math.max(1, Math.ceil(options.windowMs / 1000));
    return res.status(429).json({
      ok: false,
      error: 'rate_limited',
      retry_after_seconds: retryAfter,
      endpoint: req.originalUrl || req.path,
    });
  },
});

const bridgeSnapshotLimiter = rateLimit({
  windowMs: 60_000,
  max: 60,                      // snapshot uploads are separate from heartbeat
  skip: isTest,
  keyGenerator: (req) => `bridge-snapshot:${req.bridgeDevice?.id || req.bridgeTokenId || 'unknown'}`,
  standardHeaders: true,
  legacyHeaders: false,
  handler: (req, res, _next, options) => {
    const retryAfter = Math.max(1, Math.ceil(options.windowMs / 1000));
    return res.status(429).json({
      ok: false,
      error: 'rate_limited',
      retry_after_seconds: retryAfter,
      endpoint: req.originalUrl || req.path,
    });
  },
});

const pairingLimiter = rateLimit({
  windowMs: 60_000,
  max: 10,
  skip: isTest,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'rate_limited' },
});

// ───────────────────────────────────────────────────────────────────────────
// 1. TERMUX BRIDGE
// ───────────────────────────────────────────────────────────────────────────

/**
 * POST /api/monitor/bridge/push
 * Body: { schema, tool_version, channel, captured_at, packages: [...] }
 */
router.post('/api/monitor/bridge/push',
  monitorJsonParser,
  requireBridgeAuth,
  bridgePushLimiter,
  async (req, res) => {
    const body = req.body || {};
    if (body.schema !== 1) return badRequest(res, 'unsupported_schema');
    if (!Array.isArray(body.packages)) return badRequest(res, 'packages_required');
    if (body.packages.length > 64)     return badRequest(res, 'too_many_packages');

    const device = req.bridgeDevice;
    const nowTs = new Date().toISOString();

    // v1.0.4: accept an optional `bridge_status` object from the Termux
    // bridge so the APK Snapshot screen can render real capture / upload
    // results instead of "Waiting for first snapshot…" forever. Stored
    // as a small JSON blob on monitor_devices.last_bridge_status (added
    // by migration 010). We scrub it server-side because the bridge is
    // user-controlled — only known-safe keys + size cap survive.
    const incomingStatus = body.bridge_status;
    let bridgeStatusClean = null;
    if (incomingStatus && typeof incomingStatus === 'object' && !Array.isArray(incomingStatus)) {
      const pick = (v, max = 64) =>
        typeof v === 'string' ? v.slice(0, max) : (typeof v === 'number' && Number.isFinite(v) ? v : null);
      const pickBool = (v) => (typeof v === 'boolean' ? v : null);
      // v1.0.6: device-level RAM block (used/total/percent) for the
      // redesigned dashboard's per-device RAM list. Scrubbed + clamped.
      let deviceRamClean = null;
      const dr = incomingStatus.device_ram;
      if (dr && typeof dr === 'object' && !Array.isArray(dr)) {
        const usedMb = Math.max(0, Math.min(4_194_304, parseInt(dr.used_mb, 10) || 0));
        const totalMb = Math.max(0, Math.min(4_194_304, parseInt(dr.total_mb, 10) || 0));
        let percent = Math.max(0, Math.min(100, parseInt(dr.percent, 10)));
        if (!Number.isFinite(percent)) percent = totalMb > 0 ? Math.round((usedMb / totalMb) * 100) : 0;
        if (totalMb > 0 || percent > 0) {
          deviceRamClean = { used_mb: usedMb, total_mb: totalMb, percent };
        }
      }
      bridgeStatusClean = {
        // Snapshot pipeline diagnostics (most important for the user's bug).
        snapshot_last_result: pick(incomingStatus.snapshot_last_result, 64),
        snapshot_last_bytes: Math.max(0, Math.min(10_000_000, parseInt(incomingStatus.snapshot_last_bytes, 10) || 0)) || null,
        snapshot_last_error: pick(incomingStatus.snapshot_last_error, 200),
        snapshot_provider_called_count: Math.max(0, Math.min(1_000_000, parseInt(incomingStatus.snapshot_provider_called_count, 10) || 0)) || 0,
        snapshot_last_upload_status: pick(incomingStatus.snapshot_last_upload_status, 16),
        screencap_available: pickBool(incomingStatus.screencap_available),
        // v1.0.6 capture-provider diagnostics — proves which screencap rung
        // worked (normal/system/root stdout/root file) or why all failed.
        snapshot_provider: pick(incomingStatus.snapshot_provider, 40),
        snapshot_png_valid: pickBool(incomingStatus.snapshot_png_valid),
        snapshot_root_granted: pickBool(incomingStatus.snapshot_root_granted),
        snapshot_su_available: pickBool(incomingStatus.snapshot_su_available),
        // v1.0.6 device RAM for the dashboard.
        device_ram: deviceRamClean,
        // Push pipeline diagnostics (we already track this via timestamps,
        // but the bridge's own view is useful for debugging time skew).
        last_push_result: pick(incomingStatus.last_push_result, 32),
        last_push_error: pick(incomingStatus.last_push_error, 120),
        next_retry_at: pick(incomingStatus.next_retry_at, 64),
        // Schema marker so callers can detect old / new payloads.
        schema: 1,
        // Server-side trust timestamp — clients can't lie about when we
        // received this row.
        received_at: nowTs,
      };
    }

    // Update device heartbeat / version metadata.
    //
    // CRITICAL: the heartbeat (last_seen_at) and the OPTIONAL bridge_status
    // diagnostics are written in TWO separate UPDATEs. If they shared one
    // UPDATE and last_bridge_status (migration 010) were missing, the whole
    // statement would fail and last_seen_at would never persist — which the
    // v1.0.4 TTL would render as a permanently Disconnected device. Splitting
    // them guarantees the heartbeat always lands even when the column is gone.
    try {
      const update = {
        status_connected: true,
        last_seen_at: nowTs,
        tool_version: typeof body.tool_version === 'string' ? body.tool_version.slice(0, 32) : null,
        channel: typeof body.channel === 'string' && ['stable','beta','dev','latest','test'].includes(body.channel)
          ? body.channel : 'stable',
        last_disconnect_reason: null,
      };
      const { error: hbErr } = await supabase.from('monitor_devices')
        .update(update)
        .eq('id', device.id);
      if (hbErr) console.error('[monitor] device heartbeat update failed', hbErr.message || hbErr);
    } catch (err) {
      console.error('[monitor] device heartbeat update failed', err?.message || err);
    }

    // Optional diagnostics write — only attempted when the bridge sent a
    // status block AND migration 010's column has not been proven missing.
    if (bridgeStatusClean && bridgeStatusColumnAvailable) {
      try {
        const { error: bsErr } = await supabase.from('monitor_devices')
          .update({ last_bridge_status: bridgeStatusClean })
          .eq('id', device.id);
        if (bsErr) {
          if (isMissingBridgeStatusColumn(bsErr)) {
            bridgeStatusColumnAvailable = false;
            console.warn('[monitor] last_bridge_status column missing — apply migration 010; diagnostics disabled until then');
          } else {
            console.error('[monitor] bridge_status update failed', bsErr.message || bsErr);
          }
        }
      } catch (err) {
        if (isMissingBridgeStatusColumn(err)) {
          bridgeStatusColumnAvailable = false;
          console.warn('[monitor] last_bridge_status column missing — apply migration 010; diagnostics disabled until then');
        } else {
          console.error('[monitor] bridge_status update failed', err?.message || err);
        }
      }
    }

    // Upsert each package state. We strip anything not in the schema.
    const rows = [];
    for (const raw of body.packages) {
      if (!raw || typeof raw !== 'object') continue;
      const pkg = typeof raw.package === 'string' ? raw.package.slice(0, 128) : null;
      if (!pkg) continue;
      const tsToIso = (v) => {
        const n = Number(v);
        return Number.isFinite(n) && n > 0 ? new Date(n * 1000).toISOString() : null;
      };
      rows.push({
        monitor_device_id: device.id,
        package_name: pkg,
        display_name: typeof raw.display_name === 'string' ? raw.display_name.slice(0, 64) : null,
        username: typeof raw.username === 'string' ? raw.username.slice(0, 64) : null,
        state: typeof raw.state === 'string' ? raw.state.slice(0, 32) : 'Unknown',
        ram_mb: Math.max(0, Math.min(65536, parseInt(raw.ram_mb, 10) || 0)),
        runtime_seconds: Math.max(0, Math.min(60 * 60 * 24 * 30, parseInt(raw.runtime_seconds, 10) || 0)),
        restart_count: Math.max(0, Math.min(1_000_000, parseInt(raw.restart_count, 10) || 0)),
        pid: raw.pid && Number.isInteger(raw.pid) && raw.pid > 0 && raw.pid < 2_000_000 ? raw.pid : null,
        private_url_configured: Boolean(raw.private_url_configured),
        safe_error_reason: typeof raw.safe_error_reason === 'string' ? raw.safe_error_reason.slice(0, 200) : null,
        last_launch_at: tsToIso(raw.last_launch_at),
        last_heartbeat_at: tsToIso(raw.last_heartbeat_at),
        last_state_change_at: tsToIso(raw.last_state_change_at),
        updated_at: nowTs,
      });
    }

    if (rows.length) {
      try {
        await supabase.from('monitor_package_states')
          .upsert(rows, { onConflict: 'monitor_device_id,package_name' });
      } catch (err) {
        console.error('[monitor] package upsert failed', err?.message || err);
        return serverError(res, 'state_update_failed');
      }
    }

    // Echo the device's current monitor settings so the bridge can react
    // to APK setting changes (snapshot interval etc.) without requiring a
    // Termux relaunch. Failure here must NOT fail the whole push.
    let settingsEcho = null;
    try {
      const { data: srow } = await supabase
        .from('monitor_settings')
        .select('snapshot_interval_seconds, monitor_enabled, app_refresh_interval_seconds')
        .eq('monitor_device_id', device.id)
        .maybeSingle();
      if (srow) {
        const normalized = normalizeMonitorSettings(srow);
        settingsEcho = {
          snapshot_interval_seconds: normalized.snapshot_interval_seconds,
          monitor_enabled: normalized.monitor_enabled,
          app_refresh_interval_seconds: normalized.app_refresh_interval_seconds,
        };
      }
    } catch (err) {
      console.warn('[monitor] settings echo failed', err?.message || err);
    }

    return res.json({ ok: true, accepted: rows.length, settings: settingsEcho });
  });

/**
 * POST /api/monitor/bridge/snapshot
 * Body: raw image bytes (image/webp|png|jpeg)
 */
router.post('/api/monitor/bridge/snapshot',
  snapshotParser,
  requireBridgeAuth,
  bridgeSnapshotLimiter,
  async (req, res) => {
    if (!Buffer.isBuffer(req.body) || req.body.length === 0) {
      return badRequest(res, 'image_required');
    }
    if (req.body.length > MAX_SNAPSHOT_BYTES) {
      return res.status(413).json({ error: 'too_large' });
    }
    const mime = req.headers['content-type'] || 'image/webp';
    try {
      await supabase.from('monitor_snapshots').insert({
        monitor_device_id: req.bridgeDevice.id,
        mime_type: mime,
        image_data: req.body,
        size_bytes: req.body.length,
      });
      return res.json({ ok: true, size: req.body.length });
    } catch (err) {
      console.error('[monitor] snapshot insert failed', err?.message || err);
      return serverError(res, 'snapshot_insert_failed');
    }
  });

// ───────────────────────────────────────────────────────────────────────────
// 2. ANDROID APP
// ───────────────────────────────────────────────────────────────────────────

const DEVICE_LIST_CORE_COLS =
  'id, device_label, tool_version, channel, status_connected, last_seen_at, created_at';

router.get('/api/monitor/devices', requireAppAuth, async (req, res) => {
  try {
    // v1.0.6: the redesigned dashboard is device-centric (TOTAL / ONLINE /
    // DEAD + per-device RAM), so the list now also returns last_bridge_status
    // (device RAM + snapshot diagnostics). Guarded the same way as
    // loadOwnedDevice: if migration 010 isn't applied, fall back to the core
    // columns so the dashboard still renders (just without RAM rows).
    const cols = bridgeStatusColumnAvailable
      ? `${DEVICE_LIST_CORE_COLS}, last_bridge_status`
      : DEVICE_LIST_CORE_COLS;

    let { data, error } = await supabase
      .from('monitor_devices')
      .select(cols)
      .eq('owner_discord_user_id', req.appOwner)
      .order('last_seen_at', { ascending: false });

    if (error && isMissingBridgeStatusColumn(error)) {
      bridgeStatusColumnAvailable = false;
      console.warn('[monitor] last_bridge_status column missing — apply migration 010; listing without RAM');
      ({ data, error } = await supabase
        .from('monitor_devices')
        .select(DEVICE_LIST_CORE_COLS)
        .eq('owner_discord_user_id', req.appOwner)
        .order('last_seen_at', { ascending: false }));
    }
    if (error) throw error;

    res.set('Cache-Control', 'no-store');
    // Enrich each row with the computed connection_state (TTL-based) and a
    // compact, dashboard-ready device_ram block extracted from the scrubbed
    // bridge status. Never invents numbers: ram is null when the bridge
    // didn't report it.
    const deviceIds = (data || []).map((d) => d.id);
    const settingsMap = await loadSettingsByDeviceIds(deviceIds);
    const enriched = (data || []).map((d) => {
      const bs = d.last_bridge_status || null;
      const ram = bs && bs.device_ram && typeof bs.device_ram === 'object'
        ? {
            used_mb: Number(bs.device_ram.used_mb) || 0,
            total_mb: Number(bs.device_ram.total_mb) || 0,
            percent: Number.isFinite(Number(bs.device_ram.percent)) ? Number(bs.device_ram.percent) : null,
          }
        : null;
      const settings = settingsMap.get(d.id) || {};
      const intervalSec = settings.app_refresh_interval_seconds || 30;
      const conn = computeConnectionState(d.last_seen_at, intervalSec);
      const { last_bridge_status, ...rest } = d;
      return {
        ...rest,
        ...conn,
        monitor_interval_seconds: intervalSec,
        snapshot_interval_seconds: settings.snapshot_interval_seconds ?? 30,
        device_ram: ram,
        snapshot_last_result: bs ? (bs.snapshot_last_result || null) : null,
        last_push_status: bs ? (bs.last_push_result || null) : null,
        last_push_error: bs ? (bs.last_push_error || null) : null,
        next_retry_at: bs ? (bs.next_retry_at || null) : null,
      };
    });

    // v1.0.8: aggregate PACKAGE stats across all owned devices so the
    // dashboard's TOTAL / ONLINE / DEAD cards reflect configured packages,
    // not the number of devices. Per-device package_summary is also attached.
    const enrichedIds = enriched.map((d) => d.id);
    let pkgRows = [];
    if (enrichedIds.length) {
      try {
        const { data: prows, error: perr } = await supabase
          .from('monitor_package_states')
          .select('monitor_device_id, package_name, state, ram_mb')
          .in('monitor_device_id', enrichedIds);
        if (perr) throw perr;
        pkgRows = prows || [];
      } catch (perr) {
        console.warn('[monitor] package states query failed', perr?.message || perr);
        pkgRows = [];
      }
    }
    const byDevice = new Map();
    for (const r of pkgRows) {
      const arr = byDevice.get(r.monitor_device_id) || [];
      arr.push(r);
      byDevice.set(r.monitor_device_id, arr);
    }
    enriched.forEach((d) => { d.package_summary = aggregatePackageSummary(byDevice.get(d.id) || []); });
    const packageSummary = aggregatePackageSummary(pkgRows);

    return res.json({ devices: enriched, package_summary: packageSummary });
  } catch (err) {
    console.error('[monitor] list devices failed', err?.message || err);
    return serverError(res, 'list_devices_failed');
  }
});

// v1.0.4 included last_bridge_status (snapshot capture diagnostics pushed by
// the Termux bridge) so /status can surface real reasons to the APK instead of
// the v1.0.3 "Waiting for first snapshot…" silent state. If migration 010 has
// not been applied, SELECTing that column makes the query error → maybeSingle()
// returns data:null → loadOwnedDevice would 404 EVERY status read. So we
// build the column list dynamically and, if the optional column trips a
// "column does not exist" error, latch it off and retry with the safe columns.
const DEVICE_CORE_COLS =
  'id, owner_discord_user_id, device_label, tool_version, channel, status_connected, last_seen_at, created_at';

async function loadOwnedDevice(req, deviceId) {
  if (!deviceId || typeof deviceId !== 'string') return null;

  const cols = bridgeStatusColumnAvailable
    ? `${DEVICE_CORE_COLS}, last_bridge_status`
    : DEVICE_CORE_COLS;

  let { data, error } = await supabase
    .from('monitor_devices')
    .select(cols)
    .eq('id', deviceId)
    .maybeSingle();

  if (error && isMissingBridgeStatusColumn(error)) {
    bridgeStatusColumnAvailable = false;
    console.warn('[monitor] last_bridge_status column missing — apply migration 010; reading without diagnostics');
    ({ data, error } = await supabase
      .from('monitor_devices')
      .select(DEVICE_CORE_COLS)
      .eq('id', deviceId)
      .maybeSingle());
  }

  if (error) {
    console.error('[monitor] loadOwnedDevice failed', error.message || error);
    return null;
  }
  if (!data || data.owner_discord_user_id !== req.appOwner) return null;
  return data;
}

router.get('/api/monitor/devices/:id/status', requireAppAuth, async (req, res) => {
  const device = await loadOwnedDevice(req, req.params.id);
  if (!device) return notFound(res);

  try {
    const [{ data: pkgRows }, { data: settingsRow }, { data: snapRow }] = await Promise.all([
      supabase.from('monitor_package_states')
        .select('*')
        .eq('monitor_device_id', device.id)
        .order('package_name', { ascending: true }),
      supabase.from('monitor_settings')
        .select('snapshot_interval_seconds, monitor_enabled, app_refresh_interval_seconds, app_display_name')
        .eq('monitor_device_id', device.id)
        .maybeSingle(),
      // v1.0.3: surface the timestamp of the newest snapshot to the
      // APK so SnapshotScreen can distinguish "interval is on but the
      // bridge hasn't uploaded anything yet" (→ Waiting) from "interval
      // is on and there genuinely is no image" (→ same Waiting copy,
      // not the misleading "No snapshot yet"). Cheap query: indexed
      // (monitor_device_id, captured_at DESC).
      supabase.from('monitor_snapshots')
        .select('captured_at')
        .eq('monitor_device_id', device.id)
        .order('captured_at', { ascending: false })
        .limit(1)
        .maybeSingle(),
    ]);
    const safePackages = (pkgRows || []).map(safePackageRowForApp);
    const lastSnapshotCapturedAt = snapRow?.captured_at || null;
    let lastSnapshotAgeSeconds = null;
    if (lastSnapshotCapturedAt) {
      const ageMs = Date.now() - new Date(lastSnapshotCapturedAt).getTime();
      if (Number.isFinite(ageMs) && ageMs >= 0) {
        lastSnapshotAgeSeconds = Math.floor(ageMs / 1000);
      }
    }
    // v1.0.4: computed connection state — the raw status_connected
    // boolean used to be sticky-true forever after the first push, which
    // is why the APK still said Connected after the cloud phone was
    // rebooted. Now we anchor on last_seen_at + DEVICE_CONNECTION_TTL_SECONDS.
    const normalizedSettings = normalizeMonitorSettings(settingsRow || {});
    const intervalSec = normalizedSettings.app_refresh_interval_seconds;
    const conn = computeConnectionState(device.last_seen_at, intervalSec);
    res.set('Cache-Control', 'no-store');
    return res.json({
      device: {
        id: device.id,
        device_label: device.device_label,
        tool_version: device.tool_version,
        channel: device.channel,
        // Legacy field — kept for backwards compatibility with older APKs.
        // New APKs (v1.0.4+) should read `connected` / `connection_state`.
        status_connected: device.status_connected,
        connected: conn.connected,
        connection_state: conn.connection_state,
        seconds_since_last_seen: conn.seconds_since_last_seen,
        stale_after_seconds: conn.stale_after_seconds,
        disconnected_after_seconds: conn.disconnected_after_seconds,
        last_seen_at: device.last_seen_at,
        last_snapshot_captured_at: lastSnapshotCapturedAt,
        last_snapshot_age_seconds: lastSnapshotAgeSeconds,
        // Bridge self-reported diagnostics (optional, may be null).
        last_bridge_status: device.last_bridge_status || null,
        last_push_status: device.last_bridge_status?.last_push_result || null,
        last_push_error: device.last_bridge_status?.last_push_error || null,
        next_retry_at: device.last_bridge_status?.next_retry_at || null,
        monitor_interval_seconds: intervalSec,
        connection_ttl_seconds: connectionTtlSeconds(intervalSec),
      },
      summary: summarizePackages(safePackages),
      packages: safePackages,
      settings: settingsRow ? normalizeMonitorSettings(settingsRow) : normalizeMonitorSettings(),
    });
  } catch (err) {
    console.error('[monitor] device status failed', err?.message || err);
    return serverError(res, 'status_failed');
  }
});

router.get('/api/monitor/devices/:id/snapshot/latest', requireAppAuth, async (req, res) => {
  const device = await loadOwnedDevice(req, req.params.id);
  if (!device) return notFound(res);
  try {
    const { data, error } = await supabase
      .from('monitor_snapshots')
      .select('mime_type, image_data, size_bytes, captured_at')
      .eq('monitor_device_id', device.id)
      .order('captured_at', { ascending: false })
      .limit(1)
      .maybeSingle();
    if (error || !data) {
      res.set('Cache-Control', 'no-store');
      return res.status(204).end();
    }
    res.set('Cache-Control', 'no-store');
    res.set('Content-Type', data.mime_type || 'image/webp');
    res.set('X-Captured-At', data.captured_at);
    const bytes = Buffer.isBuffer(data.image_data)
      ? data.image_data
      : Buffer.from(data.image_data, 'base64');
    return res.send(bytes);
  } catch (err) {
    console.error('[monitor] snapshot fetch failed', err?.message || err);
    return serverError(res, 'snapshot_fetch_failed');
  }
});

router.patch('/api/monitor/devices/:id/settings',
  requireAppAuth,
  monitorJsonParser,
  async (req, res) => {
    const device = await loadOwnedDevice(req, req.params.id);
    if (!device) return notFound(res);
    const body = req.body || {};
    const patch = { monitor_device_id: device.id };

    if ('snapshot_interval_seconds' in body) {
      const n = parseInt(body.snapshot_interval_seconds, 10);
      if (!ALLOWED_SNAPSHOT_INTERVALS.has(n)) return badRequest(res, 'invalid_snapshot_interval');
      patch.snapshot_interval_seconds = n;
    }
    if ('monitor_enabled' in body) {
      patch.monitor_enabled = Boolean(body.monitor_enabled);
    }
    if ('app_refresh_interval_seconds' in body) {
      const n = parseInt(body.app_refresh_interval_seconds, 10);
      if (!(n >= 2 && n <= 60)) return badRequest(res, 'invalid_refresh_interval');
      patch.app_refresh_interval_seconds = n;
    }
    if ('app_display_name' in body) {
      patch.app_display_name = typeof body.app_display_name === 'string'
        ? body.app_display_name.slice(0, 64) : null;
    }

    try {
      const saved = await supabase.from('monitor_settings')
        .upsert(patch, { onConflict: 'monitor_device_id' });
      if (saved && saved.error) throw saved.error;
      const { data: settingsRow, error: readError } = await supabase.from('monitor_settings')
        .select('snapshot_interval_seconds, monitor_enabled, app_refresh_interval_seconds, app_display_name')
        .eq('monitor_device_id', device.id)
        .maybeSingle();
      if (readError) throw readError;
      return res.json({ ok: true, settings: normalizeMonitorSettings(settingsRow || patch) });
    } catch (err) {
      console.error('[monitor] settings update failed', err?.message || err);
      return serverError(res, 'settings_update_failed');
    }
  });

// ───────────────────────────────────────────────────────────────────────────
// 3. PAIRING + BRIDGE TOKEN ISSUE (website session required)
// ───────────────────────────────────────────────────────────────────────────

/**
 * POST /api/monitor/pairing/create
 *   Returns: { code, expires_at } — short-lived pairing code for the app.
 *   The plaintext code is returned ONCE.
 */
router.post('/api/monitor/pairing/create',
  pairingLimiter,
  requireLogin,
  monitorJsonParser,
  async (req, res) => {
    const owner = safeOwnerOf(req);
    if (!owner) return unauthorized(res);
    const code = randomPairingCode();
    try {
      await supabase.from('monitor_pairing_codes').insert({
        code_hash: sha256(code),
        owner_discord_user_id: owner,
        site_user_id: req.session.user.id || null,
        expires_at: nowIso(PAIRING_CODE_TTL_SEC),
      });
      return res.json({ code, expires_at: nowIso(PAIRING_CODE_TTL_SEC) });
    } catch (err) {
      console.error('[monitor] pairing create failed', err?.message || err);
      return serverError(res, 'pairing_create_failed');
    }
  });

/**
 * POST /api/monitor/pairing/redeem
 *   Body: { code, device_name? }
 *   Returns: { app_session_token, expires_at, owner: { discord_user_id, username } }
 *   This is the only endpoint that issues an app session token, and the
 *   plaintext token is returned ONCE.
 */
router.post('/api/monitor/pairing/redeem',
  pairingLimiter,
  monitorJsonParser,
  async (req, res) => {
    const body = req.body || {};
    const code = typeof body.code === 'string' ? body.code.trim().toUpperCase() : '';
    if (code.length < 6 || code.length > 16) return badRequest(res, 'invalid_code');
    try {
      const { data: row } = await supabase
        .from('monitor_pairing_codes')
        .select('id, owner_discord_user_id, expires_at, used_at')
        .eq('code_hash', sha256(code))
        .maybeSingle();
      if (!row) return unauthorized(res, 'invalid_code');
      if (row.used_at) return unauthorized(res, 'code_already_used');
      if (new Date(row.expires_at).getTime() < Date.now()) return unauthorized(res, 'code_expired');

      const token = randomToken(32);
      const tokenHash = sha256(token);
      const { data: sessionRow, error: sessErr } = await supabase
        .from('monitor_app_sessions')
        .insert({
          owner_discord_user_id: row.owner_discord_user_id,
          token_hash: tokenHash,
          device_name: typeof body.device_name === 'string' ? body.device_name.slice(0, 64) : null,
          expires_at: nowIso(APP_SESSION_TTL_SEC),
        })
        .select('id')
        .single();
      if (sessErr) throw sessErr;

      await supabase.from('monitor_pairing_codes')
        .update({
          used_at: new Date().toISOString(),
          consumed_by_app_session_id: sessionRow.id,
        })
        .eq('id', row.id);

      return res.json({
        app_session_token: token,
        expires_at: nowIso(APP_SESSION_TTL_SEC),
        owner: {
          discord_user_id: row.owner_discord_user_id,
        },
      });
    } catch (err) {
      console.error('[monitor] pairing redeem failed', err?.message || err);
      return serverError(res, 'pairing_redeem_failed');
    }
  });

// ── License-key proof normalizers (mirror agent.license.normalize_license_key) ─
const LICENSE_KEY_PATTERN = /^DENG-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$/;
function normalizeLicenseKey(raw) {
  if (typeof raw !== 'string') return '';
  return raw.trim().toUpperCase();
}
const INSTALL_ID_HASH_PATTERN = /^[a-f0-9]{64}$/;
const ALLOWED_CHANNELS = ['stable', 'beta', 'dev', 'latest', 'test', 'main-dev'];

/**
 * POST /api/monitor/bridge/issue-from-license
 *   Body: { license_key, install_id_hash, device_label?, tool_version?, channel? }
 *   Returns: { bridge_token, device_id, expires_at }
 *
 *   Termux-safe device registration: validates an existing license-key +
 *   install_id_hash binding against the license tables, then issues a
 *   short-lived bridge token for the owner's device. NEVER requires a
 *   website session, so `deng-rejoin` can auto-register after license
 *   verification without any manual env-var setup by public users.
 *
 *   Security:
 *     • Rejects unknown / expired / inactive license keys.
 *     • Rejects calls where install_id_hash does not match the recorded
 *       device_binding for that key (so a leaked key alone can't mint).
 *     • Hashes the bridge token before storing (raw returned ONCE).
 *     • Reuses the same monitor_devices row for (owner, fingerprint_hash)
 *       so re-runs don't pile up phantom devices.
 */
router.post('/api/monitor/bridge/issue-from-license',
  pairingLimiter,
  monitorJsonParser,
  async (req, res) => {
    const body = req.body || {};
    const rawKey = normalizeLicenseKey(body.license_key);
    const installIdHash = typeof body.install_id_hash === 'string' ? body.install_id_hash.trim().toLowerCase() : '';
    if (!LICENSE_KEY_PATTERN.test(rawKey)) return badRequest(res, 'invalid_license_key');
    if (!INSTALL_ID_HASH_PATTERN.test(installIdHash)) return badRequest(res, 'invalid_install_id_hash');

    const keyId = sha256(rawKey);
    const deviceLabel = typeof body.device_label === 'string'
      ? body.device_label.slice(0, 64).replace(/[^\x20-\x7E]/g, '').trim() || 'Termux'
      : 'Termux';
    const toolVersion = typeof body.tool_version === 'string' ? body.tool_version.slice(0, 32) : null;
    const channel = typeof body.channel === 'string' && ALLOWED_CHANNELS.includes(body.channel)
      ? body.channel : 'stable';

    try {
      // 1. Verify the license key is real, owned, and active.
      const { data: keyRow, error: keyErr } = await supabase
        .from('license_keys')
        .select('id, owner_discord_id, status, expires_at')
        .eq('id', keyId)
        .maybeSingle();
      if (keyErr) throw keyErr;
      if (!keyRow) return res.status(403).json({ error: 'invalid_license' });
      if (keyRow.status !== 'active') return res.status(403).json({ error: 'license_inactive' });
      if (keyRow.expires_at && new Date(keyRow.expires_at).getTime() < Date.now()) {
        return res.status(403).json({ error: 'license_expired' });
      }
      const owner = keyRow.owner_discord_id ? String(keyRow.owner_discord_id) : '';
      if (!owner) return res.status(403).json({ error: 'license_unowned' });

      // 2. Verify the install_id_hash matches the recorded binding.
      //    This proves the caller is on the device that redeemed the key,
      //    so a leaked license key alone cannot mint a bridge token.
      const { data: binding, error: bindErr } = await supabase
        .from('device_bindings')
        .select('key_id, install_id_hash, is_active')
        .eq('key_id', keyId)
        .maybeSingle();
      if (bindErr) throw bindErr;
      if (!binding) return res.status(403).json({ error: 'device_not_bound' });
      if (binding.is_active === false) return res.status(403).json({ error: 'device_binding_inactive' });
      if (String(binding.install_id_hash).toLowerCase() !== installIdHash) {
        return res.status(403).json({ error: 'install_id_mismatch' });
      }

      // 3. Upsert the monitor_devices row by (owner, fingerprint_hash).
      //    The install_id_hash IS the fingerprint hash — it's already a
      //    privacy-safe SHA-256 of a per-install random secret.
      const fpHash = installIdHash;
      let { data: existingDevice } = await supabase
        .from('monitor_devices')
        .select('id')
        .eq('owner_discord_user_id', owner)
        .eq('device_fingerprint_hash', fpHash)
        .maybeSingle();

      let deviceId;
      if (existingDevice) {
        deviceId = existingDevice.id;
        try {
          await supabase.from('monitor_devices').update({
            device_label: deviceLabel,
            tool_version: toolVersion,
            channel,
            updated_at: new Date().toISOString(),
          }).eq('id', deviceId);
        } catch (e) {
          console.warn('[monitor] device label/version refresh failed', e?.message || e);
        }
      } else {
        const ins = await supabase.from('monitor_devices').insert({
          owner_discord_user_id: owner,
          device_label: deviceLabel,
          device_fingerprint_hash: fpHash,
          tool_version: toolVersion,
          channel,
          status_connected: false, // becomes true on first /bridge/push
        }).select('id').single();
        if (ins.error) throw ins.error;
        deviceId = ins.data.id;
        try {
          await supabase.from('monitor_settings').upsert({
            monitor_device_id: deviceId,
          }, { onConflict: 'monitor_device_id' });
        } catch (e) {
          console.warn('[monitor] default settings insert failed', e?.message || e);
        }
      }

      // 4. Issue a fresh bridge token. Old tokens for this device are
      //    left to expire naturally (12h TTL) — the agent caches one and
      //    reissues on 401/expiry.
      const token = randomToken(32);
      const expiresAt = nowIso(BRIDGE_TOKEN_TTL_SEC);
      const tokIns = await supabase.from('monitor_bridge_tokens').insert({
        monitor_device_id: deviceId,
        token_hash: sha256(token),
        expires_at: expiresAt,
      });
      if (tokIns && tokIns.error) throw tokIns.error;

      return res.json({
        bridge_token: token,
        device_id: deviceId,
        expires_at: expiresAt,
      });
    } catch (err) {
      console.error('[monitor] bridge issue-from-license failed', err?.message || err);
      return serverError(res, 'bridge_issue_failed');
    }
  });

/**
 * POST /api/monitor/bridge/issue
 *   Body: { device_label?, device_fingerprint, tool_version?, channel? }
 *   Returns: { bridge_token, device_id, expires_at }
 *
 *   Called from the website (or from the Termux agent's first-run flow after
 *   the user logs in on the website and pastes their session — see /download
 *   instructions) to mint a short-lived bridge token tied to the user.
 */
router.post('/api/monitor/bridge/issue',
  pairingLimiter,
  requireLogin,
  monitorJsonParser,
  async (req, res) => {
    const owner = safeOwnerOf(req);
    if (!owner) return unauthorized(res);
    const body = req.body || {};
    const fp = typeof body.device_fingerprint === 'string' ? body.device_fingerprint.trim() : '';
    if (!fp || fp.length < 8 || fp.length > 128) return badRequest(res, 'device_fingerprint_required');
    const fpHash = sha256(fp);

    try {
      // Upsert device by (owner, fingerprint_hash)
      let { data: device } = await supabase
        .from('monitor_devices')
        .select('id')
        .eq('owner_discord_user_id', owner)
        .eq('device_fingerprint_hash', fpHash)
        .maybeSingle();
      if (!device) {
        const ins = await supabase.from('monitor_devices').insert({
          owner_discord_user_id: owner,
          device_label: typeof body.device_label === 'string' ? body.device_label.slice(0, 64) : 'Cloud Phone',
          device_fingerprint_hash: fpHash,
          tool_version: typeof body.tool_version === 'string' ? body.tool_version.slice(0, 32) : null,
          channel: typeof body.channel === 'string' && ['stable','beta','dev','latest','test'].includes(body.channel)
            ? body.channel : 'stable',
        }).select('id').single();
        if (ins.error) throw ins.error;
        device = ins.data;
        // Default settings
        await supabase.from('monitor_settings').upsert({
          monitor_device_id: device.id,
        }, { onConflict: 'monitor_device_id' });
      }

      const token = randomToken(32);
      await supabase.from('monitor_bridge_tokens').insert({
        monitor_device_id: device.id,
        token_hash: sha256(token),
        expires_at: nowIso(BRIDGE_TOKEN_TTL_SEC),
      });

      return res.json({
        bridge_token: token,
        device_id: device.id,
        expires_at: nowIso(BRIDGE_TOKEN_TTL_SEC),
      });
    } catch (err) {
      console.error('[monitor] bridge issue failed', err?.message || err);
      return serverError(res, 'bridge_issue_failed');
    }
  });

module.exports = router;
module.exports.__test__ = {
  sha256,
  randomPairingCode,
  summarizePackages,
  aggregatePackageSummary,
  connectionThresholds,
  connectionTtlSeconds,
  normalizeMonitorSettings,
  computeConnectionState,
  safePackageRowForApp,
  normalizeLicenseKey,
  LICENSE_KEY_PATTERN,
  INSTALL_ID_HASH_PATTERN,
  MAX_JSON_BYTES,
  MAX_SNAPSHOT_BYTES,
  BRIDGE_TOKEN_TTL_SEC,
  ALLOWED_SNAPSHOT_INTERVALS,
  DEVICE_CONNECTION_TTL_SECONDS,
};
