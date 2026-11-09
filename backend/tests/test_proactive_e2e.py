"""E2E isolato (stage 10+11, clock simulato): un job richiesto dall'utente
viene eseguito dallo scheduler e consegnato attraverso il gate proattivo —
consegna diretta di giorno E in ore silenziose per i job richiesti
dall'utente (un promemoria chiesto per un'ora arriva a quell'ora), digest
in ore silenziose solo per il traffico non richiesto, persistenza al
riavvio, nessuna doppia esecuzione. Nessuna rete, nessun messaggio reale.
"""
from __future__ import annotations

import time

from app.agent.proactive import ProactiveConfig, ProactiveGate, ProactiveStore
from app.agent.scheduler import JobStore, Scheduler

NOON = time.mktime((2026, 11, 8, 12, 0, 0, 0, 0, -1))
NIGHT = time.mktime((2026, 11, 8, 23, 30, 0, 0, 0, -1))
MORNING = time.mktime((2026, 11, 9, 8, 0, 0, 0, 0, -1))


def build(tmp_path, sent, **cfg):
    jobs = JobStore(tmp_path / "data" / "jobs.db")
    gate = ProactiveGate(
        ProactiveConfig(**cfg),
        ProactiveStore(tmp_path / "data" / "proactive.db"),
        deliver_fn=lambda c, s, t: sent.append((c, s, t)))

    def dispatch(job, text):
        gate.deliver(job.channel, job.sender, text,
                     category="reminder" if job.kind == "once" else "job",
                     solicited=job.source == "user",
                     now=dispatch.now)
    dispatch.now = NOON
    scheduler = Scheduler(jobs, run_task=lambda job: f"svolto: {job.task}",
                          dispatch=dispatch)
    return jobs, gate, scheduler, dispatch


def test_requested_job_delivered_end_to_end(tmp_path):
    sent = []
    jobs, gate, scheduler, dispatch = build(tmp_path, sent)
    job = scheduler.add_job(kind="once", schedule="2026-11-08 12:30",
                            task="ricordami la chiamata", channel="cli",
                            sender="locale", source="user", now=NOON)
    due = job.next_fire + 1
    dispatch.now = due
    assert scheduler.tick(due) == 1
    assert sent == [("cli", "locale", "svolto: ricordami la chiamata")]


def test_requested_job_in_quiet_hours_delivered_immediately(tmp_path):
    """Regressione: il promemoria RICHIESTO per le 23:30 arriva alle 23:30,
    non viene trattenuto nel digest fino al mattino."""
    sent = []
    jobs, gate, scheduler, dispatch = build(tmp_path, sent)
    job = scheduler.add_job(kind="once", schedule="2026-11-08 23:30",
                            task="promemoria notturno", channel="cli",
                            sender="locale", source="user", now=NOON)
    dispatch.now = job.next_fire + 1
    assert scheduler.tick(job.next_fire + 1) == 1
    assert sent == [("cli", "locale", "svolto: promemoria notturno")]
    assert gate.store.pending_digest_count() == 0


def test_job_survives_restart_and_runs_once(tmp_path):
    sent = []
    jobs, gate, scheduler, dispatch = build(tmp_path, sent)
    job = scheduler.add_job(kind="cron", schedule="0 9 * * *",
                            task="brief mattutino", channel="cli",
                            sender="locale", source="user", now=NOON)
    # riavvio simulato: nuovi oggetti store/scheduler sugli stessi file
    jobs2, gate2, scheduler2, dispatch2 = build(tmp_path, sent)
    persisted = [j for j in jobs2.list() if j.task == "brief mattutino"]
    assert len(persisted) == 1 and persisted[0].next_fire == job.next_fire
    # due scheduler vivi (prima e dopo riavvio): una sola esecuzione
    due = job.next_fire + 1
    dispatch.now = dispatch2.now = due
    assert scheduler.tick(due) + scheduler2.tick(due) == 1
    assert len(sent) == 1


def test_agent_job_approved_in_quiet_hours_held_then_flushed(tmp_path):
    """Regressione clock: approvazione in ore silenziose (23:30) — il job e'
    dovuto ma la consegna non sollecitata resta nel digest fino al mattino,
    con clock interamente simulato (qualsiasi wall clock reale)."""
    sent = []
    jobs, gate, scheduler, dispatch = build(tmp_path, sent)
    job = scheduler.add_job(kind="cron", schedule="* * * * *",
                            task="proposta notturna", channel="cli",
                            sender="locale", source="agent", now=NIGHT)
    scheduler.approve_job(job.id, now=NIGHT)
    due = scheduler.list_jobs()[0].next_fire + 1
    dispatch.now = due
    assert scheduler.tick(due) == 1
    assert sent == []  # ore silenziose: trattenuto
    assert gate.heartbeat(now=MORNING) == 1
    assert len(sent) == 1 and "proposta notturna" in sent[0][2]


def test_unapproved_agent_job_never_runs_even_when_due(tmp_path):
    sent = []
    jobs, gate, scheduler, dispatch = build(tmp_path, sent)
    job = scheduler.add_job(kind="cron", schedule="* * * * *",
                            task="proposta agente", channel="cli",
                            sender="locale", source="agent", now=NOON)
    dispatch.now = job.next_fire + 1 if job.next_fire else NOON + 3600
    assert scheduler.tick(NOON + 3600) == 0
    assert sent == []
    # Clock controllato anche in approvazione: senza now=, approve()
    # ricalcola next_fire dal wall clock reale — di notte il job approvato
    # finiva in ore silenziose e il gate tratteneva la consegna (test rosso
    # dopo le 23:00, verde di giorno).
    scheduler.approve_job(job.id, now=NOON)
    due = scheduler.list_jobs()[0].next_fire + 1
    dispatch.now = due
    assert scheduler.tick(due) == 1
    assert len(sent) == 1
