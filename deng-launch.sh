#!/data/data/com.termux/files/usr/bin/bash
# DENG launcher — the single entry point. Run with: start deng
set -u
HOME_DIR="$HOME"
CODE616="$HOME_DIR/code616.sh"
AGENT="$HOME_DIR/deng-agent.sh"
RAW_CODE616="https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/code616.sh"
RAW_AGENT="https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/deng-agent.sh"

say(){ printf '%s\n' "$*"; }
getfile(){ curl -fsSL "$1" -o "$2" 2>/dev/null && [ -s "$2" ]; }

menu(){
  while true; do
    clear 2>/dev/null || true
    say '========================'
    say '        D E N G'
    say '     rejoin.deng.my.id'
    say '========================'
    say '[1] Rejoin Tool (local menu)'
    say '[2] Website Agent (link to site)'
    say '[3] Update tools'
    say '[0] Exit'
    say '------------------------'
    printf 'Select [0-3]: '; read -r c
    case "$c" in
      1) [ -f "$CODE616" ] || getfile "$RAW_CODE616" "$CODE616" || { say 'Could not download code616.sh'; read -r _; continue; }
         bash "$CODE616" ;;
      2) [ -f "$AGENT" ] || getfile "$RAW_AGENT" "$AGENT" || { say 'Could not download deng-agent.sh'; read -r _; continue; }
         bash "$AGENT" ;;
      3) say 'Updating...'; getfile "$RAW_CODE616" "$CODE616" && say 'code616 updated.' || say 'code616 update failed.'
         getfile "$RAW_AGENT" "$AGENT" && say 'agent updated.' || say 'agent update failed.'
         read -r -p 'Enter to continue... ' _ ;;
      0) exit 0 ;;
      *) say 'Pick 0-3.'; sleep 1 ;;
    esac
  done
}
menu
