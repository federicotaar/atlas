# Contribuire

1. Una fase alla volta: vedi `docs/ROADMAP-STATUS.md`. Non iniziare la fase N+1
   prima che i test di uscita della fase N passino.
2. Ogni funzione arriva con test (`backend/tests`, `frontend/src/*.test.ts`).
3. Ogni modifica allo schema è una migrazione Alembic (`alembic revision`).
4. Niente contenuti personali nei log (`action_logs`: solo tipo, id, numeri).
5. Errori API sempre nel formato `{"error": {"code", "message"}}`.
6. Le memorie `inferred` non diventano mai `stated` da sole: solo una
   conferma dell'utente crea una nuova memoria `stated`.
