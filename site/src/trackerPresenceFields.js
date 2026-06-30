'use strict';

/** Presence/age fields written on every heartbeat and merged from disk sidecars. */
const PRESENCE_DISK_FIELDS = [
  'isOnline', 'trackerBuild', 'lastUploadTrackerBuild',
  'lastAccountSeenAt', 'lastValidStatusAt', 'lastSuccessfulUploadAt',
  'lastSuccessfulHeartbeatAt', 'lastHeartbeatAt', 'lastUploadReceivedAt',
  'lastUploadAcceptedAt', 'lastSeenAt', 'lastSnapshotUploadAt', 'lastInventoryAt',
  'lastStatsUploadAt', 'leaderstatsUploadedAt', 'lastOfflineAt', 'lastFailureReason', 'lastUploadRejectReason',
  'rejectReason', 'lastUploadStatusCodeReturned', 'lastUploadHttpStatus',
  'lastRealRobloxStatusAt', 'statusRevision', 'statusReportId', 'statusSeq',
  'statusSessionId', 'statusCapturedAt', 'statusSentAt', 'serverReceivedStatusAt',
  'statusIdentityReason',
  'lastRealLeaderstatsAt', 'leaderstatsRevision', 'leaderstatsReportId', 'leaderstatsSeq',
  'lastRealInventoryAt', 'inventoryRevision', 'inventoryReportId', 'inventorySeq', 'inventoryHash',
  'reportIdentitySource', 'leaderstatsIdentitySource', 'inventoryIdentitySource',
];

module.exports = { PRESENCE_DISK_FIELDS };
