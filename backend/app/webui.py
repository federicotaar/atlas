"""Web UI locale (build order 9, spec sez. 7.2): FastAPI + frontend semplice,
SOLO localhost (127.0.0.1, mai 0.0.0.0). Stesso loop agente, stessa memoria,
stessi strumenti e conferme della CLI e dei canali chat.

Modello: un solo utente locale, un task alla volta. POST /api/chat avvia il
task in un thread e risponde subito; la pagina fa polling di /api/state.
Le conferme degli strumenti (modo ask) emergono come state="question" e il
messaggio successivo e' la risposta (solo "yes" approva, come nei canali).
L'outbound passa dal SecretFilter come ogni altra superficie.

Nessuna autenticazione: il bind loopback e' il confine, per spec. Nessun
asset esterno, nessuna build: la pagina e' servita inline.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .agent.actionlog import ActionLog
from .agent.commands import CommandContext, handle_command
from .agent.loop import run_agent_loop
from .agent.memory import MemoryStore, memories_context
from .agent.permissions import Mode
from .agent.safety import SecretFilter
from .agent.seal import SealGuard
from .agent.tools import ToolContext
from .providers.base import AllProvidersFailed, ProviderError
from .providers.registry import ProviderRegistry

SENDER = "webui"

PAGE = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ATLAS</title>
<style>
:root{color-scheme:dark}
body{margin:0;background:#0d1117;color:#e6edf3;font:15px/1.5 system-ui,sans-serif;
display:flex;flex-direction:column;height:100vh}
header{padding:10px 16px;background:#161b22;border-bottom:1px solid #30363d;
font-weight:600;letter-spacing:.5px}
header span{color:#8b949e;font-weight:400;font-size:12px;margin-left:8px}
#log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px}
.msg{max-width:75%;padding:9px 13px;border-radius:14px;white-space:pre-wrap;
word-wrap:break-word}
.me{align-self:flex-end;background:#1f6feb;color:#fff;border-bottom-right-radius:4px}
.atlas{align-self:flex-start;background:#21262d;border:1px solid #30363d;
border-bottom-left-radius:4px}
.sys{align-self:center;color:#8b949e;font-size:12px;font-style:italic}
#bar{display:flex;gap:8px;padding:12px 16px;border-top:1px solid #30363d;
background:#161b22}
#in{flex:1;background:#0d1117;border:1px solid #30363d;border-radius:8px;
color:#e6edf3;padding:10px;font:inherit}
#in:focus{outline:none;border-color:#1f6feb}
button{background:#21262d;border:1px solid #30363d;border-radius:8px;color:#e6edf3;
padding:0 16px;font:inherit;cursor:pointer}
button:hover{background:#30363d}
#stop{color:#f85149}
</style></head><body>
<header><button id="back" title="Torna alla dashboard" style="display:none">&laquo; indietro</button>
ATLAS <span id="st">pronto</span>
<button id="gear" title="Provider e chiavi" style="float:right">&#9881;</button>
</header>
<div id="cfg" style="display:none;padding:12px 16px;border-bottom:1px solid #30363d">
<div id="cfglist"></div>
<div style="display:flex;gap:8px;margin-top:8px">
<input id="cfgkey" type="password" placeholder="chiave API (non viene mai mostrata)"
 style="flex:1;background:#0d1117;border:1px solid #30363d;border-radius:8px;color:#e6edf3;padding:8px;font:inherit">
<button id="cfgsave">salva e usa</button>
</div>
<div id="cfgmsg" class="muted" style="margin-top:6px"></div>
</div>
<div id="log"></div>
<div id="bar">
<input id="in" placeholder="Scrivi ad ATLAS... (invio per inviare)" autofocus>
<button id="stop" title="Annulla il task in corso">stop</button>
<button id="send">invia</button>
</div>
<script>
/* Ritorno alla dashboard quando la pagina e' aperta da un'altra schermata
   (app desktop: il link "Apri l'agente" naviga la stessa finestra). */
/* history.length > 1: il webview Tauri non manda il referrer nella
   navigazione same-window (provato dal click-through E2E in CI), ma la
   history contiene la dashboard. In un tab browser nuovo length==1. */
if (document.referrer || history.length > 1) {
  const b = document.getElementById('back');
  b.style.display = 'inline-block';
  b.onclick = () => history.back();
}
const log = document.getElementById('log'), inp = document.getElementById('in'),
      st = document.getElementById('st');
let lastSeq = 0, polling = false;
/* Rendering sicuro: PRIMA escape di tutto l'HTML del modello, POI solo
   **grassetto** e `codice` generati da noi. Niente HTML grezzo, mai. */
function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;'); }
function md(s){ return esc(s)
  .replace(/\\*\\*([^*]+)\\*\\*/g,'<strong>$1</strong>')
  .replace(/`([^`]+)`/g,'<code>$1</code>')
  .replace(/\\n/g,'<br>'); }
function add(cls, text){ const d = document.createElement('div');
  d.className = 'msg ' + cls; d.innerHTML = md(text); log.appendChild(d);
  log.scrollTop = log.scrollHeight; }
async function poll(){
  if (polling) return; polling = true;
  try {
    const r = await fetch('api/state'); const s = await r.json();
    if (s.seq !== lastSeq) {
      lastSeq = s.seq;
      if (s.question) add('atlas', s.question + '\\nRispondi "yes" per confermare, altro per annullare.');
      if (s.reply) add('atlas', s.reply);
    }
    st.textContent = s.state === 'running' ? 'sta lavorando...' :
                     s.state === 'question' ? 'attende conferma' : 'pronto';
    if (s.state !== 'idle') setTimeout(poll, 700); else polling = false;
  } catch(e){ polling = false; }
}
async function send(){
  const text = inp.value.trim(); if (!text) return;
  inp.value = ''; add('me', text);
  const r = await fetch('api/chat', {method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({message:text})});
  const s = await r.json();
  if (s.reply) add('atlas', s.reply);
  if (s.status === 'running') setTimeout(poll, 400); else poll();
}
document.getElementById('send').onclick = send;
inp.onkeydown = e => { if (e.key === 'Enter') send(); };
document.getElementById('stop').onclick = async () => {
  const r = await fetch('api/stop', {method:'POST'}); const s = await r.json();
  if (s.reply) add('sys', s.reply); poll(); };
fetch('api/history').then(r => r.json()).then(h => {
  for (const m of h.messages) add(m.role === 'user' ? 'me' : 'atlas', m.content);
});
let cfgProvider = null;
async function cfgLoad(){
  const r = await fetch('api/config'); const c = await r.json();
  const list = document.getElementById('cfglist'); list.innerHTML = '';
  for (const p of c.providers) {
    const row = document.createElement('div');
    const sel = p.name === c.active ? ' \u25c9 ' : ' \u25cb ';
    const key = p.needs_key ? (p.key_set ? 'chiave salvata' : 'chiave mancante') : 'senza chiave';
    row.textContent = sel + p.name + ' (' + p.model + ') - ' + key + (p.usable ? '' : ' [' + p.reason + ']');
    row.style.cursor = 'pointer'; row.style.padding = '3px 0';
    row.onclick = () => { cfgProvider = p.name;
      document.getElementById('cfgmsg').textContent = 'selezionato: ' + p.name; };
    list.appendChild(row);
  }
  cfgProvider = cfgProvider || c.active;
}
document.getElementById('gear').onclick = () => {
  const c = document.getElementById('cfg');
  c.style.display = c.style.display === 'none' ? 'block' : 'none';
  if (c.style.display === 'block') cfgLoad();
};
document.getElementById('cfgsave').onclick = async () => {
  if (!cfgProvider) return;
  const key = document.getElementById('cfgkey').value;
  const r = await fetch('api/config/provider', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:cfgProvider, api_key:key})});
  const s = await r.json();
  document.getElementById('cfgmsg').textContent = s.ok ?
    'provider attivo: ' + s.active : ('errore: ' + s.error);
  document.getElementById('cfgkey').value = '';
  cfgLoad();
};
</script></body></html>"""


class WebUISession:
    """Stato della conversazione web locale: un task alla volta, conferme
    via state="question", cancellazione con /stop."""

    def __init__(self, *, registry: ProviderRegistry, get_active, set_active,
                 describe_registry, ctx: ToolContext, store: MemoryStore,
                 log: ActionLog, seal: SealGuard | None, secrets: SecretFilter,
                 mode: Mode = Mode.ASK, max_steps: int = 10,
                 confirm_timeout: float = 300.0, scheduler=None,
                 proactive=None):
        self._registry = registry
        self._get_active = get_active
        self._set_active = set_active
        self._describe_registry = describe_registry
        self._ctx = ctx
        self._store = store
        self._log = log
        self._seal = seal
        self._secrets = secrets
        self._state = {"mode": mode}
        self._max_steps = max_steps
        self._confirm_timeout = confirm_timeout
        self._scheduler = scheduler
        self._proactive = proactive
        self._lock = threading.Lock()
        self._running = False
        self._cancel: threading.Event | None = None
        self._pending = threading.Event()      # c'e' una domanda in attesa
        self._answered = threading.Event()     # l'utente ha risposto
        self._confirm_answer: str | None = None
        self._question: str | None = None
        self._reply: str | None = None
        self._seq = 0

    # --- endpoint helpers ----------------------------------------------------

    def state(self) -> dict:
        if self._pending.is_set():
            state = "question"
        elif self._running:
            state = "running"
        else:
            state = "idle"
        return {"state": state, "question": self._question,
                "reply": self._reply, "seq": self._seq}

    def chat(self, text: str) -> dict:
        text = text.strip()
        if not text:
            return {"status": "ignored"}
        if self._pending.is_set():
            # Risposta a una conferma in sospeso: solo "yes" approva.
            self._confirm_answer = text.lower()
            self._pending.clear()
            self._answered.set()
            return {"status": "answered"}
        if text == "/stop":
            return self.stop()
        if text.startswith("/"):
            lines: list[str] = []
            handled = handle_command(text, CommandContext(
                registry=self._registry, state=self._state, store=self._store,
                out=lines.append, get_active=self._get_active,
                set_active=self._set_active,
                describe_registry=self._describe_registry,
                scheduler=self._scheduler, habits=self._ctx.habits,
                channel=SENDER, sender=SENDER))
            if self._proactive is not None:
                self._proactive.user_message_seen()
            if self._ctx.habits is not None:
                self._ctx.habits.observe_message(text)
            if handled:
                return {"status": "done",
                        "reply": "\n".join(lines) or "(comando eseguito)"}
        with self._lock:
            if self._running:
                return {"status": "busy",
                        "reply": "Un task e' gia' in corso: stop per annullarlo."}
            self._running = True
            self._cancel = threading.Event()
            self._reply = None
            cancel = self._cancel
        threading.Thread(target=self._run, args=(text, cancel),
                         daemon=True).start()
        return {"status": "running"}

    def stop(self) -> dict:
        if self._running and self._cancel is not None:
            self._cancel.set()
            return {"status": "stopping", "reply": "(annullamento richiesto)"}
        return {"status": "idle", "reply": "Nessun task in corso."}

    def history(self, limit: int = 50) -> list[dict]:
        return self._store.history(SENDER, SENDER)[-limit:]

    def inject(self, text: str) -> None:
        """Messaggio proattivo (output di un job schedulato): appare come
        bolla di ATLAS al prossimo poll di /api/state."""
        self._reply = self._secrets.redact(text)
        self._seq += 1

    # --- task --------------------------------------------------------------

    def _run(self, text: str, cancel: threading.Event) -> None:
        try:
            self._reply = self._secrets.redact(self._answer(text, cancel))
        finally:
            with self._lock:
                self._running = False
                self._cancel = None
            self._seq += 1

    def _answer(self, text: str, cancel: threading.Event) -> str:
        history = self._store.history(SENDER, SENDER)
        history.append({"role": "user", "content": text})
        notes_block = memories_context(self._store.relevant(text))
        call_messages = ([{"role": "system", "content": notes_block}]
                         if notes_block else []) + history

        def ask_fn(question: str) -> bool:
            self._question = question
            self._answered.clear()
            self._pending.set()
            self._seq += 1
            got = self._answered.wait(self._confirm_timeout)
            self._pending.clear()
            answer, self._confirm_answer = self._confirm_answer, None
            self._question = None
            self._seq += 1
            return got and answer == "yes"

        try:
            import dataclasses
            call_ctx = dataclasses.replace(self._ctx, channel=SENDER,
                                           sender=SENDER)
            result = run_agent_loop(
                self._registry, self._get_active(), call_messages,
                call_ctx, self._state["mode"], self._log,
                ask_fn=ask_fn, max_steps=self._max_steps,
                cancel=cancel, on_delta=None, seal=self._seal)
        except (AllProvidersFailed, ProviderError) as exc:
            return f"Errore: {exc}"
        if result.cancelled:
            return "(task annullato)"
        final = result.notes[-1] if result.stopped_by_limit else result.text
        self._store.add_message(SENDER, SENDER, "user", text)
        self._store.add_message(SENDER, SENDER, "assistant", final)
        return final


class ChatIn(BaseModel):
    message: str


class ProviderIn(BaseModel):
    name: str
    api_key: str = ""


class AgentAlreadyRunning(RuntimeError):
    """Lo stack agente su questo workspace e' gia' attivo in un altro
    processo (CLI, web UI o app desktop): una sola istanza alla volta."""


class AgentLock:
    """Lockfile con PID nel workspace: dedup istanze CLI/web/desktop."""

    def __init__(self, workspace: Path):
        self._path = workspace / "agent.lock"
        self._held = False

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            try:
                pid = int(self._path.read_text().strip())
                os.kill(pid, 0)  # esiste ancora?
            except (ValueError, ProcessLookupError, PermissionError):
                self._path.unlink(missing_ok=True)  # lock stale
            else:
                raise AgentAlreadyRunning(
                    f"agente gia' attivo (pid {pid}) su questo workspace")
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise AgentAlreadyRunning("agente gia' attivo (lock appena "
                                      "creato)") from exc
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        self._held = True

    def release(self) -> None:
        if self._held:
            self._path.unlink(missing_ok=True)
            self._held = False


class ConfigAPI:
    """Provisioning provider/chiavi dalla superficie web.

    Le chiavi si SCRIVONO soltanto (.env nel workspace, 0600 + ambiente del
    processo): non vengono mai restituite, loggate o mostrate. key_set dice
    solo se una chiave e' presente."""

    def __init__(self, registry: ProviderRegistry, workspace: Path,
                 get_active, set_active):
        self._registry = registry
        self._workspace = workspace
        self._get_active = get_active
        self._set_active = set_active

    def info(self) -> dict:
        providers = []
        for name in self._registry._order:  # noqa: SLF001 - stesso package logico
            spec = self._registry.spec(name)
            usable, reason = self._registry.usable(name)
            providers.append({
                "name": name, "model": self._registry.current_model(name),
                "usable": usable, "reason": reason,
                "key_set": bool(spec.api_key_env)
                           and bool(os.environ.get(spec.api_key_env, "")),
                "needs_key": spec.requires_key})
        return {"active": self._get_active(), "providers": providers}

    def set_provider(self, name: str, api_key: str | None = None) -> dict:
        try:
            spec = self._registry.spec(name)
        except Exception:
            return {"ok": False, "error": f"provider sconosciuto: {name}"}
        if api_key:
            if not spec.api_key_env:
                return {"ok": False,
                        "error": f"{name} non usa una chiave API"}
            os.environ[spec.api_key_env] = api_key
            self._persist_key(spec.api_key_env, api_key)
        usable, reason = self._registry.usable(name)
        if not usable and api_key and reason == "disabilitato in config":
            # Chiave fornita per un provider disabilitato: lo si abilita a
            # runtime e la scelta si persiste (il YAML non si tocca).
            self._registry.set_enabled(name, True)
            enabled = sorted({n for n in self._registry._order  # noqa: SLF001
                              if self._registry._enabled_overrides.get(n)})  # noqa: SLF001
            self._persist_key("ATLAS_ENABLED_PROVIDERS", ",".join(enabled))
            os.environ["ATLAS_ENABLED_PROVIDERS"] = ",".join(enabled)
            usable, reason = self._registry.usable(name)
        if not usable:
            return {"ok": False, "error": f"{name} non usabile: {reason}"}
        self._set_active(name)
        # La scelta del provider sopravvive ai riavvii (come la chiave):
        # ATLAS_PROVIDER nel .env del workspace, letto al boot.
        self._persist_key("ATLAS_PROVIDER", name)
        os.environ["ATLAS_PROVIDER"] = name
        return {"ok": True, "active": name}

    def _persist_key(self, env_name: str, value: str) -> None:
        env_path = self._workspace / ".env"
        lines = env_path.read_text().splitlines() if env_path.exists() else []
        out, done = [], False
        for line in lines:
            if line.startswith(f"{env_name}="):
                out.append(f"{env_name}={value}")
                done = True
            else:
                out.append(line)
        if not done:
            out.append(f"{env_name}={value}")
        env_path.write_text("\n".join(out) + "\n")
        os.chmod(env_path, 0o600)


def create_app(session: WebUISession, config: "ConfigAPI | None" = None):
    """ASGI app della web UI (montabile/testabile senza server)."""
    app = FastAPI(title="atlas-webui")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return PAGE

    @app.post("/api/chat")
    async def chat(body: ChatIn):
        return session.chat(body.message)

    @app.get("/api/state")
    async def state():
        return session.state()

    @app.post("/api/stop")
    async def stop():
        return session.stop()

    @app.get("/api/history")
    async def history():
        return {"messages": session.history()}

    if config is not None:
        @app.get("/api/config")
        async def config_info():
            return config.info()

        @app.post("/api/config/provider")
        async def config_provider(body: ProviderIn):
            return config.set_provider(body.name, body.api_key or None)

    return app


class WebUIStack:
    """Stack agente completo dietro la web UI: sessione, scheduler,
    proactive, habits, config API e lock di istanza. shutdown() ferma lo
    scheduler e rilascia il lock (chiusura pulita del desktop/servizio)."""

    def __init__(self, session: WebUISession, app, scheduler,
                 lock: AgentLock, config: ConfigAPI):
        self.session = session
        self.app = app
        self.scheduler = scheduler
        self.lock = lock
        self.config = config

    def shutdown(self) -> None:
        try:
            self.scheduler.stop()
        finally:
            self.lock.release()


def build_webui_stack(registry: ProviderRegistry, *, active: str,
                      workspace: Path, mode: Mode, max_steps: int,
                      secrets: SecretFilter,
                      out: Callable[[str], None] = print,
                      take_lock: bool = True) -> WebUIStack:
    """Costruisce lo stack agente completo per la web UI (o il mount
    desktop): una sola istanza per workspace (AgentLock)."""
    lock = AgentLock(workspace)
    if take_lock:
        lock.acquire()
    store = MemoryStore(workspace / "memory.db")
    log = ActionLog(workspace / "logs" / "actions.log", secrets)
    state = {"active": active}

    # Scheduler (stage 10): stesso jobs.db della CLI; l'atomic claim dello
    # store evita doppie esecuzioni se girano entrambe le interfacce.
    from .agent.scheduler import JobStore, Scheduler
    job_store = JobStore(workspace / "data" / "jobs.db")
    dispatchers: dict = {}

    def raw_deliver(channel: str, sender: str, text: str) -> None:
        safe = secrets.redact(text)
        deliver = dispatchers.get(channel) or dispatchers.get("webui")
        if deliver is not None:
            deliver(sender, safe)

    from .agent.proactive import ProactiveConfig, ProactiveGate, ProactiveStore
    pcfg = ProactiveConfig.load(workspace)
    gate = ProactiveGate(pcfg, ProactiveStore(workspace / "data" / "proactive.db"),
                         deliver_fn=raw_deliver)
    from .agent.habits import HabitStore
    habits = HabitStore(workspace / "data" / "habits.db")

    def dispatch_job(job, text: str) -> None:
        gate.deliver(job.channel, job.sender, text,
                     category="reminder" if job.kind == "once" else "job",
                     solicited=job.source == "user")

    def run_job_task(job) -> str:
        notes_block = memories_context(store.relevant(job.task))
        messages = ([{"role": "system", "content": notes_block}]
                    if notes_block else [])
        messages.append({"role": "user",
                         "content": f"[job schedulato #{job.id}] {job.task}"})
        result = run_agent_loop(
            registry, state["active"], messages, ctx, mode, log,
            ask_fn=lambda question: False,  # nessuno puo' confermare: rifiuta
            max_steps=max_steps, seal=seal_guard)
        return result.notes[-1] if result.stopped_by_limit else result.text

    seal_guard = SealGuard(workspace)
    ctx = ToolContext(workspace=workspace, secrets=secrets, channel=SENDER,
                      sender=SENDER)
    heartbeat_ticks = max(1, int(pcfg.heartbeat_minutes * 60 / 60))

    def on_tick(tick: int) -> None:
        if tick % heartbeat_ticks == 0:
            gate.heartbeat()

    scheduler = Scheduler(job_store, run_job_task, dispatch_job,
                          tick_seconds=60, on_tick=on_tick)
    ctx.scheduler = scheduler
    ctx.proactive = gate
    ctx.habits = habits
    scheduler.start()

    def describe(reg, act, emit):
        from .agent_cli import _describe_registry
        _describe_registry(reg, act, emit)

    session = WebUISession(
        registry=registry, get_active=lambda: state["active"],
        set_active=lambda n: state.__setitem__("active", n),
        describe_registry=describe, ctx=ctx, store=store, log=log,
        seal=seal_guard, secrets=secrets, mode=mode,
        max_steps=max_steps, scheduler=scheduler, proactive=gate)
    dispatchers["webui"] = lambda sender, text: session.inject(text)
    dispatchers["cli"] = lambda sender, text: out(f"\n[job] {text}")
    config = ConfigAPI(registry, workspace,
                       lambda: state["active"],
                       lambda n: state.__setitem__("active", n))
    app = create_app(session, config)
    return WebUIStack(session, app, scheduler, lock, config)


def run_webui(registry: ProviderRegistry, *, active: str, workspace: Path,
              mode: Mode, max_steps: int, secrets: SecretFilter,
              host: str = "127.0.0.1", port: int = 8321,
              out: Callable[[str], None] = print) -> int:
    """Avvia la web UI e blocca. SOLO loopback: rifiuta host non locali."""
    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1"):
        out(f"Errore: la web UI ascolta solo su localhost, non {host}")
        return 2
    try:
        stack = build_webui_stack(registry, active=active,
                                  workspace=workspace, mode=mode,
                                  max_steps=max_steps, secrets=secrets,
                                  out=out)
    except AgentAlreadyRunning as exc:
        out(f"Errore: {exc}. Chiudi l'altra istanza o usa quella.")
        return 2
    out(f"web UI su http://{host}:{port} (solo questa macchina; Ctrl-C per uscire)")
    try:
        uvicorn.run(stack.app, host=host, port=port, log_level="warning")
    finally:
        stack.shutdown()
    return 0
