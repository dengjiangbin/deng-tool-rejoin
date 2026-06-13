'use strict';

const express = require('express');
const { buildStabilityStatus } = require('./stabilityStatus');

const router = express.Router();

router.get('/api/internal/stability', (req, res) => {
  const token = process.env.STABILITY_STATUS_TOKEN || '';
  if (token) {
    const provided = String(req.headers['x-stability-token'] || req.query.token || '');
    if (provided !== token) {
      return res.status(403).json({ ok: false, error: 'forbidden' });
    }
  }
  res.set('Cache-Control', 'no-store');
  return res.json(buildStabilityStatus());
});

module.exports = router;
