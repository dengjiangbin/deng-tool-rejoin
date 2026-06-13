'use strict';

const UPLOAD_POST_PATHS = new Set([
  '/api/fishit-tracker/update-backpack',
  '/api/fish-it-tracker/update-backpack',
  '/api/tracker/update-backpack',
  '/api/tracker/update-catalog',
]);

function isTrackerUploadPath(method, pathname) {
  if (String(method || '').toUpperCase() !== 'POST') return false;
  const path = String(pathname || '').split('?')[0];
  return UPLOAD_POST_PATHS.has(path);
}

module.exports = {
  UPLOAD_POST_PATHS,
  isTrackerUploadPath,
};
