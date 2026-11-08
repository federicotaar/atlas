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
import threading
import time
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


def _start_app_watchdog() -> None:
    """Esce se il processo dell'app desktop (ATLAS_APP_PID) non esiste piu'.

    La chiusura normale della finestra fa uscire l'app; se il sidecar non
    viene fermato (crash, kill -9, evento di chiusura non recapitato), il
    server non deve restare orfano con la porta occupata (bug osservato in
    CI: chiusura normale, CloseRequested mai consegnato, porta 8100 in
    ascolto). Solo Linux: usa /proc, nessuna dipendenza.
    """
    if sys.platform != "linux":
        return
    try:
        pid = int(os.environ.get("ATLAS_APP_PID", "0") or 0)
    except ValueError:
        return
    if pid <= 0:
        return
    proc = f"/proc/{pid}"

    def _watch() -> None:
        while True:
            time.sleep(2)
            if not os.path.exists(proc):
                os._exit(0)

    threading.Thread(target=_watch, daemon=True).start()



def main() -> None:
    _install_parent_death_watch()
    _start_app_watchdog()
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

    # Agente ATLAS (stage 1-14) montato sullo STESSO server loopback sotto
    # /agent: niente porte nuove, niente CORS. Workspace persistente nella
    # data dir; una sola istanza per workspace (lock CLI/web/desktop).
    agent_stack = _mount_agent(asgi_app, data_dir)

    @asgi_app.on_event("shutdown")
    def _agent_shutdown() -> None:
        if agent_stack is not None:
            agent_stack.shutdown()

    # Porta scelta dal launcher Tauri (8000 se libera, altrimenti una porta
    # di riserva sua): mai contendere 8000 a un server che non e' nostro.
    port = int(os.environ.get("ATLAS_PORT", "8000"))
    uvicorn.run(asgi_app, host="127.0.0.1", port=port, log_level="warning")


def _mount_agent(asgi_app, data_dir: Path):
    """Monta la web UI dell'agente sotto /agent; None se non possibile.

    Fail-closed senza drammi: se lo stack agente non puo' partire (lock
    gia' preso da CLI/web UI, config mancante) la dashboard resta usabile
    e /agent mostra una pagina che spiega il motivo."""
    from fastapi.responses import HTMLResponse

    def mount_notice(title: str, detail: str) -> None:
        from fastapi import FastAPI

        notice = FastAPI(title="atlas-agent-unavailable")

        @notice.get("/", response_class=HTMLResponse)
        async def _page():
            return (f"<html><body style='background:#0d1117;color:#e6edf3;"
                    f"font-family:system-ui;padding:40px'>"
                    f"<h2>{title}</h2><p>{detail}</p></body></html>")

        asgi_app.mount("/agent", notice)

    workspace = data_dir / "agent"
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        from .agent.permissions import Mode
        from .agent.safety import SecretFilter
        from .providers.registry import ProviderRegistry, load_dotenv
        from .webui import AgentAlreadyRunning, build_webui_stack

        dotenv = load_dotenv(workspace / ".env")  # provisioning via app
        config_path = _agent_config(workspace)
        registry = ProviderRegistry.from_file(config_path)
        # Se nessun provider e' usabile (installazione pulita, nessuna
        # chiave): lo stack parte comunque - il pannello di configurazione
        # nella pagina permette di aggiungere la chiave e attivare il
        # provider senza toccare file a mano.
        active = _first_usable(registry) or registry._order[0]  # noqa: SLF001
        # Il filtro segreti copre .env e le variabili che suonano chiavi.
        secret_values = list(dotenv.values()) + [
            v for k, v in os.environ.items()
            if any(mark in k.upper() for mark in ("API_KEY", "TOKEN", "SECRET"))]
        stack = build_webui_stack(
            registry, active=active, workspace=workspace,
            mode=Mode.ASK, max_steps=10,
            secrets=SecretFilter(secret_values), out=lambda m: print(m))
    except AgentAlreadyRunning:
        mount_notice("Agente gia' attivo",
                     "L'agente ATLAS su questo workspace e' gia' in esecuzione "
                     "(CLI o web UI). Chiudi l'altra istanza per usarlo qui.")
        return None
    except Exception as exc:  # noqa: BLE001 - il backend v2 deve comunque partire
        print(f"agente desktop non montato: {exc}")
        mount_notice("Agente non disponibile",
                     f"Avvio dello stack agente fallito: "
                     f"{type(exc).__name__}. La dashboard resta usabile.")
        return None
    asgi_app.mount("/agent", stack.app)
    return stack


def _agent_config(workspace: Path) -> Path:
    """providers.yaml per l'agente desktop: env, bundle PyInstaller, root
    del repo in dev."""
    env_path = os.environ.get("ATLAS_AGENT_CONFIG")
    if env_path and Path(env_path).exists():
        return Path(env_path)
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "config" / "providers.yaml")
    candidates.append(Path(__file__).resolve().parents[2] / "config"
                      / "providers.yaml")
    for cand in candidates:
        if cand.exists():
            return cand
    raise FileNotFoundError("config/providers.yaml non trovato")


def _first_usable(registry) -> str | None:
    for name in registry._order:  # noqa: SLF001 - selezione iniziale
        usable, _reason = registry.usable(name)
        if usable:
            return name
    return None


if __name__ == "__main__":
    sys.exit(main())
