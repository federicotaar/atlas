"""CLI dell'agente ATLAS (spec 7.1): chat come prima interfaccia.

Comandi (spec 1, 4):
  /model                 mostra provider e modello attivi
  /model list            elenca i provider configurati e i modelli disponibili
  /model use <nome[:modello]>   cambia provider (e modello) a runtime
  /mode                  modo permessi attivo (ask default, auto-safe, yolo)
  /mode <modo>           cambia modo permessi
  /help                  aiuto
  /quit                  esci

Le chiavi API arrivano solo da .env (load_dotenv) e non vengono mai stampate.
Dal stage 3 ogni richiesta passa dal loop strumenti (spec sez. 2): il modello
puo' usare read_file/write_file/list_dir/shell con permessi e log azioni.
Il loop lavora non in streaming (i passi strumento non sono testo da mostrare):
la risposta finale arriva intera. La memoria persistente arriva allo stage 4.
"""
from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Callable

from .agent.actionlog import ActionLog
from .agent.loop import run_agent_loop
from .agent.permissions import Mode, parse_mode
from .agent.safety import SecretFilter
from .agent.seal import SealGuard
from .agent.tools import ToolContext
from .providers.base import AllProvidersFailed, ProviderError
from .providers.registry import ConfigError, ProviderRegistry, load_dotenv

DEFAULT_CONFIG = "config/providers.yaml"

HELP_TEXT = """Comandi:
  /model                       provider e modello attivi
  /model list                  provider configurati (+ modelli installati)
  /model use <nome[:modello]>  es. /model use ollama_local:llama3.2:3b
  /mode                        modo permessi attivo
  /mode <ask|auto-safe|yolo>   cambia modo permessi (spec sez. 4)
  /stop                        annulla il task in corso (spec sez. 2)
  /help                        questo aiuto
  /quit                        esci
Qualunque altro testo viene mandato all'agente, che puo' usare gli strumenti
(read_file, write_file, list_dir, shell) secondo il modo permessi attivo.
Quando l'agente chiede una conferma, rispondi si o no al prompt.
La risposta gira in un task in background: il prompt resta attivo e /stop
lo annulla. Ctrl-C equivale a /stop; Ctrl-D esce."""


def find_config(start: Path | None = None) -> Path:
    """Cerca config/providers.yaml dalla directory data risalendo al root del repo."""
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        candidate = folder / DEFAULT_CONFIG
        if candidate.exists():
            return candidate
    raise ConfigError(f"non trovo {DEFAULT_CONFIG} (esegui dal root del repo o passa --config)")


def parse_model_target(arg: str) -> tuple[str, str | None]:
    """'ollama_local:llama3.2:3b' -> ('ollama_local', 'llama3.2:3b');
    'ollama_local' -> ('ollama_local', None). Il primo ':' separa il provider:
    i tag dei modelli Ollama contengono a loro volta ':'."""
    arg = arg.strip()
    if not arg:
        raise ConfigError("uso: /model use <nome[:modello]>")
    if ":" in arg:
        name, model = arg.split(":", 1)
        if not name.strip() or not model.strip():
            raise ConfigError(f"destinazione non valida: {arg!r}")
        return name.strip(), model.strip()
    return arg, None


def _first_usable(registry: ProviderRegistry) -> str:
    for name in registry.names():
        if registry.usable(name)[0]:
            return name
    raise ConfigError(
        "nessun provider usabile: controlla config/providers.yaml e le chiavi in .env"
    )


def _describe_registry(registry: ProviderRegistry, active: str,
                       out: Callable[[str], None]) -> None:
    for name in registry.names(enabled_only=False):
        spec = registry.spec(name)
        usable, reason = registry.usable(name)
        mark = "*" if name == active else " "
        state = "pronto" if usable else f"non usabile: {reason}"
        key_state = "non richiesta" if not spec.requires_key else (
            "presente" if registry.has_key(name) else f"mancante ({spec.api_key_env})"
        )
        out(f"{mark} {name} ({spec.type})  modello: {registry.current_model(name) or '(da scegliere)'}"
            f"  chiave: {key_state}  [{state}]")
    provider = registry.get(active)
    models = provider.list_models()
    if models:
        out(f"  modelli disponibili su {active}: {', '.join(models)}")


def run_cli(registry: ProviderRegistry, *, active: str | None = None,
            once: str | None = None, stream: bool = True,
            workspace: Path | None = None,
            mode: Mode = Mode.ASK,
            max_steps: int = 10,
            secrets: SecretFilter | None = None,
            input_fn: Callable[[str], str] = input,
            out: Callable[[str], None] = print,
            write: Callable[[str], None] | None = None) -> int:
    """Loop REPL. Ritorna 0 se il run e' stato pulito, 2 su errore provider."""
    write = write or (lambda s: print(s, end="", flush=True))
    try:
        active = active or _first_usable(registry)
    except ConfigError as exc:
        out(f"Errore: {exc}")
        return 2

    workspace = (workspace or Path.cwd()).resolve()
    secrets = secrets or SecretFilter()
    ctx = ToolContext(workspace=workspace, secrets=secrets)
    log = ActionLog(workspace / "logs" / "actions.log", secrets)
    # Sigillo hard-limit (spec sez. 8): safety/permissions/.env tornano integri
    # dopo ogni azione, qualunque cosa uno strumento abbia fatto.
    seal = SealGuard(workspace, on_incident=lambda msg: out(f"[sigillo: {msg}]"))
    state = {"mode": mode}

    def on_fallback(failed: str, to: str, err: str) -> None:
        out(f"[{failed} non disponibile ({err}); passo a {to}]")

    history: list[dict] = []

    # Conferme di permesso: il task gira in un thread, ma solo il REPL legge
    # stdin. Quando il worker chiede, la prossima riga non-comando dell'utente
    # e' la risposta (si/no), inoltrata via coda.
    ask_queue: queue.Queue[str] = queue.Queue()
    asking = threading.Event()

    def ask_fn(question: str) -> bool:
        if once is not None:
            # Modalita' non interattiva: nessun REPL concorrente, chiedi diretto.
            try:
                reply = input_fn(f"L'agente vuole: {question}. Consenti? [si/no] ")
            except EOFError:
                out("(nessuna risposta possibile: azione non approvata)")
                return False
            return reply.strip().lower() in ("si", "si'", "sì", "yes", "y", "s")
        asking.set()
        out(f"L'agente vuole: {question}. Consenti? [si/no] ")
        try:
            while True:
                try:
                    reply = ask_queue.get(timeout=0.2)
                except queue.Empty:
                    if not task_running():
                        return False  # /stop mentre attendeva la conferma
                    continue
                return reply.strip().lower() in ("si", "si'", "sì", "yes", "y", "s")
        finally:
            asking.clear()

    def answer(text: str, cancel: threading.Event) -> bool:
        """Una richiesta all'agente (loop strumenti, spec sez. 2). False se
        tutti i provider hanno fallito. /stop annulla tra un passo e l'altro
        e mentre attende una conferma."""
        history.append({"role": "user", "content": text})
        write("atlas> ")
        try:
            result = run_agent_loop(
                registry, active, history, ctx, state["mode"], log,
                ask_fn=ask_fn, max_steps=max_steps, cancel=cancel,
                on_fallback=on_fallback, on_step=out, on_delta=write, seal=seal)
        except AllProvidersFailed as exc:
            out(f"Errore: {exc}")
            history.pop()
            return False
        except ProviderError as exc:
            out(f"Errore: {exc}")
            history.pop()
            return False
        if result.cancelled:
            out("(task annullato con /stop)")
            history.pop()
            return True
        if result.stopped_by_limit:
            out(f"stop: {result.notes[-1]}")
            history.append({"role": "assistant", "content": result.notes[-1]})
            return True
        history.append({"role": "assistant", "content": result.text})
        return True

    if once is not None:
        return 0 if answer(once, threading.Event()) else 2

    # Un task alla volta in background: il prompt resta attivo per /stop.
    task: dict = {"thread": None, "cancel": None}

    def task_running() -> bool:
        th = task["thread"]
        return th is not None and th.is_alive()

    def stop_task(wait: bool = False) -> bool:
        """True se c'era un task da annullare."""
        if not task_running():
            return False
        task["cancel"].set()
        if wait:
            task["thread"].join(timeout=10.0)
        return True

    def start_task(text: str) -> None:
        cancel = threading.Event()

        def work() -> None:
            try:
                answer(text, cancel)
            finally:
                task["thread"] = None
                task["cancel"] = None

        th = threading.Thread(target=work, daemon=True)
        # Registra PRIMA di start: /stop subito dopo l'invio deve trovare il task.
        task["thread"] = th
        task["cancel"] = cancel
        th.start()

    # work() azzera i riferimenti a fine task; stop_task li legge cosi'.


    out(f"ATLAS agente locale - provider attivo: {active} (modello {registry.current_model(active)})")
    out(f"modo permessi: {state['mode'].value} - workspace: {workspace}")
    out("Scrivi /help per i comandi, /quit per uscire.")
    while True:
        try:
            line = input_fn("tu> ").strip()
        except EOFError:
            out("")
            stop_task(wait=True)
            break
        except KeyboardInterrupt:
            out("")
            if not stop_task():  # nessun task: resta solo una riga nuova
                continue
            continue
        if asking.is_set() and not line.startswith("/"):
            ask_queue.put(line)  # risposta a una richiesta di conferma
            continue
        if not line:
            continue
        if line in ("/quit", "/exit"):
            stop_task(wait=True)
            break
        if line == "/stop":
            if stop_task():
                out("(annullamento richiesto: il task si ferma al prossimo delta)")
            else:
                out("Nessun task in corso.")
            continue
        if line == "/help":
            out(HELP_TEXT)
            continue
        if line == "/mode":
            out(f"modo permessi: {state['mode'].value} (ask, auto-safe, yolo)")
            continue
        if line.startswith("/mode "):
            try:
                state["mode"] = parse_mode(line.split(maxsplit=1)[1])
            except ValueError as exc:
                out(f"Errore: {exc}")
                continue
            out(f"modo permessi: {state['mode'].value}"
                + (" - nessuna conferma, restano blocklist e sandbox" if state["mode"] is Mode.YOLO else ""))
            continue
        if line == "/model":
            out(f"provider: {active}  modello: {registry.current_model(active)}")
            continue
        if line == "/model list":
            try:
                _describe_registry(registry, active, out)
            except ProviderError as exc:
                out(f"Errore: {exc}")
            continue
        if line.startswith("/model use"):
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                out("Uso: /model use <nome[:modello]>")
                continue
            try:
                name, model = parse_model_target(parts[2])
                registry.spec(name)  # valida esistenza
            except ConfigError as exc:
                out(f"Errore: {exc}")
                continue
            usable, reason = registry.usable(name)
            if not usable:
                out(f"{name} non e' usabile: {reason}")
                continue
            if model:
                registry.set_model(name, model)
            active = name
            out(f"Provider attivo: {active} (modello {registry.current_model(active)})")
            continue
        if line.startswith("/"):
            out(f"Comando sconosciuto: {line.split()[0]} (/help per l'elenco)")
            continue
        if task_running():
            out("Un task e' gia' in corso: /stop per annullarlo, oppure attendi la fine.")
            continue
        start_task(line)
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="atlas-agent",
                                     description="ATLAS agente locale - chat CLI in streaming")
    parser.add_argument("--config", help="percorso di providers.yaml "
                                         "(default: config/providers.yaml dal root del repo)")
    parser.add_argument("--provider", help="provider iniziale (default: primo usabile)")
    parser.add_argument("--model", help="modello iniziale per il provider scelto")
    parser.add_argument("--once", metavar="MESSAGGIO",
                        help="una sola richiesta non interattiva, poi esci")
    parser.add_argument("--no-stream", action="store_true",
                        help="(deprecato: il loop strumenti dello stage 3 lavora non in streaming)")
    parser.add_argument("--workspace", help="cartella sandbox dei file tool "
                                            "(default: root del repo)")
    parser.add_argument("--mode", default="ask", choices=["ask", "auto-safe", "yolo"],
                        help="modo permessi iniziale (default: ask)")
    parser.add_argument("--max-steps", type=int, default=10,
                        help="passi massimi del loop strumenti per richiesta (default: 10)")
    args = parser.parse_args(argv)

    dotenv = load_dotenv(Path(".env"))  # chiavi provider: solo da qui/ambiente
    try:
        config_path = Path(args.config) if args.config else find_config()
        registry = ProviderRegistry.from_file(config_path)
    except ConfigError as exc:
        print(f"Errore di configurazione: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if args.model:
        if not args.provider:
            print("Errore: --model richiede --provider", file=sys.stderr)
            raise SystemExit(2)
        registry.set_model(args.provider, args.model)
    workspace = Path(args.workspace) if args.workspace else config_path.parent.parent
    # Il filtro segreti copre .env e le variabili d'ambiente che suonano come
    # chiavi: nessuna finisce in log, contesto del modello o messaggi.
    secret_values = list(dotenv.values()) + [
        v for k, v in os.environ.items()
        if any(mark in k.upper() for mark in ("API_KEY", "TOKEN", "SECRET"))]
    secrets = SecretFilter(secret_values)
    code = run_cli(registry, active=args.provider, once=args.once,
                   stream=not args.no_stream, workspace=workspace,
                   mode=parse_mode(args.mode), max_steps=args.max_steps,
                   secrets=secrets)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
