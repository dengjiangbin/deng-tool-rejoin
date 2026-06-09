'use strict';
/**
 * BLOCKER10V — admin routes for global Fish It catalog management.
 */

const express = require('express');
const rateLimit = require('express-rate-limit');
const globalDb = require('./fishitGlobalDb');
const globalCatalogService = require('./fishitGlobalCatalogService');

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

router.get('/admin/fishit-global', requireAdmin, (req, res) => {
  res.render('fishit_global_admin', {
    layout: false,
    title: 'Fish It Global Catalog Admin',
    stats: globalDb.getStats(),
    conflicts: globalDb.listConflicts(25),
    mappings: globalDb.listMappings(25),
    species: globalDb.listSpecies(50),
    unresolved: globalDb.listUnresolvedSpecies(25),
    importProof: globalCatalogService.buildQuizBotSeedImportProof(),
  });
});

router.get('/api/fishit-global/stats', requireAdmin, (_req, res) => {
  res.json({
    ok: true,
    stats: globalDb.getStats(),
    importProof: globalCatalogService.buildQuizBotSeedImportProof(),
  });
});

router.post('/api/fishit-global/import-quiz-bot', requireAdmin, async (_req, res) => {
  try {
    const result = await globalCatalogService.importQuizBotSeed();
    return res.json({ ok: result.ok, result });
  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message });
  }
});

router.post('/api/fishit-global/species/:id/verify', requireAdmin, (req, res) => {
  const id = Number(req.params.id);
  if (!Number.isFinite(id)) return res.status(400).json({ ok: false, error: 'invalid_id' });
  globalDb.setSpeciesManualVerified(id, {
    canonical_name: req.body?.canonical_name,
    rarity: req.body?.rarity,
    rarity_source: 'manual_verified',
  });
  return res.json({ ok: true, species: globalDb.getSpeciesById(id) });
});

router.post('/api/fishit-global/species/:id/rarity', requireAdmin, (req, res) => {
  const id = Number(req.params.id);
  const rarity = String(req.body?.rarity || '').trim();
  if (!Number.isFinite(id) || !rarity) {
    return res.status(400).json({ ok: false, error: 'invalid_input' });
  }
  globalDb.updateSpeciesRarity(id, rarity, req.body?.rarity_source || 'manual_verified');
  return res.json({ ok: true, species: globalDb.getSpeciesById(id) });
});

router.get('/api/fishit-global/unresolved', requireAdmin, (_req, res) => {
  res.json({ ok: true, unresolved: globalDb.listUnresolvedSpecies(100) });
});

router.post('/api/fishit-global/mapping/:itemId/quarantine', requireAdmin, (req, res) => {
  const itemId = String(req.params.itemId || '').trim();
  if (!itemId) return res.status(400).json({ ok: false, error: 'invalid_item_id' });
  globalDb.quarantineMapping(itemId, req.body?.reason || 'admin_quarantine');
  return res.json({ ok: true, mapping: globalDb.getItemMapping(itemId) });
});

router.post('/api/fishit-global/admin/mappings/approve', requireAdmin, (req, res) => {
  const result = globalCatalogService.approveItemMapping(req.body || {});
  if (!result.ok) {
    return res.status(result.error === 'species_not_found' ? 404 : 400).json(result);
  }
  return res.json(result);
});

router.get('/api/fishit-global/conflicts', requireAdmin, (_req, res) => {
  res.json({ ok: true, conflicts: globalDb.listConflicts(50) });
});

module.exports = router;
