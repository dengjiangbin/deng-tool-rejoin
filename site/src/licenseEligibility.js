'use strict';
/**
 * Central license key generation eligibility (shared by website, API, admin debug).
 */

const challenge = require('./challenge');
const licenseService = require('./licenseService');

const BLOCK_MESSAGES = {
  active_unredeemed_key: 'You already have an active key. Copy it and use it in DENG Tool Rejoin, or wait until it expires.',
  cooldown_active: 'Please wait before generating another key.',
  auth_required: 'Please login with Discord first.',
  ad_completion_required: 'Complete the ad step before a key can be generated.',
  provider_attempt_invalid: 'Please start key generation again.',
  no_provider_configured: 'No ad provider is configured yet.',
  server_error: 'Could not check key generation eligibility.',
};

function maskKeyId(id) {
  const raw = String(id || '');
  if (raw.length <= 8) return raw ? `${raw.slice(0, 4)}…` : null;
  return `${raw.slice(0, 8)}…`;
}

function countByLifecycle(rows) {
  const counts = {
    activeUnredeemed: 0,
    expiredUnredeemed: 0,
    redeemed: 0,
    revoked: 0,
  };
  for (const row of rows || []) {
    const lc = licenseService.classifyLicenseLifecycle(row);
    if (lc.is_unredeemed) counts.activeUnredeemed += 1;
    else if (lc.is_expired && !row.redeemed_at && !lc.is_bound) counts.expiredUnredeemed += 1;
    else if (lc.is_revoked) counts.revoked += 1;
    else if (lc.is_redeemed) counts.redeemed += 1;
  }
  return counts;
}

async function getProviderAttemptStatus(siteUserId) {
  if (!siteUserId) {
    return { status: 'none', blocking: false, challengeId: null };
  }
  return challenge.getLatestProviderAttemptStatus(siteUserId);
}

/**
 * Single source of truth for whether a user may start key generation.
 */
async function getLicenseGenerationEligibility({
  discordUserId = '',
  siteUserId = '',
  skipProviderCheck = false,
} = {}) {
  const nowIso = new Date().toISOString();
  const queryFilters = {
    owner_discord_id: discordUserId || null,
    site_user_id: siteUserId || null,
    active_unredeemed: 'redeemed_at IS NULL AND revoked/expired/deleted excluded AND expires_at > now',
    expired_unredeemed: 'redeemed_at IS NULL AND expires_at <= now (ignored for blocking)',
    max_key_policy: 'license_key_limits scope user|global',
  };

  await licenseService.markExpiredUnredeemedKeys({ discordUserId, siteUserId });
  const rows = await licenseService.getPortalUserLicenses({
    discordUserId,
    siteUserId,
    limit: 500,
  });

  const activeUnredeemed = rows.filter(licenseService.isActiveUnredeemedKey);
  const lifecycleCounts = countByLifecycle(rows);
  const [limitCheck, cooldown, providerAttempt] = await Promise.all([
    licenseService.canUserReceiveNewKey(discordUserId, siteUserId),
    siteUserId ? challenge.checkCooldown(siteUserId) : { allowed: true, secondsLeft: 0, cooldownUntil: null },
    skipProviderCheck ? Promise.resolve({ status: 'skipped', blocking: false }) : getProviderAttemptStatus(siteUserId),
  ]);

  const maxKeys = limitCheck.maxKeys;
  const activeCount = limitCheck.activeCount;

  let canGenerate = true;
  let blockReason = null;
  let message = null;
  let remainingSeconds = 0;
  let expiresAt = null;

  if (activeUnredeemed.length > 0) {
    canGenerate = false;
    blockReason = 'active_unredeemed_key';
    message = BLOCK_MESSAGES.active_unredeemed_key;
    const hit = activeUnredeemed[0];
    expiresAt = hit.expires_at || hit.key_expires_at || null;
    if (expiresAt) {
      const expMs = Date.parse(expiresAt);
      if (Number.isFinite(expMs)) {
        remainingSeconds = Math.max(0, Math.ceil((expMs - Date.now()) / 1000));
      }
    }
  } else if (!cooldown.allowed) {
    canGenerate = false;
    blockReason = 'cooldown_active';
    message = BLOCK_MESSAGES.cooldown_active;
    remainingSeconds = Math.max(0, Number(cooldown.secondsLeft) || 0);
  } else if (providerAttempt.blocking) {
    canGenerate = false;
    blockReason = providerAttempt.blockReason || 'provider_attempt_invalid';
    message = BLOCK_MESSAGES[blockReason] || BLOCK_MESSAGES.provider_attempt_invalid;
  }

  const result = {
    canGenerate,
    blockReason,
    message: message || null,
    remainingSeconds,
    expiresAt,
    cooldownUntil: cooldown.cooldownUntil || null,
    now: nowIso,
    discordUserId: discordUserId || null,
    siteUserId: siteUserId || null,
    activeUnredeemedCount: activeUnredeemed.length,
    activeUnredeemedKeys: activeUnredeemed.map((row) => ({
      id: maskKeyId(row.id),
      masked_key: row.masked_key || licenseService.classifyLicenseLifecycle(row).display_status,
      expires_at: row.expires_at || row.key_expires_at || null,
    })),
    expiredUnredeemedCount: lifecycleCounts.expiredUnredeemed,
    redeemedKeysCount: lifecycleCounts.redeemed,
    revokedKeysCount: lifecycleCounts.revoked,
    activeKeySlotCount: activeCount,
    maxKeyPolicyUsed: maxKeys,
    maxKeyPolicySource: 'license_key_limits',
    providerAttemptStatus: providerAttempt.status,
    providerAttemptBlocking: providerAttempt.blocking === true,
    providerAttemptChallengeId: providerAttempt.challengeId || null,
    queryFilters,
  };

  if (process.env.NODE_ENV !== 'test') {
    console.log(
      '[license/eligibility] discord=%s site=%s canGenerate=%s blockReason=%s activeUnredeemed=%d activeSlots=%d/%d cooldownLeft=%s',
      discordUserId || '-',
      siteUserId || '-',
      canGenerate,
      blockReason || '-',
      activeUnredeemed.length,
      activeCount,
      maxKeys,
      cooldown.allowed ? '0' : String(remainingSeconds),
    );
  }

  return result;
}

function messageForBlockReason(blockReason, fallbackSeconds = 0) {
  const base = BLOCK_MESSAGES[blockReason] || BLOCK_MESSAGES.server_error;
  if (blockReason === 'cooldown_active' && fallbackSeconds > 0) {
    return `${base} Try again in ${fallbackSeconds}s.`;
  }
  if (blockReason === 'active_unredeemed_key' && fallbackSeconds > 0) {
    return `${base} Expires in ${Math.ceil(fallbackSeconds / 60)} min.`;
  }
  return base;
}

module.exports = {
  BLOCK_MESSAGES,
  getLicenseGenerationEligibility,
  messageForBlockReason,
};
