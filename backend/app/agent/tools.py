"""Strumenti dell'agente (spec sez. 3): read_file, write_file, list_dir, shell.

Ogni strumento e' una funzione(args: dict, ctx: ToolContext) -> str con il suo
ToolSpec (nome, descrizione, JSON schema) per il provider. Il contesto porta
workspace e filtro segreti: i risultati passano dal SecretFilter prima di
tornare al modello, cosi' una chiave letta per sbaglio non entra nel contesto.
L'esecuzione vera e' nel loop: qui ci sono definizione ed effetto, li' il
controllo permessi e il logging.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .execsafe import validate as validate_command
from .safety import SecretFilter, is_blocked, resolve_in_workspace

SHELL_TIMEOUT = 60  # secondi fissi: un comando appeso non blocca l'agente
MAX_OUTPUT = 4000   # caratteri restituiti al modello per chiamata


@dataclass
class ToolContext:
    workspace: Path
    secrets: SecretFilter
    # Stage 10: scheduler per il tool schedule_job (None = tool non disponibile);
    # channel/sender = conversazione a cui consegnare l'output dei job creati
    # da questo contesto (l'hub li sostituisce per messaggio).
    scheduler: object | None = None
    # Stage 11: guardrail proattivita' per il tool proactive_feedback.
    proactive: object | None = None
    # Stage 13: abitudini apprese (observe/summarize).
    habits: object | None = None
    # Stage 14: libreria skill e forge dei tool auto-generati.
    skills: object | None = None
    forge: object | None = None
    # callback dopo una proposta salvata (commit git + notifica a Fred)
    selfmod_hook: object | None = None
    channel: str = "cli"
    sender: str = "local"


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any], ToolContext], str]
    read_only: bool = False  # True: nessuna modifica a file o sistema

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}


def _clip(text: str) -> str:
    if len(text) > MAX_OUTPUT:
        return text[:MAX_OUTPUT] + f"\n... [troncato: {len(text)} caratteri totali]"
    return text


def _read_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = str(args.get("path", "")).strip()
    if not path:
        return "errore: path vuoto"
    target, _ = resolve_in_workspace(ctx.workspace, path)
    if not target.is_file():
        return f"errore: {path} non e' un file"
    try:
        return _clip(target.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return f"errore lettura: {exc}"


def _write_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = str(args.get("path", "")).strip()
    content = str(args.get("content", ""))
    if not path:
        return "errore: path vuoto"
    target, _ = resolve_in_workspace(ctx.workspace, path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"errore scrittura: {exc}"
    return f"scritto {target} ({len(content)} caratteri)"


def _list_dir(args: dict[str, Any], ctx: ToolContext) -> str:
    path = str(args.get("path", ".")).strip() or "."
    target, _ = resolve_in_workspace(ctx.workspace, path)
    if not target.is_dir():
        return f"errore: {path} non e' una cartella"
    entries = []
    for entry in sorted(target.iterdir(), key=lambda e: e.name)[:200]:
        kind = "dir " if entry.is_dir() else "file"
        entries.append(f"{kind} {entry.name}")
    return "\n".join(entries) or "(cartella vuota)"


def _scrubbed_env() -> dict[str, str]:
    """Ambiente dei comandi shell SENZA segreti: i processi figli non ereditano
    chiavi API/token/segreti (enforcement reale, non lessicale: `env`,
    `cat $VAR`, subshell e script figli non vedono nulla). Restano solo le
    variabili innocue necessarie a eseguire comandi comuni."""
    keep = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "USER",
            "LOGNAME", "SHELL", "TMPDIR", "SYSTEMROOT", "COMSPEC"}
    return {k: v for k, v in os.environ.items()
            if k in keep and not any(
                mark in k.upper() for mark in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))}


# git legge configurazione da /etc, $HOME e .git/config: alias, diff esterni,
# textconv, pager possono eseguire processi arbitrari, e .git/config sarebbe
# scrivibile via write_file. Neutralizziamo TUTTE le sorgenti: il git della
# superficie ristretta gira senza nessuna configurazione ereditabile.
_GIT_NEUTRAL_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_LOCAL": "/dev/null",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "GIT_EXTERNAL_DIFF": "",
    "GIT_DIFF_OPTS": "",
    "GIT_SSH": "/bin/false",
    "GIT_SSH_COMMAND": "/bin/false",
    "GIT_ASKPASS": "/bin/false",
    "SSH_ASKPASS": "/bin/false",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_EDITOR": "/bin/false",
    "EDITOR": "/bin/false",
    "VISUAL": "/bin/false",
}


def _tool_env() -> dict[str, str]:
    env = _scrubbed_env()
    env.update(_GIT_NEUTRAL_ENV)
    return env


def _is_protected_path(target: Path) -> bool:
    norm = str(target).replace("\\", "/")
    if "/.git/" in norm or norm.endswith("/.git"):
        return True  # config/hooks/attributes del repo: mai scrivibili dai tool
    if any(part in norm for part in ("app/agent/", "app/agent_cli.py")):
        return True
    return target.name == ".env"


def _shell(args: dict[str, Any], ctx: ToolContext) -> str:
    command = str(args.get("command", "")).strip()
    if not command:
        return "errore: comando vuoto"
    # Blocklist: gia' controllata dal loop, resta come ultima rete.
    reason = is_blocked(command)
    if reason:
        return f"BLOCCATO: {reason}"
    # NESSUNA shell. argv diretto, allowlist, path
    # risolti (symlink compresi) prima dell'esecuzione: la prevenzione e'
    # strutturale, non un filtro sul testo del comando.
    check = validate_command(command, ctx.workspace,
                             resolve_in_workspace, _is_protected_path)
    if not check.ok:
        return f"NON DISPONIBILE: {check.reason}"
    try:
        proc = subprocess.run(
            check.argv, shell=False, cwd=str(ctx.workspace),
            capture_output=True, text=True, timeout=SHELL_TIMEOUT,
            errors="replace", env=_tool_env(), stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"errore: comando oltre il timeout di {SHELL_TIMEOUT}s, terminato"
    except FileNotFoundError:
        return f"errore: {check.argv[0]} non trovato su questo sistema"
    out = (proc.stdout or "") + (proc.stderr or "")
    return _clip(f"exit {proc.returncode}\n{out}" if out else f"exit {proc.returncode}")


READ_FILE = Tool(
    name="read_file",
    description="Legge un file di testo dal workspace dell'agente.",
    parameters={"type": "object",
                "properties": {"path": {"type": "string", "description": "percorso del file, relativo al workspace"}},
                "required": ["path"]},
    run=_read_file,
    read_only=True,
)

WRITE_FILE = Tool(
    name="write_file",
    description="Scrive (o sovrascrive) un file di testo nel workspace dell'agente.",
    parameters={"type": "object",
                "properties": {
                    "path": {"type": "string", "description": "percorso del file, relativo al workspace"},
                    "content": {"type": "string", "description": "contenuto completo del file"},
                },
                "required": ["path", "content"]},
    run=_write_file,
)

LIST_DIR = Tool(
    name="list_dir",
    description="Elenca il contenuto di una cartella del workspace.",
    parameters={"type": "object",
                "properties": {"path": {"type": "string", "description": "cartella, default '.'"}},
                "required": []},
    run=_list_dir,
    read_only=True,
)

SHELL = Tool(
    name="shell",
    description=("Esegue un comando dalla superficie ristretta nel workspace: solo "
                 "comandi di lettura/ispezione allowlistati (ls, cat, grep su file "
                 "espliciti, git status/log...), senza pipe, redirect o variabili - "
                 "non esiste una shell. Le scritture passano da write_file. Comandi "
                 "fuori superficie: non disponibili. Soggetto a permessi e blocklist."),
    parameters={"type": "object",
                "properties": {"command": {"type": "string", "description": "comando da eseguire"}},
                "required": ["command"]},
    run=_shell,
)

def _proactive_feedback(args: dict[str, Any], ctx: ToolContext) -> str:
    if ctx.proactive is None:
        return "errore: messaggi proattivi non attivi in questa interfaccia"
    score = ctx.proactive.feedback(args["category"],
                                   more=args["action"] == "more")
    direction = "alzata" if args["action"] == "more" else "abbassata"
    return f"Importanza di '{args['category']}' {direction} a {score}."


PROACTIVE_FEEDBACK = Tool(
    name="proactive_feedback",
    description=("Registra il feedback di Fred sui messaggi proattivi: piu' o "
                 "meno di una categoria (job, brief, heads-up, reminder, digest). "
                 "Usa quando dice 'meno di questo', 'troppi messaggi su X' o "
                 "'voglio piu' aggiornamenti su Y'."),
    parameters={"type": "object",
                "properties": {
                    "category": {"type": "string",
                                 "description": "categoria del messaggio proattivo"},
                    "action": {"type": "string", "enum": ["more", "less"]}},
                "required": ["category", "action"]},
    run=_proactive_feedback,
)


def _schedule_job(args: dict[str, Any], ctx: ToolContext) -> str:
    """Crea un job ricorrente o un promemoria (spec sez. 8). I job RICORRENTI
    creati dall'agente nascono NON approvati: il si' di Fred arriva con
    /jobs approve <id>, in qualunque modo permessi."""
    if ctx.scheduler is None:
        return "errore: scheduler non disponibile in questa interfaccia"
    kind = str(args.get("kind", "")).strip()
    schedule = str(args.get("schedule", "")).strip()
    task = str(args.get("task", "")).strip()
    if kind not in ("cron", "once") or not schedule or not task:
        return "errore: servono kind (cron|once), schedule e task"
    try:
        job = ctx.scheduler.add_job(kind=kind, schedule=schedule, task=task,
                                    channel=ctx.channel, sender=ctx.sender,
                                    source="agent")
    except ValueError as exc:
        return f"errore: {exc}"
    import time as _time
    next_at = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(job.next_fire))
    if not job.approved:
        return (f"job #{job.id} creato ma IN ATTESA DI APPROVAZIONE: i job "
                "ricorrenti proposti dall'agente partono solo dopo il si' di "
                "Fred (/jobs approve). Mostragli la proposta adesso.")
    return f"promemoria #{job.id} creato: esecuzione {next_at}."


SCHEDULE_JOB = Tool(
    name="schedule_job",
    description=("Crea un promemoria una tantum (kind=once, schedule='YYYY-MM-DD HH:MM') "
                 "o un job ricorrente (kind=cron, schedule='<5 campi cron>'). I job "
                 "ricorrenti richiedono l'approvazione di Fred prima di partire."),
    parameters={"type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["cron", "once"]},
                    "schedule": {"type": "string",
                                 "description": "espressione cron a 5 campi oppure data/ora ISO locale"},
                    "task": {"type": "string", "description": "cosa deve fare l'agente quando scatta"},
                },
                "required": ["kind", "schedule", "task"]},
    run=_schedule_job,
)

def _save_skill(args: dict[str, Any], ctx: ToolContext) -> str:
    if ctx.skills is None:
        return "errore: libreria skill non attiva"
    try:
        ctx.skills.save(args["name"], args["procedure"],
                        source_task=args.get("source_task", ""))
    except ValueError as exc:
        return f"errore: {exc}"
    if ctx.selfmod_hook is not None:
        ctx.selfmod_hook(f"nuova skill proposta: {args['name']}")
    return (f"skill '{args['name']}' salvata come PROPOSTA: sara' riusabile "
            "solo dopo l'approvazione di Fred (/skills approve "
            f"{args['name']}). Digli che l'hai preparata.")


SAVE_SKILL = Tool(
    name="save_skill",
    description=("Salva la procedura di un task a piu' passi riuscito come "
                 "skill riusabile. Nasce come proposta: Fred la attiva con "
                 "/skills approve. Usala dopo aver completato con successo "
                 "un procedimento che potrebbe ripetersi."),
    parameters={"type": "object",
                "properties": {
                    "name": {"type": "string",
                             "description": "nome breve a trattini (es. brief-mattutino)"},
                    "procedure": {"type": "string",
                                  "description": "i passi da riusare, in elenco"},
                    "source_task": {"type": "string",
                                    "description": "il task da cui l'hai imparata"}},
                "required": ["name", "procedure"]},
    run=_save_skill,
)


def _propose_tool(args: dict[str, Any], ctx: ToolContext) -> str:
    if ctx.forge is None:
        return "errore: tool auto-generati non attivi"
    try:
        result = ctx.forge.propose(args["name"], args["code"], args["tests"])
    except ValueError as exc:
        return f"errore: {exc}"
    if ctx.selfmod_hook is not None:
        ctx.selfmod_hook(f"nuovo tool proposto: {args['name']}")
    tests = "i suoi test passano" if result["tests_ok"] else         "ATTENZIONE: i suoi test NON passano"
    return (f"tool '{args['name']}' proposto ({tests}). E' INATTIVO finche' "
            f"Fred non lo approva (/tools approve {args['name']}); il codice "
            f"e' in tools_generated/{args['name']}.py, visibile con "
            "/tools diff. Presentaglielo: non usarlo prima del si'.")


PROPOSE_TOOL = Tool(
    name="propose_tool",
    description=("Proponi un nuovo tool auto-generato: scrivi codice Python "
                 "(deve esporre name, description, parameters e "
                 "run(args, ctx)) e i suoi test. I test girano in sandbox "
                 "subito; il tool resta INATTIVO finche' Fred non lo "
                 "approva. Non proporre tool che toccano app/, la "
                 "sicurezza, .env o i log."),
    parameters={"type": "object",
                "properties": {
                    "name": {"type": "string",
                             "description": "nome snake_case del tool"},
                    "code": {"type": "string",
                             "description": "codice del modulo del tool"},
                    "tests": {"type": "string",
                              "description": "test pytest del tool"}},
                "required": ["name", "code", "tests"]},
    run=_propose_tool,
)


def _strip_html(html: str) -> str:
    """Testo grezzo da HTML: parser stdlib, niente dipendenze."""
    from html.parser import HTMLParser

    class _Text(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []
            self._skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript"):
                self._skip += 1

        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript") and self._skip:
                self._skip -= 1

        def handle_data(self, data):
            if not self._skip:
                self.parts.append(data)

    parser = _Text()
    parser.feed(html)
    return " ".join("".join(parser.parts).split())


def _web_search(args: dict[str, Any], ctx: ToolContext) -> str:
    """Ricerca web via endpoint HTML pubblico di DuckDuckGo: nessuna chiave,
    nessun dato dell'utente oltre la query stessa."""
    import httpx

    query = str(args.get("query", "")).strip()
    if not query:
        return "errore: query vuota"
    try:
        resp = httpx.post("https://html.duckduckgo.com/html/",
                          data={"q": query},
                          headers={"User-Agent": "ATLAS-agent/3.0"},
                          timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return f"errore ricerca web: {type(exc).__name__}"
    import re
    results = []
    for m in re.finditer(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            resp.text, re.S):
        href, title = m.group(1), _strip_html(m.group(2))
        if "uddg=" in href:
            from urllib.parse import parse_qs, urlparse
            href = parse_qs(urlparse(href).query).get("uddg", [href])[0]
        results.append(f"- {title}: {href}")
        if len(results) >= 8:
            break
    if not results:
        return "nessun risultato trovato"
    return _clip("\n".join(results))


def _is_public_host(url: str) -> str | None:
    """Rifiuta host privati/loopback: il tool non deve raggiungere la
    macchina locale o la rete interna (niente SSRF verso l'agente stesso)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    if not host:
        return "url senza host"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return f"host non risolvibile: {host}"
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return f"host non pubblico: {host}"
    return None


def _fetch_url(args: dict[str, Any], ctx: ToolContext) -> str:
    """Scarica una pagina pubblica e ne restituisce il testo (max 4000
    caratteri al modello). Solo http/https su host pubblici."""
    import httpx

    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return "errore: url deve iniziare con http:// o https://"
    blocked = _is_public_host(url)
    if blocked:
        return f"errore: {blocked}"
    try:
        resp = httpx.get(url, headers={"User-Agent": "ATLAS-agent/3.0"},
                         timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return f"errore fetch: {type(exc).__name__}"
    ctype = resp.headers.get("content-type", "")
    text = resp.text[:200_000]
    if "html" in ctype:
        text = _strip_html(text)
    return _clip(text)


def _python_exec(args: dict[str, Any], ctx: ToolContext) -> str:
    """Esegue codice Python nel workspace: interprete di sistema, stdin
    chiuso, timeout fisso, ambiente ripulito dai segreti. Nessuna shell."""
    import os
    import subprocess

    code = str(args.get("code", ""))
    if not code.strip():
        return "errore: codice vuoto"
    env = {k: v for k, v in os.environ.items()
           if not any(t in k.upper() for t in ("KEY", "SECRET", "TOKEN", "PASSWORD"))}
    try:
        proc = subprocess.run(
            ["python3", "-c", code],
            cwd=str(ctx.workspace), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "errore: timeout di 30s superato"
    except OSError as exc:
        return f"errore esecuzione: {exc}"
    out = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    if proc.returncode != 0:
        out = f"[exit {proc.returncode}]\n" + out
    return _clip(out) or "(nessun output)"


WEB_SEARCH = Tool(
    name="web_search",
    description=("Cerca sul web con DuckDuckGo: titoli e link dei primi "
                 "risultati. Per leggere una pagina usa fetch_url. La query "
                 "esce dal PC verso il motore di ricerca."),
    parameters={"type": "object",
                "properties": {"query": {"type": "string",
                                         "description": "testo da cercare"}},
                "required": ["query"]},
    run=_web_search,
    read_only=True,
)

FETCH_URL = Tool(
    name="fetch_url",
    description=("Scarica una pagina web pubblica e ne restituisce il testo. "
                 "Solo http/https su host pubblici: mai la macchina locale o "
                 "la rete interna."),
    parameters={"type": "object",
                "properties": {"url": {"type": "string",
                                       "description": "url http/https da scaricare"}},
                "required": ["url"]},
    run=_fetch_url,
    read_only=True,
)

PYTHON_EXEC = Tool(
    name="python_exec",
    description=("Esegue codice Python nel workspace (interprete python3 di "
                 "sistema, timeout 30s, stdin chiuso, ambiente senza "
                 "segreti). Per calcoli, parsing e piccoli script."),
    parameters={"type": "object",
                "properties": {"code": {"type": "string",
                                        "description": "codice Python da eseguire"}},
                "required": ["code"]},
    run=_python_exec,
)


ALL_TOOLS: tuple[Tool, ...] = (READ_FILE, WRITE_FILE, LIST_DIR, SHELL,
                               WEB_SEARCH, FETCH_URL, PYTHON_EXEC,
                               SCHEDULE_JOB, PROACTIVE_FEEDBACK, SAVE_SKILL,
                               PROPOSE_TOOL)
TOOLS_BY_NAME: dict[str, Tool] = {t.name: t for t in ALL_TOOLS}


def register_generated_tools(namespaces: dict) -> int:
    """Registra i tool auto-generati APPROVATI nel set attivo. Ogni
    namespace deve esporre name/description/parameters/run."""
    added = 0
    for key, ns in namespaces.items():
        if key in TOOLS_BY_NAME:
            continue
        TOOLS_BY_NAME[key] = Tool(
            name=str(ns["name"]), description=str(ns["description"]),
            parameters=ns["parameters"], run=ns["run"])
        added += 1
    return added
