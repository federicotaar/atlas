"""Comandi slash condivisi tra CLI e canali chat (spec sez. 6: /model,
/mode, /remember, /forget devono esistere anche su Telegram/WhatsApp).

/stop e /quit restano per-contesto: la CLI ferma il task del REPL, il canale
ferma il task della conversazione. /help ha un testo per superficie.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..providers.base import ProviderError
from ..providers.registry import ConfigError, ProviderRegistry
from .localzone import fmt_wall
from .memory import MemoryStore
from .permissions import parse_mode


@dataclass
class CommandContext:
    registry: ProviderRegistry
    state: dict                       # {"mode": Mode}
    store: MemoryStore
    out: Callable[[str], None]
    get_active: Callable[[], str]
    set_active: Callable[[str], None]
    describe_registry: Callable[[ProviderRegistry, str, Callable[[str], None]], None]
    # Stage 10: scheduler dei jobs (None = comandi /schedule e /jobs disabiliti).
    # channel/sender identificano dove consegnare l'output dei job creati da qui.
    scheduler: object | None = None
    # Stage 13: abitudini apprese (None = /habits disabilitato).
    habits: object | None = None
    # Stage 14: skill / tool generati / rollback auto-modifiche.
    skills: object | None = None
    forge: object | None = None
    rollback_fn: object | None = None
    post_approve_fn: object | None = None  # registra tool + health check
    channel: str = "cli"
    sender: str = "local"
    # Fuso orario: gate proattivo e workspace (per /timezone, persistenza).
    proactive: object | None = None
    workspace: object | None = None


def parse_model_target(arg: str) -> tuple[str, str | None]:
    """'ollama_local:llama3.2:3b' -> ('ollama_local', 'llama3.2:3b')."""
    arg = arg.strip()
    if not arg:
        raise ConfigError("uso: /model use <nome[:modello]>")
    if ":" in arg:
        name, model = arg.split(":", 1)
        if not name.strip() or not model.strip():
            raise ConfigError(f"destinazione non valida: {arg!r}")
        return name.strip(), model.strip()
    return arg, None


def _handle_habits(line: str, cc: CommandContext) -> bool:
    if cc.habits is None:
        cc.out("Abitudini non attive in questa interfaccia.")
        return True
    arg = line[len("/habits"):].strip()
    if arg == "off":
        cc.habits.set_enabled(False)
        cc.out("Apprendimento abitudini SPENTO e dati raccolti cancellati. "
               "Riattiva con /habits on.")
        return True
    if arg == "on":
        cc.habits.set_enabled(True)
        cc.out("Apprendimento abitudini attivo.")
        return True
    if arg.startswith("forget"):
        keyword = arg[len("forget"):].strip()
        if not keyword:
            cc.out("Uso: /habits forget <parola>")
            return True
        removed = cc.habits.forget(keyword)
        cc.out(f"Rimossi {removed} elementi su '{keyword}'.")
        return True
    if arg:
        cc.out("Uso: /habits [off|on|forget <parola>]")
        return True
    items = cc.habits.summarize()
    if not cc.habits.enabled():
        cc.out("Apprendimento abitudini spento (/habits on per riattivare).")
    if not items:
        cc.out("Ancora nessuna abitudine con abbastanza evidenze.")
    else:
        cc.out("Abitudini apprese (suggerimenti, non fatti):")
        import time as _time
        for item in items:
            seen = _time.strftime("%Y-%m-%d",
                                  _time.localtime(item["last_seen"]))
            cc.out(f"- {item['text']} (evidenze: {item['evidence']}, "
                   f"ultima: {seen})")
    return True


def _handle_skills(line: str, cc: CommandContext) -> bool:
    if cc.skills is None:
        cc.out("Libreria skill non attiva in questa interfaccia.")
        return True
    arg = line[len("/skills"):].strip()
    if arg.startswith("approve"):
        name = arg[len("approve"):].strip()
        if cc.skills.approve(name):
            cc.out(f"Skill '{name}' approvata: d'ora in poi la riuso "
                   "quando serve.")
        else:
            cc.out(f"Nessuna skill '{name}'.")
        return True
    if arg.startswith("reject"):
        name = arg[len("reject"):].strip()
        cc.out(f"Skill '{name}' scartata." if cc.skills.reject(name)
               else f"Nessuna skill '{name}'.")
        return True
    items = cc.skills.list()
    if not items:
        cc.out("Nessuna skill salvata.")
    else:
        for item in items:
            cc.out(f"- {item['name']} [{item['status']}]")
        cc.out("Le skill proposte si attivano con /skills approve <nome>.")
    return True


def _handle_tools(line: str, cc: CommandContext) -> bool:
    if cc.forge is None:
        cc.out("Tool auto-generati non attivi in questa interfaccia.")
        return True
    arg = line[len("/tools"):].strip()
    if arg.startswith("approve"):
        name = arg[len("approve"):].strip()
        result = cc.forge.approve(name)
        if result["ok"]:
            cc.out(f"Tool '{name}' approvato e attivo.")
            if cc.post_approve_fn is not None:
                cc.post_approve_fn(name)
        else:
            cc.out(f"Attivazione rifiutata: {result['error']}")
        return True
    if arg.startswith("reject"):
        name = arg[len("reject"):].strip()
        cc.out(f"Tool '{name}' scartato." if cc.forge.reject(name)
               else f"Nessuna proposta '{name}'.")
        return True
    if arg.startswith("diff"):
        name = arg[len("diff"):].strip()
        text = cc.forge.diff(name)
        cc.out(text if text else f"Nessuna proposta '{name}'.")
        return True
    items = cc.forge.list()
    if not items:
        cc.out("Nessun tool proposto.")
    else:
        for item in items:
            tests = "test ok" if item.get("tests_ok") else "test FALLITI"
            cc.out(f"- {item['name']} [{item['status']}, {tests}]")
        cc.out("Attiva con /tools approve <nome>; vedi il codice con "
               "/tools diff <nome>.")
    return True


def handle_command(line: str, cc: CommandContext) -> bool:
    """True se `line` era un comando condiviso gestito."""
    if line.startswith("/habits"):
        return _handle_habits(line, cc)
    if line.startswith("/skills"):
        return _handle_skills(line, cc)
    if line.startswith("/tools"):
        return _handle_tools(line, cc)
    if line == "/rollback":
        if cc.rollback_fn is None:
            cc.out("Rollback non disponibile in questa interfaccia.")
            return True
        ok, message = cc.rollback_fn()
        cc.out(message)
        return True
    if line == "/model":
        cc.out(f"provider: {cc.get_active()}  modello: {cc.registry.current_model(cc.get_active())}")
        return True
    if line == "/model list":
        try:
            cc.describe_registry(cc.registry, cc.get_active(), cc.out)
        except ProviderError as exc:
            cc.out(f"Errore: {exc}")
        return True
    if line.startswith("/model use"):
        parts = line.split(maxsplit=2)
        if len(parts) < 3:
            cc.out("Uso: /model use <nome[:modello]>")
            return True
        try:
            name, model = parse_model_target(parts[2])
            cc.registry.spec(name)
        except ConfigError as exc:
            cc.out(f"Errore: {exc}")
            return True
        usable, reason = cc.registry.usable(name)
        if not usable:
            cc.out(f"{name} non e' usabile: {reason}")
            return True
        if model:
            cc.registry.set_model(name, model)
        cc.set_active(name)
        cc.out(f"Provider attivo: {name} (modello {cc.registry.current_model(name)})")
        return True
    if line == "/mode":
        cc.out(f"modo permessi: {cc.state['mode'].value} (ask, auto-safe, yolo)")
        return True
    if line.startswith("/mode "):
        try:
            cc.state["mode"] = parse_mode(line.split(maxsplit=1)[1])
        except ValueError as exc:
            cc.out(f"Errore: {exc}")
            return True
        cc.out(f"modo permessi: {cc.state['mode'].value}")
        return True
    if line.startswith("/remember"):
        try:
            note_id = cc.store.remember(line[len("/remember"):].strip())
        except ValueError as exc:
            cc.out(f"Errore: {exc}")
            return True
        cc.out(f"Ricordato (#{note_id}).")
        return True
    if line.startswith("/forget"):
        try:
            removed = cc.store.forget(line[len("/forget"):].strip())
        except ValueError as exc:
            cc.out(f"Errore: {exc}")
            return True
        # Onesta' semantica: /forget rimuove NOTE, non la cronologia.
        cc.out((f"Dimenticate {removed} note (la cronologia resta)."
                if removed else "Nessuna nota contiene quel testo."))
        return True
    if line == "/memories":
        notes = cc.store.all_memories()
        if not notes:
            cc.out("Nessuna nota salvata. /remember <testo> per aggiungerne una.")
        else:
            for note_id, text in notes:
                cc.out(f"  #{note_id} {text}")
        return True
    if line.startswith("/schedule"):
        _handle_schedule(line, cc)
        return True
    if line.startswith("/jobs"):
        _handle_jobs(line, cc)
        return True
    if line.startswith("/timezone"):
        _handle_timezone(line, cc)
        return True
    return False


def _handle_schedule(line: str, cc: CommandContext) -> None:
    """/schedule <frase> - crea un job da linguaggio naturale (spec sez. 8).
    La frase passa dal modello attivo per diventare cron/una-tantum; il job
    creato qui e' dell'UTENTE: attivo subito (l'approvazione riguarda i job
    ricorrenti proposti dall'agente, non quelli chiesti da Fred)."""

    from .scheduler import parse_schedule_phrase

    if cc.scheduler is None:
        cc.out("Scheduler non attivo in questa interfaccia.")
        return
    phrase = line[len("/schedule"):].strip()
    if not phrase:
        cc.out("Uso: /schedule <quando> <cosa> - es. /schedule ogni giorno "
               "feriale alle 8 mandami il brief")
        return
    try:
        parsed = parse_schedule_phrase(cc.registry, cc.get_active(), phrase,
                                       zone=cc.scheduler.zone)
        job = cc.scheduler.add_job(
            kind=parsed["kind"], schedule=parsed["schedule"],
            task=parsed["task"], channel=cc.channel, sender=cc.sender,
            source="user")
    except ValueError as exc:
        cc.out(f"Errore: {exc}")
        return
    next_at = fmt_wall(job.next_fire, cc.scheduler.zone)
    kind_it = "ricorrente" if job.kind == "cron" else "una tantum"
    cc.out(f"Job #{job.id} creato ({kind_it}): \"{job.task}\" - "
           f"prossima esecuzione {next_at} "
           f"(fuso {cc.scheduler.zone.key}).")


def _handle_jobs(line: str, cc: CommandContext) -> None:
    """/jobs | /jobs pause|resume|delete|approve <id> (spec sez. 8)."""

    if cc.scheduler is None:
        cc.out("Scheduler non attivo in questa interfaccia.")
        return
    parts = line.split()
    if len(parts) == 1:
        jobs = cc.scheduler.list_jobs()
        if not jobs:
            cc.out("Nessun job. /schedule <quando> <cosa> per crearne uno.")
            return
        cc.out(f"Fuso orario attivo: {cc.scheduler.zone.key}")
        for job in jobs:
            flags = []
            if job.paused:
                flags.append("in pausa")
            if not job.approved:
                flags.append("DA APPROVARE (/jobs approve "
                             f"{job.id})")
            next_at = fmt_wall(job.next_fire, cc.scheduler.zone)
            flag_txt = f"  [{', '.join(flags)}]" if flags else ""
            cc.out(f"  #{job.id} ({job.kind}) {job.schedule} - \"{job.task}\""
                   f" - prossima: {next_at}{flag_txt}")
        return
    action = parts[1]
    if action in ("pause", "resume", "delete", "approve") and len(parts) > 2:
        try:
            job_id = int(parts[2])
        except ValueError:
            cc.out(f"Id non valido: {parts[2]}")
            return
        fn = {"pause": cc.scheduler.pause_job,
              "resume": cc.scheduler.resume_job,
              "approve": cc.scheduler.approve_job}.get(action)
        if action == "delete":
            cc.out((f"Job #{job_id} eliminato." if cc.scheduler.delete_job(job_id)
                    else f"Job #{job_id} non trovato."))
            return
        job = fn(job_id)
        if job is None:
            cc.out(f"Job #{job_id} non trovato.")
        else:
            verb = {"pause": "in pausa", "resume": "riattivato",
                    "approve": "approvato e attivo"}[action]
            cc.out(f"Job #{job_id} {verb}.")
        return
    cc.out("Uso: /jobs | /jobs pause <id> | /jobs resume <id> | "
           "/jobs delete <id> | /jobs approve <id>")


def _handle_timezone(line: str, cc: CommandContext) -> None:
    """/timezone [nome-IANA] - mostra o imposta il fuso dell'installazione.

    Il fuso e' persistito in config/app.json e governa scheduler, ore
    silenziose e parsing delle schedulazioni. Senza argomenti mostra quello
    attivo e la sua fonte."""
    from .localzone import detect_system_zone, load_timezone_name, save_timezone
    arg = line[len("/timezone"):].strip()
    workspace = cc.workspace
    if workspace is None:
        cc.out("Fuso orario non configurabile in questa interfaccia.")
        return
    if not arg:
        active = load_timezone_name(workspace)
        cc.out(f"Fuso orario attivo: {active} "
               f"(sistema rilevato: {detect_system_zone() or 'n/d'}). "
               "Cambialo con /timezone <nome IANA>, es. /timezone Europe/Rome.")
        return
    try:
        name = save_timezone(workspace, arg)
    except ValueError as exc:
        cc.out(f"Errore: {exc}")
        return
    if cc.scheduler is not None:
        from zoneinfo import ZoneInfo
        cc.scheduler.set_zone(ZoneInfo(name))
    if cc.proactive is not None:
        cc.proactive.cfg.timezone = name
    cc.out(f"Fuso orario impostato: {name}. Vale da subito per scheduler, "
           "ore silenziose e nuove schedulazioni.")
