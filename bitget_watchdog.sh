#!/bin/bash
# Bitget Bot Watchdog — healthcheck, no-duplicates, auto-restart
# Läuft als Cronjob (no_agent). 
# Output-Regel: Bei OK → stille (exit 0, stdout leer). Nur Aktionen → melden.
# Exit: 0=OK(silent), 1=restarted, 2=duplicates-removed, 3=error

set -euo pipefail

BOT_DIR="$HOME/bitget_bot_v1"
BOT_SCRIPT="spread_scalper.py"
BOT_CMD="$BOT_DIR/.venv/bin/python3 $BOT_DIR/$BOT_SCRIPT"
PID_FILE="/tmp/bitget-bot.pid"
LOG_FILE="$BOT_DIR/watchdog.log"

# Log nur in Datei, nicht auf stdout (wg. no_agent silent-on-ok)
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Nachricht auf stdout (wird bei no_agent an Telegram geliefert)
msg() {
    echo "$*"
    log "$*"
}

cd "$BOT_DIR" 2>/dev/null || { msg "❌ BOT_DIR nicht gefunden"; exit 3; }

# Bot-Prozesse finden (case-insensitive, full cmdline match)
PIDS=$(pgrep -fi "spread_scalper\.py" || true)
COUNT=$(echo "$PIDS" | grep -c . || true)

case "$COUNT" in
    0)
        msg "⚠️ Bitget Bot tot — starte neu"
        nohup $BOT_CMD > /dev/null 2>&1 &
        NEW_PID=$!
        echo "$NEW_PID" > "$PID_FILE"
        msg "  ✅ PID $NEW_PID gestartet"
        exit 1
        ;;
    1)
        # Exakt 1 Instanz — alles gut, still bleiben
        BOT_PID=$(echo "$PIDS" | head -1)
        echo "$BOT_PID" > "$PID_FILE"
        log "✅ Bot läuft (PID $BOT_PID)"
        exit 0
        ;;
    *)
        # Mehrere Instanzen — älteste killen, jüngste behalten
        msg "⚠️ $COUNT Bitget Bot Instanzen — bereinige"
        SORTED=$(echo "$PIDS" | tr ' ' '\n' | sort -n)
        KEEP=$(echo "$SORTED" | tail -1)
        for pid in $(echo "$SORTED" | head -n -1); do
            kill "$pid" 2>/dev/null && msg "  ✂️ Duplikat PID $pid gekillt" || true
        done
        echo "$KEEP" > "$PID_FILE"
        msg "  ✅ Behalte PID $KEEP"
        exit 0
        ;;
esac
