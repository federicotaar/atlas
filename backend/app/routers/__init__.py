"""Router FastAPI del backend ATLAS, uno per area funzionale.

Ogni modulo definisce un APIRouter che main.py monta sotto il proprio
prefisso. Le dipendenze condivise (db, auth) vivono in app.deps / app.security.
"""
