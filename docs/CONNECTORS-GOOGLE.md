# Connettori Google reali (opzionali: calendario read-only, bozze Gmail)

Fase 4 della specifica: "Primo connettore di sola lettura (calendario), poi
bozze email." I connettori fittizi restano per i test; quelli reali usano
OAuth gratuito e token cifrati (spec sicurezza: mai in chiaro, mai nei log).

**Opzionali.** ATLAS funziona senza account Google: i connettori reali si
attivano solo se chi installa ATLAS collega il PROPRIO account Google,
seguendo il setup qui sotto sulla propria macchina. Nessun account viene
collegato dall'installazione di base.

## Cosa fa (e cosa non fa)

- `google_calendar`: SOLA LETTURA (scope `calendar.readonly`), livello 0.
- `google_gmail`: crea BOZZE (scope `gmail.compose`), livello 1; annullare
  l'azione elimina la bozza. **Non esiste alcuna operazione di invio nel
  codice.** Nota sul permesso: lo scope Google `gmail.compose` e definito
  da Google come "Gestisci bozze e invia email", quindi il consenso OAuth
  mostra anche l'invio; ATLAS non lo usa mai (nessuna chiamata di invio
  esiste nel codice e i test lo verificano).
- I token (access + refresh) sono cifrati con Fernet in
  `connector_credentials` usando `ATLAS_CREDENTIALS_KEY`. Senza chiave il
  salvataggio fallisce chiuso (503): niente credenziali in chiaro, mai.

## Setup (opzionale, gratuito, circa 10 minuti, sul tuo account Google)

Chi installa ATLAS e vuole i connettori reali esegue questi passaggi con il
proprio account Google, sulla propria macchina. Client ID e secret restano
nel `.env` locale: non vanno mai in chat, nel repo o in un sito pubblico.

1. Google Cloud Console (console.cloud.google.com) -> nuovo progetto
   (gratuito, nessuna carta richiesta).
2. "API e servizi" -> abilita **Google Calendar API** e **Gmail API**.
3. Schermata consenso OAuth -> Esterna, dati minimi; modalita Testing
   (gratuita, max 100 utenti di test) con la tua email tra i test user.
4. Credenziali -> "Crea ID client OAuth" -> **Applicazione web**, URI di
   reindirizzamento: `http://127.0.0.1:8000/connectors/google/callback`.
5. Nel `.env` di ATLAS (solo sulla tua macchina):
   ```
   ATLAS_GOOGLE_CLIENT_ID=...apps.googleusercontent.com
   ATLAS_GOOGLE_CLIENT_SECRET=...
   ATLAS_CREDENTIALS_KEY=<output di: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
   ```

## Uso

1. `GET /connectors/google/auth-url?scope_set=calendar` (o `gmail`) con il
   token ATLAS -> apri `auth_url` nel browser e concedi.
2. Google torna su `/connectors/google/callback`: token cifrati salvati,
   connettore autorizzato.
3. Azioni: `google_calendar.list_events` (livello 0) e
   `google_gmail.create_draft` (livello 1, con anteprima esatta e
   annullamento che elimina la bozza).

## Verifica

I test (`tests/test_google_connectors.py`, 14 test) coprono OAuth (state
firmato, scadenza, fail-closed senza chiave), cifratura round-trip e chiave
sbagliata, mapping eventi, bozza + annullamento, isolamento utenti, 401/403.
Le chiamate Google sono sostituite con monkeypatch e fixture sintetiche:
nessun account reale viene toccato in CI. La validazione con account reale
resta un passaggio manuale e volontario di chi installa ATLAS.
