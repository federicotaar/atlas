#!/usr/bin/env bash
# Batteria completa del worker codici ATLAS (locale / CI):
#   1. .dev.vars di test generato al volo (chiavi fresche, MAI committato)
#   2. schema + fixture su D1 locale
#   3. worker.test.mjs (14 casi) + verify.test.mjs (12 casi)
#   4. fail-closed: istanza SENZA TURNSTILE_SECRET deve rifiutare (400 turnstile_failed)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== .dev.vars di test (generato, non committato) =="
JWK=$(node -e '
const { generateKeyPairSync } = require("crypto");
const { privateKey } = generateKeyPairSync("ed25519");
process.stdout.write(JSON.stringify(privateKey.export({ format: "jwk" })));')
cat > .dev.vars <<EOF
BREVO_API_KEY=test-local
CODE_PEPPER=test-pepper-locale
CODE_SIGNING_JWK=$JWK
DAILY_SEND_MAX=100
SENDER_EMAIL=test@example.com
ALLOWED_ORIGINS=http://localhost:5173
TURNSTILE_SECRET=test-local
EOF

echo "== schema + fixture D1 locale =="
rm -rf .wrangler/state
npx --yes wrangler@4 d1 execute DB --local --file schema.sql >/dev/null
npx --yes wrangler@4 d1 execute DB --local --command "$(node test/fixtures.mjs)" >/dev/null

start_dev() {
  for i in $(seq 1 15); do
    curl -s -o /dev/null http://127.0.0.1:8787/logo.png || break
    sleep 1 # porta ancora occupata da un'istanza precedente
  done
  npx --yes wrangler@4 dev --port 8787 --ip 127.0.0.1 "$@" > /tmp/wrangler-battery.log 2>&1 &
  WPID=$!
  for i in $(seq 1 30); do
    curl -s -o /dev/null http://127.0.0.1:8787/logo.png && return 0
    sleep 1
  done
  echo "FATAL: wrangler dev non avviato"; cat /tmp/wrangler-battery.log; exit 1
}
stop_dev() {
  kill $WPID 2>/dev/null || true
  wait $WPID 2>/dev/null || true
  pkill -f 'wrangler dev' 2>/dev/null || true
  pkill -f workerd 2>/dev/null || true
  for i in $(seq 1 15); do
    curl -s -o /dev/null http://127.0.0.1:8787/logo.png || return 0
    sleep 1
  done
  echo "FATAL: istanza wrangler non terminata"; exit 1
}

echo "== batteria worker (TURNSTILE_SECRET=test-local) =="
start_dev
node test/worker.test.mjs | tee /tmp/battery1.txt
node test/verify.test.mjs | tee /tmp/battery2.txt
stop_dev
grep -q '^FAIL' /tmp/battery1.txt && exit 1 || true
grep -q '^FAIL' /tmp/battery2.txt && exit 1 || true
! grep -q '^FAIL' /tmp/battery1.txt && ! grep -q '^FAIL' /tmp/battery2.txt

echo "== fail-closed: istanza SENZA TURNSTILE_SECRET =="
# --var con valore vuoto NON sovrascrive .dev.vars in modo affidabile:
# spostiamo .dev.vars per simulare un deploy senza segreti configurati.
mv .dev.vars .dev.vars.bak
start_dev
RESP=$(curl -s -X POST http://127.0.0.1:8787/v1/codes/request \
  -H 'Content-Type: application/json' \
  -d '{"email":"fc@example.com","purpose":"register","turnstile_token":"test-token"}')
stop_dev
mv .dev.vars.bak .dev.vars
echo "$RESP" | grep -q '"error":"turnstile_failed"' \
  && echo "PASS  fail-closed: senza TURNSTILE_SECRET -> 400 turnstile_failed" \
  || { echo "FAIL  fail-closed: risposta inattesa: $RESP"; exit 1; }

echo "BATTERY: ALL PASS"
