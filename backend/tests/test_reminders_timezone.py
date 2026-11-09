"""Regressioni: promemoria richiesti vs soppressioni, fuso orario esplicito.

Un promemoria chiesto dall'utente per una certa ora NON e' un ping non
richiesto: non puo' sparire per una categoria silenziata, non puo' essere
ritardato dalle ore silenziose, non conta nel tetto e non va nel digest di
bassa priorita'. Il fuso orario e' esplicito (IANA/zoneinfo), persistito in
config/app.json, indipendente dal fuso del processo; il cambio ora legale
segue regole deterministiche documentate in localzone.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.agent.localzone import (detect_system_zone, load_timezone_name,
                                 save_timezone, wall_to_epoch, epoch_to_wall)
from app.agent.proactive import (HIGH, LOW, URGENT, ProactiveConfig,
                                 ProactiveGate, ProactiveStore, in_quiet_hours)
from app.agent.scheduler import (JobStore, Scheduler, next_fire, cron_match,
                                 parse_schedule_phrase)

ROME = ZoneInfo("Europe/Rome")
NY = ZoneInfo("America/New_York")


def rome(y, mo, d, h, mi):
    return wall_to_epoch(datetime(y, mo, d, h, mi), ROME)


def make_gate(tmp_path, **cfg_kwargs):
    cfg_kwargs.setdefault("timezone", "Europe/Rome")
    cfg = ProactiveConfig(**cfg_kwargs)
    store = ProactiveStore(tmp_path / "data" / "proactive.db")
    sent, digested_flush = [], []
    gate = ProactiveGate(cfg, store,
                         deliver_fn=lambda c, s, t: sent.append((c, s, t)),
                         on_digest_flush=lambda c, s, t: digested_flush.append(t))
    return gate, store, sent, digested_flush


QUIET = rome(2026, 11, 9, 3, 0)      # 03:00 Roma: ore silenziose default
DAY = rome(2026, 11, 9, 12, 0)       # mezzogiorno Roma


# --- il richiesto parte sempre ----------------------------------------------

def test_solicited_survives_muted_category(tmp_path):
    """Regressione: categoria a importanza 0 non sopprime un promemoria
    richiesto (prima spariva senza invio, digest o traccia)."""
    gate, store, sent, _ = make_gate(tmp_path)
    store.set_importance("reminder", 0)
    d = gate.deliver("cli", "fred", "chiama mamma", category="reminder",
                     priority=HIGH, solicited=True, now=DAY)
    assert d.send is True
    assert ("cli", "fred", "chiama mamma") in sent
    # il non richiesto resta soppresso come prima
    d = gate.deliver("cli", "fred", "spam", category="reminder",
                     priority=HIGH, solicited=False, now=DAY)
    assert not d.send and not d.queued_digest
    assert store.pending_digest_count() == 0


def test_solicited_fires_in_quiet_hours(tmp_path):
    """Regressione: un promemoria richiesto per le 3:00 arriva alle 3:00,
    non viene ritardato al mattino dentro il digest."""
    gate, store, sent, flushed = make_gate(tmp_path)
    d = gate.deliver("cli", "fred", "sveglia alle 3", category="reminder",
                     priority=HIGH, solicited=True, now=QUIET)
    assert d.send is True
    assert ("cli", "fred", "sveglia alle 3") in sent
    assert store.pending_digest_count() == 0
    # il non richiesto non urgente resta digest in ore silenziose
    d = gate.deliver("cli", "fred", "rumore", category="heads-up",
                     priority=HIGH, solicited=False, now=QUIET)
    assert not d.send and d.queued_digest
    # l'urgente non richiesto passa, come prima
    d = gate.deliver("cli", "fred", "urgente", category="heads-up",
                     priority=URGENT, solicited=False, now=QUIET)
    assert d.send


def test_solicited_bypasses_low_priority_batch(tmp_path):
    gate, store, sent, _ = make_gate(tmp_path)
    d = gate.deliver("cli", "fred", "richiesto low", category="reminder",
                     priority=LOW, solicited=True, now=DAY)
    assert d.send is True
    # il low non richiesto resta nel digest
    d = gate.deliver("cli", "fred", "low qualsiasi", category="heads-up",
                     priority=LOW, solicited=False, now=DAY)
    assert not d.send and d.queued_digest


def test_solicited_bypasses_daily_cap(tmp_path):
    gate, store, sent, _ = make_gate(tmp_path, max_unsolicited_per_day=1)
    gate.deliver("cli", "fred", "u1", category="job", now=DAY)
    d = gate.deliver("cli", "fred", "u2 oltre tetto", category="job", now=DAY)
    assert not d.send and d.queued_digest  # non richiesto: digest
    d = gate.deliver("cli", "fred", "richiesto", category="reminder",
                     solicited=True, now=DAY)
    assert d.send is True


# --- integrazione scheduler -> dispatch --------------------------------------

def _dispatch_job_wiring(gate):
    """Stessa espressione di dispatch_job in agent_cli.py e webui.py."""
    def dispatch_job(job, text):
        gate.deliver(job.channel, job.sender, text,
                     category="reminder" if job.kind == "once" else "job",
                     solicited=job.source == "user")
    return dispatch_job


def test_user_once_job_fires_at_requested_time_even_in_quiet_hours(tmp_path):
    """Integrazione: job una-tantum dell'utente dovuto in ore silenziose ->
    consegna immediata attraverso il gate. Job dell'agente -> digest."""
    gate, store, sent, flushed = make_gate(tmp_path, quiet_start="23:00",
                                           quiet_end="07:30")
    jobs = JobStore(tmp_path / "data" / "jobs.db", zone=ROME)
    sched = Scheduler(jobs, run_task=lambda j: f"out:{j.task}",
                      dispatch=_dispatch_job_wiring(gate), tick_seconds=999)
    at_3am = "2026-11-09 03:00"
    sched.add_job(kind="once", schedule=at_3am, task="sveglia",
                  channel="cli", sender="fred", source="user")
    sched.add_job(kind="once", schedule=at_3am, task="idea notturna",
                  channel="cli", sender="fred", source="agent")
    ran = sched.tick(now=rome(2026, 11, 9, 3, 0))
    assert ran == 2
    # il promemoria dell'utente e' uscito SUBITO, quello dell'agente no
    assert ("cli", "fred", "out:sveglia") in sent
    assert all("idea notturna" not in t for _, _, t in sent)
    assert store.pending_digest_count() == 1
    # al mattino il digest porta l'idea dell'agente
    gate.heartbeat(now=rome(2026, 11, 9, 8, 0))
    assert any("idea notturna" in f for f in flushed)


# --- fuso orario esplicito ----------------------------------------------------

def test_config_roundtrip_persists_zone(tmp_path):
    assert save_timezone(tmp_path, "America/New_York") == "America/New_York"
    assert load_timezone_name(tmp_path) == "America/New_York"
    with pytest.raises(ValueError):
        save_timezone(tmp_path, "Marte/Olympus")
    # nome non valido nel file: niente crash, ripiego documentato
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "app.json").write_text('{"timezone": "Narnia"}')
    assert load_timezone_name(tmp_path) == (detect_system_zone() or "UTC")


def test_jobs_use_configured_zone_not_process_tz(tmp_path):
    jobs = JobStore(tmp_path / "jobs.db", zone=NY)
    job = jobs.add(kind="once", schedule="2026-11-09 09:00", task="x",
                   channel="cli", sender="s", source="user")
    # 09:00 a New York = 14:00 UTC, qualunque sia il fuso del processo
    assert job.next_fire == datetime(2026, 11, 9, 14, 0,
                                     tzinfo=ZoneInfo("UTC")).timestamp()
    # riavvio: stesso db, stesso fuso da config -> stesso istante
    jobs.close()
    jobs2 = JobStore(tmp_path / "jobs.db", zone=NY)
    assert jobs2.list()[0].next_fire == job.next_fire
    jobs2.close()
    # cambio il fuso del PROCESSO: il job non si sposta (vale il config)
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tokyo"
    time.tzset()
    try:
        jobs3 = JobStore(tmp_path / "jobs.db", zone=NY)
        assert jobs3.list()[0].next_fire == job.next_fire
        # e il cron e' valutato nel fuso configurato: 09:00 NY, non Tokyo
        assert cron_match("0 9 * * *", job.next_fire, NY) is True
        assert cron_match("0 9 * * *", job.next_fire,
                          ZoneInfo("Asia/Tokyo")) is False
        jobs3.close()
    finally:
        if old is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_quiet_hours_evaluated_in_configured_zone(tmp_path):
    cfg = ProactiveConfig(quiet_start="23:00", quiet_end="07:30",
                          timezone="America/New_York")
    # mezzogiorno a Roma = le 6 del mattino a New York: silenzio
    assert in_quiet_hours(cfg, rome(2026, 11, 9, 12, 0)) is True
    cfg.timezone = "Europe/Rome"
    assert in_quiet_hours(cfg, rome(2026, 11, 9, 12, 0)) is False


# --- DST deterministico --------------------------------------------------------

def test_dst_nonexistent_time_shifts_forward():
    """29 marzo 2026 a Roma le 02:30 non esistono: il job scatta alle 03:30
    (spostato in avanti del salto), non si perde e non anticipa."""
    before = rome(2026, 3, 28, 12, 0)
    nf = next_fire("30 2 * * *", before, ROME)
    wall = epoch_to_wall(nf, ROME)
    assert (wall.year, wall.month, wall.day) == (2026, 3, 29)
    assert (wall.hour, wall.minute) == (3, 30)
    assert wall.utcoffset().total_seconds() == 7200  # CEST


def test_dst_ambiguous_time_first_occurrence_only():
    """25 ottobre 2026 a Roma le 02:30 capitano due volte: vale la PRIMA
    (CEST) e quella in CET viene saltata - mai due esecuzioni."""
    before = rome(2026, 10, 24, 12, 0)
    nf = next_fire("30 2 * * *", before, ROME)
    wall = epoch_to_wall(nf, ROME)
    assert (wall.year, wall.month, wall.day) == (2026, 10, 25)
    assert (wall.hour, wall.minute) == (2, 30)
    assert wall.utcoffset().total_seconds() == 7200  # CEST = prima occorrenza
    after = next_fire("30 2 * * *", nf, ROME)
    wall2 = epoch_to_wall(after, ROME)
    assert (wall2.year, wall2.month, wall2.day) == (2026, 10, 26)


def test_once_job_at_nonexistent_time_still_fires(tmp_path):
    jobs = JobStore(tmp_path / "jobs.db", zone=ROME)
    job = jobs.add(kind="once", schedule="2026-03-29 02:30", task="x",
                   channel="cli", sender="s", source="user")
    wall = epoch_to_wall(job.next_fire, ROME)
    assert (wall.hour, wall.minute) == (3, 30)
    jobs.close()


# --- parsing NL dichiara il fuso ------------------------------------------------

class _CaptureRegistry:
    def __init__(self):
        self.messages = None

    def chat_with_fallback(self, active, messages, tools=None, stream=False,
                           on_fallback=None):
        self.messages = messages
        from app.providers.base import ProviderResponse
        return ProviderResponse(
            text='{"kind":"once","schedule":"2026-11-10 18:30","task":"x"}')


def test_parse_prompt_declares_timezone():
    reg = _CaptureRegistry()
    parsed = parse_schedule_phrase(reg, "fake", "domani alle 18:30 x",
                                   zone=ROME)
    prompt = reg.messages[0]["content"]
    assert "Europe/Rome" in prompt
    assert parsed["kind"] == "once"


# --- i job conservano il fuso della creazione ---------------------------------

def test_jobs_keep_creation_zone_across_install_zone_change(tmp_path):
    """Cron = orario di parete nel fuso richiesto; una-tantum = istante gia'
    risolto. Cambiare /timezone dell'installazione non sposta NESSUNO dei
    due, nemmeno dopo riavvio o pause/resume."""
    rome = JobStore(tmp_path / "jobs.db", zone=ROME)
    cron = rome.add(kind="cron", schedule="0 8 * * *", task="brief",
                    channel="cli", sender="s", source="user")
    once = rome.add(kind="once", schedule="2026-12-01 09:00", task="x",
                    channel="cli", sender="s", source="user")
    cron_nf, once_nf = cron.next_fire, once.next_fire
    rome.close()

    # l'installazione passa a New York (riavvio con altro fuso configurato)
    ny = JobStore(tmp_path / "jobs.db", zone=NY)
    j_cron, j_once = ny.get(cron.id), ny.get(once.id)
    assert j_cron.zone == "Europe/Rome" and j_once.zone == "Europe/Rome"
    assert j_cron.next_fire == cron_nf and j_once.next_fire == once_nf
    # resume ricalcola NEL FUSO DEL JOB: la una-tantum resta 09:00 Roma
    # (14:00 UTC d'inverno? no: 1 dicembre Roma = UTC+1 -> 08:00 UTC)
    ny.resume(once.id)
    assert ny.get(once.id).next_fire == once_nf
    # idem il cron: 08:00 di Roma, non di New York
    ny.pause(cron.id)
    ny.resume(cron.id)
    wall = epoch_to_wall(ny.get(cron.id).next_fire, ROME)
    assert (wall.hour, wall.minute) == (8, 0)
    ny.close()


def test_legacy_rows_without_zone_use_store_zone(tmp_path):
    """Righe scritte prima dei fusi (zone=''): niente crash, valgono nel
    fuso attivo dello store, next_fire gia' salvato intatto."""
    import sqlite3
    db = tmp_path / "jobs.db"
    raw = sqlite3.connect(str(db))
    raw.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " kind TEXT NOT NULL, schedule TEXT NOT NULL, task TEXT NOT NULL,"
        " channel TEXT NOT NULL, sender TEXT NOT NULL, source TEXT NOT NULL,"
        " approved INTEGER NOT NULL, paused INTEGER NOT NULL DEFAULT 0,"
        " next_fire REAL, last_run REAL, created REAL NOT NULL)")
    raw.execute(
        "INSERT INTO jobs (kind,schedule,task,channel,sender,source,"
        " approved,paused,next_fire,created)"
        " VALUES ('once','2026-12-01 09:00','vecchio','cli','s','user',1,0,?,?)",
        (rome(2026, 12, 1, 9, 0), time.time()))
    raw.commit()
    raw.close()
    store = JobStore(db, zone=ROME)   # migrazione: aggiunge la colonna
    job = store.list()[0]
    assert job.zone == ""
    assert job.next_fire == rome(2026, 12, 1, 9, 0)
    # resume sulla riga legacy: ricalcola nel fuso dello store, stesso esito
    store.resume(job.id)
    assert store.get(job.id).next_fire == rome(2026, 12, 1, 9, 0)
    store.close()
