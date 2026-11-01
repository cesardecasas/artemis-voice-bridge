#!/usr/bin/env bash
# Manage the Artemis systemd --user services.
#
#   scripts/artemis-services.sh install     install units, enable at boot, start
#   scripts/artemis-services.sh uninstall   stop, disable and remove the units
#   scripts/artemis-services.sh start|stop|restart [stt|tts|app]
#   scripts/artemis-services.sh status      one line per service + /health
#   scripts/artemis-services.sh logs [stt|tts|app] [journalctl args...]
#
# Re-run `install` after changing ports in .env or moving the project folder.
set -Eeuo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$BASE_DIR/deploy/systemd"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
TARGET=artemis-voice.target
SERVICES=(stt tts app)

unit() { echo "artemis-voice-$1.service"; }

units_for() { # [name] -> unit names (all when empty)
    if [[ -z "${1:-}" ]]; then
        # List the services explicitly: stopping only the target returns before
        # the PartOf= stops finish, and a quick start would cancel them.
        echo "$TARGET $(unit stt) $(unit tts) $(unit app)"
    else
        [[ " ${SERVICES[*]} " == *" $1 "* ]] || { echo "unknown service '$1' (stt|tts|app)" >&2; exit 2; }
        unit "$1"
    fi
}

port_of() { # stt|tts|app -> configured port (reads .env through artemis.config)
    "$BASE_DIR/venv/bin/python" -c "
import sys; sys.path.insert(0, '$BASE_DIR')
from artemis.config import get_settings
s = get_settings(); print({'stt': s.stt.port, 'tts': s.tts.port, 'app': s.app.port}['$1'])"
}

installed() { [[ -f "$UNIT_DIR/$TARGET" ]]; }

cmd_install() {
    for file in venv/bin/python venv-openvino/bin/python venv-tts/bin/python; do
        [[ -x "$BASE_DIR/$file" ]] || { echo "Missing $file (see README, Setup)" >&2; exit 1; }
    done
    # The old background mode (pid files) holds the ports: stop it first.
    "$BASE_DIR/stop-artemis-voice.sh" --legacy-only

    mkdir -p "$UNIT_DIR"
    install -m 644 "$UNIT_SRC/$TARGET" "$UNIT_DIR/$TARGET"
    for name in "${SERVICES[@]}"; do
        local port; port="$(port_of "$name")"
        sed -e "s|@BASE_DIR@|$BASE_DIR|g" -e "s|@PORT@|$port|g" \
            "$UNIT_SRC/$(unit "$name").in" > "$UNIT_DIR/$(unit "$name")"
        chmod 644 "$UNIT_DIR/$(unit "$name")"
        echo "installed $(unit "$name") (port $port)"
    done
    systemctl --user daemon-reload
    systemctl --user enable "$(unit stt)" "$(unit tts)" "$(unit app)" "$TARGET"

    if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]]; then
        echo "NOTE: lingering is off, so services start at login, not at boot."
        echo "      Enable boot start with: sudo loginctl enable-linger $USER"
    fi
    echo "Starting (Whisper takes up to ~1 min to load onto the GPU)..."
    systemctl --user reset-failed "$TARGET" "$(unit stt)" "$(unit tts)" "$(unit app)" 2>/dev/null || true
    systemctl --user restart "$TARGET" "$(unit stt)" "$(unit tts)" "$(unit app)"
    cmd_status
}

cmd_uninstall() {
    systemctl --user disable --now "$TARGET" "$(unit stt)" "$(unit tts)" "$(unit app)" 2>/dev/null || true
    rm -f "$UNIT_DIR/$TARGET" "$UNIT_DIR/$(unit stt)" "$UNIT_DIR/$(unit tts)" "$UNIT_DIR/$(unit app)"
    systemctl --user daemon-reload
    systemctl --user reset-failed 2>/dev/null || true
    echo "Removed. ./start-artemis-voice.sh now uses the background mode again."
}

cmd_status() {
    installed || { echo "Not installed (scripts/artemis-services.sh install)"; return 1; }
    printf '%-6s %-10s %-8s %s\n' SERVICE STATE HEALTH SINCE
    for name in "${SERVICES[@]}"; do
        local u state since port health
        u="$(unit "$name")"
        state="$(systemctl --user is-active "$u" 2>/dev/null || true)"
        since="$(systemctl --user show "$u" -p ActiveEnterTimestamp --value)"
        port="$(port_of "$name")"
        health="$(curl -sS --max-time 2 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/health" 2>/dev/null || true)"
        [[ "$health" == "200" ]] && health=ok || health="-"
        printf '%-6s %-10s %-8s %s\n' "$name" "$state" "$health" "${since:--}"
    done
    printf 'boot   %s\n' "$(systemctl --user is-enabled "$TARGET" 2>/dev/null || true)"
}

cmd_logs() {
    local name="${1:-}"; shift || true
    local args=(-o cat)
    if [[ -n "$name" && "$name" != -* ]]; then
        args+=(-u "$(unit "$name")")
        units_for "$name" >/dev/null
    else
        [[ -n "$name" ]] && set -- "$name" "$@"
        args+=(-u "$(unit stt)" -u "$(unit tts)" -u "$(unit app)")
    fi
    exec journalctl --user "${args[@]}" "$@"
}

case "${1:-}" in
    install) cmd_install ;;
    uninstall) cmd_uninstall ;;
    start | stop | restart)
        installed || { echo "Not installed (scripts/artemis-services.sh install)" >&2; exit 1; }
        # A deliberate start/restart shouldn't hit the crash-loop limit
        # (StartLimitBurst counts every start), so clear its counter first.
        # shellcheck disable=SC2046  # word splitting intended
        [[ "$1" == stop ]] || systemctl --user reset-failed $(units_for "${2:-}") 2>/dev/null || true
        # shellcheck disable=SC2046
        systemctl --user "$1" $(units_for "${2:-}")
        [[ "$1" == stop ]] || cmd_status ;;
    status) cmd_status ;;
    logs) shift; cmd_logs "$@" ;;
    *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
