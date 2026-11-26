#!/data/data/com.termux/files/usr/bin/bash
# DENG Rejoin — phone agent. Links this cloud phone to your rejoin.deng.my.id account,
# reports clone status, and runs commands from the website (start / rejoin / scripts /
# target / Delta key). Saved login persists; you only re-enter it if the account
# password changes. Needs: root (su), curl, jq.
set -u

BASE="$HOME/.deng-rejoin"
CONF="$BASE/agent.conf"
mkdir -p "$BASE"; chmod 700 "$BASE"
DEFAULT_SERVER="https://rejoin.deng.my.id"
POLL=5

SERVER=""; DEVICE_TOKEN=""; DEVICE_ID=""; NAME=""
TARGET_URL=""; TARGET_PLACE=""; TARGET_JOB=""; DELAY=20

say(){ printf '%s\n' "$*"; }
need(){ command -v "$1" >/dev/null 2>&1 || { say "Missing $1 — run: pkg install $2"; exit 1; }; }
need curl curl; need jq jq

load_conf(){ [ -f "$CONF" ] || return 0; . "$CONF" 2>/dev/null || true; }
save_conf(){
  umask 077
  { printf 'SERVER=%q\n' "$SERVER"
    printf 'DEVICE_TOKEN=%q\n' "$DEVICE_TOKEN"
    printf 'DEVICE_ID=%q\n' "$DEVICE_ID"
    printf 'NAME=%q\n' "$NAME"
    printf 'TARGET_URL=%q\n' "$TARGET_URL"
    printf 'TARGET_PLACE=%q\n' "$TARGET_PLACE"
    printf 'TARGET_JOB=%q\n' "$TARGET_JOB"
    printf 'DELAY=%q\n' "$DELAY"
  } > "$CONF"; chmod 600 "$CONF"
}

allowed(){ [[ "$1" =~ ^com\.(moons|luarm)(\.[A-Za-z0-9_]+)*$ ]]; }
root(){ su -c "$1"; }
need_root(){ root id 2>/dev/null | grep -q 'uid=0' || { say 'Root not active — allow Termux in Magisk/SU.'; return 1; }; }

declare -a APPS=()
load_apps(){ APPS=(); local p
  while IFS= read -r p; do p=${p//$'\r'/}; allowed "$p" && APPS+=("$p"); done \
    < <(root 'pm list packages' | sed -n 's/^package://p' | grep -E '^com\.(moons|luarm)(\.|$)' | LC_ALL=C sort -u)
}

# ---------- auth ----------
HTTP=0; RESP=""
api(){ # api METHOD PATH [json]  -> sets globals RESP (body) and HTTP (status)
  local m="$1" path="$2" data="${3:-}"; local -a a=(-sS -X "$m" -w '\n%{http_code}')
  [ -n "$DEVICE_TOKEN" ] && a+=(-H "Authorization: Bearer $DEVICE_TOKEN")
  [ -n "$data" ] && a+=(-H 'Content-Type: application/json' --data-binary "$data")
  local out; out=$(curl "${a[@]}" "$SERVER$path" 2>/dev/null) || out=$'\n000'
  HTTP=${out##*$'\n'}; RESP=${out%$'\n'*}
}
first_setup(){
  say ''; say '=== DENG Rejoin — link this phone ==='
  read -r -p "Server [$DEFAULT_SERVER]: " SERVER; [ -n "$SERVER" ] || SERVER="$DEFAULT_SERVER"
  SERVER="${SERVER%/}"
  read -r -p "Device name [$(getprop ro.product.model 2>/dev/null || echo phone)]: " NAME
  [ -n "$NAME" ] || NAME="$(getprop ro.product.model 2>/dev/null || echo phone)"
  say ''; say 'Link by:  [1] Pairing code (from the website)   [2] Username + password'
  local mode; read -r -p 'Choose [1/2]: ' mode
  if [ "$mode" = 2 ]; then
    local u p; read -r -p 'Username: ' u; read -r -s -p 'Password: ' p; say ''
    api POST /api/device/login "$(jq -nc --arg u "$u" --arg p "$p" --arg n "$NAME" '{username:$u,password:$p,name:$n}')"
    finish_link "$RESP"
  else
    local c; read -r -p 'Pairing code: ' c
    api POST /api/device/pair "$(jq -nc --arg c "$c" --arg n "$NAME" '{code:$c,name:$n}')"
    finish_link "$RESP"
  fi
}
finish_link(){
  local b="$1"
  if [ "$HTTP" = 200 ]; then
    DEVICE_TOKEN=$(printf '%s' "$b" | jq -r '.device_token'); DEVICE_ID=$(printf '%s' "$b" | jq -r '.device_id')
    save_conf; say "Linked as $(printf '%s' "$b" | jq -r '.name'). Saved — no need to log in again."
    return 0
  fi
  say "Link failed: $(printf '%s' "$b" | jq -r '.error // "error"') (HTTP $HTTP)"; return 1
}
ensure_linked(){
  while [ -z "$DEVICE_TOKEN" ]; do first_setup || sleep 2; done
}

# ---------- status ----------
probe_status(){
  load_apps
  local total=${#APPS[@]} run=0 p
  for p in "${APPS[@]}"; do root "pidof '$p'" >/dev/null 2>&1 && run=$((run+1)); done
  jq -nc --argjson c "$total" --argjson r "$run" --argjson d "$((total-run))" \
    '{clones:$c,running:$r,dead:$d}'
}

# ---------- command execution ----------
open_one(){ allowed "$1" || return 1; root "monkey -p '$1' -c android.intent.category.LAUNCHER 1" 2>&1 | grep -q 'Events injected: 1'; }
rejoin_one(){ allowed "$1" || return 1; local url="$2"; root "am start -a android.intent.action.VIEW -d '$url' -p '$1'" >/dev/null 2>&1; }
target_url(){
  if [ -n "$TARGET_URL" ]; then printf '%s' "$TARGET_URL"
  elif [ -n "$TARGET_PLACE" ] && [ -n "$TARGET_JOB" ]; then printf 'roblox://experiences/start?placeId=%s&gameInstanceId=%s' "$TARGET_PLACE" "$TARGET_JOB"
  fi
}
write_script(){ # name body  -> each clone's Autoexecute
  local name="$1" body="$2" p dir tmp n=0
  [[ "$name" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]*$ ]] || return 1
  [[ "$name" == *.lua ]] || name="$name.lua"
  tmp="$BASE/.push.$$.$RANDOM"; printf '%s' "$body" > "$tmp"
  for p in "${APPS[@]}"; do
    dir="/storage/emulated/0/Android/data/$p/files/gloop/external/Autoexecute"
    root "mkdir -p '$dir' && test ! -L '$dir' && cat > '$dir/$name'" < "$tmp" >/dev/null 2>&1 && n=$((n+1))
  done
  rm -f "$tmp"; [ "$n" -gt 0 ]
}
remove_script(){ local name="$1" p n=0; [[ "$name" == *.lua ]] || name="$name.lua"
  [[ "$name" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]*\.lua$ ]] || return 1
  for p in "${APPS[@]}"; do root "rm -f '/storage/emulated/0/Android/data/$p/files/gloop/external/Autoexecute/$name'" >/dev/null 2>&1 && n=$((n+1)); done; true
}
write_key(){ # key mode
  local key="$1" mode="$2" p path n=0 dir
  [[ "$key" =~ ^[A-Za-z0-9._-]{1,512}$ ]] || return 1
  if [ "$mode" = shared ]; then
    path='/storage/emulated/0/Delta/Internals/Cache/license'; dir="${path%/*}"
    root "mkdir -p '$dir' && test ! -L '$dir' && printf %s '$key' > '$path'" >/dev/null 2>&1 && n=1
  else
    for p in "${APPS[@]}"; do
      dir="/storage/emulated/0/Android/data/$p/files/gloop/external/Internals/cache"
      root "mkdir -p '$dir' && test ! -L '$dir' && printf %s '$key' > '$dir/license'" >/dev/null 2>&1 && n=$((n+1))
    done
  fi
  [ "$n" -gt 0 ]
}
run_command(){ # action payload_json  -> echoes result, returns 0/1
  local action="$1" pl="$2" p ok=1 url
  case "$action" in
    start_all) for p in "${APPS[@]}"; do open_one "$p" || ok=0; sleep "$DELAY"; done; echo "opened ${#APPS[@]}";;
    stop_all)  for p in "${APPS[@]}"; do root "am force-stop '$p'" >/dev/null 2>&1; done; echo "stopped ${#APPS[@]}";;
    rejoin_all) url=$(target_url); [ -n "$url" ] || { echo 'no target set'; return 1; }
               for p in "${APPS[@]}"; do rejoin_one "$p" "$url" || ok=0; sleep "$DELAY"; done; echo "rejoined ${#APPS[@]}";;
    push_script) write_script "$(jq -r '.name' <<<"$pl")" "$(jq -r '.body' <<<"$pl")" && echo pushed || { echo 'push failed'; return 1; };;
    remove_script) remove_script "$(jq -r '.name' <<<"$pl")"; echo removed;;
    set_target) TARGET_URL=$(jq -r '.url // ""' <<<"$pl"); TARGET_PLACE=$(jq -r '.place // ""' <<<"$pl"); TARGET_JOB=$(jq -r '.job // ""' <<<"$pl"); save_conf; echo 'target saved';;
    set_settings) local d; d=$(jq -r '.delay // empty' <<<"$pl"); [[ "$d" =~ ^[0-9]+$ ]] && DELAY=$d && save_conf; echo 'settings saved';;
    set_key) write_key "$(jq -r '.key' <<<"$pl")" "$(jq -r '.mode' <<<"$pl")" && echo 'key written' || { echo 'key failed'; return 1; };;
    *) echo "unknown action"; return 1;;
  esac
  [ "$ok" = 1 ]
}

# ---------- main loop ----------
loop(){
  say "Agent online → $SERVER  (device $DEVICE_ID). Ctrl+C to stop."
  command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock 2>/dev/null || true
  while true; do
    local st body cmds n
    load_apps                     # populate APPS in this shell (not a subshell) so commands can act on it
    st=$(probe_status)
    api POST /api/device/heartbeat "$(jq -nc --argjson s "$st" '{status:$s}')"; body=$RESP
    if [ "$HTTP" = 401 ]; then
      say 'Session ended (password changed?). Please link again.'
      DEVICE_TOKEN=''; save_conf; ensure_linked; continue
    fi
    if [ "$HTTP" = 200 ]; then
      # apply current config (target) so rejoins use the latest
      local turl; turl=$(jq -r '.config.target.url // empty' <<<"$body")
      [ -n "$turl" ] && TARGET_URL="$turl"
      cmds=$(jq -c '.commands[]?' <<<"$body")
      while IFS= read -r c; do
        [ -n "$c" ] || continue
        local id act pl res rc
        id=$(jq -r '.id' <<<"$c"); act=$(jq -r '.action' <<<"$c"); pl=$(jq -c '.payload' <<<"$c")
        say "→ $act (#$id)"
        res=$(run_command "$act" "$pl"); rc=$?
        api POST /api/device/ack "$(jq -nc --argjson id "$id" --argjson ok $([ $rc = 0 ] && echo true || echo false) --arg r "$res" '{id:$id,ok:$ok,result:$r}')" >/dev/null
      done <<< "$cmds"
    fi
    sleep "$POLL"
  done
}

main(){
  load_conf
  [ -n "$SERVER" ] || SERVER="$DEFAULT_SERVER"
  need_root || exit 1
  ensure_linked
  loop
}
main "$@"
