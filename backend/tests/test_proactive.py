"""Stage 11 (spec sez. 8): guardrail dei messaggi proattivi — ore silenziose,
tetto giornaliero ai non richiesti, digest per la bassa priorita', importanza
per categoria con feedback "piu'/meno" e decadimento quando ignorata.
"""
from __future__ import annotations

import time

from app.agent.proactive import HIGH, LOW, URGENT, ProactiveConfig, ProactiveGate, ProactiveStore, in_quiet_hours

DAY_NOON = time.mktime((2026, 11, 8, 12, 0, 0, 0, 0, -1))   # 12:00
NIGHT = time.mktime((2026, 11, 8, 23, 30, 0, 0, 0, -1))     # 23:30
MORNING = time.mktime((2026, 11, 9, 8, 0, 0, 0, 0, -1))     # 08:00


def make_gate(tmp_path, **cfg_kwargs):
    cfg = ProactiveConfig(**cfg_kwargs)
    store = ProactiveStore(tmp_path / "data" / "proactive.db")
    sent = []
    gate = ProactiveGate(cfg, store,
                         deliver_fn=lambda c, s, t: sent.append((c, s, t)))
    return gate, store, sent


# --- ore silenziose -----------------------------------------------------------

def test_quiet_hours_overnight_window():
    cfg = ProactiveConfig(quiet_start="23:00", quiet_end="07:30")
    assert in_quiet_hours(cfg, NIGHT) is True
    assert in_quiet_hours(cfg, DAY_NOON) is False
    assert in_quiet_hours(cfg, time.mktime((2026, 11, 9, 3, 0, 0, 0, 0, -1)))


def test_quiet_hours_same_day_window():
    cfg = ProactiveConfig(quiet_start="13:00", quiet_end="15:00")
    assert in_quiet_hours(cfg, time.mktime((2026, 11, 8, 14, 0, 0, 0, 0, -1)))
    assert not in_quiet_hours(cfg, DAY_NOON)


def test_quiet_hours_block_low_and_high_but_not_urgent(tmp_path):
    gate, store, sent = make_gate(tmp_path)
    for priority in (LOW, HIGH):
        decision = gate.deliver("cli", "locale", "testo", category="job",
                                priority=priority, now=NIGHT)
        assert decision.send is False and decision.queued_digest is True
    decision = gate.deliver("cli", "locale", "URGENTE", category="job",
                            priority=URGENT, now=NIGHT)
    assert decision.send is True
    assert sent == [("cli", "locale", "URGENTE")]
    assert store.pending_digest_count() == 2


def test_digest_flushes_after_quiet_hours(tmp_path):
    gate, store, sent = make_gate(tmp_path)
    gate.deliver("cli", "locale", "a", category="job", now=NIGHT)
    gate.deliver("cli", "locale", "b", category="brief", now=NIGHT)
    assert gate.heartbeat(now=NIGHT) == 0  # ancora silenzio: niente flush
    assert gate.heartbeat(now=MORNING) == 1  # un solo messaggio riepilogativo
    assert len(sent) == 1
    assert "- a" in sent[0][2] and "- b" in sent[0][2]
    assert store.pending_digest_count() == 0


# --- tetto giornaliero ----------------------------------------------------------

def test_daily_cap_applies_only_to_unsolicited(tmp_path):
    gate, store, sent = make_gate(tmp_path, max_unsolicited_per_day=2)
    for _ in range(2):
        assert gate.deliver("cli", "locale", "x", category="job",
                            now=DAY_NOON).send
    # terzo non richiesto: tetto raggiunto, va nel digest
    decision = gate.deliver("cli", "locale", "x", category="job", now=DAY_NOON)
    assert decision.send is False and decision.queued_digest
    # i sollecitati (job creati con /schedule) passano sempre
    assert gate.deliver("cli", "locale", "y", category="reminder",
                        solicited=True, now=DAY_NOON).send
    assert len(sent) == 3


# --- digest per bassa priorita' ---------------------------------------------------

def test_low_priority_always_batched(tmp_path):
    gate, store, sent = make_gate(tmp_path)
    decision = gate.deliver("cli", "locale", "poco importante", category="job",
                            priority=LOW, now=DAY_NOON)
    assert decision.send is False and decision.queued_digest
    assert sent == []


def test_low_priority_immediate_when_batching_off(tmp_path):
    gate, store, sent = make_gate(tmp_path, batch_digest=False)
    assert gate.deliver("cli", "locale", "x", category="job",
                        priority=LOW, now=DAY_NOON).send


# --- importanza e feedback ----------------------------------------------------------

def test_feedback_raises_and_lowers_importance(tmp_path):
    gate, store, _ = make_gate(tmp_path)
    before = store.importance("heads-up")
    assert gate.feedback("heads-up", more=True) == before + 1
    assert gate.feedback("heads-up", more=False) == before


def test_ignored_category_decays_and_stops(tmp_path):
    gate, store, sent = make_gate(tmp_path)
    store.set_importance("heads-up", 1)  # al limite della soglia
    for _ in range(3):  # tre consegne senza alcun messaggio dell'utente
        gate.deliver("cli", "locale", "x", category="heads-up", now=DAY_NOON)
    assert store.importance("heads-up") == 0  # decaduta dopo 3 ignorate
    decision = gate.deliver("cli", "locale", "x", category="heads-up",
                            now=DAY_NOON)
    assert decision.send is False and not decision.queued_digest  # silenziata


def test_user_message_resets_ignore_streak(tmp_path):
    gate, store, sent = make_gate(tmp_path)
    store.set_importance("heads-up", 2)
    gate.deliver("cli", "locale", "x", category="heads-up", now=DAY_NOON)
    gate.deliver("cli", "locale", "x", category="heads-up", now=DAY_NOON)
    gate.user_message_seen()  # Fred risponde: streak azzerata
    assert store.ignored_streak("heads-up") == 0
    gate.deliver("cli", "locale", "x", category="heads-up", now=DAY_NOON)
    assert store.importance("heads-up") == 2  # nessun decadimento


def test_reminder_category_stays_max_importance(tmp_path):
    gate, store, _ = make_gate(tmp_path)
    assert store.importance("reminder") == 5  # i promemoria non si filtrano


# --- config da file -----------------------------------------------------------------

def test_config_load_from_workspace(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "proactive.json").write_text(
        '{"quiet_start": "22:00", "max_unsolicited_per_day": 3}')
    cfg = ProactiveConfig.load(tmp_path)
    assert cfg.quiet_start == "22:00" and cfg.max_unsolicited_per_day == 3
    assert cfg.quiet_end == "07:30"  # default per i campi assenti


def test_config_load_tolerates_broken_file(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "proactive.json").write_text("non json")
    assert ProactiveConfig.load(tmp_path).quiet_start == "23:00"


# --- tool proactive_feedback ---------------------------------------------------------

def test_proactive_feedback_tool(tmp_path):
    from app.agent.safety import SecretFilter
    from app.agent.tools import TOOLS_BY_NAME, ToolContext
    gate, store, _ = make_gate(tmp_path)
    ctx = ToolContext(workspace=tmp_path, secrets=SecretFilter([]),
                      proactive=gate)
    out = TOOLS_BY_NAME["proactive_feedback"].run(
        {"category": "brief", "action": "less"}, ctx)
    assert "abbassata" in out
    assert store.importance("brief") == 2
    ctx.proactive = None
    out = TOOLS_BY_NAME["proactive_feedback"].run(
        {"category": "brief", "action": "more"}, ctx)
    assert "errore" in out
