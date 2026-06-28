'use strict';

/**
 * Structured access log for diagnosing 502/5xx (route, service, ms, status).
 */
function logRequestFinish(service, req, res, startedMs, extra = {}) {
  const pathOnly = String(req.url || '').split('?')[0];
  const ms = Date.now() - startedMs;
  const status = res.statusCode || 0;
  const level = status >= 500 ? 'error' : (status >= 400 ? 'warn' : 'info');
  const payload = {
    service,
    method: req.method,
    path: pathOnly,
    status,
    ms,
    ...extra,
  };
  const line = `[access] ${JSON.stringify(payload)}`;
  if (level === 'error') console.error(line);
  else if (level === 'warn') console.warn(line);
  else if (ms > 500 || status >= 400) console.warn(line);
}

function attachAccessLog(req, res, service, startedMs, extra) {
  const done = () => logRequestFinish(service, req, res, startedMs, extra);
  res.once('finish', done);
  res.once('close', done);
}

function wrapHttpHandler(service, handler, extraFn) {
  return function loggedHandler(req, res) {
    const started = Date.now();
    const extra = typeof extraFn === 'function' ? extraFn(req) : {};
    attachAccessLog(req, res, service, started, extra);
    return handler(req, res);
  };
}

function expressAccessLogMiddleware(service) {
  return (req, res, next) => {
    const started = Date.now();
    attachAccessLog(req, res, service, started, {
      via: 'express',
    });
    next();
  };
}

module.exports = {
  logRequestFinish,
  attachAccessLog,
  wrapHttpHandler,
  expressAccessLogMiddleware,
};
