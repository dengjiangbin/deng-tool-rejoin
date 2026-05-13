#!/data/data/com.termux/files/usr/bin/sh
# Copy to ~/.termux/boot/deng-tool-rejoin.sh and chmod +x it when Termux:Boot is installed.
sleep 15
APP_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"
cd "$APP_HOME" || exit 0
sh scripts/start-agent.sh >> "$APP_HOME/logs/agent.log" 2>&1
