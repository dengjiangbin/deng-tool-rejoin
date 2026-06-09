'use strict';
// Load env in priority order (dotenv never overrides already-set vars):
//   1. process.env (always wins – PM2 / system env)
//   2. site/.env   (portal-specific overrides)
//   3. ../.env     (project root – shared Discord/Supabase credentials)
//   4. ../env      (same root, alternate filename some setups use)
const path   = require('path');
const dotenv = require('dotenv');
dotenv.config({ path: path.join(__dirname, '.env') });
dotenv.config({ path: path.join(__dirname, '..', '.env') });
dotenv.config({ path: path.join(__dirname, '..', 'env') });

const app = require('./src/app');
const { isStateSecretConfigured } = require('./src/crypto');

if (!isStateSecretConfigured()) {
  console.error(
    '[deng-tool-site] FATAL: TOOL_SITE_STATE_SECRET is missing or shorter than 32 characters. '
    + 'License key provider redirects cannot be signed until this is set in .env',
  );
  process.exit(1);
}

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
