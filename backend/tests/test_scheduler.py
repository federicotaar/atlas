"""Stage 10 (spec sez. 8, build order 10): scheduler con jobs persistiti,
cron interno, approvazione dei job ricorrenti dell'agente, comandi
/schedule e /jobs. Nessuna rete: registry finta, tempo iniettato.
"""
from __future__ import annotations

import time

from app.agent.commands import CommandContext, handle_command
from app.agent.safety import SecretFilter
from app.agent.scheduler import JobStore, Scheduler, cron_match, next_fire, parse_cron, parse_schedule_phrase
from app.agent.tools import TOOLS_BY_NAME, ToolContext
from app.providers.base import ProviderResponse

T0 = time.mktime((2026, 11, 8, 10, 0, 0, 0, 0, -1))  # domenica 8 nov 2026, 10:00


# --- cron -------------------------------------------------------------------

def test_parse_cron_validates_fields():
    assert 0 in parse_cron("* * * * *")[0] and 59 in parse_cron("* * * * *")[0]
    for bad in ("* * * *", "* * * * * *", "61 * * * *", "* 25 * * *",
                "* * 0 * *", "* * * 13 *", "* * * * 8", "a b c d e"):
        try:
            parse_cron(bad)
        except ValueError:
            continue
        raise AssertionError(f"espressione accettata: {bad}")


def test_cron_match_basic_and_steps_and_ranges():
    assert cron_match("0 8 * * 1-5", T0) is False      # domenica
    assert cron_match("0 10 * * 0", T0) is True
    assert cron_match("0 10 * * 7", T0) is True        # domenica = 7
    assert cron_match("*/15 * * * *", T0) is True      # minuto 0
    assert cron_match("5-10/2 * * * *", T0) is False   # minuto 0 non in 5,7,9
    assert cron_match("0 10 8 nov *", T0) is True      # nomi dei mesi
    assert cron_match("0 10 * * sun", T0) is True      # nomi dei giorni


def test_cron_dom_dow_or_semantics():
    # Entrambi ristretti: OR (Vixie). 8 nov 2026 e' domenica.
    assert cron_match("0 10 15 * 0", T0) is True    # dom (dow ok, dom no)
    assert cron_match("0 10 8 * 1", T0) is True     # 8 del mese (dom ok, dow no)
    assert cron_match("0 10 15 * 1", T0) is False   # nessuno dei due


def test_next_fire():
    nxt = next_fire("0 8 * * 1-5", T0)  # domenica 10:00 -> lunedi' 08:00
    assert time.localtime(nxt).tm_wday == 0
    assert time.localtime(nxt).tm_hour == 8
    assert next_fire("0 11 * * *", T0) - T0 == 3600
    assert next_fire("30 12 30 2 *", T0) is None  # 30 febbraio


# --- store ---------------------------------------------------------------------

def make_store(tmp_path):
    return JobStore(tmp_path / "data" / "jobs.db")


def test_store_add_list_persist(tmp_path):
    store = make_store(tmp_path)
    job = store.add(kind="cron", schedule="0 8 * * 1-5", task="brief",
                    channel="cli", sender="locale", source="user", now=T0)
    assert job.approved and not job.paused and job.next_fire > T0
    store.close()
    reopened = make_store(tmp_path)  # persistenza: sopravvive al riavvio
    jobs = reopened.list()
    assert len(jobs) == 1 and jobs[0].task == "brief"


def test_agent_recurring_job_needs_approval(tmp_path):
    store = make_store(tmp_path)
    job = store.add(kind="cron", schedule="0 9 * * *", task="proposta",
                    channel="cli", sender="locale", source="agent", now=T0)
    assert job.approved is False
    assert store.due(job.next_fire + 1) == []  # mai dovuto finche' non approvato
    approved = store.approve(job.id, now=T0)
    assert approved.approved and approved.next_fire > T0


def test_agent_oneoff_is_auto_approved_and_consumed(tmp_path):
    store = make_store(tmp_path)
    job = store.add(kind="once", schedule="2026-11-08 12:00", task="ricordami",
                    channel="cli", sender="locale", source="agent", now=T0)
    assert job.approved is True
    claimed = store.claim_due(time.mktime((2026, 11, 8, 12, 0, 0, 0, 0, -1)))
    assert [j.id for j in claimed] == [job.id]
    assert store.list() == []  # una tantum consumato


def test_pause_resume_delete(tmp_path):
    store = make_store(tmp_path)
    job = store.add(kind="cron", schedule="0 8 * * *", task="x",
                    channel="cli", sender="locale", source="user", now=T0)
    store.pause(job.id)
    assert store.due(10**12) == []
    resumed = store.resume(job.id, now=T0)
    assert resumed.paused is False and resumed.next_fire > T0
    assert store.delete(job.id) is True and store.delete(job.id) is False


def test_claim_is_atomic_across_stores(tmp_path):
    """Due processi (CLI + web UI) sullo stesso db non eseguono due volte."""
    store_a, store_b = make_store(tmp_path), make_store(tmp_path)
    job = store_a.add(kind="cron", schedule="* * * * *", task="x",
                      channel="cli", sender="locale", source="user", now=T0)
    due_at = job.next_fire + 1
    assert len(store_a.claim_due(due_at)) == 1
    assert store_b.claim_due(due_at) == []  # gia' preso dall'altro processo


# --- scheduler -------------------------------------------------------------------

def test_scheduler_tick_runs_and_dispatches(tmp_path):
    store = make_store(tmp_path)
    store.add(kind="once", schedule="2026-11-08 12:00", task="fai x",
                    channel="cli", sender="locale", source="user", now=T0)
    sent = []
    sched = Scheduler(store, run_task=lambda j: f"fatto: {j.task}",
                      dispatch=lambda job, text: sent.append(
                          (job.channel, job.sender, text)))
    ran = sched.tick(time.mktime((2026, 11, 8, 12, 0, 1, 0, 0, -1)))
    assert ran == 1
    assert sent == [("cli", "locale", "fatto: fai x")]
    assert sched.tick(time.mktime((2026, 11, 8, 12, 0, 2, 0, 0, -1))) == 0


def test_scheduler_survives_broken_job(tmp_path):
    store = make_store(tmp_path)
    store.add(kind="once", schedule="2026-11-08 12:00", task="boom",
              channel="cli", sender="locale", source="user", now=T0)
    sent = []

    def broken(job):
        raise RuntimeError("esplode")

    sched = Scheduler(store, run_task=broken,
                      dispatch=lambda job, text: sent.append(text))
    sched.tick(time.mktime((2026, 11, 8, 12, 0, 1, 0, 0, -1)))
    assert "fallito" in sent[0] and "esplode" in sent[0]


# --- parse frase -> job (via modello) ---------------------------------------------

class FakeRegistry:
    def __init__(self, text):
        self._text = text

    def chat_with_fallback(self, active, messages, tools=None, stream=False,
                           on_fallback=None):
        return ProviderResponse(text=self._text)


def test_parse_schedule_phrase_valid_json():
    reg = FakeRegistry('{"kind":"cron","schedule":"0 8 * * 1-5","task":"brief"}')
    parsed = parse_schedule_phrase(reg, "fake", "ogni feriale alle 8 il brief")
    assert parsed == {"kind": "cron", "schedule": "0 8 * * 1-5", "task": "brief"}


def test_parse_schedule_phrase_rejects_garbage_and_impossible():
    for text in ("non lo so", "{}",
                 '{"kind":"cron","schedule":"61 8 * * *","task":"x"}',
                 '{"kind":"once","schedule":"ieri","task":"x"}'):
        try:
            parse_schedule_phrase(FakeRegistry(text), "fake", "frase")
        except ValueError:
            continue
        raise AssertionError(f"frase accettata: {text}")


# --- comandi /schedule e /jobs -----------------------------------------------------

def make_cc(tmp_path, phrase_response=None):
    from app.agent.memory import MemoryStore
    store = MemoryStore(tmp_path / "memory.db")
    sched = Scheduler(make_store(tmp_path), run_task=lambda j: "ok",
                      dispatch=lambda c, s, t: None)
    lines = []
    registry = (FakeRegistry(phrase_response) if phrase_response is not None
                else FakeRegistry(""))
    cc = CommandContext(
        registry=registry, state={}, store=store, out=lines.append,
        get_active=lambda: "fake", set_active=lambda n: None,
        describe_registry=lambda r, a, o: None,
        scheduler=sched, channel="cli", sender="locale")
    return cc, lines, sched


def test_schedule_command_creates_user_job(tmp_path):
    cc, lines, sched = make_cc(
        tmp_path, '{"kind":"cron","schedule":"0 8 * * 1-5","task":"brief"}')
    assert handle_command("/schedule ogni feriale alle 8 mandami il brief", cc)
    jobs = sched.list_jobs()
    assert len(jobs) == 1 and jobs[0].source == "user" and jobs[0].approved
    assert "Job #1 creato" in lines[-1]


def test_jobs_command_lists_and_acts(tmp_path):
    cc, lines, sched = make_cc(tmp_path)
    sched.add_job(kind="cron", schedule="0 9 * * *", task="proposta agente",
                  channel="cli", sender="locale", source="agent")
    assert handle_command("/jobs", cc)
    assert any("DA APPROVARE" in line for line in lines)
    lines.clear()
    assert handle_command("/jobs approve 1", cc)
    assert "approvato" in lines[-1]
    assert sched.list_jobs()[0].approved
    assert handle_command("/jobs pause 1", cc) and "in pausa" in lines[-1]
    assert handle_command("/jobs delete 1", cc) and "eliminato" in lines[-1]
    assert sched.list_jobs() == []


def test_schedule_command_without_scheduler(tmp_path):
    cc, lines, _ = make_cc(tmp_path)
    cc.scheduler = None
    assert handle_command("/schedule domani alle 9 x", cc)
    assert "non attivo" in lines[-1]


# --- tool schedule_job ------------------------------------------------------------

def test_schedule_job_tool_creates_unapproved_recurring(tmp_path):
    sched = Scheduler(make_store(tmp_path), run_task=lambda j: "ok",
                      dispatch=lambda c, s, t: None)
    ctx = ToolContext(workspace=tmp_path, secrets=SecretFilter([]),
                      scheduler=sched, channel="whatsapp", sender="39333")
    out = TOOLS_BY_NAME["schedule_job"].run(
        {"kind": "cron", "schedule": "0 7 * * *", "task": "brief mattutino"}, ctx)
    assert "ATTESA DI APPROVAZIONE" in out
    job = sched.list_jobs()[0]
    assert job.channel == "whatsapp" and job.sender == "39333" and not job.approved


def test_schedule_job_tool_without_scheduler(tmp_path):
    ctx = ToolContext(workspace=tmp_path, secrets=SecretFilter([]))
    out = TOOLS_BY_NAME["schedule_job"].run(
        {"kind": "once", "schedule": "2026-11-09 08:00", "task": "x"}, ctx)
    assert "errore" in out
