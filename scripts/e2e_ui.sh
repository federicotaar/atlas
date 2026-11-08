#!/usr/bin/env bash
# E2E UI reale: registrazione, memoria, riavvio. Chiamato da ci.yml.
set -e
sudo apt-get install -y xdotool tesseract-ocr >/dev/null
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
# Anche i processi figli WebKit: se sopravvivono tengono il lock del profilo
# e la finestra del secondo avvio non viene mai disegnata (screenshot nero).
pkill -f 'WebKitWebProcess' || true
pkill -f 'WebKitNetworkProcess' || true
for i in $(seq 1 15); do
  sleep 1
  pgrep -f 'target/release/atlas-desktop|WebKitWebProcess' >/dev/null 2>&1 || break
done
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
# La finestra post-riavvio va riattivata esplicitamente: su Xvfb nudo
# altrimenti lo screenshot puo' venire vuoto e il focus non e' nella app.
# shot(): cattura onesta — riprova l'attivazione e FALLISCE se il PNG resta
# nero/vuoto, mai uno screenshot finto-verde.
shot() {
  local out="$1"
  for i in 1 2 3 4 5; do
    xdotool search --name 'ATLAS' | head -1 | xargs -r xdotool windowactivate --sync 2>/dev/null || true
    sleep 2
    import -window root "$out"
    if [ "$(stat -c%s "$out" 2>/dev/null || echo 0)" -gt 10000 ]; then
      return 0
    fi
    sleep 2
  done
  echo "E2E UI: screenshot $out vuoto/nero dopo 5 tentativi: cattura fallita"
  exit 1
}
shot /tmp/e2e-6-riavvio.png
# OCR onesto (tesseract): la prova e' quel che si VEDE nel frame, non solo
# che il PNG non e' nero o diverso dal precedente.
ocr_has() {
  convert "$1" -negate /tmp/ocr-work.png
  tesseract /tmp/ocr-work.png stdout 2>/dev/null | grep -qi "$2"
}
# La sessione sopravvive al riavvio: stato loggato visibile (Esci).
ocr_has /tmp/e2e-6-riavvio.png 'Esci' || { echo 'E2E UI: post-riavvio NON loggato (Esci non visibile)'; exit 1; }
# Richiamo via UI dopo il riavvio: click MIRATO sul composer (a 1280x800 il
# campo e' in basso al centro; il focus non e' nella app, i Tab a occhio
# chiuso finiscono sui pulsanti di navigazione), domanda, risposta visibile.
xdotool mousemove 640 737 click 1
sleep 1
xdotool type --delay 20 'come si chiama il mio gatto?'
xdotool key Tab Return
sleep 8
shot /tmp/e2e-7-richiamo-riavvio.png
ocr_has /tmp/e2e-7-richiamo-riavvio.png 'Briciola' || { echo 'E2E UI: risposta Briciola NON visibile dopo riavvio'; exit 1; }
# Relogin esplicito: Esci -> form login -> click mirato sul campo email,
# password, Entra. Coordinate dal layout noto del form (card centrata).
xdotool mousemove 1145 55 click 1
sleep 3
shot /tmp/e2e-8-logout.png
ocr_has /tmp/e2e-8-logout.png 'Entra' || { echo 'E2E UI: form login NON visibile dopo Esci'; exit 1; }
xdotool mousemove 640 410 click 1
xdotool type --delay 30 'e2e@example.com'
xdotool key Tab
xdotool type --delay 30 'password-locale-e2e'
xdotool key Tab Return
sleep 6
shot /tmp/e2e-9-relogin.png
ocr_has /tmp/e2e-9-relogin.png 'Esci' || { echo 'E2E UI: relogin NON riuscito (stato loggato non visibile)'; exit 1; }
# Richiamo via UI dopo il relogin: la memoria sopravvive a riavvio + relogin.
xdotool mousemove 640 737 click 1
sleep 1
xdotool type --delay 20 'come si chiama il mio gatto?'
xdotool key Tab Return
sleep 8
shot /tmp/e2e-10-richiamo-relogin.png
ocr_has /tmp/e2e-10-richiamo-relogin.png 'Briciola' || { echo 'E2E UI: risposta Briciola NON visibile dopo relogin'; exit 1; }
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login -H 'content-type: application/json' -d '{"email":"e2e@example.com","password":"password-locale-e2e"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
curl -s http://127.0.0.1:8000/me/export -H "Authorization: Bearer $TOKEN" | tee /tmp/e2e-export.json | grep -i briciola || { echo 'Briciola NON trovata in /me/export:'; cat /tmp/e2e-export.json; exit 1; }
echo 'E2E UI: registrazione + salvataggio + riavvio + relogin UI + persistenza OK'
