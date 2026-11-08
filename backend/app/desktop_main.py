"""Entrypoint del backend impacchettato come sidecar Tauri (desktop standalone).

Differenze rispetto a `uvicorn app.main:app`:
- database SQLite nella app-data dir (ATLAS_DESKTOP_DATA_DIR), creata se manca;
- provider di default locali (embedding hash, LLM estrattivo): zero setup;
- bind solo su 127.0.0.1 (mai esposto in rete).
"""
import os
import sys
from pathlib import Path


def main() -> None:
    data_dir = Path(
        os.environ.get("ATLAS_DESKTOP_DATA_DIR")
        or Path.home() / ".atlas" / "desktop"
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("ATLAS_DATABASE_URL", f"sqlite:///{data_dir / 'atlas.db'}")
    os.environ.setdefault("ATLAS_EMBED_PROVIDER", "local")
    os.environ.setdefault("ATLAS_LLM_PROVIDER", "extractive")
    # Il segreto JWT/credentials deve essere stabile tra avvii: se non
    # configurato, ne viene generato uno e salvato nella data dir (0600).
    for var, fname in (("ATLAS_JWT_SECRET", "jwt_secret"), ("ATLAS_CREDENTIALS_KEY", "credentials_key")):
        if not os.environ.get(var):
            secret_path = data_dir / fname
            if secret_path.exists():
                value = secret_path.read_text().strip()
            else:
                import secrets

                if fname == "credentials_key":
                    from cryptography.fernet import Fernet

                    value = Fernet.generate_key().decode()
                else:
                    value = secrets.token_urlsafe(48)
                secret_path.write_text(value)
                secret_path.chmod(0o600)
            os.environ[var] = value

    import uvicorn

    # Import statico: PyInstaller non vede import per stringa ("app.main:app").
    from app.main import app as asgi_app

    uvicorn.run(asgi_app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())
