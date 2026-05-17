# Live test loop — break the "tool still acts old" cycle

This file explains the three things you can use to close the loop between
the cloud phone and the operator without endless round-trips.

The fundamental problem we hit in every previous round:

1. Operator writes code that *should* parse phone output.
2. Operator unit-tests it against what they *imagine* the phone returns.
3. Real phone returns something slightly different.
4. Tool silently degrades; tests still pass.
5. New build deploys; phone still wrong.

The only way out is to capture the *real* output from the *real* phone and
fix the code against real bytes. This doc gives you three escalating ways
to do that.

---

## Option 1 — `deng-rejoin probe` (paste in chat)

Captures everything an operator needs to reason about layout and presence:

* Build proof: `BUILD-INFO.json`, `.installed-build.json`, version output
* Device: model, brand, Android release/SDK, root status, screen size & density
* Settings: `settings list global/secure/system` filtered to layout-relevant keys
* Verbs: `cmd activity help`, `am help`, `cmd window help` — so we see what
  this OEM actually supports for `resize-task`, launch bounds, etc.
* Per-package:
  * `pidof`, `pgrep -f`, `ps -A`, `/proc/*/cmdline` (root)
  * `dumpsys window windows`, `dumpsys activity activities`,
    `dumpsys activity recents`, `dumpsys SurfaceFlinger --list`
  * Every `shared_prefs/*.xml` (root) — masked for secrets
  * Roblox presence API result
* Logs: last 200 lines of `agent.log`
* Last Start self-diagnostics

Secrets are **always** masked (cookies, license keys, Discord webhooks,
HMAC signing keys, bearer tokens, `ROBLOSECURITY`). Private server URLs
are kept because we need them to reason about join state.

### Run it

```bash
deng-rejoin probe
# probe saved: ~/.deng-tool/rejoin/data/probes/probe-20260517T003314Z-a1b2c3d4ef.json (118.4 KB, 2 step errors, 9.3s)
# to share, either paste the JSON file in chat, or run:
#   deng-rejoin probe --upload
```

Then `cat` the file and paste its content in chat. The operator reads
real bytes and writes a real fix.

## Option 2 — `deng-rejoin probe --upload` (no paste)

Same probe, but POSTs the JSON to the PM2 server at `/api/dev-probe/upload`
(gzip-compressed, ≤4 MB request / ≤16 MB after decompress). Returns a
short id you paste in chat:

```bash
deng-rejoin probe --upload
# probe saved: ~/.deng-tool/rejoin/data/probes/probe-20260517T003314Z-a1b2c3d4ef.json (118.4 KB, 2 step errors, 9.3s)
# uploading...
# probe_id: p-9f2a83bc91
# share this id in chat.
```

The operator reads it on the PM2 host:

```bash
# on the server (PM2 host)
cat ~/Desktop/DENG\ Tool\ Rejoin/data/dev_probes/p-9f2a83bc91.json
# or via the panel API:
curl -s http://127.0.0.1:8787/api/dev-probe/p-9f2a83bc91 | jq .
```

Storage rotates at 200 probes (oldest deleted first).

## Option 3 — Live SSH (reverse tunnel through the panel host)

Cloud phones are usually behind NAT, so we use an **outbound** SSH from
the phone to the panel host. The operator then connects locally on the
panel host.

### On the cloud phone (one-time):

```bash
pkg update -y && pkg install -y openssh termux-services
# generate a key pair for the phone
ssh-keygen -t ed25519 -f $HOME/.ssh/deng_phone -N ""
# print public key — paste this into the panel host's authorized_keys
cat $HOME/.ssh/deng_phone.pub
```

### On the panel host (one-time):

```powershell
# add the phone's pubkey to a dedicated user
# (do NOT use admin / root; create a tunnel-only user)
# Windows OpenSSH server:
# C:\ProgramData\ssh\authorized_keys (system-wide), or per-user
# ~\.ssh\authorized_keys
```

Restrict the key to tunnel-only:

```
no-pty,no-X11-forwarding,no-agent-forwarding,permitopen="127.0.0.1:22" ssh-ed25519 AAAA... deng-phone
```

### On the cloud phone (every session):

```bash
sshd  # start local sshd in Termux
# Open the reverse tunnel: phone:22 → panel-host:2222
ssh -i $HOME/.ssh/deng_phone -N -R 2222:127.0.0.1:8022 \
    tunnel-user@<panel-host-public-ip>
```

### On the panel host (operator side):

```powershell
ssh -p 2222 <termux-user>@127.0.0.1
```

The operator now has an interactive shell on the phone and can run
`dumpsys`, `cmd activity resize-task`, `settings get global ...`, etc., in
real time. Layout & state-detection fixes can be validated within
minutes instead of days.

### Security guard-rails

* The tunnel user on the panel host has **shell disabled** and only
  `permitopen="127.0.0.1:22"` — they can forward but not exec.
* The phone-side `sshd` accepts only one key, and you `kill` it when done.
* Revoke at any time by deleting the line from `authorized_keys`.

---

## Recommended day-to-day flow

1. After installing a new build:
   ```bash
   deng-rejoin version
   deng-rejoin doctor install
   ```
   Confirm the new SHA / commit is live.
2. Reproduce the bug once:
   ```bash
   deng-rejoin    # Start, let it run for ~30s, hit Ctrl+C
   ```
3. Capture evidence and share:
   ```bash
   deng-rejoin probe --upload   # share the probe_id in chat
   ```
4. If layout/state is still wrong after a fix, open the SSH tunnel for a
   live session. This is the escape hatch when single-shot probes don't
   isolate the issue (e.g. timing-dependent bugs).

The combination of build proof (`version`/`doctor install`), evidence
capture (`probe`), and live access (SSH tunnel) is what makes the next
fix the *real* fix.
