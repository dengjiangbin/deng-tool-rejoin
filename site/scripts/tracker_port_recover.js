'use strict';

/**
 * One-shot recovery: stop tracker site/read, kill orphan listeners on
 * 8791/8793, restart under PM2, verify PM2 pid === port owner.
 *
 * Usage: node scripts/tracker_port_recover.js
 */

const { execSync } = require('child_process');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
const PORTS = [8791, 8793];
const APPS = [
  { name: 'deng-tool-site', port: 8791, eco: path.join(ROOT, 'ecosystem.site.json') },
  { name: 'deng-tracker-read', port: 8793, eco: path.join(ROOT, 'ecosystem.scale.json') },
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

console.log('Stopping deng-tool-site and deng-tracker-read...');
try { run('npx pm2 stop deng-tool-site deng-tracker-read'); } catch (_) { /* ok */ }

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
    run(`npx pm2 start "${app.eco}" --only ${app.name} --update-env`);
  } catch (e) {
    console.warn(`start failed for ${app.name}, trying delete+start...`);
    try { run(`npx pm2 delete ${app.name}`); } catch (_) { /* ok */ }
    run(`npx pm2 start "${app.eco}" --only ${app.name} --update-env`);
  }
  execSync('ping -n 12 127.0.0.1 > nul', { windowsHide: true });
}

try { run('npx pm2 reset deng-tool-site deng-tracker-read'); } catch (_) { /* ok */ }

const report = {};
for (const app of APPS) {
  const owner = findListenerPids(app.port)[0] || 0;
  const tracked = pm2Pid(app.name);
  report[app.name] = { port: app.port, pm2Pid: tracked, portOwner: owner, aligned: tracked === owner && tracked > 0 };
}
console.log(JSON.stringify(report, null, 2));
const ok = Object.values(report).every((r) => r.aligned);
process.exit(ok ? 0 : 1);
