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
    printf 'Username: '; IFS= read -r u
    printf 'Password: '; stty -echo 2>/dev/null; IFS= read -r p; stty echo 2>/dev/null; printf '\n'
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
agent_menu(){
  say ''; say "Website: $(agent_status)"
  say "This phone joins $USERNAME automatically and shows up in Fleet on the website."
  say '[1] Restart connection   [2] Show recent log   [Enter] Back'
  printf 'Select: '; IFS= read -r c
  case "$c" in
    1) agent_stop; agent_start; say "Website: $(agent_status)"; read -r -p 'Enter to continue... ' _ ;;
    2) tail -n 15 "$AGLOG" 2>/dev/null || say 'No log yet.'; read -r -p 'Enter to continue... ' _ ;;
  esac
}
logout(){
  agent_stop
  [ -f "$AGCONF" ] && sed -i "s/^DEVICE_TOKEN=.*/DEVICE_TOKEN=''/" "$AGCONF"
  rm -f "$AUTH"; TOKEN=""; say 'Logged out. This phone left the website.'
}

# ---- menu ----
menu(){
  while true; do
    clear 2>/dev/null || true
    say '========================'
    say '        D E N G'
    say '     rejoin.deng.my.id'
    say "   signed in: $USERNAME"
    say '========================'
    say '[1] Rejoin Tool (local menu)'
    say "[2] Website ($(agent_status))"
    say '[3] Update tools'
    say '[4] Log out'
    say '[0] Exit'
    say '------------------------'
    printf 'Select [0-4]: '; IFS= read -r c
    case "$c" in
      1) [ -f "$CODE616" ] || getfile "$RAW/code616.sh" "$CODE616" || { say 'Download failed'; read -r _; continue; }
         bash "$CODE616" ;;
      2) agent_menu ;;
      3) getfile "$RAW/code616.sh" "$CODE616" && say 'code616 updated.' || say 'code616 update failed.'
         getfile "$RAW/deng-agent.sh" "$AGENT" && { say 'agent updated.'; agent_stop; agent_start; } || say 'agent update failed.'
         getfile "$RAW/deng-launch.sh" "$DENG/deng-launch.sh" && say 'launcher updated (restart start deng).' || true
         read -r -p 'Enter to continue... ' _ ;;
      4) logout; gate; agent_start ;;
      0) exit 0 ;;
      *) say 'Pick 0-4.'; sleep 1 ;;
    esac
  done
}
gate
agent_start
menu
