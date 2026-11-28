#!/data/data/com.termux/files/usr/bin/bash
# One-time Termux setup so you can launch everything with:  start deng
# Optional: a setup code from the website signs this cloud phone in automatically:
#   curl -fsSL .../install-deng.sh | bash -s -- CODE
set -u
SETUP_CODE="${1:-}"
SERVER="https://deng-rejoin.discloud.dev"
RAW="https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main"
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
BIN="$PREFIX/bin"
DENG="$HOME/.deng"
mkdir -p "$DENG"

# colors only on a real terminal
if [ -t 1 ]; then Y=$'\033[33m'; G=$'\033[32m'; R=$'\033[31m'; D=$'\033[2m'; B=$'\033[1m'; Z=$'\033[0m'; else Y=; G=; R=; D=; B=; Z=; fi

RED=$'\033[1;31m'
banner(){
  printf '\n'
  printf '%b\n' ' \033[1;91m██████\033[0;31m╗ \033[1;91m███████\033[0;31m╗\033[1;91m███\033[0;31m╗   \033[1;91m██\033[0;31m╗ \033[1;91m██████\033[0;31m╗\033[0m'
  printf '%b\n' ' \033[1;91m██\033[0;31m╔══\033[1;91m██\033[0;31m╗\033[1;91m██\033[0;31m╔════╝\033[1;91m████\033[0;31m╗  \033[1;91m██\033[0;31m║\033[1;91m██\033[0;31m╔════╝\033[0m'
  printf '%b\n' ' \033[1;91m██\033[0;31m║  \033[1;91m██\033[0;31m║\033[1;91m█████\033[0;31m╗  \033[1;91m██\033[0;31m╔\033[1;91m██\033[0;31m╗ \033[1;91m██\033[0;31m║\033[1;91m██\033[0;31m║  \033[1;91m███\033[0;31m╗\033[0m'
  printf '%b\n' ' \033[1;91m██\033[0;31m║  \033[1;91m██\033[0;31m║\033[1;91m██\033[0;31m╔══╝  \033[1;91m██\033[0;31m║╚\033[1;91m██\033[0;31m╗\033[1;91m██\033[0;31m║\033[1;91m██\033[0;31m║   \033[1;91m██\033[0;31m║\033[0m'
  printf '%b\n' ' \033[1;91m██████\033[0;31m╔╝\033[1;91m███████\033[0;31m╗\033[1;91m██\033[0;31m║ ╚\033[1;91m████\033[0;31m║╚\033[1;91m██████\033[0;31m╔╝\033[0m'
  printf '%b\n' ' \033[0;31m╚═════╝ ╚══════╝╚═╝  ╚═══╝ ╚═════╝\033[0m'
  printf '%b\n' '\033[1;91m        R  E  J  O  I  N\033[0m'
  printf '  %sMenyiapkan cloud phone kamu…%s\n\n' "$D" "$Z"
}

# run a command while showing a spinner + elapsed seconds, then ✓ or ✗
step(){
  local label=$1; shift
  "$@" >/dev/null 2>&1 &
  local pid=$! start=$SECONDS i=0 sp='|/-\'
  [ -t 1 ] && printf '\033[?25l'
  while kill -0 "$pid" 2>/dev/null; do
    printf '\r  %s%s%s %-30s %ss ' "$Y" "${sp:i++%4:1}" "$Z" "$label" "$((SECONDS-start))"
    sleep 0.2
  done
  wait "$pid"; local rc=$?
  if [ "$rc" = 0 ]; then printf '\r  %s✓%s %-30s %s%ss%s   \n' "$G" "$Z" "$label" "$D" "$((SECONDS-start))" "$Z"
  else printf '\r  %s✗%s %-30s %sgagal%s   \n' "$R" "$Z" "$label" "$R" "$Z"; fi
  [ -t 1 ] && printf '\033[?25h'
  return "$rc"
}

export DEBIAN_FRONTEND=noninteractive
CONF='-o Dpkg::Options::=--force-confold'

update_repos(){ yes | pkg update -y $CONF >/dev/null 2>&1; apt-get update -y >/dev/null 2>&1; return 0; }
install_pkgs(){ # fresh phones need repos updated first; retry each tool that is still missing
  pkg install -y $CONF curl jq coreutils openssl-tool gzip >/dev/null 2>&1
  local p ok=1
  for p in curl:curl jq:jq stat:coreutils openssl:openssl-tool gzip:gzip; do
    command -v "${p%%:*}" >/dev/null 2>&1 && continue
    pkg install -y $CONF "${p##*:}" >/dev/null 2>&1
    command -v "${p%%:*}" >/dev/null 2>&1 || ok=0
  done
  [ "$ok" = 1 ]
}
# Each file: GitHub first, then the same file served by the website, a few tries each.
# Only a real script is kept (never an error page); the last error is kept to show the user.
MIRROR="https://rejoin.deng.my.id/dl"
fetch(){ local u t
  for u in "$RAW/$1" "$MIRROR/$1"; do
    for t in 1 2 3; do
      if curl -fsSL --connect-timeout 15 -m 90 "$u?t=$(date +%s)" -o "$2.tmp" 2>"$DENG/.dlerr" && [ -s "$2.tmp" ] && head -c 2 "$2.tmp" | grep -q '#!'; then
        mv -f "$2.tmp" "$2" && chmod +x "$2" && return 0; fi
      sleep $((t*2))
    done
  done
  rm -f "$2.tmp"; return 1; }
get_tools(){ fetch deng-launch.sh "$DENG/deng-launch.sh" && fetch code616.sh "$HOME/code616.sh" && fetch deng-agent.sh "$HOME/deng-agent.sh"; }

banner
step 'Memperbarui repositori' update_repos
if ! step 'Memasang paket' install_pkgs; then
  printf '\n  %sBeberapa paket gagal dipasang.%s Jalankan ini lalu ulangi:\n' "$R" "$Z"
  printf '    %spkg update && pkg install curl jq coreutils openssl-tool gzip%s\n\n' "$B" "$Z"
  exit 1
fi
if ! step 'Mengunduh DENG tools' get_tools; then
  why=$(tail -1 "$DENG/.dlerr" 2>/dev/null)
  printf '\n  %sGagal mengunduh DENG tools.%s\n' "$R" "$Z"
  [ -n "$why" ] && printf '  %s%s%s\n' "$D" "$why" "$Z"
  if ! curl --version >/dev/null 2>&1; then printf '  curl rusak setelah update. Jalankan: %spkg upgrade -y curl openssl%s lalu ulangi.\n\n' "$B" "$Z"
  else printf '  Cek internet cloud phone ini, lalu jalankan perintah pasang sekali lagi.\n\n'; fi
  exit 1
fi

# install the `start` command so `start deng` works
cat > "$BIN/start" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
case "\${1:-}" in
  deng) exec bash "$DENG/deng-launch.sh" "\${@:2}" ;;
  *) echo 'usage: start deng' ;;
esac
EOF
chmod +x "$BIN/start"
printf '  %s✓%s %-30s\n' "$G" "$Z" 'Memasang perintah start deng'

# open DENG automatically whenever Termux starts (turn off in the DENG menu)
bash "$DENG/deng-launch.sh" --autostart-install >/dev/null 2>&1 || true

# sign in with the website's setup code: no username/password needed
if [ -n "$SETUP_CODE" ]; then
  redeem(){ out=$(curl -sS -m 20 -w '\n%{http_code}' -X POST -H 'Content-Type: application/json' \
        --data-binary "{\"code\":\"$(printf '%s' "$SETUP_CODE" | tr -cd 'A-Za-z0-9')\"}" "$SERVER/api/setup-code/redeem" 2>/dev/null) || out=$'\n000'
    code=${out##*$'\n'}; body=${out%$'\n'*}; [ "$code" = 200 ]; }
  step 'Masuk ke akun' redeem
  if [ "${code:-}" = 200 ]; then
    tok=$(printf '%s' "$body" | jq -r '.token // empty')
    ( umask 077; printf '%s' "$tok" > "$DENG/auth"; printf '%s' "$SERVER" > "$DENG/server" )
    printf '    %smasuk sebagai %s%s\n' "$D" "$(printf '%s' "$body" | jq -r '.user.username')" "$Z"
  else
    printf '    %s%s. Bisa juga login pakai username dan password nanti.%s\n' "$D" "$(printf '%s' "${body:-}" | jq -r '.error // "server tidak terjangkau"' 2>/dev/null)" "$Z"
  fi
fi

printf '\n  %s%s✓ Siap!%s DENG terbuka sendiri setiap Termux dibuka.\n' "$B" "$G" "$Z"
printf '  %sAtau jalankan kapan saja:%s start deng\n\n' "$D" "$Z"
# open it right away when run from a Termux window
if [ -e /dev/tty ] && { : < /dev/tty; } 2>/dev/null; then
  export DENG_AUTO_DONE=1
  exec bash "$DENG/deng-launch.sh" < /dev/tty
fi
