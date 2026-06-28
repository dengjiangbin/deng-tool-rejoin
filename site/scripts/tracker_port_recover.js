'use strict';

/**
 * One-shot recovery: stop portal + tracker site/ingest/read/worker, kill orphan listeners on
 * 8790/8791/8792/8793, restart under PM2 with fresh ecosystem env, verify PID alignment.
 *
 * Usage: node scripts/tracker_port_recover.js
 */

const { execSync } = require('child_process');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
const PORTS = [8790, 8791, 8792, 8793];
const APPS = [
  { name: 'deng-portal-license', port: 8790, eco: path.join(ROOT, 'ecosystem.portal.json') },
  { name: 'deng-tracker-site', port: 8791, eco: path.join(ROOT, 'ecosystem.site.json') },
  { name: 'deng-tracker-ingest', port: 8792, eco: path.join(ROOT, 'ecosystem.site.json') },
  { name: 'deng-tracker-read', port: 8793, eco: path.join(ROOT, 'ecosystem.scale.json') },
  { name: 'deng-tracker-worker', port: null, eco: path.join(ROOT, 'ecosystem.scale.json') },
];

function run(cmd) {
  return execSync(cmd, { encoding: 'utf8', cwd: ROOT, windowsHide: true, timeout: 120000 });
}

function findListenerPids(port) {
  const out = run('netstat -ano -p TCP');
  const pids = new Set();
  const re = new RegExp(`\\sTCP\\s+\\S*:${port}\\s+\\S+\\s+LISTENING\\s+(\\d+)`, 'i');
  for (const line of out.split(/\r?\n/)) {
    const m = line.match(re);
    if (m) pids.add(parseInt(m[1], 10));
  }
  return [...pids];
}

function pm2Pid(name) {
  const list = JSON.parse(run('npx pm2 jlist'));
  const app = list.find((a) => a && a.name === name);
  return app && app.pid ? app.pid : 0;
}

function pm2Env(name, key) {
  const list = JSON.parse(run('npx pm2 jlist'));
  const app = list.find((a) => a && a.name === name);
  return app && app.pm2_env && app.pm2_env.env ? app.pm2_env.env[key] : undefined;
}

function killPort(port) {
  for (const pid of findListenerPids(port)) {
    try {
      run(`taskkill /F /PID ${pid}`);
      console.log(`killed pid ${pid} on port ${port}`);
    } catch (e) {
      console.warn(`taskkill ${pid} failed:`, e.message || e);
    }
  }
}

const stopNames = APPS.map((a) => a.name).join(' ');
console.log(`Stopping ${stopNames}...`);
try { run(`npx pm2 stop ${stopNames}`); } catch (_) { /* ok */ }
try { run('npx pm2 delete deng-tool-site'); } catch (_) { /* legacy name */ }

for (let i = 0; i < 5; i += 1) {
  let any = false;
  for (const port of PORTS) {
    const pids = findListenerPids(port);
    if (pids.length) {
      any = true;
      killPort(port);
    }
  }
  if (!any) break;
  execSync('ping -n 3 127.0.0.1 > nul', { windowsHide: true });
}

for (const app of APPS) {
  console.log(`Starting ${app.name}...`);
  try {
    run(`npx pm2 delete ${app.name}`);
  } catch (_) { /* ok if missing */ }
  run(`npx pm2 start "${app.eco}" --only ${app.name} --update-env`);
  execSync('ping -n 15 127.0.0.1 > nul', { windowsHide: true });
}

try { run(`npx pm2 reset ${stopNames}`); } catch (_) { /* ok */ }

const report = {};
for (const app of APPS) {
  if (!app.port) {
    report[app.name] = { pm2Pid: pm2Pid(app.name), coalesceMs: pm2Env('deng-tracker-ingest', 'TRACKER_UPLOAD_COALESCE_MS') };
    continue;
  }
  const owner = findListenerPids(app.port)[0] || 0;
  const tracked = pm2Pid(app.name);
  report[app.name] = {
    port: app.port,
    pm2Pid: tracked,
    portOwner: owner,
    aligned: tracked === owner && tracked > 0,
    coalesceMs: app.name === 'deng-tracker-ingest' ? pm2Env('deng-tracker-ingest', 'TRACKER_UPLOAD_COALESCE_MS') : undefined,
    enrichmentMax: app.name === 'deng-tracker-ingest' ? pm2Env('deng-tracker-ingest', 'TRACKER_ENRICHMENT_MAX_CONCURRENT') : undefined,
  };
}
console.log(JSON.stringify(report, null, 2));
const ok = APPS.filter((a) => a.port).every((a) => {
  const r = report[a.name];
  return r && r.aligned;
}) && report['deng-tracker-ingest'].coalesceMs === '3000';
process.exit(ok ? 0 : 1);
