# Sicurezza e privacy

Progettate dall'inizio (specifica sez. 9), non aggiunte alla fine.

- **Password**: hash argon2id (argon2-cffi, parametri di default RFC 9106).
- **Token**: JWT HS256 con scadenza (default 24h, `ATLAS_JWT_TTL_MINUTES`).
  Il segreto è `ATLAS_JWT_SECRET` (mai committare il valore reale).
- **Isolamento**: ogni query filtra per `user_id`; test dedicati
  (`tests/test_isolation.py`) verificano accessi incrociati.
- **Log**: `action_logs` non contiene mai contenuti personali — solo tipo di
  operazione, id di risorse, metadati numerici (durata, token stimati, costo).
- **Fonti esterne**: il contenuto di file/messaggi importati è sempre trattato
  come dato, mai come istruzione. In fase 0 non esistono connettori né un
  Action Engine, quindi nessuna istruzione esterna può avviare azioni.
- **Esportazione**: `GET /me/export` restituisce tutti i dati dell'utente.
- **Cancellazione**: `DELETE /me` elimina davvero tutti i dati dell'utente
  (diritto all'oblio, unica eccezione all'append-only).
- **Dati sensibili in locale**: i provider di default girano sulla macchina
  dell'utente; nessun dato lascia il sistema senza una decisione esplicita.
- **Backup**: con Docker Compose i dati vivono nel volume `pgdata`;
  esportarli con `pg_dump`. Il ripristino testato è sulla roadmap di fase 6.

## Segnalazioni

Aprite una issue senza includere dati sensibili o segreti.
