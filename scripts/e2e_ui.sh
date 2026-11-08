#!/usr/bin/env bash
# E2E UI reale: registrazione, memoria, riavvio. Chiamato da ci.yml.
set -e
sudo apt-get install -y xdotool >/dev/null
export DISPLAY=:99
ss -ltn | grep ':8000' || { echo 'nessun listener su 8000' >&2; exit 1; }
ss -ltn | grep ':8000' | grep -q '127.0.0.1:8000' || { echo 'backend NON in ascolto su 127.0.0.1' >&2; exit 1; }
if ss -ltn | grep ':8000' | grep -qE '(0\.0\.0\.0|\*|\[::\]):8000'; then echo 'bind pubblico vietato' >&2; exit 1; fi
echo 'bind solo loopback OK'
curl -sf http://127.0.0.1:8000/auth/modes | tee /tmp/modes.json
grep -q 'local_password' /tmp/modes.json || { echo 'modes NON locali: il sidecar non e in modalita desktop' >&2; exit 1; }
echo 'modes desktop OK'
xdotool search --name 'ATLAS' | head -1 | xargs -r xdotool windowactivate --sync || true
sleep 2
import -window root /tmp/e2e-1-auth.png
xdotool key Tab Tab Tab Tab Return
sleep 1
xdotool type --delay 30 'e2e@example.com'
xdotool key Tab
xdotool type --delay 30 'password-locale-e2e'
import -window root /tmp/e2e-2-registrazione.png
xdotool key Tab Return
sleep 4
import -window root /tmp/e2e-3-chat.png
xdotool type --delay 20 'ricorda che il gatto si chiama Briciola'
xdotool key Tab Return
sleep 5
import -window root /tmp/e2e-4-salvato.png
xdotool type --delay 20 'come si chiama il mio gatto?'
xdotool key Tab Return
sleep 5
import -window root /tmp/e2e-5-richiamo.png
pkill -f 'target/release/atlas-desktop' || true
sleep 3
DISPLAY=:99 ./desktop/target/release/atlas-desktop >> /tmp/atlas-desktop.log 2>&1 &
for i in $(seq 1 60); do
  sleep 1
  curl -s http://127.0.0.1:8000/health | grep -q '"app":"atlas"' && break
done
for i in $(seq 1 30); do
  sleep 1
  xdotool search --name 'ATLAS' >/dev/null 2>&1 && break
done
sleep 5
import -window root /tmp/e2e-6-riavvio.png
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login -H 'content-type: application/json' -d '{"email":"e2e@example.com","password":"password-locale-e2e"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
curl -s http://127.0.0.1:8000/me/export -H "Authorization: Bearer $TOKEN" | tee /tmp/e2e-export.json | grep -i briciola || { echo 'Briciola NON trovata in /me/export:'; cat /tmp/e2e-export.json; exit 1; }
echo 'E2E UI: registrazione + salvataggio + riavvio + persistenza OK'
