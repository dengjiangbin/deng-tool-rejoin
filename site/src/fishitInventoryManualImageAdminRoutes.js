'use strict';

const express = require('express');
const rateLimit = require('express-rate-limit');
const manualImages = require('./fishitInventoryManualImages');

const router = express.Router();

const adminLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 60,
  skip: () => process.env.NODE_ENV === 'test',
  standardHeaders: true,
  legacyHeaders: false,
});

function requireAdmin(req, res, next) {
  const token = process.env.FISHIT_GLOBAL_ADMIN_TOKEN
    || process.env.TOOL_SITE_ADMIN_TOKEN;
  const provided = req.headers['x-admin-token']
    || req.query.admin_token
    || req.body?.admin_token;
  if (!token || provided !== token) {
    return res.status(401).json({ ok: false, error: 'unauthorized' });
  }
  return next();
}

router.use(adminLimiter);

router.get('/admin/fishit-inventory-images', requireAdmin, (req, res) => {
  const category = req.query.category || '';
  res.render('fishit_inventory_manual_images_admin', {
    layout: false,
    title: 'Fish It Inventory Manual Images',
    category,
    overrides: manualImages.listManualOverrides(category || null),
    categories: ['totems', 'stones', 'fish', 'item'],
    adminToken: req.query.admin_token || '',
  });
});

router.get('/api/fishit-tracker/admin/inventory-manual-images', requireAdmin, (req, res) => {
  return res.json({
    ok: true,
    overrides: manualImages.listManualOverrides(req.query.category || null),
    catalogPath: manualImages.DATA_PATH,
    cacheDir: manualImages.CACHE_DIR,
  });
});

router.get('/api/fishit-tracker/admin/inventory-missing-images/:username', requireAdmin, async (req, res) => {
  try {
    const fs = require('fs');
    const gameItemDbPublic = require('./fishitGameItemDbPublic');
    const fishImageCache = require('./fishitFishImageCache');
    const sessionStore = require('./fishitSessionStore');
    const clean = String(req.params.username || '').trim().toLowerCase();
    let sessionData = null;
    try {
      const raw = JSON.parse(fs.readFileSync(sessionStore.STORE_PATH, 'utf8'));
      sessionData = raw?.sessions?.[clean] || null;
    } catch (_) {
      sessionData = null;
    }
    if (!sessionData) {
      return res.status(404).json({ ok: false, error: 'session_not_found' });
    }
    const baseUrl = `${req.protocol}://${req.get('host')}`;
    const publicData = await gameItemDbPublic.buildPublicFromPlayerDataGameItemDb(sessionData, baseUrl, {
      fishImageCache,
    });
    const fish = manualImages.buildMissingImageDebugList(publicData.fishItems || [], 'fish');
    const stones = manualImages.buildMissingImageDebugList(publicData.stoneItems || [], 'stones');
    const totems = manualImages.buildMissingImageDebugList(publicData.totemItems || [], 'totems');
    return res.json({
      ok: true,
      username: clean,
      missing: { fish, stones, totems, total: fish.length + stones.length + totems.length },
      resolved: {
        fish: (publicData.fishItems || []).filter((i) => i.imageResolved).length,
        stones: (publicData.stoneItems || []).filter((i) => i.imageResolved).length,
        totems: (publicData.totemItems || []).filter((i) => i.imageResolved).length,
      },
      manualImageProof: publicData.manualImageProof || null,
    });
  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message });
  }
});

router.post('/api/fishit-tracker/admin/inventory-image-upload', requireAdmin, express.json({ limit: '12mb' }), (req, res) => {
  try {
    const entry = manualImages.upsertManualOverride({
      category: req.body?.category,
      itemId: req.body?.itemId,
      name: req.body?.name || req.body?.originalName,
      imageBase64: req.body?.imageBase64 || req.body?.imageData,
      mimeType: req.body?.mimeType || req.body?.contentType,
      admin_token: req.body?.admin_token,
    });
    const baseUrl = `${req.protocol}://${req.get('host')}`;
    return res.json({
      ok: true,
      entry: {
        ...entry,
        imageUrl: manualImages.buildManualImageUrl(baseUrl, entry.category, entry.uploadedFile),
        imageSource: manualImages.MANUAL_OVERRIDE_SOURCE,
        imageResolved: true,
      },
    });
  } catch (err) {
    const code = ['category_required', 'item_id_or_name_required', 'image_required', 'unsupported_image_type']
      .includes(err.message) ? 400 : 500;
    return res.status(code).json({ ok: false, error: err.message });
  }
});

module.exports = router;
