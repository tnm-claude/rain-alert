#!/bin/bash
# Manage the Rain Alert LaunchAgent (runs at login, restarted by launchd if it dies).
#
#   scripts/install-service.sh install     render plist, install, (re)load  (idempotent)
#   scripts/install-service.sh uninstall   unload and delete the plist
#   scripts/install-service.sh start|stop|restart|status
#
# Overrides (for testing against a worktree): LABEL, PORT, APP_DIR, PYTHON.
# `stop` uses bootout so KeepAlive does not resurrect the job; it comes back at next
# login (RunAtLoad) or on `start`. Use `uninstall` to remove it for good.
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LABEL="${LABEL:-com.rainalert.server}"
PORT="${PORT:-58101}"
PYTHON="${PYTHON:-$APP_DIR/.venv/bin/python}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
TEMPLATE="$APP_DIR/deploy/com.rainalert.server.plist"

loaded() { launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }

unload() {
    loaded || return 0
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    for _ in $(seq 1 20); do loaded || return 0; sleep 0.5; done
    echo "Timed out waiting for $LABEL to unload" >&2
    return 1
}

load() {
    [ -f "$PLIST" ] || { echo "$PLIST missing; run: scripts/install-service.sh install" >&2; exit 1; }
    launchctl bootstrap "$DOMAIN" "$PLIST"
}

show_status() {
    if loaded; then
        launchctl print "$DOMAIN/$LABEL" | grep -E '^\s*(state|pid|runs|last exit code) =' || true
        curl -s -m 5 -w '\nHTTP %{http_code}\n' "http://127.0.0.1:$PORT/health" || echo "health: no response on port $PORT"
    else
        echo "$LABEL: not loaded"
    fi
}

case "${1:-status}" in
    install)
        [ -x "$PYTHON" ] || { echo "Python not found: $PYTHON (create .venv first)" >&2; exit 1; }
        mkdir -p "$HOME/Library/LaunchAgents" "$APP_DIR/logs"
        sed -e "s|__LABEL__|$LABEL|g" -e "s|__PYTHON__|$PYTHON|g" \
            -e "s|__APP_DIR__|$APP_DIR|g" -e "s|__PORT__|$PORT|g" "$TEMPLATE" > "$PLIST"
        plutil -lint "$PLIST" >/dev/null
        unload
        load
        echo "Installed $LABEL (port $PORT, dir $APP_DIR)"
        sleep 3
        show_status
        ;;
    uninstall)
        unload
        rm -f "$PLIST"
        echo "Removed $LABEL"
        ;;
    start)
        if loaded; then launchctl kickstart "$DOMAIN/$LABEL"; else load; fi
        echo "Started $LABEL"
        ;;
    stop)
        unload
        echo "Stopped $LABEL (until next login or start)"
        ;;
    restart)
        if loaded; then launchctl kickstart -k "$DOMAIN/$LABEL"; else load; fi
        echo "Restarted $LABEL"
        ;;
    status)
        show_status
        ;;
    *)
        echo "usage: $0 {install|uninstall|start|stop|restart|status}" >&2
        exit 1
        ;;
esac
