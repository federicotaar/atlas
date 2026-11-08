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
from .agent.commands import parse_model_target
from .agent.loop import run_agent_loop
from .agent.memory import MemoryStore, memories_context
from .agent.permissions import Mode, parse_mode
from .agent.safety import SecretFilter
from .agent.seal import SealGuard
from .agent.tools import ToolContext
from .channels.base import ChannelHub, ChannelLog
from .channels.config import channel_enabled, load_channels_config
from .channels.media import MediaProcessor
from .channels.pairing import PairingStore
from .channels.telegram import TelegramChannel
from .channels.whatsapp import WhatsAppChannel, normalize_number
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
  /pairing                     elenca le richieste di pairing in sospeso
  /pairing challenge           genera un codice monouso per autorizzare Fred
  /pairing approve <n>         autorizza il mittente n (allowlist canali)
  /pairing reject <n>          scarta la richiesta n
  /whatsapp                    stato del collegamento WhatsApp
  /whatsapp pair [numero]      QR o codice per collegare il numero dell'agente
  /remember <testo>            salva una nota persistente (spec sez. 5)
  /forget <testo>              rimuove le note che contengono il testo
  /memories                    elenca le note salvate
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


def _spawn_bridge(bridge_bin: str, addr: str, token: str, workspace: Path,
                  allow_dms: bool, out):
    """Avvia il sidecar Go wa-bridge come processo figlio (termina con la
    CLI via atexit). Il token passa solo come argomento del processo figlio,
    mai in config/log. Ritorna il Popen o None se non e' partito."""
    import atexit
    import subprocess
    import time as _time
    import urllib.request

    repo_root = Path(__file__).resolve().parents[2]
    binary = Path(bridge_bin)
    if not binary.is_absolute():
        binary = repo_root / binary
    if not binary.exists():
        out(f"bridge whatsapp non trovato: {binary} "
            "(compilalo con `go build` in wa-bridge/)")
        return None
    db_path = workspace / "data" / "wa-session.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(binary), "-addr", addr, "-token", token, "-db", str(db_path)]
    if allow_dms:
        cmd.append("-allow-dms")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except OSError as exc:
        out(f"avvio bridge whatsapp fallito: {exc}")
        return None
    atexit.register(proc.terminate)
    deadline = _time.monotonic() + 10
    while _time.monotonic() < deadline:
        try:
            req = urllib.request.Request(
                f"http://{addr}/healthz", headers={"X-Bridge-Token": token})
            with urllib.request.urlopen(req, timeout=2):
                out(f"bridge whatsapp avviato ({addr})")
                return proc
        except Exception:
            if proc.poll() is not None:
                out("bridge whatsapp terminato subito dopo l'avvio")
                return None
            _time.sleep(0.2)
    out(f"bridge whatsapp avviato ma /healthz non risponde ancora su {addr}: "
        "il canale riprova da solo")
    return proc


def _maybe_start_channels(*, workspace: Path, registry: ProviderRegistry,
                          get_active, set_active, ctx, store, log, seal,
                          secrets, dotenv: dict, out, max_steps: int,
                          pairing: PairingStore):
    """Avvia i canali abilitati in config/channels.yaml.
    Ritorna (hub, channels): hub None se nessun canale e' partito."""
    config = load_channels_config(workspace)
    tg_on = channel_enabled(config, "telegram")
    wa_on = channel_enabled(config, "whatsapp_unofficial")
    if not tg_on and not wa_on:
        return None, {}
    # ATTENZIONE: il dict di load_dotenv e' REDATTO ("(impostata)") - serve
    # solo per la diagnostica. I valori veri restano in os.environ.
    allowed: dict[str, set[str]] = {}

    tg_token = ""
    if tg_on:
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not tg_token:
            out("telegram abilitato ma TELEGRAM_BOT_TOKEN mancante in .env: canale non avviato")
            tg_on = False
        else:
            raw_ids = os.environ.get("ALLOWED_TELEGRAM_IDS", "")
            # Allowlist = .env + pairing approvati in locale con /pairing approve.
            allowed["telegram"] = (
                {s.strip() for s in raw_ids.split(",") if s.strip()}
                | (pairing.paired_ids("telegram") if pairing is not None else set()))
            if not allowed["telegram"]:
                out("ALLOWED_TELEGRAM_IDS vuoto: il canale ignorera' TUTTI i mittenti "
                    "(richieste registrate in locale; approva con /pairing approve)")

    wa_addr = ""
    wa_token = ""
    wa_poll = 2.0
    if wa_on:
        wa_cfg = config.get("channels", {}).get("whatsapp_unofficial", {})
        wa_addr = str(wa_cfg.get("bridge_addr", "127.0.0.1:18741"))
        wa_poll = float(wa_cfg.get("poll_interval", 2))
        wa_token = os.environ.get("WA_BRIDGE_TOKEN", "")
        bridge_bin = str(wa_cfg.get("bridge_bin", "") or "")
        if bridge_bin:
            if not wa_token:
                # Token effimero generato a ogni avvio: mai in config ne' in log.
                from secrets import token_hex
                wa_token = token_hex(16)
            if _spawn_bridge(bridge_bin, wa_addr, wa_token, workspace,
                             allow_dms=bool(wa_cfg.get("allow_dms", True)),
                             out=out) is None:
                wa_on = False
        if wa_on and not wa_token:
            out("whatsapp_unofficial abilitato ma WA_BRIDGE_TOKEN mancante in .env "
                "(o bridge_bin non configurato): canale non avviato")
            wa_on = False
        if wa_on:
            raw_nums = os.environ.get("ALLOWED_WHATSAPP_NUMBERS", "")
            allowed["whatsapp"] = (
                {normalize_number(n) for n in raw_nums.split(",") if n.strip()}
                | (pairing.paired_ids("whatsapp") if pairing is not None else set()))
            if not allowed["whatsapp"]:
                out("ALLOWED_WHATSAPP_NUMBERS vuoto: il canale ignorera' TUTTI i "
                    "mittenti (richieste registrate in locale; approva con "
                    "/pairing approve)")

    if not tg_on and not wa_on:
        return None, {}

    # Stage 6: vocali (trascrizione Groq), foto (descrizione Gemini) e file.
    # Le chiavi arrivano da .env/ambiente come quelle dei provider; senza
    # chiave il file resta salvato e la nota segnala il servizio mancante.
    media = MediaProcessor(
        workspace,
        groq_key=os.environ.get("GROQ_API_KEY", ""),
        gemini_key=os.environ.get("GEMINI_API_KEY", ""))
    hub = ChannelHub(
        registry=registry, active_getter=get_active, set_active=set_active,
        describe_registry=_describe_registry,
        ctx=ctx, store=store, log=log, seal=seal, secrets=secrets,
        channels_log=ChannelLog(workspace / "logs" / "channels.log", secrets),
        allowed=allowed, pairing=pairing, media=media, max_steps=max_steps)
    channels: dict[str, object] = {}
    if tg_on:
        tg_cfg = config.get("channels", {}).get("telegram", {})
        tg_channel = TelegramChannel(
            tg_token,
            drop_pending_updates=bool(tg_cfg.get("drop_pending_updates", True)),
            # Gate del download: `allowed` e' lo stesso dict dell'hub, quindi
            # un /pairing approve live vale subito anche per gli allegati.
            allow_check=lambda sender: ChannelHub.is_allowed(sender, "telegram", allowed))
        tg_channel.on_message(
            lambda m: hub.handle(m, lambda text: tg_channel.send(m.sender, text)))
        tg_channel.start()
        channels["telegram"] = tg_channel
        out("canale telegram avviato (long polling)")
    if wa_on:
        wa_channel = WhatsAppChannel(wa_addr, wa_token, poll_interval=wa_poll)
        wa_channel.on_message(
            lambda m: hub.handle(m, lambda text: wa_channel.send(m.sender, text)))
        wa_channel.start()
        channels["whatsapp"] = wa_channel
        out(f"canale whatsapp avviato (bridge {wa_addr})")
    return hub, channels


def _handle_pairing(line: str, pairing: PairingStore, hub, out) -> None:
    """/pairing [list] | approve <n> | reject <n> - gestione locale delle
    richieste di pairing dei canali (vedi app/channels/pairing.py)."""
    import time as _time

    parts = line.split()
    sub = parts[1] if len(parts) > 1 else "list"
    if sub == "challenge":
        # Codice monouso da consegnare a Fred sul canale autenticato: chi lo
        # presenta al bot viene abbinato. Non finire in nessun log.
        code = pairing.create_challenge()
        out(f"Challenge monouso (30 min): {code}")
        out("Condividila solo sul canale autenticato; chi la invia al bot "
            "viene autorizzato.")
        return
    pending = pairing.pending()
    if sub in ("list", "ls"):
        if not pending:
            out("Nessuna richiesta di pairing in sospeso.")
        for i, p in enumerate(pending, 1):
            age = int((_time.time() - p.get("ts", 0)) // 60)
            display = f" ({p['display']})" if p.get("display") else ""
            out(f"{i}. {p['channel']} id={p['sender']}{display} - {age} min fa")
        return
    if sub in ("approve", "reject") and len(parts) > 2:
        try:
            idx = int(parts[2]) - 1
            entry = pending[idx]
        except (ValueError, IndexError):
            out(f"Numero non valido: /pairing per l'elenco (1-{len(pending)}).")
            return
        if sub == "reject":
            pairing.reject(entry["channel"], entry["sender"])
            out(f"Richiesta scartata: {entry['channel']} id={entry['sender']}.")
            return
        if hub is not None:
            # L'hub aggiorna anche l'allowlist live; senza hub vale dal
            # prossimo avvio del canale (lettura da disco).
            hub.approve_pairing(entry["channel"], entry["sender"])
        else:
            pairing.approve(entry["channel"], entry["sender"])
        out(f"Autorizzato {entry['channel']} id={entry['sender']}: "
            "aggiunto all'allowlist" + (" (attivo subito)." if hub is not None
                                        else " (attivo dal prossimo avvio del canale)."))
        return
    out("Uso: /pairing [list] | /pairing approve <n> | /pairing reject <n>")


def _handle_whatsapp(line: str, wa_channel, out) -> None:
    """/whatsapp [status] | /whatsapp pair [numero] - collegamento
    dell'account WhatsApp dell'agente. SOLO dalla CLI locale."""
    if wa_channel is None:
        out("whatsapp non attivo: abilitalo in config/channels.yaml "
            "(whatsapp_unofficial).")
        return
    parts = line.split()
    sub = parts[1] if len(parts) > 1 else "status"
    if sub == "status":
        try:
            health = wa_channel.health()
        except Exception as exc:
            out(f"bridge non raggiungibile: {exc}")
            return
        if health.get("linked"):
            out(f"WhatsApp collegato: {health.get('pushname', '?')} "
                f"({health.get('jid', '?')})")
        else:
            out("WhatsApp NON collegato: /whatsapp pair per collegare "
                "il numero dell'agente.")
        return
    if sub == "pair":
        phone = parts[2] if len(parts) > 2 else ""
        if phone:
            # Codice numerico: si digita sul telefono del NUMERO DELL'AGENTE
            # (WhatsApp > Dispositivi collegati > Collega con numero).
            try:
                code = wa_channel.pair_code(phone).get("code", "")
            except Exception as exc:
                out(f"codice non disponibile: {exc}")
                return
            out(f"Codice di collegamento: {code}")
            out("Sul telefono del numero dell'agente: WhatsApp > Dispositivi "
                "collegati > Collega dispositivo > Collega invece con numero "
                "di telefono.")
            return
        try:
            wa_channel.pair()
        except Exception as exc:
            out(f"pairing non avviato: {exc}")
            return
        out("In attesa del QR (ruota ogni ~20s, validita' ~2 min)...")
        import time as _time
        last = ""
        deadline = _time.monotonic() + 180
        while _time.monotonic() < deadline:
            try:
                qr = wa_channel.qr_state()
            except Exception as exc:
                out(f"stato QR non leggibile: {exc}")
                return
            if qr.get("state") == "paired":
                out("WhatsApp collegato.")
                return
            if qr.get("state") == "expired":
                out("QR scaduto: riprova con /whatsapp pair.")
                return
            code = qr.get("qr") or ""
            if code and code != last:
                last = code
                out(f"QR (payload testuale): {code}")
                out("L'app desktop lo mostra come immagine; da CLI serve un "
                    "generatore QR esterno oppure /whatsapp pair <numero>.")
            _time.sleep(2)
        out("Timeout pairing.")
        return
    out("Uso: /whatsapp [status] | /whatsapp pair [numero]")


def run_cli(registry: ProviderRegistry, *, active: str | None = None,
            once: str | None = None, stream: bool = True,
            workspace: Path | None = None,
            mode: Mode = Mode.ASK,
            max_steps: int = 10,
            secrets: SecretFilter | None = None,
            dotenv: dict | None = None,
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

    # Memoria persistente (spec sez. 5): cronologia per (mittente, canale) -
    # la CLI e' ("locale", "cli") - piu' note /remember iniettate a richiesta.
    store = MemoryStore(workspace / "memory.db")
    history: list[dict] = store.history("locale", "cli")

    def set_active(name: str) -> None:
        nonlocal active
        active = name

    # Canali chat (spec sez. 6): partono solo se abilitati in
    # config/channels.yaml; token e allowlist arrivano da .env, mai da config.
    # Il pairing (/pairing) esiste anche a canali spenti: un'approvazione
    # senza hub attivo vale dal prossimo avvio del canale.
    pairing = PairingStore(workspace / "data" / "pairing.json")
    hub = None
    wa_channel = None
    if dotenv is not None:
        hub, channels = _maybe_start_channels(
            workspace=workspace, registry=registry,
            get_active=lambda: active, set_active=set_active,
            ctx=ctx, store=store, log=log, seal=seal, secrets=secrets,
            dotenv=dotenv, out=out, max_steps=max_steps, pairing=pairing)
        wa_channel = channels.get("whatsapp")

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
        # Note pertinenti iniettate come contesto di sistema SOLO per questa
        # chiamata: la storia persistente resta utente/assistente.
        notes_block = memories_context(store.relevant(text))
        call_messages = ([{"role": "system", "content": notes_block}]
                         if notes_block else []) + history
        write("atlas> ")
        try:
            result = run_agent_loop(
                registry, active, call_messages, ctx, state["mode"], log,
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
            store.add_message("locale", "cli", "user", text)
            store.add_message("locale", "cli", "assistant", result.notes[-1])
            return True
        history.append({"role": "assistant", "content": result.text})
        store.add_message("locale", "cli", "user", text)
        store.add_message("locale", "cli", "assistant", result.text)
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
        if line.startswith("/pairing"):
            # Solo CLI locale: la chat non puo' approvare pairing (altrimenti
            # uno sconosciuto potrebbe autorizzarsi da solo).
            _handle_pairing(line, pairing, hub, out)
            continue
        if line.startswith("/whatsapp"):
            # Solo CLI locale: il collegamento dell'account WhatsApp (QR o
            # codice) e' un'operazione del proprietario, mai dalla chat.
            _handle_whatsapp(line, wa_channel, out)
            continue
        if line == "/help":
            out(HELP_TEXT)
            continue
        if line.startswith("/remember"):
            note = line[len("/remember"):].strip()
            try:
                note_id = store.remember(note)
            except ValueError as exc:
                out(f"Errore: {exc}")
                continue
            out(f"Ricordato (#{note_id}).")
            continue
        if line.startswith("/forget"):
            fragment = line[len("/forget"):].strip()
            try:
                removed = store.forget(fragment)
            except ValueError as exc:
                out(f"Errore: {exc}")
                continue
            out(f"Dimenticate {removed} note." if removed else
                "Nessuna nota contiene quel testo.")
            continue
        if line == "/memories":
            notes = store.all_memories()
            if not notes:
                out("Nessuna nota salvata. /remember <testo> per aggiungerne una.")
            else:
                for note_id, text_note in notes:
                    out(f"  #{note_id} {text_note}")
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
                   secrets=secrets, dotenv=dotenv)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
