"""Stage 13 (spec sez. 8): abitudini apprese in locale — osservazione,
sintesi con evidenze, /habits off/forget, filtro temi sensibili, local-only.
"""
from __future__ import annotations

import time

from app.agent.commands import CommandContext, handle_command
from app.agent.habits import HabitStore, habits_allowed_for_provider, habits_context

T0 = time.mktime((2026, 11, 8, 10, 0, 0, 0, 0, -1))  # domenica 10:00


def at(hour: int, weekday_offset: int = 0) -> float:
    base = list(time.localtime(T0))
    base[3] = hour
    return time.mktime(tuple(base)) + weekday_offset * 86400


def test_observe_and_summarize_active_hours(tmp_path):
    store = HabitStore(tmp_path / "data" / "habits.db")
    for hour in (9, 10, 11, 14, 18):
        store.observe_message("ciao, come va?", now=at(hour))
    items = {i["key"]: i for i in store.summarize(now=at(20))}
    assert "orari attivi" in items
    assert items["orari attivi"]["evidence"] == 5
    assert items["orari attivi"]["last_seen"] > 0


def test_frequent_topics_and_weekday_routines(tmp_path):
    store = HabitStore(tmp_path / "data" / "habits.db")
    for i in range(3):  # tre lunedi' di fila, stessa richiesta
        store.observe_message("fammi il riepilogo allenamenti della settimana",
                              now=at(9, weekday_offset=-i * 7))
    items = {i["key"]: i for i in store.summarize(now=at(20))}
    assert any(k.startswith("argomento frequente: allenamenti") for k in items)
    assert any(k.startswith("routine:") for k in items)
    routines = store.routines_to_propose()
    assert routines and routines[0]["evidence"] >= 3


def test_sensitive_topics_leave_no_content(tmp_path):
    store = HabitStore(tmp_path / "data" / "habits.db")
    for i in range(5):
        store.observe_message("devo andare dal dottore per la medicina",
                              now=at(10, i))
    items = store.summarize(now=at(20))
    assert not any("dottore" in i["text"] or "medicina" in i["text"]
                   for i in items)
    # l'impronta oraria resta (non e' contenuto)
    assert any(i["key"] == "orari attivi" for i in items)


def test_length_feedback_becomes_habit(tmp_path):
    store = HabitStore(tmp_path / "data" / "habits.db")
    for _ in range(3):
        store.observe_length_feedback("shorter", now=at(10))
    items = {i["key"]: i for i in store.summarize(now=at(20))}
    assert items["lunghezza risposte"]["text"] == "preferisce risposte brevi"
    block = habits_context(store.summarize(now=at(20)))
    assert "brevi" in block


def test_habits_md_written(tmp_path):
    store = HabitStore(tmp_path / "data" / "habits.db")
    for hour in (9, 10, 11, 12, 13):
        store.observe_message("fammi il riepilogo allenamenti", now=at(hour))
    items = store.write_habits_md(tmp_path / "habits.md", now=at(20))
    text = (tmp_path / "habits.md").read_text()
    assert "suggerimenti, non fatti" in text
    assert str(items[0]["evidence"]) in text


def test_forget_and_off(tmp_path):
    store = HabitStore(tmp_path / "data" / "habits.db")
    for i in range(3):
        store.observe_message("fammi il riepilogo allenamenti", now=at(9, i))
    assert store.forget("allenamenti") == 3
    assert not any("allenamenti" in i["text"] for i in store.summarize())
    store.set_enabled(False)
    store.observe_message("nuovo argomento bicicletta", now=at(9))
    assert store.summarize() == []  # off = dati cancellati e niente nuove raccolte


def test_local_only_gate():
    assert habits_allowed_for_provider("ollama_local", local_only=True)
    assert not habits_allowed_for_provider("groq", local_only=True)
    assert habits_allowed_for_provider("groq", local_only=False)


# --- comandi /habits --------------------------------------------------------------

def make_cc(tmp_path):
    from app.agent.memory import MemoryStore
    habits = HabitStore(tmp_path / "data" / "habits.db")
    lines = []
    cc = CommandContext(
        registry=None, state={}, store=MemoryStore(tmp_path / "memory.db"),
        out=lines.append, get_active=lambda: "fake", set_active=lambda n: None,
        describe_registry=lambda r, a, o: None, habits=habits)
    return cc, lines, habits


def test_habits_command_shows_items(tmp_path):
    cc, lines, habits = make_cc(tmp_path)
    for hour in (9, 10, 11, 12, 13):
        habits.observe_message("ciao", now=at(hour))
    assert handle_command("/habits", cc)
    assert any("orari" in line or "evidenze" in line for line in lines)


def test_habits_off_and_forget_commands(tmp_path):
    cc, lines, habits = make_cc(tmp_path)
    habits.observe_message("fammi il riepilogo allenamenti", now=at(9))
    assert handle_command("/habits forget allenamenti", cc)
    assert "Rimossi" in lines[-1]
    assert handle_command("/habits off", cc)
    assert habits.enabled() is False
    assert handle_command("/habits on", cc)
    assert habits.enabled() is True


def test_habits_command_disabled_interface(tmp_path):
    cc, lines, _ = make_cc(tmp_path)
    cc.habits = None
    assert handle_command("/habits", cc)
    assert "non attive" in lines[-1]
