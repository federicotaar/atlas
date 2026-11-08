#!/usr/bin/env bash
# E2E porta di riserva (caso upgrade 2.0.x): una vecchia API versionless
# occupa 127.0.0.1:8000. L'app desktop deve:
#   1. avviare il SUO backend sulla porta di riserva 8100 (mai riusare ne'
#      contendere 8000);
#   2. connettere la UI a quel backend (registrazione via UI = prova);
#   3. lasciare la vecchia API viva e intoccata;
#   4. mostrare la voce "Importa dati da una versione precedente" e, alla
#      verifica, spiegare onestamente l'irraggiungibile (senza credenziali).
# Chiamato da ci.yml prima del lancio "normale" (che richiede 8000 libera).
set -e
sudo apt-get install -y xdotool tesseract-ocr imagemagick >/dev/null
export DISPLAY=:98
export ATLAS_DESKTOP_DATA_DIR=/tmp/atlas-ci-reserve

# --- vecchia API 2.0.x (versionless) su 8000 -------------------------------
python3 scripts/mock_old_api.py > /tmp/mock-old-api.log 2>&1 &
MPID=$!
for i in $(seq 1 10); do
  sleep 1
  curl -s http://127.0.0.1:8000/health | grep -q '"app":"atlas"' && break
done
if curl -s http://127.0.0.1:8000/health | grep -q '"version"'; then
  echo 'SETUP ROTTO: il mock deve essere versionless (2.0.x)' >&2
  kill -9 $MPID 2>/dev/null; exit 1
fi
echo 'vecchia API versionless attiva su 8000'

# --- avvio app desktop: il sidecar deve finire su 8100 ----------------------
Xvfb :98 -screen 0 1280x800x24 > /tmp/xvfb98.log 2>&1 &
sleep 2
DISPLAY=:98 openbox > /tmp/openbox98.log 2>&1 &
sleep 2
echo "T_app_launch $(date +%T.%3N)"
DISPLAY=:98 ./desktop/target/release/atlas-desktop > /tmp/atlas-reserve.log 2>&1 &

ok=0
for i in $(seq 1 90); do
  sleep 1
  BODY=$(curl -s http://127.0.0.1:8100/health || true)
  if echo "$BODY" | grep -q '"app":"atlas"' && echo "$BODY" | grep -q '"version":"2.1.1"'; then
    ok=1; break
  fi
done
if [ "$ok" != 1 ]; then
  echo 'FALLITO: backend NON avviato sulla porta di riserva 8100' >&2
  echo "T_fail $(date +%T.%3N)" >&2
  tail -40 /tmp/atlas-reserve.log >&2 || true
  exit 1
fi
echo "T_sidecar_health_ok $(date +%T.%3N)"
echo "backend di riserva attivo: $BODY"

# --- la vecchia API resta viva e intoccata ----------------------------------
if curl -s http://127.0.0.1:8000/health | grep -q '"version"'; then
  echo 'FALLITO: 8000 sostituita dal nuovo backend: vecchia API toccata' >&2
  exit 1
fi
kill -0 $MPID 2>/dev/null || { echo 'FALLITO: processo vecchia API TERMINATO' >&2; exit 1; }
echo 'vecchia API ancora viva e intoccata'

# --- helpers screenshot/OCR onesti ------------------------------------------
shot() {
  local out="$1"
  for i in 1 2 3 4 5; do
    xdotool search --name 'ATLAS' | head -1 | xargs -r xdotool windowactivate --sync 2>/dev/null || true
    sleep 2
    import -window root "$out"
    if [ "$(stat -c%s "$out" 2>/dev/null || echo 0)" -gt 10000 ]; then return 0; fi
    sleep 2
  done
  echo "E2E riserva: screenshot $out vuoto/nero" >&2
  exit 1
}
ocr_has() {
  convert "$1" -negate /tmp/ocr-reserve.png
  tesseract /tmp/ocr-reserve.png stdout 2>/dev/null | grep -qi "$2"
}
# Clicca al centro della prima parola trovata via OCR (coordinate TSV).
click_word() {
  local png="$1" word="$2"
  convert "$png" -negate /tmp/ocr-click.png
  local geo
  geo=$(tesseract /tmp/ocr-click.png stdout tsv 2>/dev/null | \
    awk -F'\t' -v w="$word" 'NR>1 && tolower($12) ~ tolower(w) {print $7" "$8" "$9" "$10; exit}')
  [ -n "$geo" ] || { echo "E2E riserva: parola '$word' NON trovata via OCR" >&2; return 1; }
  set -- $geo
  xdotool mousemove $(( $1 + $3 / 2 )) $(( $2 + $4 / 2 )) click 1
}

# --- UI: registrazione = prova che il frontend parla con 8100 ---------------
for i in $(seq 1 30); do
  sleep 1
  xdotool search --name 'ATLAS' >/dev/null 2>&1 && break
done
sleep 5
shot /tmp/reserve-1-auth.png
xdotool key Tab Tab Tab Tab Return
sleep 1
xdotool type --delay 30 'riserva@example.com'
xdotool key Tab
xdotool type --delay 30 'password-riserva-e2e'
xdotool key Tab Return
sleep 6
shot /tmp/reserve-2-login.png
# Il mock 2.0.x su 8000 risponderebbe 404 a /auth/register: se la UI entra,
# il frontend sta parlando col backend di riserva su 8100.
ocr_has /tmp/reserve-2-login.png 'Esci' || {
  echo 'E2E riserva: registrazione NON riuscita (frontend NON connesso al backend di riserva?)' >&2
  echo "T_ui_fail $(date +%T.%3N)" >&2
  tail -40 /tmp/atlas-reserve.log >&2 || true
  exit 1
}
echo 'registrazione via UI OK: frontend connesso al backend di riserva'

# --- Dashboard: voce import sempre disponibile ------------------------------
click_word /tmp/reserve-2-login.png 'Dashboard'
sleep 4
shot /tmp/reserve-3-dashboard.png
ocr_has /tmp/reserve-3-dashboard.png 'Importa' || {
  echo 'E2E riserva: voce "Importa dati da una versione precedente" NON visibile in Dashboard' >&2
  exit 1
}
echo 'voce import visibile in Dashboard'

# --- probe onesto via API del backend di riserva: irraggiungibile, niente
#     credenziali nell'errore -----------------------------------------------
TOKEN=$(curl -s -X POST http://127.0.0.1:8100/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"riserva@example.com","password":"password-riserva-e2e"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
PROBE=$(curl -s -X POST http://127.0.0.1:8100/setup/migration-probe -H "Authorization: Bearer $TOKEN")
echo "probe: $PROBE"
echo "$PROBE" | grep -q '"state":"unreachable"' || {
  echo 'E2E riserva: probe NON unreachable (Postgres assente in CI)' >&2; exit 1; }
echo "$PROBE" | grep -qi 'atlas:atlas@' && {
  echo 'E2E riserva: credenziali nel messaggio di errore!' >&2; exit 1; }

# --- pulizia: 8000 deve tornare libera per il lancio "normale" --------------
pkill -f 'target/release/atlas-desktop' || true
pkill -f 'WebKitWebProcess' || true
pkill -f 'WebKitNetworkProcess' || true
kill $MPID 2>/dev/null || true
for i in $(seq 1 15); do
  sleep 1
  curl -s http://127.0.0.1:8000/health >/dev/null 2>&1 || break
done
if ss -ltn | grep -q ':8000'; then
  echo 'porta 8000 ancora occupata dopo pulizia' >&2
  exit 1
fi
if ss -ltn | grep -q ':8100'; then
  echo 'porta 8100 ancora occupata dopo pulizia' >&2
  exit 1
fi
echo 'E2E porta di riserva: OK (8100 usata, vecchia API intoccata, UI connessa, import visibile, probe onesto)'
