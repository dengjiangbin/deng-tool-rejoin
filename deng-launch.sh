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
logout(){ rm -f "$AUTH"; TOKEN=""; say 'Logged out.'; }

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
    say '[2] Website Agent (link to site)'
    say '[3] Update tools'
    say '[4] Log out'
    say '[0] Exit'
    say '------------------------'
    printf 'Select [0-4]: '; IFS= read -r c
    case "$c" in
      1) [ -f "$CODE616" ] || getfile "$RAW/code616.sh" "$CODE616" || { say 'Download failed'; read -r _; continue; }
         bash "$CODE616" ;;
      2) [ -f "$AGENT" ] || getfile "$RAW/deng-agent.sh" "$AGENT" || { say 'Download failed'; read -r _; continue; }
         bash "$AGENT" ;;
      3) getfile "$RAW/code616.sh" "$CODE616" && say 'code616 updated.' || say 'code616 update failed.'
         getfile "$RAW/deng-agent.sh" "$AGENT" && say 'agent updated.' || say 'agent update failed.'
         getfile "$RAW/deng-launch.sh" "$DENG/deng-launch.sh" && say 'launcher updated (restart start deng).' || true
         read -r -p 'Enter to continue... ' _ ;;
      4) logout; gate ;;
      0) exit 0 ;;
      *) say 'Pick 0-4.'; sleep 1 ;;
    esac
  done
}
gate
menu
