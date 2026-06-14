'use strict';

/**
 * Production runtime guard — blocks child_process from spawning wmic.exe.
 * Load this module before any other project code in PM2 Node entrypoints.
 */

const path = require('path');
const cp = require('child_process');

const GUARD_MARKER = 'NO_WMIC_PROCESS_GUARD_2026_06_14';
const BLOCK_CODE = 'WMIC_BLOCKED_RUNTIME_GUARD';

const WMIC_NAMES = new Set(['wmic', 'wmic.exe']);

function shouldActivateGuard() {
  if (process.env.WMIC_RUNTIME_GUARD === '1') return true;
  if (process.env.WMIC_RUNTIME_GUARD === '0') return false;
  return process.env.NODE_ENV === 'production';
}

function normalizePath(file) {
  return String(file || '')
    .trim()
    .replace(/^"+|"+$/g, '')
    .replace(/\\/g, '/')
    .toLowerCase();
}

function isWmicTarget(fileOrCommand) {
  const raw = String(fileOrCommand || '').trim();
  if (!raw) return false;
  const base = path.basename(raw.replace(/^"+|"+$/g, '')).toLowerCase();
  if (WMIC_NAMES.has(base)) return true;
  const normalized = normalizePath(raw);
  return normalized.endsWith('/system32/wbem/wmic.exe') || normalized.includes('/wmic.exe');
}

function firstCommandToken(command) {
  const trimmed = String(command || '').trim();
  if (!trimmed) return '';
  if (trimmed.startsWith('"')) {
    const end = trimmed.indexOf('"', 1);
    return end > 0 ? trimmed.slice(1, end) : trimmed;
  }
  return trimmed.split(/\s+/)[0];
}

function blockIfWmic(fileOrCommand, method) {
  if (!isWmicTarget(fileOrCommand) && !isWmicTarget(firstCommandToken(fileOrCommand))) return;
  const err = new Error(`${BLOCK_CODE}: spawning wmic.exe is permanently disabled (${method})`);
  err.code = BLOCK_CODE;
  console.error(`[wmic-guard] ${BLOCK_CODE} blocked ${method} target=${fileOrCommand}`);
  console.error(err.stack);
  throw err;
}

function wrapSpawn(original, method) {
  return function guardedSpawn(file, args, options) {
    blockIfWmic(file, method);
    return original.call(this, file, args, options);
  };
}

function wrapExec(original, method) {
  return function guardedExec(command, options, callback) {
    blockIfWmic(command, method);
    return original.call(this, command, options, callback);
  };
}

function wrapExecFile(original, method) {
  return function guardedExecFile(file, args, options, callback) {
    blockIfWmic(file, method);
    return original.call(this, file, args, options, callback);
  };
}

function wrapFork(original) {
  return function guardedFork(modulePath, args, options) {
    blockIfWmic(modulePath, 'fork');
    return original.call(this, modulePath, args, options);
  };
}

function installWmicRuntimeGuard() {
  if (!shouldActivateGuard()) return { installed: false, marker: GUARD_MARKER };
  if (cp.__dengWmicGuardInstalled) return { installed: true, marker: GUARD_MARKER, already: true };

  cp.spawn = wrapSpawn(cp.spawn, 'spawn');
  cp.spawnSync = wrapSpawn(cp.spawnSync, 'spawnSync');
  cp.exec = wrapExec(cp.exec, 'exec');
  cp.execSync = wrapExec(cp.execSync, 'execSync');
  cp.execFile = wrapExecFile(cp.execFile, 'execFile');
  cp.execFileSync = wrapExecFile(cp.execFileSync, 'execFileSync');
  cp.fork = wrapFork(cp.fork);

  cp.__dengWmicGuardInstalled = true;
  return { installed: true, marker: GUARD_MARKER };
}

module.exports = {
  GUARD_MARKER,
  BLOCK_CODE,
  installWmicRuntimeGuard,
  isWmicTarget,
  shouldActivateGuard,
};

installWmicRuntimeGuard();
