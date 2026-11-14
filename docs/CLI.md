# CLI `atlas`

Il backend installa anche una CLI da terminale (`app/cli.py`), inclusa in
`pip install -e .` del backend.

## Comandi

```bash
atlas register EMAIL PASSWORD   # crea l'account sul backend configurato
atlas login EMAIL PASSWORD      # salva il token in ~/.atlas.toml
atlas ask "A che punto sono col progetto?"
atlas note "Il server di produzione e su Oracle Cloud"
atlas search "database"
atlas projects                  # elenca i progetti registrati
```

## Configurazione

- `ATLAS_API_URL` sceglie il backend (default `http://localhost:8000`).
- Il token di sessione vive in `~/.atlas.toml` (scritto da `atlas login`),
  non in variabili d'ambiente ne nel repository.

Le risposte di `ask` sono le stesse della chat web: stessa pipeline, con le
citazioni delle memorie usate.
