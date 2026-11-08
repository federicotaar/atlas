#!/bin/sh
# Installa ATLAS come servizio on-boot (spec sez. 8): systemd utente su
# Linux, launchd su macOS. Scrive SOLO nella home dell'utente. Esegui dalla
# root del repo: scripts/service/install-service.sh
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
PYTHON="$DIR/backend/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=$(command -v python3)
mkdir -p "$DIR/logs"
if [ "$(uname -s)" = "Darwin" ]; then
    plist="$HOME/Library/LaunchAgents/com.atlas.agent.plist"
    sed -e "s|__ATLAS_DIR__|$DIR|g" -e "s|__PYTHON__|$PYTHON|g" \
        "$DIR/scripts/service/com.atlas.agent.plist" > "$plist"
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
    echo "ATLAS installato come agente launchd: $plist"
else
    unit="$HOME/.config/systemd/user/atlas-agent.service"
    mkdir -p "$HOME/.config/systemd/user"
    sed -e "s|__ATLAS_DIR__|$DIR|g" -e "s|__PYTHON__|$PYTHON|g" \
        "$DIR/scripts/service/atlas-agent.service" > "$unit"
    systemctl --user daemon-reload
    systemctl --user enable --now atlas-agent.service
    echo "ATLAS installato come servizio systemd utente: $unit"
fi
echo "Log: $DIR/logs/. Per rimuoverlo: scripts/service/uninstall-service.sh"
