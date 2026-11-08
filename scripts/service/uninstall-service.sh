#!/bin/sh
# Rimuove il servizio on-boot di ATLAS. Non tocca dati, memoria o .env.
set -eu
if [ "$(uname -s)" = "Darwin" ]; then
    plist="$HOME/Library/LaunchAgents/com.atlas.agent.plist"
    launchctl unload "$plist" 2>/dev/null || true
    rm -f "$plist"
    echo "Agente launchd rimosso."
else
    systemctl --user disable --now atlas-agent.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/atlas-agent.service"
    systemctl --user daemon-reload
    echo "Servizio systemd utente rimosso."
fi
