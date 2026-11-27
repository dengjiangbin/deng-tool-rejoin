#!/data/data/com.termux/files/usr/bin/bash
# DENG launcher — single entry point (run: start deng).
# Gated: requires a valid DENG Rejoin account before the tools open.
set -u
DENG="$HOME/.deng"
AUTH="$DENG/auth"
SRVFILE="$DENG/server"
mkdir -p "$DENG"; chmod 700 "$DENG"
DEFAULT_SERVER="https://deng-rejoin.discloud.dev"
SERVER="$DEFAULT_SERVER"; [ -f "$SRVFILE" ] && SERVER="$(cat "$SRVFILE" 2>/dev/null)"
SERVER="${SERVER%/}"
CODE616="$HOME/code616.sh"
AGENT="$HOME/deng-agent.sh"
RAW="https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main"

say(){ printf '%s\n' "$*"; }
if [ -t 1 ]; then R=$'\033[1;31m'; DIM=$'\033[2m'; Z=$'\033[0m'; else R=; DIM=; Z=; fi
# Root calls must never hold the terminal: some root managers switch it to raw mode and leave it
# that way, which breaks the menu layout and stops typing. Detach them from the keyboard.
su(){ command su "$@" </dev/null; }
tty_fix(){ stty sane </dev/tty 2>/dev/null || true; }
clear_screen(){ printf '\033[2J\033[H'; }   # ANSI clear (works without the `clear` package)
logo(){ printf '  %s-=[ %sDENG REJOIN%s%s ]=-%s\n' "$DIM" "$R" "$Z" "$DIM" "$Z"; }
need(){ command -v "$1" >/dev/null 2>&1 || { say "Missing $1 — run: pkg install $2"; exit 1; }; }
need curl curl; need jq jq
getfile(){ curl -fsSL "$1" -o "$2" 2>/dev/null && [ -s "$2" ]; }

# ---- account gate ----
TOKEN=""; USERNAME=""
verify(){ # returns 0 if TOKEN maps to a valid account
  [ -n "$TOKEN" ] || return 1
  local out code
  out=$(curl -sS -m 15 -w '\n%{http_code}' -H "Authorization: Bearer $TOKEN" "$SERVER/api/me" 2>/dev/null) || return 2
  code=${out##*$'\n'}
  if [ "$code" = 200 ]; then USERNAME=$(printf '%s' "${out%$'\n'*}" | jq -r '.user.username // empty'); return 0; fi
  [ "$code" = 000 ] && return 2   # cannot reach server
  return 1                         # token invalid/expired
}
login(){
  local u p out code
  say ''; say 'Sign in to your DENG Rejoin account.'
  say "(No account? Create one at $SERVER)"
  while true; do
    tty_fix
    printf 'Username: '; IFS= read -r u </dev/tty
    printf 'Password: '; stty -echo 2>/dev/null </dev/tty; IFS= read -r p </dev/tty; stty echo 2>/dev/null </dev/tty; printf '\n'
    [ -n "$u" ] && [ -n "$p" ] || { say 'Enter username and password.'; continue; }
    out=$(curl -sS -m 15 -w '\n%{http_code}' -X POST -H 'Content-Type: application/json' \
          --data-binary "$(jq -nc --arg u "$u" --arg p "$p" '{username:$u,password:$p}')" "$SERVER/api/login" 2>/dev/null)
    code=${out##*$'\n'}
    if [ "$code" = 200 ]; then
      TOKEN=$(printf '%s' "${out%$'\n'*}" | jq -r '.token // empty')
      if [ -n "$TOKEN" ]; then umask 077; printf '%s' "$TOKEN" > "$AUTH"; chmod 600 "$AUTH"; say 'Signed in.'; return 0; fi
    fi
    if [ "$code" = 000 ]; then say 'Cannot reach the server. Check your internet and try again.'; return 1; fi
    say "Login failed: $(printf '%s' "${out%$'\n'*}" | jq -r '.error // "wrong username or password"')"
  done
}
gate(){
  [ -f "$AUTH" ] && TOKEN="$(cat "$AUTH" 2>/dev/null)"
  local rc
  verify; rc=$?
  if [ $rc -eq 0 ]; then return 0; fi
  if [ $rc -eq 2 ] && [ -n "$TOKEN" ]; then
    say 'Cannot verify your account (server unreachable). Connect to the internet and relaunch.'
    exit 1
  fi
  # need login
  login || exit 1
  verify || { say 'Could not verify account. Try again later.'; exit 1; }
}
# Hardware ID of this cloud phone (needs root). A hash of identifiers that stay the same
# across reboots and Termux reinstalls; the raw values never leave the device.
deng_hwid(){
  local idf=/data/local/.deng_hwid seed parts
  seed=$(su -c "cat $idf 2>/dev/null" 2>/dev/null | tr -cd 'a-f0-9' | head -c 32)
  if [ ${#seed} -ne 32 ]; then
    seed=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
    su -c "printf %s $seed > $idf && chmod 600 $idf" >/dev/null 2>&1
    seed=$(su -c "cat $idf 2>/dev/null" 2>/dev/null | tr -cd 'a-f0-9' | head -c 32)
  fi
  [ ${#seed} -eq 32 ] || return 1
  parts=$(su -c "settings get secure android_id; getprop ro.serialno; getprop ro.boot.serialno; getprop ro.product.model; getprop ro.build.fingerprint; getprop | grep -iE 'pad[._]?code|instance[._]?id|vm[._]?id' | sort" 2>/dev/null | tr -d '\r')
  printf 'deng1|%s|%s' "$seed" "$parts" | sha256sum | cut -c1-64
}
# ---- key check: this cloud phone needs an active key bound on the website ----
KEY_LEFT=''
key_check(){ # 0 = active key, 1 = no key, 2 = cannot check
  local h out code body exp nowms left
  h=$(deng_hwid) || { say 'Root is needed to identify this cloud phone.'; return 2; }
  out=$(curl -sS -m 15 -w '\n%{http_code}' -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
        --data-binary "{\"hwid\":\"$h\"}" "$SERVER/api/license/check" 2>/dev/null) || out=$'\n000'
  code=${out##*$'\n'}; body=${out%$'\n'*}
  [ "$code" = 200 ] || return 2
  [ "$(printf '%s' "$body" | jq -r '.licensed')" = true ] || { KEY_LEFT=''; return 1; }
  exp=$(printf '%s' "$body" | jq -r '.expires_at'); nowms=$(printf '%s' "$body" | jq -r '.now'); left=$(( (exp-nowms)/1000 ))
  KEY_LEFT="$((left/86400))d $((left%86400/3600))h"; return 0
}
key_wall(){
  key_check; case $? in
    0) return 0;;
    1) say ''; say 'No active key on this cloud phone.'; say 'Open the website > Key Panel, pick a key and bind it to this cloud phone,'; say 'then choose this menu again.';;
    *) say 'Cannot check the key right now (no internet or server busy). Try again.';;
  esac
  printf 'Press Enter...'; read -r _ </dev/tty; return 1
}
# ---- website agent (background, joins this account automatically) ----
AGDIR="$HOME/.deng-rejoin"; AGPID="$AGDIR/agent.pid"; AGLOG="$AGDIR/agent.log"; AGCONF="$AGDIR/agent.conf"
agent_pid(){ local p; [ -f "$AGPID" ] && p=$(cat "$AGPID" 2>/dev/null) && kill -0 "$p" 2>/dev/null && printf '%s' "$p"; }
agent_start(){
  agent_pid >/dev/null && return 0
  [ -f "$AGENT" ] || getfile "$RAW/deng-agent.sh" "$AGENT" || { say 'Agent download failed.'; return 1; }
  mkdir -p "$AGDIR"; chmod 700 "$AGDIR"
  [ -f "$AGLOG" ] && [ "$(wc -c < "$AGLOG")" -gt 1000000 ] && tail -c 200000 "$AGLOG" > "$AGLOG.tmp" && mv "$AGLOG.tmp" "$AGLOG"
  nohup bash "$AGENT" >> "$AGLOG" 2>&1 < /dev/null &
  sleep 3
}
agent_stop(){ local p; p=$(agent_pid) && kill "$p" 2>/dev/null; rm -f "$AGPID"; }
agent_name(){ [ -f "$AGCONF" ] && ( NAME=''; . "$AGCONF" 2>/dev/null; printf '%s' "$NAME" ); }
agent_status(){
  if agent_pid >/dev/null; then printf 'online as %s' "$(agent_name)"; else printf 'offline'; fi
}
logout(){
  agent_stop
  [ -f "$AGCONF" ] && sed -i "s/^DEVICE_TOKEN=.*/DEVICE_TOKEN=''/" "$AGCONF"
  rm -f "$AUTH"; TOKEN=""; say 'Logged out. This phone left the website.'
}

# ---- silent update of agent + launcher on every launch (code616 updates itself) ----
fresh(){ # url dest -> 0 if dest was replaced with a newer valid copy
  local tmp; tmp=$(mktemp "$DENG/.upd.XXXXXX") || return 1
  if getfile "$1" "$tmp" && head -1 "$tmp" | grep -q '^#!/data/data/com.termux' && bash -n "$tmp" 2>/dev/null && ! cmp -s "$tmp" "$2"; then
    chmod 700 "$tmp" && mv -f "$tmp" "$2" && return 0
  fi
  rm -f "$tmp"; return 1
}
update_tools(){
  if fresh "$RAW/deng-launch.sh" "$DENG/deng-launch.sh" && [ -z "${DENG_UPDATED:-}" ]; then
    DENG_UPDATED=1 exec bash "$DENG/deng-launch.sh"
  fi
  if fresh "$RAW/deng-agent.sh" "$AGENT"; then agent_stop; fi
  [ -f "$CODE616" ] || getfile "$RAW/code616.sh" "$CODE616"
}

# ---- open DENG automatically when Termux starts ----
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
AUTOSH="$PREFIX/etc/profile.d/deng-auto.sh"; NOAUTO="$DENG/noauto"
autostart_install(){
  mkdir -p "${AUTOSH%/*}"
  cat > "$AUTOSH" <<'EOS'
# DENG: open the DENG menu when Termux starts (turn off in the DENG menu)
if [ -z "${DENG_AUTO_DONE:-}" ] && [ -t 0 ] && [ ! -f "$HOME/.deng/noauto" ] && command -v start >/dev/null 2>&1; then
  export DENG_AUTO_DONE=1
  start deng
fi
EOS
  # non-login shells read ~/.bashrc instead
  grep -q 'deng-auto.sh' "$HOME/.bashrc" 2>/dev/null || printf '\n[ -f "%s" ] && . "%s"\n' "$AUTOSH" "$AUTOSH" >> "$HOME/.bashrc"
}
autostart_on(){ [ -f "$AUTOSH" ] && [ ! -f "$NOAUTO" ]; }
autostart_toggle(){ if autostart_on; then : > "$NOAUTO"; say 'Auto-start OFF.'; else rm -f "$NOAUTO"; autostart_install; say 'Auto-start ON.'; fi; sleep 1; }
if [ "${1:-}" = --autostart-install ]; then autostart_install; exit 0; fi

# ---- menu ----
bind_key(){
  local h out code body k
  h=$(deng_hwid) || { say 'Root diperlukan.'; sleep 1; return; }
  tty_fix; printf 'Tempel DENG key (atau Enter batal): '; IFS= read -r k </dev/tty
  k=$(printf '%s' "$k" | tr -d ' \t' | tr a-z A-Z)
  [ -n "$k" ] || return
  out=$(curl -sS -m 20 -w '\n%{http_code}' -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
        --data-binary "$(jq -nc --arg c "$k" --arg h "$h" '{code:$c,hwid:$h}')" "$SERVER/api/keys/bind-here" 2>/dev/null) || out=$'\n000'
  code=${out##*$'\n'}; body=${out%$'\n'*}
  if [ "$code" = 200 ]; then say ''; say 'Key aktif untuk cloud phone ini.'; key_check >/dev/null 2>&1
  else say ''; say "Gagal: $(printf '%s' "$body" | jq -r '.error // "server tidak terjangkau"' 2>/dev/null)"; fi
  printf 'Enter untuk lanjut...'; read -r _ </dev/tty
}
# Samsung-type keyboards may not deliver typing to Termux; this documented Termux setting fixes it.
kb_fix(){ local f="$HOME/.termux/termux.properties"
  grep -q '^[[:space:]]*enforce-char-based-input' "$f" 2>/dev/null && return 0
  mkdir -p "$HOME/.termux"; printf '\n# DENG: make typing reach the terminal on Samsung-type keyboards\nenforce-char-based-input = true\n' >> "$f"
  command -v termux-reload-settings >/dev/null 2>&1 && termux-reload-settings >/dev/null 2>&1; return 0; }
menu(){
  while true; do
    key_check >/dev/null 2>&1 </dev/null
    tty_fix
    clear_screen
    logo
    say "  ${DIM}rejoin.deng.my.id${Z}"
    say "  signed in: ${R}$USERNAME${Z}"
    say "  website: $(agent_status)"
    say "  key: $([ -n "$KEY_LEFT" ] && echo "active, $KEY_LEFT left" || echo 'none (bind in Key Panel)')"
    say '  --------------------------'
    say '[1] Rejoin Tool'
    say '[2] Bind key'
    say "[3] Open DENG when Termux starts: $(autostart_on && echo ON || echo OFF)"
    say '[0] Exit'
    say '------------------------'
    tty_fix; printf 'Select [0-3]: '; IFS= read -r c </dev/tty || exit 0
    case "$c" in
      1) key_wall || continue
         [ -f "$CODE616" ] || getfile "$RAW/code616.sh" "$CODE616" || { say 'Download failed'; read -r _ </dev/tty; continue; }
         bash "$CODE616" ;;
      2) bind_key ;;
      3) autostart_toggle ;;
      0) exit 0 ;;
      *) say 'Pick 0-3.'; sleep 1 ;;
    esac
  done
}
gate
update_tools
[ -f "$NOAUTO" ] || autostart_on || autostart_install   # older installs get auto-start too
agent_start
kb_fix
menu
