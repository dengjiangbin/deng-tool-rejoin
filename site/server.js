'use strict';
require('dotenv').config();

const app = require('./src/app');

const HOST = process.env.TOOL_SITE_HOST || '127.0.0.1';
const PORT = parseInt(process.env.TOOL_SITE_PORT || '8791', 10);

const server = app.listen(PORT, HOST, () => {
  console.log(`[deng-tool-site] Listening on http://${HOST}:${PORT}`);
});

// Graceful shutdown
function shutdown(signal) {
  console.log(`[deng-tool-site] ${signal} received – shutting down`);
  server.close(() => {
    console.log('[deng-tool-site] HTTP server closed');
    process.exit(0);
  });
  setTimeout(() => process.exit(1), 10_000);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT',  () => shutdown('SIGINT'));
process.on('uncaughtException', (err) => {
  console.error('[deng-tool-site] Uncaught exception:', err);
  process.exit(1);
});
process.on('unhandledRejection', (reason) => {
  console.error('[deng-tool-site] Unhandled rejection:', reason);
  process.exit(1);
});
