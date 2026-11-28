#!/data/data/com.termux/files/usr/bin/bash
# DENG Rejoin — phone agent. Joins this cloud phone to the account you signed in with
# in `start deng` (no extra login, no prompts), names the phone from the device itself,
# reports clone status, and runs commands from the website (start / rejoin / scripts /
# target / Delta key). Started in the background by `start deng`. Needs: root (su), curl, jq.
set -u

BASE="$HOME/.deng-rejoin"
CONF="$BASE/agent.conf"
mkdir -p "$BASE"; chmod 700 "$BASE"
LAUNCH_DIR="$HOME/.deng"
LAUNCH_AUTH="$LAUNCH_DIR/auth"
DEFAULT_SERVER="https://deng-rejoin.discloud.dev"
launcher_server(){ local s="$DEFAULT_SERVER"; [ -f "$LAUNCH_DIR/server" ] && s="$(cat "$LAUNCH_DIR/server" 2>/dev/null)"; printf '%s' "${s%/}"; }
PIDFILE="$BASE/agent.pid"
AGENT_VERSION="2026.11.29-2"
SELF="$(readlink -f "$0" 2>/dev/null || printf '%s' "$0")"
SELF_URL="https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/deng-agent.sh"
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

allowed(){ [ ${#1} -le 100 ] && [[ "$1" =~ ^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$ ]]; }

# ---------- shared settings with the code616 Termux menu ----------
# The private link and delay live in code616's own config, so the Termux menu, the
# code616 monitor and this agent always use the same values.
C616_CFG="$HOME/.code616/config"
cfg_get(){ [ -f "$C616_CFG" ] && sed -n "s/^$1=//p" "$C616_CFG" | tail -1; }
cfg_set(){ # KEY VALUE ...   (rewrites only these keys, keeps everything else)
  mkdir -p "${C616_CFG%/*}"; chmod 700 "${C616_CFG%/*}" 2>/dev/null
  local tmp="$C616_CFG.agent.$$" k v
  if [ -f "$C616_CFG" ]; then cp "$C616_CFG" "$tmp"; else printf 'CONFIG_SCHEMA=2\n' > "$tmp"; fi
  while [ $# -ge 2 ]; do k=$1 v=$2; shift 2
    if grep -q "^$k=" "$tmp"; then awk -v k="$k" -v v="$v" 'BEGIN{FS=OFS="="} $1==k{print k"="v; next} {print}' "$tmp" > "$tmp.n" && mv "$tmp.n" "$tmp"
    else printf '%s=%s\n' "$k" "$v" >> "$tmp"; fi
  done
  chmod 600 "$tmp"; mv "$tmp" "$C616_CFG"
}
ps_valid(){ [[ "$1" =~ ^roblox://placeId=[0-9]{1,20}\&linkCode=[A-Za-z0-9_-]{1,128}$ ||
               "$1" =~ ^roblox://navigation/share_links\?code=[A-Za-z0-9_-]{1,128}\&type=Server$ ]]; }
# Any Roblox private-server link (old or new style, or an app deep link) -> deep link.
to_deeplink(){ local in="$1" id code
  if ps_valid "$in"; then printf '%s' "$in"; return 0; fi
  if [[ "$in" =~ privateServerLinkCode=([A-Za-z0-9_-]{1,128}) ]]; then code=${BASH_REMATCH[1]}
    [[ "$in" =~ /games/([0-9]{1,20}) || "$in" =~ [?\&]placeId=([0-9]{1,20}) ]] || return 1
    printf 'roblox://placeId=%s&linkCode=%s' "${BASH_REMATCH[1]}" "$code"; return 0; fi
  if [[ "$in" =~ [?\&]code=([A-Za-z0-9_-]{1,128}) && "$in" =~ type=Server ]]; then
    [[ "$in" =~ [?\&]code=([A-Za-z0-9_-]{1,128}) ]]; printf 'roblox://navigation/share_links?code=%s&type=Server' "${BASH_REMATCH[1]}"; return 0; fi
  return 1
}
# Deep link -> normal https link for the website.
to_weblink(){
  if [[ "$1" =~ ^roblox://placeId=([0-9]+)\&linkCode=(.+)$ ]]; then printf 'https://www.roblox.com/games/%s?privateServerLinkCode=%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
  elif [[ "$1" =~ ^roblox://navigation/share_links\?code=([^&]+) ]]; then printf 'https://www.roblox.com/share?code=%s&type=Server' "${BASH_REMATCH[1]}"; fi
}
# Canonical form: "ps|<deeplink>", "pj|<place>|<job>" or "" (not set).
local_target(){ local u p j; u=$(cfg_get PS_URL); p=$(cfg_get PLACE_ID); j=$(cfg_get JOB_ID)
  if ps_valid "$u"; then printf 'ps|%s' "$u"
  elif [[ "$p" =~ ^[0-9]{1,20}$ && "$j" =~ ^[a-fA-F0-9-]{8,64}$ ]]; then printf 'pj|%s|%s' "$p" "$j"; fi; }
remote_target(){ local u p j d; u=$(jq -r '.target.url // empty' <<<"$1"); p=$(jq -r '.target.place // empty' <<<"$1"); j=$(jq -r '.target.job // empty' <<<"$1")
  if [ -n "$u" ]; then d=$(to_deeplink "$u") && printf 'ps|%s' "$d"
  elif [ -n "$p" ] && [ -n "$j" ]; then printf 'pj|%s|%s' "$p" "$j"; fi; }
apply_target(){ case "$1" in
    ps\|*) cfg_set PS_URL "${1#ps|}";;
    pj\|*) local r=${1#pj|}; cfg_set PS_URL '' PLACE_ID "${r%%|*}" JOB_ID "${r#*|}";;
    *) cfg_set PS_URL '' PLACE_ID '' JOB_ID '';;
  esac; }
target_json(){ case "$1" in
    ps\|*) jq -nc --arg u "$(to_weblink "${1#ps|}")" '{url:$u}';;
    pj\|*) local r=${1#pj|}; jq -nc --arg p "${r%%|*}" --arg j "${r#*|}" '{place:$p,job:$j}';;
    *) printf '{}';;
  esac; }
clone_delay(){ local d; d=$(cfg_get DELAY); [[ "$d" =~ ^[0-9]{1,3}$ ]] && printf '%s' "$((10#$d))" || printf '25'; }
root(){ su -c "$1"; }
need_root(){ root id 2>/dev/null | grep -q 'uid=0' || { say 'Root not active — allow Termux in Magisk/SU.'; return 1; }; }

declare -a APPS=()
# Packages: with no selection every com.moons / com.luarm clone is used (automatic). A selection made in
# Configuration > Package (Termux or website) lives in ~/.code616/packages, one package per line.
PKGF="$HOME/.code616/packages"
declare -a ALLPK=() PICKED=() INST=() HANDLERS=()
HAND_AT=0
# Every Roblox package on this cloud phone, whatever it is called: known names plus any app that can
# open a roblox:// link. The link check is repeated at most every 5 minutes.
load_apps(){ APPS=(); ALLPK=(); PICKED=(); INST=(); local p w now
  while IFS= read -r p; do p=${p//$'\r'/}; allowed "$p" && INST+=("$p"); done \
    < <(root 'pm list packages' | sed -n 's/^package://p' | LC_ALL=C sort -u)
  now=$(date +%s)
  if [ $((now-HAND_AT)) -ge 300 ]; then HAND_AT=$now; HANDLERS=()
    while IFS= read -r p; do allowed "$p" && HANDLERS+=("$p"); done \
      < <(root "cmd package query-activities --brief -a android.intent.action.VIEW -d 'roblox://navigation' 2>/dev/null" | tr -d '\r' | sed -n 's#^[[:space:]]*\([A-Za-z][A-Za-z0-9_.]*\)/.*#\1#p' | LC_ALL=C sort -u)
  fi
  for p in "${INST[@]}"; do
    if [[ "$p" =~ ^com\.(moons|luarm|roblox)(\.|$) ]]; then ALLPK+=("$p"); continue; fi
    for w in "${HANDLERS[@]}"; do [ "$w" = "$p" ] && { ALLPK+=("$p"); break; }; done
  done
  if [ -f "$PKGF" ]; then while IFS= read -r p; do p=${p//$'\r'/}; allowed "$p" && PICKED+=("$p"); done < "$PKGF"; fi
  for p in "${INST[@]}"; do
    if [ ${#PICKED[@]} -gt 0 ]; then for w in "${PICKED[@]}"; do [ "$w" = "$p" ] && { APPS+=("$p"); break; }; done
    elif [[ "$p" =~ ^com\.(moons|luarm)(\.|$) ]]; then APPS+=("$p"); fi
  done
}
pk_json(){ local a='[]' s='[]'
  [ ${#ALLPK[@]} -gt 0 ] && a=$(printf '%s\n' "${ALLPK[@]}" | jq -Rnc '[inputs]')
  [ ${#PICKED[@]} -gt 0 ] && s=$(printf '%s\n' "${PICKED[@]}" | jq -Rnc '[inputs]')
  printf '{"all":%s,"sel":%s}' "$a" "$s"; }
# Save the selection sent by the website. Only installed, supported packages are kept; none = automatic again.
set_packages(){ local p n=0 asked=0 miss='' tmp="$PKGF.tmp"; mkdir -p "${PKGF%/*}"; : > "$tmp"
  HAND_AT=0; load_apps                     # fresh scan, so an app installed a moment ago is known
  while IFS= read -r p; do allowed "$p" || continue; asked=$((asked+1))
    local q ok=0; for q in "${ALLPK[@]}" "${APPS[@]}" "${INST[@]}"; do [ "$q" = "$p" ] && { printf '%s\n' "$p" >> "$tmp"; n=$((n+1)); ok=1; break; }; done   # installed on this phone
    [ $ok = 1 ] || miss+="$p "
  done < <(jq -r '.pkgs[]? // empty' <<<"$1" 2>/dev/null | head -100)
  if [ "$asked" -gt 0 ] && [ "$n" = 0 ]; then rm -f "$tmp"; echo "not installed on this phone: ${miss% }"; return 1; fi
  if [ "$n" -gt 0 ]; then LC_ALL=C sort -u "$tmp" > "$PKGF"; chmod 600 "$PKGF"; rm -f "$tmp"; load_apps; echo "$n package$([ $n = 1 ] || echo s) on${miss:+, not installed: ${miss% }}"
  else rm -f "$tmp" "$PKGF"; load_apps; echo 'back to automatic'; fi; }

# ---------- auth ----------
HTTP=0; RESP=""
api(){ # api METHOD PATH [json]  -> sets globals RESP (body) and HTTP (status)
  local m="$1" path="$2" data="${3:-}"; local -a a=(-sS -X "$m" -w '\n%{http_code}')
  [ -n "$DEVICE_TOKEN" ] && a+=(-H "Authorization: Bearer $DEVICE_TOKEN")
  [ -n "$data" ] && a+=(-H 'Content-Type: application/json' --data-binary "$data")
  local out; out=$(curl "${a[@]}" "$SERVER$path" 2>/dev/null) || out=$'\n000'
  HTTP=${out##*$'\n'}; RESP=${out%$'\n'*}
}
# Phone name from the device itself (root): cloud-phone ID if the provider sets one,
# else the device name, else model; plus 4 chars of android_id so phones never collide.
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
device_name(){
  local cand id4 n
  cand=$(root "getprop" 2>/dev/null | grep -iE '\[[^]]*(pad[._]?code|padcode|instance[._]?id|cloud[._]?(phone|id)|vm[._]?id)[^]]*\]: \[[^]]+\]' \
         | head -1 | sed -E 's/.*\]: \[([^]]*)\].*/\1/')
  [ -n "$cand" ] || cand=$(root "settings get global device_name" 2>/dev/null | tr -d '\r')
  [ "$cand" = null ] && cand=''
  [ -n "$cand" ] || cand=$(getprop ro.product.model 2>/dev/null)
  [ -n "$cand" ] || cand=phone
  id4=$(root "settings get secure android_id" 2>/dev/null | tr -d '\r' | tail -c 5)
  n=$(printf '%s' "$cand" | tr -c 'A-Za-z0-9._:-' '-' | sed -E 's/-+/-/g; s/^-|-$//g' | cut -c1-30)
  [ -n "$id4" ] && [ "$id4" != null ] && n="$n-$id4"
  printf '%s' "${n:-phone}"
}
# Join the account signed in via `start deng`. No prompts.
auto_link(){
  local tok out code b
  [ -f "$LAUNCH_AUTH" ] && tok=$(cat "$LAUNCH_AUTH" 2>/dev/null) || tok=''
  if [ -z "$tok" ]; then say 'Not signed in. Run: start deng'; return 1; fi
  [ -n "$NAME" ] || NAME=$(device_name)
  out=$(curl -sS -m 20 -w '\n%{http_code}' -X POST -H "Authorization: Bearer $tok" -H 'Content-Type: application/json' \
        --data-binary "$(jq -nc --arg n "$NAME" --arg h "$(deng_hwid)" '{name:$n,hwid:$h}')" "$SERVER/api/device/claim" 2>/dev/null) || out=$'\n000'
  code=${out##*$'\n'}; b=${out%$'\n'*}
  if [ "$code" = 200 ]; then
    DEVICE_TOKEN=$(printf '%s' "$b" | jq -r '.device_token'); DEVICE_ID=$(printf '%s' "$b" | jq -r '.device_id')
    NAME=$(printf '%s' "$b" | jq -r '.name'); save_conf; say "Phone joined the account as $NAME."
    return 0
  fi
  case "$code" in
    000) say 'Cannot reach the server; retrying.';;
    401) say 'Account session ended. Run: start deng and sign in again.';;
    *)   say "Could not join the account (HTTP $code).";;
  esac
  return 1
}
ensure_linked(){
  while [ -z "$DEVICE_TOKEN" ]; do auto_link || sleep 30; done
}

# ---------- status ----------
# Reports phone totals AND a per-clone list so the site can control each clone.
# Per clone: off / loading / ingame / dc / running. "ingame" comes from the heartbeat file
# the code616 Lua writes every 5s while the clone is really inside a server.
HB_REL='files/gloop/external/Workspace/code616_hb.txt'
# Timing settings as they are on this cloud phone right now (shown and edited on the website too).
cfg_num(){ local v; v=$(cfg_get "$1"); [[ "$v" =~ ^[0-9]+$ ]] && printf '%s' "$v" || printf '%s' "$2"; }
probe_cfg(){ printf '{"delay":%s,"interval":%s,"cols":%s,"key_wait":%s,"hb":%s,"hb_stale":%s,"mindp":%s,"autostart":%s}' \
  "$(cfg_num DELAY 25)" "$(cfg_num INTERVAL 20)" "$(cfg_num COLS 0)" "$(cfg_num KEY_WAIT 20)" "$(cfg_num HB 1)" "$(cfg_num HB_STALE 120)" "$(cfg_num MINDP 96)" "$(cfg_num AUTOSTART 0)"; }
set_num(){ # payload key CFGKEY min max
  local v; v=$(jq -r --arg k "$2" '.[$k] // empty' <<<"$1"); [[ "$v" =~ ^[0-9]{1,4}$ ]] && [ "$v" -ge "$4" ] && [ "$v" -le "$5" ] && cfg_set "$3" "$v"; return 0; }
probe_status(){
  load_apps; exec_map
  local total=${#APPS[@]} run=0 list='' now pkg r mt w x st age raw
  now=$(date +%s)
  raw=$(root "for p in ${APPS[*]}; do f=/storage/emulated/0/Android/data/\$p/$HB_REL; r=0; pidof \$p >/dev/null 2>&1 && r=1
          if [ -f \$f ]; then echo \"\$p \$r \$(stat -c %Y \$f) \$(head -c 60 \$f | tr -d '\n')\"; else echo \"\$p \$r - -\"; fi; done" 2>/dev/null)
  while read -r pkg r mt w _t x _; do   # file: '<ok|dc> <time> [start]'
    [ -n "$pkg" ] || continue
    if [ "$r" != 1 ]; then st=off
    else run=$((run+1)); st=running
      if [[ "$mt" =~ ^[0-9]+$ ]]; then age=$((now-mt))
        if [ "$w" = dc ]; then st=dc
        elif [ "${x:-}" = start ]; then [ $age -lt 600 ] && st=loading
        elif [ "$w" = ok ] && [ $age -le 120 ]; then st=ingame
        elif [ "$w" = ok ]; then st=stuck; fi
      fi
    fi
    list+=$(jq -nc --arg pkg "$pkg" --arg name "${pkg##*.}" --arg user "${RBX[$pkg]:-}" --argjson run "$([ "$r" = 1 ] && echo true || echo false)" --arg st "$st" --arg ex "${EXE[$pkg]:-}" '{pkg:$pkg,name:$name,user:$user,running:$run,state:$st,ex:$ex}')','
  done <<< "$raw"
  jq -nc --argjson c "$total" --argjson r "$run" --argjson d "$((total-run))" \
    --argjson list "[${list%,}]" --argjson sys "$(probe_sys)" --argjson key "$KEYINFO" --arg ver "$AGENT_VERSION" --argjson cfg "$(probe_cfg)" --argjson pk "$(pk_json)" --argjson scr "$SCRX" --arg tool "$(tool_ver)" --argjson arev "${APPLIED_REV:--1}" '{clones:$c,running:$r,dead:$d,list:$list,sys:$sys,key:$key,ver:$ver,tool:$tool,cfg:$cfg,pk:$pk,scr:$scr,arev:$arev}'
}
# CPU % (1-second sample of /proc/stat), RAM and /data storage in MB. Any value that
# can't be read is sent as null so the site shows "—" instead of a wrong number.
probe_sys(){
  local a b cpu=null cores ram_t=null ram_a=null dk=null
  a=$(root 'head -1 /proc/stat' 2>/dev/null); sleep 1; b=$(root 'head -1 /proc/stat' 2>/dev/null)
  if [ -n "$a" ] && [ -n "$b" ]; then
    cpu=$(printf '%s\n%s\n' "$a" "$b" | awk '{i=$5+$6; t=0; for(k=2;k<=NF;k++) t+=$k; if(NR==1){i1=i;t1=t}else{dt=t-t1; if(dt>0) printf "%d", (dt-(i-i1))*100/dt; else print "null"}}')
    [ -n "$cpu" ] || cpu=null
  fi
  cores=$(grep -c '^processor' /proc/cpuinfo 2>/dev/null); [ "${cores:-0}" -gt 0 ] 2>/dev/null || cores=null
  ram_t=$(awk '/^MemTotal:/{print int($2/1024)}' /proc/meminfo 2>/dev/null); [ -n "$ram_t" ] || ram_t=null
  ram_a=$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo 2>/dev/null); [ -n "$ram_a" ] || ram_a=null
  dk=$(root 'df -kP /data 2>/dev/null || df -k /data' 2>/dev/null | awk 'NR>1 && $2 ~ /^[0-9]+$/ {printf "[%d,%d]", $2/1024, $3/1024; exit}'); [ -n "$dk" ] || dk='[null,null]'
  jq -nc --argjson cpu "$cpu" --argjson cores "$cores" --argjson rt "$ram_t" --argjson ra "$ram_a" --argjson dk "$dk" \
    '{cpu:$cpu,cores:$cores,ram_total:$rt,ram_used:(if $rt!=null and $ra!=null then $rt-$ra else null end),disk_total:$dk[0],disk_used:$dk[1]}' 2>/dev/null \
    || printf '{}'
}

# ---------- command execution ----------
# Before a stopped clone is (re)launched, put it back in its grid slot (same math as
# code616 Start All), so a clone the user resized doesn't come back at that size.
grid_slot(){ [ -f "$HOME/code616.sh" ] || return 0; root "pidof '$1'" >/dev/null 2>&1 && return 0
  timeout 20 bash "$HOME/code616.sh" --grid-slot "$1" </dev/null >/dev/null 2>&1 || true; }
# Fresh "starting" heartbeat: code616's auto-rejoin would otherwise read the previous
# session's file (old/"dc") and kill the clone while it is still loading. Keeps the
# file's owner so the game can keep writing it.
mark_starting(){ local f="$EXT_ROOT/$1/$HB_REL" d; d="${f%/*}"
  root "[ -d '$d' ] || exit 0; if [ -f '$f' ]; then printf 'ok %s start' \$(date +%s) > '$f'; else printf 'ok %s start' \$(date +%s) > '$f' && chown \$(stat -c %u:%g '$d') '$f'; fi" >/dev/null 2>&1; }
open_one(){ allowed "$1" || return 1; busy_mark "$1"; grid_slot "$1"; mark_starting "$1"; root "monkey -p '$1' -c android.intent.category.LAUNCHER 1" 2>&1 | grep -q 'Events injected: 1'; }
# Rejoin = close the clone, put it back in its grid slot, then open it into the server.
# Opening a link in a clone that is still running skips the grid and the window lands anywhere.
# The busy mark tells code616's monitor to leave this clone alone meanwhile (no double launch).
busy_mark(){ mkdir -p "${C616_CFG%/*}/busy" && : > "${C616_CFG%/*}/busy/$1"; }
rejoin_one(){ allowed "$1" || return 1; local url="$2"; busy_mark "$1"
  root "am force-stop '$1'" >/dev/null 2>&1; sleep 2
  grid_slot "$1"; mark_starting "$1"; root "am start -a android.intent.action.VIEW -d '$url' -p '$1'" >/dev/null 2>&1; }
canon_url(){ case "$1" in ps\|*) printf '%s' "${1#ps|}";; pj\|*) local r=${1#pj|}; printf 'roblox://experiences/start?placeId=%s&gameInstanceId=%s' "${r%%|*}" "${r#*|}";; esac; }
target_url(){ canon_url "$(local_target)"; }
# Links set per package on the website ("<pkg> <canonical target>" per line), shared with code616.
PKG_LINKS="${C616_CFG%/*}/pkg_links"
pkg_target(){ [ -f "$PKG_LINKS" ] && awk -v p="$1" '$1==p{print $2; exit}' "$PKG_LINKS"; }
want_target(){ local t; t=$(pkg_target "$1"); [ -n "$t" ] && printf '%s' "$t" || local_target; }   # package link, else the phone's link
want_url(){ canon_url "$(want_target "$1")"; }
write_pkg_links(){ local cfg="$1" tmp="$PKG_LINKS.agent.$$" n=0 p one t
  mkdir -p "${PKG_LINKS%/*}"; : > "$tmp"
  while IFS= read -r p; do allowed "$p" || continue
    one=$(jq -c --arg p "$p" '{target:.pkg_targets[$p]}' <<<"$cfg"); t=$(remote_target "$one")
    [ -n "$t" ] && { printf '%s %s\n' "$p" "$t" >> "$tmp"; n=$((n+1)); }
  done < <(jq -r '.pkg_targets // {} | keys[]' <<<"$cfg" 2>/dev/null)
  if [ "$n" = 0 ]; then rm -f "$tmp" "$PKG_LINKS"; else chmod 600 "$tmp"; cmp -s "$tmp" "$PKG_LINKS" && rm -f "$tmp" || mv "$tmp" "$PKG_LINKS"; fi
  # the saved link bank, so the Termux menu can pick one: "<canonical target> <name>" per line
  local bk="${C616_CFG%/*}/link_bank" i cnt nm; : > "$bk.$$"; cnt=$(jq '.links // [] | length' <<<"$cfg" 2>/dev/null || echo 0)
  for ((i=0;i<cnt;i++)); do nm=$(jq -r ".links[$i].name" <<<"$cfg" | tr -cd 'A-Za-z0-9 _.-' | cut -c1-24); t=$(remote_target "$(jq -c ".links[$i]" <<<"$cfg")")
    [ -n "$t" ] && [ -n "$nm" ] && printf '%s %s\n' "$t" "$nm" >> "$bk.$$"; done
  chmod 600 "$bk.$$"; mv "$bk.$$" "$bk"; }
# ---------- scripts (synced with the code616 Script menu) ----------
EXT_ROOT=/storage/emulated/0/Android/data
# Executors. Delta keeps its files inside each clone's own folder (files/gloop/external).
# Arceus X leaves that folder empty and keeps one shared folder on the phone for all its clones.
ARX_DIR='/storage/emulated/0/Arceus X'; ARX_AUTO="$ARX_DIR/Autoexec"
declare -A EXE=()
exec_map(){ local raw p e; EXE=(); [ ${#APPS[@]} -gt 0 ] || return 0
  raw=$(root "a=0; [ -d '$ARX_DIR' ] && a=1; for p in ${APPS[*]}; do if [ -d $EXT_ROOT/\$p/files/gloop/external ]; then echo \"\$p delta\"; elif [ \$a = 1 ]; then echo \"\$p arceus\"; fi; done" 2>/dev/null)
  while read -r p e; do [ -n "$p" ] && EXE[$p]=$e; done <<< "$raw"; }
script_dirs(){ # prints every script folder: one per Delta clone, plus the shared Arceus X folder once
  local p ext dir owner arx=0
  for p in "${APPS[@]}"; do ext="$EXT_ROOT/$p/files/gloop/external"; dir="$ext/Autoexecute"
    if root "test -d '$dir' && test ! -L '$dir'" >/dev/null 2>&1; then printf '%s\n' "$dir"
    elif root "test -d '$ext' && test ! -L '$ext' && mkdir '$dir'" >/dev/null 2>&1; then
      owner=$(root "stat -c '%u:%g' '$ext'" 2>/dev/null); [[ "$owner" =~ ^[0-9]+:[0-9]+$ ]] && root "chown '$owner' '$dir'" >/dev/null 2>&1
      printf '%s\n' "$dir"
    elif root "test -d '$ARX_DIR' && test ! -L '$ARX_DIR'" >/dev/null 2>&1; then arx=1; fi
  done
  [ "$arx" = 1 ] && root "test -d '$ARX_AUTO' || mkdir '$ARX_AUTO'" >/dev/null 2>&1 && printf '%s\n' "$ARX_AUTO"
  return 0; }
script_dirs_ex(){ local d; while IFS= read -r d; do [ -n "$d" ] || continue
    case "${1:-}" in delta) [ "$d" = "$ARX_AUTO" ] || printf '%s\n' "$d";; arceus) [ "$d" = "$ARX_AUTO" ] && printf '%s\n' "$d";; *) printf '%s\n' "$d";; esac
  done < <(script_dirs); }
# where a script lives on this phone -> executor: d = Delta only, a = Arceus X only, da = both
ex_of_groups(){ case "$1" in d) echo delta;; a) echo arceus;; *) echo '';; esac; }
script_ok(){ [[ "$1" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]{0,39}$ && "$1" != code616_hb && "$1" != deng_srv ]]; }
write_script_file(){ # name file [delta|arceus|'']   (byte-exact copy into that executor's folders)
  local name="${1%.lua}" src="$2" dir n=0
  script_ok "$name" || return 1
  while IFS= read -r dir; do [ -n "$dir" ] || continue
    root "cat > '$dir/$name.lua'" < "$src" >/dev/null 2>&1 && n=$((n+1)); done < <(script_dirs_ex "${3:-}")
  [ "$n" -gt 0 ]
}
write_script(){ local tmp="$BASE/.push.$$.$RANDOM" rc; printf '%s' "$2" > "$tmp"; write_script_file "$1" "$tmp" "${3:-}"; rc=$?; rm -f "$tmp"; return $rc; }
remove_script(){ local name="${1%.lua}" dir; script_ok "$name" || return 1
  while IFS= read -r dir; do [ -n "$dir" ] && root "rm -f '$dir/$name.lua'" >/dev/null 2>&1; done < <(script_dirs); true; }
# "name md5" for every script on this cloud phone (union of all clones).
local_scripts(){ local cmd='' d t   # each folder quoted on its own: "Arceus X" has a space in it
  while IFS= read -r d; do [ -n "$d" ] || continue; t=d; [ "$d" = "$ARX_AUTO" ] && t=a
    cmd+="(cd '$d' && md5sum *.lua | sed 's/^/$t /') 2>/dev/null; "; done < <(script_dirs); [ -n "$cmd" ] || return 0
  root "$cmd" 2>/dev/null \
    | awk '{t=$1; f=$3; sub(/^\*/,"",f); sub(/\.lua$/,"",f); if(f=="code616_hb"||f=="deng_srv") next; c[f]++; if(t=="d") nd[f]++; g[f]=g[f] t; if(!(f in s)) s[f]=$2}
           END{for(f in s){x=""; if(index(g[f],"d")) x=x "d"; if(index(g[f],"a")) x=x "a"; print f, s[f], c[f], x, nd[f]+0}}'; }
local_script_file(){ # name outfile
  local name="$1" out="$2" dir
  while IFS= read -r dir; do [ -n "$dir" ] || continue
    root "test -f '$dir/$name.lua' && cat '$dir/$name.lua'" > "$out" 2>/dev/null && return 0; done < <(script_dirs); return 1; }

# Same safe method as the code616 Delta Key menu: stop the clones (a running Delta keeps
# its old key and can write it back), replace every key file that clone uses while
# keeping the file's owner/permissions, read it back to verify, then start the clones
# that were running again.
key_files(){ # pkg -> plain/JSON key files Delta already uses for this clone (besides our cache file)
  root "find '$EXT_ROOT/$1/files' '/data/data/$1/files' -maxdepth 6 -type f \( -name license -o -name .license -o -name 'license.*' \) 2>/dev/null | head -10" 2>/dev/null; }
put_key_file(){ # path keyfile key [create]
  local f="$1" kt="$2" key="$3" create="${4:-}" old new t="$1.deng.$$.tmp" check
  if root "test -f '$f' && test ! -L '$f'" >/dev/null 2>&1; then
    old=$(root "head -c 4096 '$f'" 2>/dev/null)
    if [[ "$old" =~ ^[A-Za-z0-9._=-]*$ ]]; then new=''                       # plain key file: replace whole content
    elif [[ "$old" =~ ([\"\']?(license|key)[\"\']?[[:space:]]*[:=>][[:space:]]*[\"\']?)([A-Za-z0-9._=-]{8,512}) ]]; then
      new=${old/"${BASH_REMATCH[0]}"/"${BASH_REMATCH[1]}$key"}                # JSON/XML: swap only the key value
    else return 1; fi
    if [ -n "$new" ]; then printf '%s' "$new" > "$kt.j"; else cp "$kt" "$kt.j"; fi
    root "(cp -p '$f' '$t' 2>/dev/null || cp '$f' '$t') && cat '$kt.j' > '$t' && mv -f '$t' '$f'" >/dev/null 2>&1
    root "rm -f '$t'" >/dev/null 2>&1; check=$(root "cat '$f'" 2>/dev/null); rm -f "$kt.j"
    [ -n "$new" ] && [ "$check" = "$new" ] && return 0
    [ "$check" = "$key" ]
  elif [ -n "$create" ]; then
    root "mkdir -p '${f%/*}' && test ! -L '${f%/*}' && cat '$kt' > '$f'" >/dev/null 2>&1
    [ "$(root "cat '$f'" 2>/dev/null)" = "$key" ]
  else return 1; fi
}
write_key(){ # key mode [pkg]
  local key="$1" mode="$2" only="${3:-}" p f kt ok=0 bad=0 okc
  local -a targets=() was=()
  [[ "$key" =~ ^[A-Za-z0-9._=-]{1,512}$ ]] || { echo 'invalid key'; return 1; }
  exec_map
  for p in "${APPS[@]}"; do [ "${EXE[$p]:-}" = arceus ] && continue; [ -z "$only" ] || [ "$p" = "$only" ] && targets+=("$p"); done
  [ ${#targets[@]} -gt 0 ] || { echo 'no Delta clones (Arceus X needs no key)'; return 1; }
  kt="$BASE/.key.$$"; ( umask 077; printf '%s' "$key" > "$kt" )
  for p in "${targets[@]}"; do root "pidof '$p'" >/dev/null 2>&1 && { root "am force-stop '$p'" >/dev/null 2>&1; was+=("$p"); }; done
  [ ${#was[@]} -gt 0 ] && sleep 2
  if [ "$mode" = shared ] && [ -z "$only" ]; then
    put_key_file /storage/emulated/0/Delta/Internals/Cache/license "$kt" "$key" create && ok=1 || bad=1
  else
    for p in "${targets[@]}"; do okc=0
      put_key_file "$EXT_ROOT/$p/files/gloop/external/Internals/cache/license" "$kt" "$key" create && okc=1
      while IFS= read -r f; do [ -n "$f" ] || continue
        [ "$f" = "$EXT_ROOT/$p/files/gloop/external/Internals/cache/license" ] && continue
        put_key_file "$f" "$kt" "$key" && okc=1
      done < <(key_files "$p")
      [ $okc = 1 ] && ok=$((ok+1)) || bad=$((bad+1))
    done
  fi
  rm -f "$kt"; root sync >/dev/null 2>&1
  DELAY=$(clone_delay)
  for p in "${was[@]}"; do open_one "$p"; [ "$p" = "${was[-1]}" ] || sleep "$DELAY"; done
  scan_key
  echo "key saved on $ok clone(s)$([ $bad -gt 0 ] && echo ", failed on $bad")$([ ${#was[@]} -gt 0 ] && echo ", restarted ${#was[@]}")"
  [ $ok -gt 0 ] && [ $bad = 0 ]
}
one_pkg(){ # validate payload.pkg is one of our clones; echoes it or fails
  local want; want=$(jq -r '.pkg // empty' <<<"$1"); [ -n "$want" ] || return 1
  allowed "$want" || return 1
  local p; for p in "${APPS[@]}"; do [ "$p" = "$want" ] && { printf '%s' "$want"; return 0; }; done
  return 1
}
run_command(){ # action payload_json  -> echoes result, returns 0/1
  local action="$1" pl="$2" p ok=1 url pkg
  case "$action" in
    start_all) DELAY=$(clone_delay); for p in "${APPS[@]}"; do root "pidof '$p'" >/dev/null 2>&1 && continue; open_one "$p" || ok=0; sleep "$DELAY"; done; echo "opened ${#APPS[@]}";;
    stop_all)  for p in "${APPS[@]}"; do root "am force-stop '$p'" >/dev/null 2>&1; done; echo "stopped ${#APPS[@]}";;
    rejoin_all) DELAY=$(clone_delay); local n=0
               for p in "${APPS[@]}"; do url=$(want_url "$p"); [ -n "$url" ] || continue; rejoin_one "$p" "$url" || ok=0; n=$((n+1)); sleep "$DELAY"; done
               [ "$n" -gt 0 ] || { echo 'no target set'; return 1; }; echo "rejoined $n";;
    start_clone) pkg=$(one_pkg "$pl") || { echo 'unknown clone'; return 1; }; open_one "$pkg" && echo "started $pkg" || { echo 'failed'; return 1; };;
    stop_clone)  pkg=$(one_pkg "$pl") || { echo 'unknown clone'; return 1; }; root "am force-stop '$pkg'" >/dev/null 2>&1; echo "stopped $pkg";;
    rejoin_clone) pkg=$(one_pkg "$pl") || { echo 'unknown clone'; return 1; }; url=$(want_url "$pkg"); [ -n "$url" ] || { echo 'no target set'; return 1; }
               rejoin_one "$pkg" "$url" && echo "rejoined $pkg" || { echo 'failed'; return 1; };;
    set_clone_auto) echo 'noted';;  # per-clone auto handled by on-phone monitor; acked for now
    push_script) write_script "$(jq -r '.name' <<<"$pl")" "$(jq -r '.body' <<<"$pl")" "$(jq -r '.ex // empty' <<<"$pl")" && echo pushed || { echo 'push failed'; return 1; };;
    remove_script) remove_script "$(jq -r '.name' <<<"$pl")"; echo removed;;
    set_target) local t; t=$(remote_target "$(jq -c '{target:.}' <<<"$pl")"); apply_target "$t"; echo 'target saved';;
    set_settings) set_num "$pl" delay DELAY 0 300; set_num "$pl" interval INTERVAL 1 3600; set_num "$pl" cols COLS 0 8
                  set_num "$pl" key_wait KEY_WAIT 1 180; set_num "$pl" hb HB 0 1; set_num "$pl" hb_stale HB_STALE 30 3600; set_num "$pl" mindp MINDP 40 400; set_num "$pl" autostart AUTOSTART 0 1
                  # Auto Start needs DENG to open with Termux: make sure that is on as well
                  if [ "$(cfg_num AUTOSTART 0)" = 1 ]; then rm -f "$HOME/.deng/noauto"; [ -f "$HOME/.deng/deng-launch.sh" ] && bash "$HOME/.deng/deng-launch.sh" --autostart-install >/dev/null 2>&1 </dev/null; fi
                  echo 'settings saved';;
    set_key) local only=''; only=$(jq -r '.pkg // empty' <<<"$pl"); [ -n "$only" ] && { allowed "$only" || { echo 'unknown clone'; return 1; }; }
             write_key "$(jq -r '.key' <<<"$pl")" "$(jq -r '.mode' <<<"$pl")" "$only";;
    set_packages) set_packages "$pl";;
    update_agent) : > "$BASE/.update"; echo "update requested (running $AGENT_VERSION)";;
    *) echo "unknown action"; return 1;;
  esac
  [ "$ok" = 1 ]
}

# ---------- Delta key status (shown on the website; the key itself never leaves) ----------
KEYINFO='{}'
# One key per clone, wherever Delta keeps it: the cache file our tools write, any
# license/.license file Delta itself saved (key typed in the Delta app), or the shared
# file all clones read. Only the last 4 characters are reported.
scan_key(){
  local pkgs="${APPS[*]}" raw shared
  [ -n "$pkgs" ] || { KEYINFO='{}'; return; }
  raw=$(root "for p in $pkgs; do
      for f in \$(find /storage/emulated/0/Android/data/\$p/files /data/data/\$p/files /data/data/\$p/shared_prefs -maxdepth 6 -type f \\( -name license -o -name .license -o -name 'license.*' -o -name '*license*.xml' \\) 2>/dev/null | head -20); do
        printf '%s\t%s\t' \"\$p\" \"\$f\"; head -c 2048 \"\$f\" 2>/dev/null | tr -d '\r\n\t'; echo
      done
    done" 2>/dev/null)
  shared=$(root "head -c 2048 /storage/emulated/0/Delta/Internals/Cache/license" 2>/dev/null | tr -d '\r\n\t')
  exec_map; local arx='' p; for p in "${APPS[@]}"; do [ "${EXE[$p]:-}" = arceus ] && arx+="$p "; done
  KEYINFO=$(jq -Rnc --arg raw "$raw" --arg shared "$shared" --arg pkgs "$pkgs" --arg arx "$arx" '
    def keyof: if test("^[A-Za-z0-9._=-]{4,512}$") then .
               else ((capture("(?i)\"?(license|key)\"?\\s*[:=>]\\s*\"?(?<k>[A-Za-z0-9._=-]{8,512})") // {}).k) end;
    def tail4: if . == null then null else .[-4:] end;
    def rank: if test("/gloop/external/Internals/cache/license$") then 0 elif startswith("/storage") then 1 else 2 end;
    ($shared | keyof) as $sk
    | ($raw | split("\n") | map(select(length>0) | split("\t") | {pkg:.[0], path:.[1], k:((.[2] // "") | keyof)}) | map(select(.k != null))) as $found
    | ($arx | split(" ") | map(select(length>0))) as $ax
    | ($pkgs | split(" ") | map(select(length>0))) as $all
    | [ $all[] as $p | select(($ax | index([$p])) == null) | ($found | map(select(.pkg == $p)) | sort_by(.path|rank) | first) as $f
        | {name: ($p | split(".") | last), pkg: $p,
           tail: (if $f then ($f.k|tail4) elif $sk then ($sk|tail4) else null end),
           src: (if $f then (if ($f.path|rank) == 0 then "cache" else "delta" end) elif $sk then "shared" else null end)} ] as $list
    | {shared: ($sk|tail4), list: $list, clones: ($list|map(select(.tail))|length), total: ($list|length),
       arceus: [ $ax[] | {name: (split(".") | last), pkg: .} ],
       tails: ($list|map(.tail)|map(select(.))|unique|.[:3]), same: (($list|map(.tail)|map(select(.))|unique|length) <= 1)}' 2>/dev/null) || KEYINFO='{}'
}

# ---------- Roblox account per clone ----------
# The Roblox app keeps the signed-in username in its shared_prefs. Read-only; cookies
# and tokens are never touched. Empty when no account is signed in.
declare -A RBX=()
scan_users(){ local raw pkg u
  RBX=(); [ ${#APPS[@]} -gt 0 ] || return 0
  raw=$(root "for p in ${APPS[*]}; do d=/data/data/\$p/shared_prefs; [ -d \$d ] || continue
      u=\$(grep -hoE '<string name=\"(username|userName|UserName|user_name|LastUsername|lastLoggedInUsername)\">[^<]{3,20}<' \$d/*.xml 2>/dev/null | head -1 | sed -E 's/.*\">([^<]*)<$/\\1/')
      [ -n \"\$u\" ] && echo \"\$p \$u\"; done" 2>/dev/null)
  while read -r pkg u; do [[ -n "$pkg" && "$u" =~ ^[A-Za-z0-9_]{3,20}$ ]] && RBX[$pkg]=$u; done <<< "$raw"
}

# The "different server" auto-rejoin was removed (it caused chaos). Clean up what it left in each clone.
remove_reporter(){ [ -f "$BASE/.srv_gone" ] && return 0; local dir
  while IFS= read -r dir; do [ -n "$dir" ] && root "rm -f '$dir/deng_srv.lua' '${dir%/Autoexecute}/Workspace/deng_srv.txt'" >/dev/null 2>&1; done < <(script_dirs)
  : > "$BASE/.srv_gone"; }
SCRX='{}'
scan_scripts(){ SCRX=$(local_scripts | awk '{printf "%s\t%s\n", $1, $4}' | jq -Rnc '[inputs | split("\t") | {(.[0]): .[1]}] | add // {}' 2>/dev/null) || SCRX='{}'; [ -n "$SCRX" ] || SCRX='{}'; }

# ---------- settings sync: website <-> Termux menu (3-way merge) ----------
# Last agreed state lives in sync.state. If only one side changed since then, that side
# wins; if both changed, the website wins. Runs when the website's rev changes and
# every 30 seconds to catch edits made in the Termux menu.
STATE="$BASE/sync.state"; REMOTE="$BASE/remote.json"; LAST_REV=-1
md5(){ md5sum | cut -d' ' -f1; }
sync_now(){
  local cfg="$1" lt rt bt nt push='{}' name l r b
  [ -n "$cfg" ] || return 0
  write_pkg_links "$cfg"   # per-package links come from the website only
  lt=$(local_target); rt=$(remote_target "$cfg"); bt=$(sed -n 's/^T //p' "$STATE" 2>/dev/null)
  if [ "$lt" = "$rt" ]; then nt=$lt
  elif [ "$lt" = "$bt" ]; then apply_target "$rt"; nt=$rt; say "↓ private link from website"
  elif [ "$rt" = "$bt" ]; then push=$(jq -c --argjson t "$(target_json "$lt")" '.target=$t' <<<"$push"); nt=$lt; say "↑ private link to website"
  else apply_target "$rt"; nt=$rt; fi

  declare -A L=() R=() B=() N=() C=()
  local ndd cn gx dn; ndd=$(script_dirs_ex delta | grep -c .); declare -A G=() RX=()
  while read -r name l cn gx dn; do [ -n "$name" ] && { L[$name]=$l; C[$name]=${dn:-0}; G[$name]=$gx; }; done < <(local_scripts)
  local rdir="$BASE/rs"; rm -rf "$rdir"; mkdir -p "$rdir"; local i cnt
  cnt=$(jq '.scripts|length' <<<"$cfg" 2>/dev/null || echo 0)
  for ((i=0;i<cnt;i++)); do name=$(jq -r ".scripts[$i].name" <<<"$cfg"); script_ok "$name" || continue
    jq -j ".scripts[$i].body" <<<"$cfg" > "$rdir/$name"; R[$name]=$(md5 < "$rdir/$name"); RX[$name]=$(jq -r ".scripts[$i].ex // empty" <<<"$cfg"); done
  while read -r name b; do [ -n "$name" ] && B[$name]=$b; done < <(sed -n 's/^S //p' "$STATE" 2>/dev/null)
  local -a puts=() dels=()
  for name in $(printf '%s\n' "${!L[@]}" "${!R[@]}" "${!B[@]}" | sort -u); do
    l=${L[$name]:-}; r=${R[$name]:-}; b=${B[$name]:-}
    if [ "$l" = "$r" ]; then [ -n "$l" ] && N[$name]=$l
      # some Delta clones are missing a Delta script: copy it to them (never across Delta and Arceus X)
      if [ -n "$l" ] && [[ "${G[$name]:-}" == *d* ]] && [ "${C[$name]:-0}" -lt "$ndd" ] && [ -f "$rdir/$name" ]; then write_script_file "$name" "$rdir/$name" delta; fi
    elif [ "$l" = "$b" ] || { [ "$r" != "$b" ] && [ -n "$r" ]; }; then   # take the website's version
      if [ -n "$r" ]; then local wx; if [ -n "${G[$name]:-}" ]; then wx=$(ex_of_groups "${G[$name]}"); else wx=${RX[$name]:-}; fi
        write_script_file "$name" "$rdir/$name" "$wx" && N[$name]=$r; else remove_script "$name"; fi
      say "↓ script $name"
    else                                                                  # take the cloud phone's version
      if [ -n "$l" ]; then puts+=("$name"); N[$name]=$l; else dels+=("$name"); fi
      say "↑ script $name"
    fi
  done
  if [ ${#puts[@]} -gt 0 ] || [ ${#dels[@]} -gt 0 ]; then
    local pj='[]' f="$BASE/.up.$$"
    for name in "${puts[@]}"; do local_script_file "$name" "$f" || continue
      pj=$(jq -c --arg n "$name" --rawfile b "$f" --arg x "$(ex_of_groups "${G[$name]:-da}")" '.+[{name:$n,body:$b,ex:$x}]' <<<"$pj"); done; rm -f "$f"
    push=$(jq -c --argjson p "$pj" --argjson d "$(printf '%s\n' "${dels[@]}" | jq -Rsc 'split("\n")|map(select(length>0))')" '.scripts_put=$p|.scripts_del=$d' <<<"$push")
  fi
  if [ "$push" != '{}' ]; then
    api POST /api/device/sync "$push"
    [ "$HTTP" = 200 ] && { printf '%s' "$RESP" > "$REMOTE"; LAST_REV=$(jq -r '.rev // -1' <<<"$RESP"); } || return 1
  fi
  { printf 'T %s\n' "$nt"; for name in "${!N[@]}"; do printf 'S %s %s\n' "$name" "${N[$name]}"; done; } > "$STATE.n" && mv "$STATE.n" "$STATE"
}
pull_and_sync(){ api GET /api/device/config; [ "$HTTP" = 200 ] || return 1
  printf '%s' "$RESP" > "$REMOTE"; LAST_REV=$(jq -r '.rev // -1' <<<"$RESP"); sync_now "$RESP"; scan_scripts
  APPLIED_REV=$LAST_REV
  # tell the website right away that this change is in place (its progress bar waits for this)
  api POST /api/device/heartbeat "$(jq -nc --argjson s "$(probe_status)" '{status:$s}')" >/dev/null 2>&1 || true; }

# ---------- command worker ----------
# Commands run here, one after another, so a long Start All never stops the heartbeat
# (the website would otherwise show the cloud phone as offline mid-command).
worker(){
  local c id act pl res rc
  while IFS= read -r c; do
    [ -n "$c" ] || continue
    load_conf; load_apps
    id=$(jq -r '.id' <<<"$c"); act=$(jq -r '.action' <<<"$c"); pl=$(jq -c '.payload' <<<"$c")
    say "→ $act (#$id)"
    res=$(run_command "$act" "$pl"); rc=$?
    [ "$act" = set_key ] && : > "$BASE/.rescan"      # main loop re-reads key status right away
    api POST /api/device/ack "$(jq -nc --argjson id "$id" --argjson ok $([ $rc = 0 ] && echo true || echo false) --arg r "$res" '{id:$id,ok:$ok,result:$r}')"
  done
}

# ---------- self-update ----------
# Runs only when the website's Update button asks: fetch the latest agent; if it is a
# valid, newer script, replace this file and restart in place (same PID).
# The Termux tool (code616.sh) updates together with the agent. A running tool keeps going on
# the old copy; the new one is used the next time it starts.
TOOL="$HOME/code616.sh"; TOOL_URL="${SELF_URL%/*}/code616.sh"
tool_ver(){ sed -n 's/^CODE616_VERSION="\([A-Za-z0-9._-]*\)"$/\1/p' "$TOOL" 2>/dev/null | head -1; }
update_tool(){ local tmp="$BASE/.tool.new" nv ov; ov=$(tool_ver)
  curl -fsSL -m 60 "$TOOL_URL?t=$(date +%s)" -o "$tmp" 2>/dev/null || curl -fsSL -m 60 "https://rejoin.deng.my.id/dl/code616.sh" -o "$tmp" 2>/dev/null || { rm -f "$tmp"; return 1; }
  nv=$(sed -n 's/^CODE616_VERSION="\([A-Za-z0-9._-]*\)"$/\1/p' "$tmp" | head -1)
  if [ -z "$nv" ] || ! grep -q '__C616_PACKED__' "$tmp" || ! bash -n "$tmp" 2>/dev/null; then rm -f "$tmp"; return 1; fi
  if [ -n "$ov" ] && { [ "$nv" = "$ov" ] || [ "$(printf '%s\n%s\n' "$ov" "$nv" | sort -V | tail -1)" != "$nv" ]; }; then rm -f "$tmp"; return 1; fi
  chmod 700 "$tmp" && mv -f "$tmp" "$TOOL" && say "Tool updated to $nv"; }
self_update(){
  local tmp="$BASE/.agent.new"
  curl -fsSL -m 60 "$SELF_URL?t=$(date +%s)" -o "$tmp" 2>/dev/null || curl -fsSL -m 60 "https://rejoin.deng.my.id/dl/deng-agent.sh" -o "$tmp" 2>/dev/null || { rm -f "$tmp"; return 1; }
  local nv; nv=$(sed -n 's/^AGENT_VERSION="\([0-9.]*-[0-9]*\)"$/\1/p' "$tmp" | head -1)
  # only move forward: GitHub's cache can briefly serve an older copy
  if [ -z "$nv" ] || [ "$nv" = "$AGENT_VERSION" ] || [ "$(printf '%s\n%s\n' "$AGENT_VERSION" "$nv" | sort -V | tail -1)" != "$nv" ]; then rm -f "$tmp"; return 1; fi
  if head -1 "$tmp" | grep -q '^#!/data/data/com.termux' && bash -n "$tmp" 2>/dev/null && ! cmp -s "$tmp" "$SELF"; then
    chmod 700 "$tmp" && mv -f "$tmp" "$SELF" || return 1
    say "Updating agent…"
    kill "$WORKER" 2>/dev/null; exec 3>&-; rm -f "$BASE/cmd.fifo"; trap - EXIT
    exec bash "$SELF"
  fi
  rm -f "$tmp"; return 1
}

# ---------- main loop ----------
loop(){
  say "Agent online → $SERVER  (device $DEVICE_ID). Ctrl+C to stop."
  command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock 2>/dev/null || true
  local FIFO="$BASE/cmd.fifo"; rm -f "$FIFO"; mkfifo -m 600 "$FIFO"
  worker < "$FIFO" & WORKER=$!
  exec 3>"$FIFO"
  trap 'kill $WORKER 2>/dev/null; rm -f "$PIDFILE" "$FIFO"' EXIT
  declare -A SENT=()
  local tick=0 st body rev c id
  while true; do
    load_apps
    if [ -f "$BASE/.rescan" ]; then rm -f "$BASE/.rescan"; scan_key; fi
    if [ -f "$BASE/.update" ]; then rm -f "$BASE/.update"; update_tool; self_update || say "Agent is up to date ($AGENT_VERSION)."; fi
    if [ $((tick % 6)) = 0 ]; then
      scan_key; scan_users
      [ -s "$REMOTE" ] && sync_now "$(cat "$REMOTE")"
      scan_scripts
    fi
    tick=$((tick+1))
    st=$(probe_status)
    api POST /api/device/heartbeat "$(jq -nc --argjson s "$st" '{status:$s}')"; body=$RESP
    if [ "$HTTP" = 401 ]; then
      say 'Phone session ended (password changed?). Re-joining with the start deng login...'
      DEVICE_TOKEN=''; save_conf; ensure_linked; continue
    fi
    if [ "$HTTP" = 200 ] && [ "$(jq -r '.licensed' <<<"$body")" = false ]; then
      [ "${NOKEY:-0}" = 1 ] || say 'No active key on this cloud phone. Bind a key on the website (Key Panel).'
      NOKEY=1; LAST_REV=-1; sleep "$POLL"; continue
    fi
    NOKEY=0
    if [ $((tick % 6)) = 1 ]; then remove_reporter; fi
    if [ "$HTTP" = 200 ]; then
      rev=$(jq -r '.config.rev // -1' <<<"$body")
      [ "$rev" != "$LAST_REV" ] && pull_and_sync
      while IFS= read -r c; do
        [ -n "$c" ] || continue
        id=$(jq -r '.id' <<<"$c"); [ -n "${SENT[$id]:-}" ] && continue
        SENT[$id]=1; printf '%s\n' "$c" >&3
      done < <(jq -c '.commands[]?' <<<"$body")
    fi
    sleep "$POLL"
  done
}

main(){
  load_conf
  SERVER=$(launcher_server)          # always the same server as `start deng`
  need_root || exit 1
  # one agent per phone
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null && [ "$(cat "$PIDFILE")" != "$$" ]; then
    say "Agent already running (PID $(cat "$PIDFILE"))."; exit 0
  fi
  echo $$ > "$PIDFILE"; trap 'rm -f "$PIDFILE"' EXIT
  need timeout coreutils; need md5sum coreutils; need mkfifo coreutils
  ensure_linked
  loop
}
main "$@"
