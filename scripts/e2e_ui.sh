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
# Click al centro della parola trovata via OCR (coordinate TSV). Prima la
# corrispondenza ESATTA (case-sensitive): la voce di navigazione "Chat" si
# distingue cosi' dal "chat" minuscolo nel testo della card WhatsApp. Senza
# questo, il primo match TSV (non ordinato top-down) era quello sbagliato e
# il click non navigava (run 42472102718). Fallback: match case-insensitive
# piu' in alto nella pagina.
click_word() {
  local png="$1" word="$2"
  convert "$png" -negate /tmp/ocr-click.png
  local geo
  geo=$(tesseract /tmp/ocr-click.png stdout tsv 2>/dev/null | \
    awk -F'\t' -v w="$word" 'NR>1 && $12 == w {print $7" "$8" "$9" "$10; exit}')
  if [ -z "$geo" ]; then
    geo=$(tesseract /tmp/ocr-click.png stdout tsv 2>/dev/null | \
      awk -F'\t' -v w="$word" 'NR>1 && tolower($12) ~ tolower(w) {print $8" "$7" "$9" "$10}' | \
      sort -n | head -1 | awk '{print $2" "$1" "$3" "$4}')
  fi
  [ -n "$geo" ] || { echo "E2E UI: parola '$word' NON trovata via OCR" >&2; return 1; }
  set -- $geo
  echo "click_word '$word' -> x=$1 y=$2 w=$3 h=$4"
  xdotool mousemove $(( $1 + $3 / 2 )) $(( $2 + $4 / 2 )) click 1
}
# OCR onesto (tesseract): la prova e' quel che si VEDE nel frame.
ocr_has() {
  convert "$1" -negate /tmp/ocr-work.png
  tesseract /tmp/ocr-work.png stdout 2>/dev/null | grep -qi "$2"
}
echo 'modes desktop OK'
xdotool search --name 'ATLAS' | head -1 | xargs -r xdotool windowactivate --sync || true
sleep 2
import -window root /tmp/e2e-1-auth.png
# Il profilo webview e' condiviso fra le istanze dell'app sul runner: la
# sessione creata dal test della porta di riserva puo' trovare l'app GIA'
# loggata (run 42474126750: sessione riserva@example.com ereditata, la
# "registrazione" digitava dentro una app gia' autenticata e il relogin
# e2e falliva perche' l'utente non era mai stato creato). Se loggata,
# uscire PRIMA: la registrazione qui deve essere reale e deterministica.
if ocr_has /tmp/e2e-1-auth.png 'Esci'; then
  click_word /tmp/e2e-1-auth.png 'Esci' || { echo 'E2E UI: Esci non cliccabile'; exit 1; }
  sleep 3
  import -window root /tmp/e2e-1b-logout.png
  ocr_has /tmp/e2e-1b-logout.png 'password' || { echo 'E2E UI: form login NON visibile dopo il logout iniziale'; exit 1; }
  echo 'sessione ereditata rilevata: logout iniziale OK'
fi
xdotool key Tab Tab Tab Tab Return
sleep 1
xdotool type --delay 30 'e2e@example.com'
xdotool key Tab
xdotool type --delay 30 'password-locale-e2e'
import -window root /tmp/e2e-2-registrazione.png
xdotool key Tab Return
sleep 4
import -window root /tmp/e2e-3-chat.png
ocr_has /tmp/e2e-3-chat.png 'Esci' || { echo 'E2E UI: registrazione NON riuscita (stato loggato assente)'; exit 1; }
# La destinazione post-registrazione non e' garantita (vista Chat oppure
# Dashboard): navigare ESPLICITAMENTE alla Chat col click OCR sulla voce di
# navigazione. Digitare a occhio chiuso qui finiva nel campo token Telegram
# della Dashboard (run 42470527636: la frase "ricorda che..." e' stata
# inviata come token e il salvataggio non e' mai avvenuto).
click_word /tmp/e2e-3-chat.png 'Chat' || { echo 'E2E UI: voce Chat NON trovata via OCR'; exit 1; }
sleep 3
# Click mirato sul composer (stesso punto usato dopo il riavvio): il focus
# non arriva da solo nel campo di input.
xdotool mousemove 640 737 click 1
sleep 1
xdotool type --delay 20 'ricorda che il gatto si chiama Briciola'
xdotool key Tab Return
sleep 6
import -window root /tmp/e2e-4-salvato.png
# Asserzione sulla conferma del backend ("Salvato.", bolla scura = OCR
# affidabile dopo negate). NON sul testo utente: la bolla utente e' chiara,
# dopo -negate diventa scura e tesseract la legge male (run 42473097331:
# "Briciola" perso, salvataggio in realta' riuscito).
ocr_has /tmp/e2e-4-salvato.png 'Salvato' || { echo 'E2E UI: conferma salvataggio memoria NON visibile in chat'; exit 1; }
echo 'memoria salvata via chat OK'
xdotool type --delay 20 'come si chiama il mio gatto?'
xdotool key Tab Return
sleep 6
import -window root /tmp/e2e-5-richiamo.png
ocr_has /tmp/e2e-5-richiamo.png 'Briciola' || { echo 'E2E UI: richiamo Briciola NON visibile prima del riavvio'; exit 1; }
echo 'richiamo pre-riavvio OK'
pkill -f 'target/release/atlas-desktop' || true
# Anche i processi figli WebKit: se sopravvivono tengono il lock del profilo
# e la finestra del secondo avvio non viene mai disegnata (screenshot nero).
pkill -f 'WebKitWebProcess' || true
pkill -f 'WebKitNetworkProcess' || true
for i in $(seq 1 15); do
  sleep 1
  pgrep -f 'target/release/atlas-desktop|WebKitWebProcess' >/dev/null 2>&1 || break
done
# Attendere anche il sidecar: il watchdog (o il close handler) lo termina
# entro pochi secondi dalla morte dell'app. Se 8000 resta occupata, il nuovo
# avvio finirebbe sulla porta di riserva e i check seguenti su 8000 sarebbero
# una corsa contro il sidecar morente. Porta non libera = lifecycle rotto.
for i in $(seq 1 30); do
  sleep 1
  ss -ltn | grep -q ':8000' || break
done
ss -ltn | grep -q ':8000' && { echo 'E2E UI: sidecar ancora in ascolto dopo la chiusura dell app (lifecycle/watchdog fallito)' >&2; exit 1; }
DISPLAY=:99 ./desktop/target/release/atlas-desktop >> /tmp/atlas-desktop.log 2>&1 &
ok=0
for i in $(seq 1 60); do
  sleep 1
  curl -s http://127.0.0.1:8000/health | grep -q '"app":"atlas"' && { ok=1; break; }
done
[ "$ok" = 1 ] || { echo 'E2E UI: backend NON risalito su 8000 dopo il riavvio' >&2; exit 1; }
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
ocr_has /tmp/e2e-8-logout.png 'password' || { echo 'E2E UI: form login NON visibile dopo Esci (campo password assente)'; exit 1; }
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

# --- Click-through "Apri l'agente": dalla dashboard impacchettata alla
# superficie /agent con roundtrip messaggio reale. L'asserzione e' su cio'
# che si VEDE dopo il click (navigation/window reale), non sull'href
# composto. Nessun windowactivate qui: se il link apre una nuova finestra o
# il browser di sistema, la cattura deve vedere quello.
click_word /tmp/e2e-10-richiamo-relogin.png 'Dashboard' || { echo 'E2E UI: voce Dashboard NON trovata via OCR'; exit 1; }
sleep 3
# La sezione "Agente ATLAS" e' sotto la piega a 1280x800: scrollare in fondo
# (rotella sul centro pagina) prima della cattura e del click OCR su "Apri".
xdotool mousemove 640 400
for i in $(seq 1 12); do xdotool click 5; sleep 0.2; done
sleep 1
shot /tmp/e2e-11-dashboard.png
click_word /tmp/e2e-11-dashboard.png 'Apri' || { echo 'E2E UI: link Apri agente NON trovato via OCR in dashboard'; exit 1; }
sleep 6
import -window root /tmp/e2e-12-agente.png
ocr_has /tmp/e2e-12-agente.png 'invia' || ocr_has /tmp/e2e-12-agente.png 'Scrivi ad ATLAS' || { echo 'E2E UI: click su Apri agente NON ha aperto la superficie agente (composer assente nel frame)'; exit 1; }
echo 'click-through dashboard -> agente OK'
# Usabilita' della superficie: messaggio reale dal composer. Senza provider
# configurati la risposta onesta e' l'avviso "nessun provider usabile".
xdotool mousemove 500 746 click 1
sleep 1
xdotool type --delay 30 'ciao'
xdotool key Return
# Asserzione sulla RISPOSTA (bolla scura, OCR affidabile dopo negate), mai
# sulla bolla utente chiara. Attesa a poll: la chain provider puo' metterci
# qualche secondo a fallire/rispondere.
ok=0
for i in 1 2 3 4 5; do
  sleep 4
  import -window root /tmp/e2e-13-agente-roundtrip.png
  ocr_has /tmp/e2e-13-agente-roundtrip.png 'provider' && { ok=1; break; }
done
[ "$ok" = 1 ] || { echo 'E2E UI: risposta agente NON visibile dopo invio messaggio'; exit 1; }
echo 'E2E UI: click-through Agente + roundtrip messaggio OK'

# Ritorno alla dashboard: "indietro" (history.back) chiude il ciclo di
# navigazione nella stessa finestra.
click_word /tmp/e2e-13-agente-roundtrip.png 'indietro' || { echo 'E2E UI: pulsante indietro NON trovato nella superficie agente'; exit 1; }
sleep 5
xdotool mousemove 640 400
for i in $(seq 1 12); do xdotool click 5; sleep 0.2; done
sleep 1
shot /tmp/e2e-14-ritorno.png
ocr_has /tmp/e2e-14-ritorno.png 'Apri' || { echo 'E2E UI: ritorno alla dashboard NON riuscito (link Apri agente non visibile)'; exit 1; }
echo 'E2E UI: ciclo completo dashboard -> agente -> dashboard OK'
