'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const cp = require('child_process');

process.env.WMIC_RUNTIME_GUARD = '1';
delete require.cache[require.resolve('../src/wmicRuntimeGuard')];
const guard = require('../src/wmicRuntimeGuard');

describe('wmic runtime guard', () => {
  beforeEach(() => {
    guard.installWmicRuntimeGuard();
  });

  test('spawn wmic.exe throws WMIC_BLOCKED_RUNTIME_GUARD', () => {
    assert.throws(
      () => cp.spawnSync('wmic.exe', ['logicaldisk', 'get', 'caption']),
      (err) => err && err.code === guard.BLOCK_CODE,
    );
  });

  test('execFile wmic throws WMIC_BLOCKED_RUNTIME_GUARD', () => {
    assert.throws(
      () => cp.execFileSync('wmic', ['logicaldisk', 'get', 'caption'], { timeout: 1000 }),
      (err) => err && err.code === guard.BLOCK_CODE,
    );
  });

  test('exec wmic command string throws WMIC_BLOCKED_RUNTIME_GUARD', () => {
    assert.throws(
      () => cp.execSync('wmic logicaldisk get caption', { timeout: 1000 }),
      (err) => err && err.code === guard.BLOCK_CODE,
    );
  });

  test('powershell spawn is not blocked', () => {
    const result = cp.spawnSync(
      process.platform === 'win32' ? 'powershell.exe' : 'sh',
      process.platform === 'win32'
        ? ['-NoProfile', '-Command', 'Write-Output ok']
        : ['-c', 'echo ok'],
      { encoding: 'utf8', timeout: 10_000 },
    );
    assert.equal(result.status, 0);
    assert.match(String(result.stdout || ''), /ok/i);
  });
});

describe('disk monitor without wmic', () => {
  test('getDiskFreeStatus uses statfs not wmic', () => {
    const diskMonitor = require('../src/diskMonitor');
    diskMonitor._resetCacheForTests();
    const disk = diskMonitor.getDiskFreeStatusFresh();
    assert.ok(Array.isArray(disk.drives));
    assert.notEqual(disk.source, 'wmic');
    assert.equal(disk.source, 'statfs');
    const cDrive = disk.drives.find((d) => d.drive === 'C:');
    if (cDrive) {
      assert.ok(['ok', 'warning', 'critical'].includes(cDrive.level));
      assert.ok(cDrive.freeBytes > 0);
    }
  });

  test('repeated disk lookups do not spawn wmic', () => {
    const diskMonitor = require('../src/diskMonitor');
    diskMonitor._resetCacheForTests();
    for (let i = 0; i < 5; i += 1) {
      const disk = diskMonitor.getDiskFreeStatus();
      assert.notEqual(disk.source, 'wmic');
    }
    assert.throws(
      () => cp.spawnSync('wmic.exe', ['logicaldisk', 'get', 'caption']),
      (err) => err && err.code === guard.BLOCK_CODE,
    );
  });
});
