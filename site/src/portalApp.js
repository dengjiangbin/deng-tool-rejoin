'use strict';

/**
 * Portal-only Express app (8790): license, auth, ad completion, login.
 * Must NOT mount fishitTrackerRoutes or tracker proxy middleware.
 */
process.env.SITE_APP_MODE = 'portal';
process.env.PORTAL_MODE = '1';
process.env.TRACKER_WEB_MODE = '0';
process.env.SKIP_TRACKER_UPLOAD_ROUTES = '1';

const { createSiteExpressApp, mountStandardBodyParsers } = require('./siteAppCommon');
const routes = require('./routes');
const aioRoutes = require('./aioRoutes');
const portalPublicRoutes = require('./portalPublicRoutes');

const PORT = Number(process.env.PORTAL_PORT || process.env.TOOL_SITE_PORT || 8790);
const app = createSiteExpressApp({
  service: 'deng-portal-license',
  port: PORT,
  includeTrackerViewAssets: false,
  stabilityRoutes: false,
});

app.use('/', require('./oauthRoutes'));
mountStandardBodyParsers(app);
app.use('/', portalPublicRoutes);
app.use('/', aioRoutes);
app.use('/', routes);

app.use((_req, res) => {
  res.status(404).render('error', { code: 404, message: 'Page not found.' });
});

// eslint-disable-next-line no-unused-vars
app.use((err, req, res, _next) => {
  console.error('[deng-portal-license] Unhandled error:', err);
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
