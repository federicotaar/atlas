#!/usr/bin/env bash
# Demo fase 0 (specifica settimana 1): inserisci informazioni, riavvia, ritrova.
# Prerequisito: docker compose up -d
set -euo pipefail
API=${ATLAS_API_URL:-http://localhost:8000}

echo "1) registro un utente demo"
TOKEN=$(curl -sf -X POST "$API/auth/register" -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"demo-password-1"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])' 2>/dev/null || true)
if [ -z "$TOKEN" ]; then
  TOKEN=$(curl -sf -X POST "$API/auth/login" -H 'Content-Type: application/json' \
    -d '{"email":"demo@example.com","password":"demo-password-1"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
fi

echo "2) salvo due informazioni"
curl -sf -X POST "$API/ask" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"text":"Ricorda che il progetto ATLAS usa PostgreSQL 16 con pgvector"}' | python3 -m json.tool | head -5
curl -sf -X POST "$API/ask" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"text":"Ricorda che la prima milestone e il nucleo memoria"}' | python3 -m json.tool | head -5

echo "3) riavvia il backend con: docker compose restart api"
echo "   poi chiedi:"
echo "curl -X POST $API/ask -H \"Authorization: Bearer $TOKEN\" -H 'Content-Type: application/json' -d '{\"text\":\"Quale database usa ATLAS?\"}'"
