# ATLAS codes worker

API codici email (register/login) per ATLAS. Cloudflare Worker + D1, invio via Brevo
transactional. Pattern NOVA (api.federicotaranu.site) ma worker/dominio separati.

## Trust boundary

- Il client pubblico (app desktop/web, repo pubblico) NON ha segreti e NON vede
  mai le attestazioni: parla solo col backend locale.
- Il backend locale chiama `/v1/codes/verify` e verifica l'attestazione Ed25519
  con la CHIAVE PUBBLICA (config `ATLAS_CODES_PUBLIC_KEY`, non un segreto).
  La privata e' il secret worker `CODE_SIGNING_JWK`.
- Anti-replay: il worker consuma il codice atomicamente
  (`UPDATE ... WHERE consumed=0`, check `meta.changes == 1`); il backend registra
  ogni `jti` in `used_attestations` (PK) nella stessa transazione della
  creazione utente/sessione.
- Il worker invia SOLO template fissi a 6 cifre: niente subject/body/destinatari
  controllati dal chiamante (non e' un relay). `purpose` solo register|login;
  password-reset richiede review separata.
- Footer email: NESSUN indirizzo postale (Brevo non lo richiede per
  transazionali; quello dell'account resta privato). Non reinserire senza
  decisione esplicita dell'utente.

## Quote e limiti (protezione quota, NON prevenzione abusi completa)

- `DAILY_SEND_MAX` (default 100): budget giornaliero contato PRIMA dell'invio
  (tabella `daily_sends`, fail-safe). Protegge la quota Brevo 300/g condivisa
  con NOVA. Un attaccante puo' comunque esaurire il budget del giorno:
  mitigazione futura = Turnstile su /v1/codes/request.
- Per-email 3/10min, per-IP 10/10min (tabella `code_requests`).
- IP: solo `CF-Connecting-IP` (scritto dall'edge, non spoofabile in produzione).
  Il chiamante e' il backend locale dell'utente, quindi l'IP visto e' quello
  giusto. Con un backend hosted condiviso, per-email e' il vero guardrail.
- Codice: 6 cifre, TTL 10 min, max 5 tentativi, hash SHA-256 con pepper.

## Test locali (zero invii reali)

`.dev.vars` con `BREVO_API_KEY=test-local` attiva il test seam (salta l'invio;
in produzione il secret e' una chiave `xkeysib-*`, mai quel valore).

```bash
npx wrangler d1 execute DB --local --file schema.sql
npx wrangler dev --port 8787 --ip 127.0.0.1 &
node test/worker.test.mjs                      # batteria richieste/rate limit/CORS
node test/fixtures.mjs                          # SQL codici noti
npx wrangler d1 execute DB --local --command "$(node test/fixtures.mjs)"
node test/verify.test.mjs                       # verify, firma, replay, concorrenza
```

Ultimo run (2026-11-07): 24/24 PASS, inclusa concorrenza 8-way con esattamente
1 vincitore e verifica firma Ed25519 reale.

## Deploy (SOLO dopo review utente di sender+template e gate di Main)

1. `wrangler d1 create atlas-codes-db` -> id in wrangler.toml (ora placeholder).
2. Secrets: `wrangler secret put BREVO_API_KEY` (chiave NUOVA restricted
   transactional-only da dashboard Brevo - NON quella NOVA), `CODE_PEPPER`
   (generato), `CODE_SIGNING_JWK` (coppia Ed25519 generata al deploy; la
   pubblica va in ATLAS come config pubblica).
3. `wrangler deploy` + custom domain (proposta: atlas.federicotaranu.site) ->
   `LOGO_URL=https://<subdomain>/logo.png` in vars.
4. Sender proposto: `ATLAS <atlas@federicotaranu.site>` (auto-verificato: il
   dominio e' gia' autenticato Brevo; zero DNS).
5. NOTA token: il token Cloudflare in vault e' "nova-api-deploy" - verificare
   lo scope prima di usarlo per ATLAS (altrimenti login Cloudflare completo).
6. Turnstile (PRE-PRODUZIONE, obbligatorio): creare widget in dashboard
   Cloudflare -> `wrangler secret put TURNSTILE_SECRET`; la SITEKEY (pubblica)
   va in VITE_TURNSTILE_SITEKEY del frontend. Hostname Management (docs CF:
   gli hostname locali SONO ammessi): aggiungere
   - `federicotaranu.site` (copre anche eventuale web app su sottodominio)
   - `localhost` (Tauri produzione Linux serve da tauri://localhost ->
     hostname "localhost"; anche sviluppo vite)
   - `tauri.localhost` (Tauri produzione Windows/macOS)
   E2E/CI: dummy sitekey `1x00000000000000000000AA` + dummy secret
   `1x0000000000000000000000000000000AA` (funzionano da qualsiasi dominio ed
   evitano che Playwright sia rilevato come bot).
   CSP desktop aggiornata di conseguenza (script/connect/frame-src
   challenges.cloudflare.com - vedi desktop/tauri.conf.json).
