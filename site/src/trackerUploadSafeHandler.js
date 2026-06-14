'use strict';

const { recordUploadRequest } = require('./trackerUploadRequestMetrics');

function safeTrackerUploadHandler(label, handler) {
  return function safeUploadEntry(req, res) {
    try {
      const result = handler(req, res);
      if (result && typeof result.then === 'function') {
        return result.catch((err) => handleUploadError(req, res, label, err));
      }
      return result;
    } catch (err) {
      return handleUploadError(req, res, label, err);
    }
  };
}

function handleUploadError(req, res, label, err) {
  if (res.headersSent) {
    console.error('[fishit-tracker] upload error after response label=%s err=%s', label, err?.message || err);
    return undefined;
  }
  console.error('[fishit-tracker] upload handler error label=%s err=%s', label, err?.stack || err?.message || err);
  recordUploadRequest({
    route: req.path,
    payloadType: req.body?.type || 'unknown',
    usernameKey: req.body?.username ? String(req.body.username).toLowerCase() : '?',
    contentLength: Number(req.headers['content-length'] || 0),
    durationMs: 0,
    statusCode: 202,
    accepted: true,
    rejectReason: 'upload_processing_error',
    errorClass: 'server_recovered',
  });
  return res.status(202).json({
    ok: true,
    accepted: true,
    deferred: true,
    retryable: false,
    error: 'upload_processing_deferred',
    message: 'Upload received; last valid session data preserved.',
    detail: String(err?.message || 'processing_error').slice(0, 200),
  });
}

module.exports = {
  safeTrackerUploadHandler,
};
