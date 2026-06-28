'use strict';

/**
 * Tracker frontend Express app (8791): home, /tracker, static assets, tracker APIs
 * that Cloudflare still routes here. No license/auth/ad routes.
 */
process.env.SITE_APP_MODE = 'tracker';
process.env.TRACKER_WEB_MODE = '0';
process.env.SKIP_TRACKER_UPLOAD_ROUTES = '1';

const { createSiteExpressApp, mountStandardBodyParsers } = require('./siteAppCommon');
const publicRoutes = require('./publicRoutes');
const monitorRoutes = require('./monitorRoutes');
const fishitRoutes = require('./fishitRoutes');
const fishitTrackerRoutes = require('./fishitTrackerRoutes');
const fishitGlobalAdminRoutes = require('./fishitGlobalAdminRoutes');
const fishitInventoryManualImageAdminRoutes = require('./fishitInventoryManualImageAdminRoutes');

const PORT = Number(process.env.TOOL_SITE_PORT || 8791);
const app = createSiteExpressApp({
  service: 'deng-tracker-site',
  port: PORT,
  includeTrackerViewAssets: true,
  stabilityRoutes: true,
});

app.use('/', require('./oauthRoutes'));
app.use('/', fishitTrackerRoutes);
app.use('/', fishitGlobalAdminRoutes);
app.use('/', fishitInventoryManualImageAdminRoutes);
mountStandardBodyParsers(app);
app.use('/', monitorRoutes);
app.use('/', require('./aioRoutes'));
app.use('/', fishitRoutes);
app.use('/', publicRoutes);

app.use((_req, res) => {
  res.status(404).render('error', { code: 404, message: 'Page not found.' });
});

// eslint-disable-next-line no-unused-vars
app.use((err, req, res, _next) => {
  console.error('[deng-tracker-site] Unhandled error:', err);
  if (err.type === 'entity.too.large' && req.path.startsWith('/api/')) {
    return res.status(413).json({
      ok: false,
      error: 'payload_too_large',
      limit: err.limit || '16kb',
    });
  }
  const code = err.status || 500;
  res.status(code).render('error', { code, message: 'An unexpected error occurred.' });
});

module.exports = app;
