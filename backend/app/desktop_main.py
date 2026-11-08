"""Entrypoint del backend impacchettato come sidecar Tauri (desktop standalone).

Differenze rispetto a `uvicorn app.main:app`:
- database SQLite nella app-data dir (ATLAS_DESKTOP_DATA_DIR), creata se manca;
- provider di default locali (embedding hash, LLM estrattivo): zero setup;
- bind solo su 127.0.0.1 (mai esposto in rete).
"""
import ctypes
import os
import signal
import sys
from pathlib import Path


def _install_parent_death_watch() -> None:
    """Linux: SIGTERM quando il processo padre (bootloader PyInstaller) muore.

    Il launcher Tauri termina il bootloader alla chiusura della finestra;
    senza questo watch il server Python resta orfano con la porta occupata
    (bug osservato in CI: chiusura normale, porta 8100 ancora in ascolto).
    """
    if sys.platform != "linux":
        return
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_PDEATHSIG = 1
        if libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM) != 0:
            return
        # Race: padre gia' morto tra spawn e prctl.
        if os.getppid() == 1:
            os._exit(0)
    except Exception:
        # Mai bloccare l'avvio per il watch: il fallback resta il kill Rust.
        pass


def main() -> None:
    _install_parent_death_watch()
    data_dir = Path(
        os.environ.get("ATLAS_DESKTOP_DATA_DIR")
        or Path.home() / ".atlas" / "desktop"
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("ATLAS_DATABASE_URL", f"sqlite:///{data_dir / 'atlas.db'}")
    os.environ.setdefault("ATLAS_DESKTOP", "1")
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

    # Bridge WhatsApp: binario accanto al backend nel bundle Tauri
    # (wa-bridge-<triple>), sessione e dedup nella data dir (segreti locali).
    if not os.environ.get("ATLAS_WA_BRIDGE_BIN"):
        exe_dir = Path(sys.executable).resolve().parent
        for cand in sorted(exe_dir.glob("wa-bridge*")):
            if cand.is_file() and os.access(cand, os.X_OK):
                os.environ["ATLAS_WA_BRIDGE_BIN"] = str(cand)
                break
    os.environ.setdefault("ATLAS_WA_BRIDGE_DB", str(data_dir / "wa-session.db"))

    import uvicorn

    # Import statico: PyInstaller non vede import per stringa ("app.main:app").
    from app.main import app as asgi_app

    # Porta scelta dal launcher Tauri (8000 se libera, altrimenti una porta
    # di riserva sua): mai contendere 8000 a un server che non e' nostro.
    port = int(os.environ.get("ATLAS_PORT", "8000"))
    uvicorn.run(asgi_app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())
