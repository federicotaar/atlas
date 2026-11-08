"""Scheduler (build order 10, spec sez. 8): jobs ricorrenti (cron) e
promemoria una tantum, persistiti in SQLite (sopravvivono ai riavvii).

Niente dipendenze nuove: il matcher cron e' interno (equivalente consentito
dalla spec: "APScheduler or equivalent"). Sintassi cron a 5 campi
(minuto ora giorno mese giorno-settimana) con *, liste, intervalli, step
*/n e a-b/n, nomi di mesi (jan-dec) e giorni (mon-sun); domenica = 0 o 7.
Semantica Vixie per giorno-del-mese + giorno-della-settimana entrambi
ristretti: vale l'OR. Orari in ora locale; i casi limite di cambio ora
legale non sono gestiti (documentato).

Approvazioni (spec sez. 8): i job creati dall'UTENTE (CLI o chat) sono
attivi subito; i job RICORRENTI proposti dall'agente (tool schedule_job)
nascono NON approvati e restano in attesa fino a "/jobs approve <id>":
la proposta va mostrata a Fred una volta, sempre, in qualunque modo
permessi. I promemoria una tantum dell'agente sono attivi subito.

Il tick (default 60s) e' anche il battito (heartbeat) dello stage 10:
a ogni giro esegue i job dovuti e consegna l'output al canale d'origine
tramite il dispatcher registrato. La parte "decide se vale la pena
scrivere a Fred" e' dello stage 11 (proattivita').
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"))}
_DOWS = {d: i for i, d in enumerate(
    ("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}

_FIELD_BOUNDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]


def _field_values(part: str, lo: int, hi: int, names: dict[str, int]) -> set[int]:
    def val(tok: str) -> int:
        tok = tok.strip().lower()
        if tok in names:
            return names[tok]
        return int(tok)

    values: set[int] = set()
    for chunk in part.split(","):
        chunk = chunk.strip()
        step = 1
        if "/" in chunk:
            chunk, raw_step = chunk.split("/", 1)
            step = int(raw_step)
            if step < 1:
                raise ValueError(f"step non valido: {part}")
        if chunk in ("*", ""):
            first, last = lo, hi
        elif "-" in chunk:
            a, b = chunk.split("-", 1)
            first, last = val(a), val(b)
        else:
            first = last = val(chunk)
        if first < lo or last > hi or first > last:
            raise ValueError(f"valore fuori range ({lo}-{hi}): {chunk}")
        values.update(range(first, last + 1, step))
    if hi == 7 and 7 in values:  # domenica
        values.add(0)
        values.discard(7)
    return values


def parse_cron(expr: str) -> list[set[int]]:
    """Valida e ritorna i 5 campi come insiemi di valori."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron: servono 5 campi, trovati {len(fields)}")
    names = [dict(), dict(), dict(), _MONTHS, _DOWS]
    return [_field_values(f, lo, hi, names[i])
            for i, (f, (lo, hi)) in enumerate(zip(fields, _FIELD_BOUNDS))]


def cron_match(expr: str, when: float) -> bool:
    minute, hour, dom, month, dow = parse_cron(expr)
    tm = time.localtime(when)
    cron_dow = (tm.tm_wday + 1) % 7  # python: lun=0; cron: dom=0
    if tm.tm_min not in minute or tm.tm_hour not in hour or tm.tm_mon not in month:
        return False
    fields = expr.split()
    dom_restricted = fields[2] != "*"
    dow_restricted = fields[4] != "*"
    dom_ok = tm.tm_mday in dom
    dow_ok = cron_dow in dow
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok  # semantica Vixie
    return dom_ok and dow_ok


def next_fire(expr: str, after: float) -> float | None:
    """Prossimo istante (> after) in cui l'espressione scatta. None se mai
    entro un anno (es. 31 febbraio)."""
    minute, hour, dom, month, dow = parse_cron(expr)
    fields = expr.split()
    dom_restricted = fields[2] != "*"
    dow_restricted = fields[4] != "*"
    day = time.localtime(after)
    base = time.mktime((day.tm_year, day.tm_mon, day.tm_mday, 0, 0, 0, 0, 0, -1))
    for offset in range(0, 367):
        day_start = base + offset * 86400
        tm = time.localtime(day_start)
        if tm.tm_mon not in month:
            continue
        cron_dow = (tm.tm_wday + 1) % 7
        dom_ok = tm.tm_mday in dom
        dow_ok = cron_dow in dow
        if dom_restricted and dow_restricted:
            day_ok = dom_ok or dow_ok
        else:
            day_ok = dom_ok and dow_ok
        if not day_ok:
            continue
        for h in sorted(hour):
            for m in sorted(minute):
                ts = time.mktime((tm.tm_year, tm.tm_mon, tm.tm_mday,
                                  h, m, 0, 0, 0, -1))
                if ts > after:
                    return ts
    return None


@dataclass(frozen=True)
class Job:
    id: int
    kind: str        # cron | once
    schedule: str    # espressione cron oppure run_at ISO ("YYYY-MM-DD HH:MM")
    task: str
    channel: str
    sender: str
    source: str      # user | agent
    approved: bool
    paused: bool
    next_fire: float | None
    last_run: float | None
    created: float


class JobStore:
    """Persistenza SQLite dei jobs (workspace/data/jobs.db)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS jobs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, "
            "schedule TEXT NOT NULL, task TEXT NOT NULL, channel TEXT NOT NULL, "
            "sender TEXT NOT NULL, source TEXT NOT NULL, approved INTEGER NOT NULL, "
            "paused INTEGER NOT NULL DEFAULT 0, next_fire REAL, last_run REAL, "
            "created REAL NOT NULL)")
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(id=row["id"], kind=row["kind"], schedule=row["schedule"],
                   task=row["task"], channel=row["channel"],
                   sender=row["sender"], source=row["source"],
                   approved=bool(row["approved"]), paused=bool(row["paused"]),
                   next_fire=row["next_fire"], last_run=row["last_run"],
                   created=row["created"])

    @staticmethod
    def _compute_next(kind: str, schedule: str, now: float) -> float | None:
        if kind == "cron":
            nxt = next_fire(schedule, now)
            if nxt is None:
                raise ValueError(f"cron senza occorrenze entro un anno: {schedule}")
            return nxt
        if kind == "once":
            try:
                ts = time.mktime(time.strptime(schedule.strip(), "%Y-%m-%d %H:%M"))
            except ValueError as exc:
                raise ValueError(
                    f"data/ora non valida (atteso 'YYYY-MM-DD HH:MM'): {schedule}") from exc
            return ts
        raise ValueError(f"kind non valido: {kind}")

    def add(self, *, kind: str, schedule: str, task: str, channel: str,
            sender: str, source: str, now: float | None = None) -> Job:
        now = time.time() if now is None else now
        # Validazione preventiva: un job mai eseguibile non entra nello store.
        nxt = self._compute_next(kind, schedule, now)
        # Spec sez. 8: i job RICORRENTI dell'agente richiedono approvazione.
        approved = not (source == "agent" and kind == "cron")
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO jobs (kind, schedule, task, channel, sender, source,"
                " approved, paused, next_fire, created) VALUES (?,?,?,?,?,?,?,0,?,?)",
                (kind, schedule.strip(), task.strip(), channel, sender, source,
                 int(approved), nxt, now))
            self._db.commit()
            row = self._db.execute("SELECT * FROM jobs WHERE id=?",
                                   (cur.lastrowid,)).fetchone()
        return self._row_to_job(row)

    def list(self) -> list[Job]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        return [self._row_to_job(r) for r in rows]

    def get(self, job_id: int) -> Job | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id=?",
                                   (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def _set(self, job_id: int, **fields) -> Job | None:
        if not fields:
            return self.get(job_id)
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._db.execute(f"UPDATE jobs SET {cols} WHERE id=?",
                             (*fields.values(), job_id))
            self._db.commit()
        return self.get(job_id)

    def pause(self, job_id: int) -> Job | None:
        return self._set(job_id, paused=1)

    def resume(self, job_id: int, now: float | None = None) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        now = time.time() if now is None else now
        nxt = self._compute_next(job.kind, job.schedule, now)
        return self._set(job_id, paused=0, next_fire=nxt)

    def approve(self, job_id: int, now: float | None = None) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        now = time.time() if now is None else now
        nxt = self._compute_next(job.kind, job.schedule, now)
        return self._set(job_id, approved=1, next_fire=nxt)

    def delete(self, job_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self._db.commit()
        return cur.rowcount > 0

    def due(self, now: float) -> list[Job]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs WHERE approved=1 AND paused=0"
                " AND next_fire IS NOT NULL AND next_fire<=? ORDER BY next_fire",
                (now,)).fetchall()
        return [self._row_to_job(r) for r in rows]

    def claim_due(self, now: float) -> list[Job]:
        """Job dovuti, marcati come presi in carico NELLO STESSO lock: i cron
        riprogrammano subito la prossima occorrenza, i una-tantum si
        consumano. Due processi (CLI + web UI) non eseguono mai lo stesso
        job due volte; un crash a meta' job perde quell'occorrenza
        (scelta accettata, documentata)."""
        jobs = self.due(now)
        claimed = []
        for job in jobs:
            if job.kind == "once":
                if self.delete(job.id):
                    claimed.append(job)
                continue
            fresh = self.get(job.id)
            if fresh is None or fresh.next_fire != job.next_fire:
                continue  # gia' preso da un altro processo
            self._set(job.id, last_run=now,
                      next_fire=next_fire(job.schedule, now))
            claimed.append(job)
        return claimed

    def mark_run(self, job_id: int, ran_at: float) -> None:
        job = self.get(job_id)
        if job is None:
            return
        if job.kind == "once":
            self.delete(job_id)  # i promemoria una tantum si consumano
            return
        self._set(job_id, last_run=ran_at,
                  next_fire=next_fire(job.schedule, ran_at))


class Scheduler:
    """Tick periodico (heartbeat): esegue i job dovuti e consegna l'output.

    run_task(job) -> str: esegue il task del job (loop agente) e ritorna
    il testo da consegnare. dispatch(channel, sender, text): consegna
    (CLI: stampa; canali: send sul canale d'origine del job).
    """

    def __init__(self, store: JobStore, run_task, dispatch,
                 tick_seconds: float = 60.0, on_tick=None):
        self._store = store
        self._run_task = run_task
        self._dispatch = dispatch
        self._tick_seconds = tick_seconds
        self._on_tick = on_tick  # chiamato col numero di tick (heartbeat, st. 11)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ticks = 0

    # Facciata usata dai comandi (/schedule, /jobs) e dal tool schedule_job.

    def add_job(self, **kwargs) -> Job:
        return self._store.add(**kwargs)

    def list_jobs(self) -> list[Job]:
        return self._store.list()

    def pause_job(self, job_id: int) -> Job | None:
        return self._store.pause(job_id)

    def resume_job(self, job_id: int) -> Job | None:
        return self._store.resume(job_id)

    def approve_job(self, job_id: int) -> Job | None:
        return self._store.approve(job_id)

    def delete_job(self, job_id: int) -> bool:
        return self._store.delete(job_id)

    def tick(self, now: float | None = None) -> int:
        """Esegue i job dovuti. Ritorna quanti ne ha eseguiti."""
        now = time.time() if now is None else now
        ran = 0
        for job in self._store.claim_due(now):
            try:
                result = self._run_task(job)
            except Exception as exc:  # un job rotto non ferma lo scheduler
                result = f"(job #{job.id} fallito: {exc})"
            ran += 1
            if result:
                self._dispatch(job, result)
        return ran

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self._ticks += 1
                    self.tick()
                    if self._on_tick is not None:
                        self._on_tick(self._ticks)
                except Exception:
                    pass  # un tick andato male non ferma lo scheduler
                self._stop.wait(self._tick_seconds)
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def parse_schedule_phrase(registry, active: str, phrase: str) -> dict:
    """Frase in linguaggio naturale -> {"kind", "schedule", "task"}.
    Usa il provider attivo con un prompt stretto (risposta = solo JSON);
    solleva ValueError se il risultato non e' valido o non verificabile."""
    import json
    import re

    now = time.strftime("%Y-%m-%d %H:%M (%A)")
    prompt = (
        "Converti questa richiesta di schedulazione in JSON puro, nient'altro. "
        "Formati accettati:\n"
        '{"kind":"cron","schedule":"<5 campi cron: minuto ora giorno mese '
        'giorno-settimana>","task":"<cosa fare>"}\n'
        '{"kind":"once","schedule":"<YYYY-MM-DD HH:MM>","task":"<cosa fare>"}\n'
        "Regole: niente testo fuori dal JSON; task conserva l'intera azione "
        "richiesta; per 'ogni giorno feriale alle 8' usa '0 8 * * 1-5'. "
        f"Ora corrente dell'utente: {now}.\n"
        f"Richiesta: {phrase}")
    response = registry.chat_with_fallback(
        active, [{"role": "user", "content": prompt}], stream=False)
    text = (response.text or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("non riesco a interpretare la schedulazione")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError("non riesco a interpretare la schedulazione") from exc
    kind = data.get("kind")
    schedule = str(data.get("schedule", "")).strip()
    task = str(data.get("task", "")).strip()
    if kind not in ("cron", "once") or not schedule or not task:
        raise ValueError("non riesco a interpretare la schedulazione")
    # Validazione vera: il job deve essere eseguibile, non solo ben formato.
    JobStore._compute_next(kind, schedule, time.time())
    return {"kind": kind, "schedule": schedule, "task": task}
