'use strict';

function healthzPayload(service, port) {
  return JSON.stringify({
    status: 'ok',
    service,
    port: Number(port),
    probe: 'healthz',
    timestamp: new Date().toISOString(),
  });
}

function sendHealthz(res, service, port) {
  res.writeHead(200, {
    'Content-Type': 'application/json',
    'Cache-Control': 'no-store',
    'X-DENG-Health-Probe': 'healthz',
  });
  res.end(healthzPayload(service, port));
}

function mountHealthz(app, service, port) {
  app.get('/healthz', (_req, res) => {
    res.set('Cache-Control', 'no-store');
    res.set('X-DENG-Health-Probe', 'healthz');
    res.json({
      status: 'ok',
      service,
      port: Number(port),
      probe: 'healthz',
      timestamp: new Date().toISOString(),
    });
  });
}

module.exports = {
  healthzPayload,
  sendHealthz,
  mountHealthz,
};
