"""Stage 14 (spec sez. 8): libreria skill come proposte approvabili, tool
auto-generati con test in sandbox e attivazione SOLO dopo approvazione,
versioning git delle auto-modifiche con /rollback, riflessione e revisione
settimanale. Hard limit: il perimetro protetto non si tocca.
"""
from __future__ import annotations

import time

import pytest

from app.agent.forge import ToolForge
from app.agent.reflection import ReflectionLog, weekly_review
from app.agent.selfmods import commit_selfmod, ensure_repo, rollback
from app.agent.skills import SkillStore, skills_context

T0 = time.mktime((2026, 11, 8, 12, 0, 0, 0, 0, -1))

GOOD_TOOL = '''
name = "echo_upper"
description = "Risponde con il testo in maiuscolo"
parameters = {"type": "object",
              "properties": {"text": {"type": "string"}},
              "required": ["text"]}


def run(args, ctx):
    return args["text"].upper()
'''
GOOD_TESTS = '''
import echo_upper


def test_upper():
    assert echo_upper.run({"text": "ciao"}, None) == "CIAO"
'''


# --- skill library -----------------------------------------------------------

def test_skill_saved_as_proposal_and_approved(tmp_path):
    store = SkillStore(tmp_path)
    store.save("brief-mattutino", "1. leggi il calendario\n2. riassumi",
               source_task="brief del mattino", now=T0)
    items = store.list()
    assert items[0]["status"] == "proposed"
    # non iniettata finche' non approvata
    assert skills_context(store, "fammi il brief mattutino") == ""
    assert store.approve("brief-mattutino")
    block = skills_context(store, "fammi il brief mattutino")
    assert "leggi il calendario" in block


def test_skill_reject_and_bad_name(tmp_path):
    store = SkillStore(tmp_path)
    store.save("x-y", "procedura")
    assert store.reject("x-y")
    assert skills_context(store, "x y") == ""
    try:
        store.save("Nome Maiuscolo!!", "x")
    except ValueError:
        return
    raise AssertionError("nome invalido accettato")


# --- forge -----------------------------------------------------------------------

def test_propose_runs_tests_in_sandbox(tmp_path):
    forge = ToolForge(tmp_path)
    result = forge.propose("echo_upper", GOOD_TOOL, GOOD_TESTS, now=T0)
    assert result["tests_ok"] is True
    assert forge.list()[0]["status"] == "pending"  # MAI attivo da solo


def test_propose_with_failing_tests_stays_pending(tmp_path):
    forge = ToolForge(tmp_path)
    result = forge.propose("echo_upper", GOOD_TOOL,
                           "def test_x():\n    assert False\n", now=T0)
    assert result["tests_ok"] is False
    approved = forge.approve("echo_upper")
    assert approved["ok"] is False and "test" in approved["error"]


def test_approve_requires_interface_and_activates(tmp_path):
    forge = ToolForge(tmp_path)
    forge.propose("echo_upper", GOOD_TOOL, GOOD_TESTS, now=T0)
    result = forge.approve("echo_upper")
    assert result["ok"] is True
    loaded = forge.load_approved()
    assert loaded["echo_upper"]["run"]({"text": "ok"}, None) == "OK"


def test_approve_rejects_incomplete_interface(tmp_path):
    forge = ToolForge(tmp_path)
    forge.propose("echo_upper", "name = 'x'\n", GOOD_TESTS.replace(
        "import echo_upper", "import echo_upper as _"), now=T0)
    result = forge.approve("echo_upper")
    assert result["ok"] is False


def test_no_overwrite_of_approved_tool(tmp_path):
    forge = ToolForge(tmp_path)
    forge.propose("echo_upper", GOOD_TOOL, GOOD_TESTS, now=T0)
    forge.approve("echo_upper")
    try:
        forge.propose("echo_upper", GOOD_TOOL, GOOD_TESTS, now=T0 + 60)
    except ValueError:
        return
    raise AssertionError("tool approvato sovrascritto")


# --- versioning e rollback ---------------------------------------------------------

def test_selfmod_commits_and_rollback(tmp_path):
    assert ensure_repo(tmp_path)
    store = SkillStore(tmp_path)
    store.save("brief-mattutino", "procedura", now=T0)
    sha = commit_selfmod(tmp_path, "nuova skill proposta: brief-mattutino")
    assert sha  # ogni auto-modifica e' un commit
    ok, message = rollback(tmp_path)
    assert ok, message
    assert store.get("brief-mattutino") is None  # file annullato dal revert


def test_rollback_refuses_when_nothing_to_undo(tmp_path):
    ensure_repo(tmp_path)
    ok, message = rollback(tmp_path)
    assert not ok and "nessuna" in message


def test_databases_not_versioned(tmp_path):
    ensure_repo(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "jobs.db").write_bytes(b"x")
    commit_selfmod(tmp_path, "tentativo")
    import subprocess
    out = subprocess.run(["git", "ls-files"], cwd=tmp_path,
                         capture_output=True, text=True).stdout
    assert "jobs.db" not in out and ".env" not in out


# --- riflessione -----------------------------------------------------------------

def test_reflection_log_and_weekly_review(tmp_path):
    log = ReflectionLog(tmp_path / "data" / "reflections.jsonl")
    assert weekly_review(log, now=T0) is None
    log.record("fammi il brief", ok=True, steps=3, now=T0)
    log.record("prenota il treno", ok=False, steps=5,
               note="provider groq rate limit", now=T0 + 60)
    log.record("prenota l'hotel", ok=False, steps=4,
               note="provider groq rate limit", now=T0 + 120)
    review = weekly_review(log, now=T0 + 3600)
    assert "1 riusciti, 2 falliti" in review
    assert "rate limit" in review
    assert "nulla parte da solo" in review  # proposte, mai auto-applicate


# --- isolamento bwrap del forge ----------------------------------------------------

def test_static_gate_rejects_dangerous_import(tmp_path):
    forge = ToolForge(tmp_path)
    result = forge.propose(
        "bad_tool",
        'import subprocess\nname="bad_tool"\ndescription="d"\n'
        'parameters={}\ndef run(args, ctx):\n    return "x"\n',
        "def test_x():\n    assert True\n", now=T0)
    assert result["tests_ok"] is False
    assert "subprocess" in result["tests_out"]
    assert not (tmp_path / "tools_generated" / "bad_tool.py").exists()


def test_forge_records_isolation_level(tmp_path):
    forge = ToolForge(tmp_path)
    result = forge.propose("echo_upper", GOOD_TOOL, GOOD_TESTS, now=T0)
    assert result["tests_ok"] is True
    assert result["isolation"] in ("bwrap", "reduced")


def test_bwrap_blocks_network_and_writes(tmp_path):
    import shutil
    if shutil.which("bwrap") is None:
        pytest.skip("bwrap non disponibile su questo host")
    from app.agent.forge import _bwrap_cmd
    cmd = _bwrap_cmd(tmp_path)
    assert cmd is not None
    probe = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 443), timeout=2)\n"
        "    raise SystemExit('rete raggiungibile dentro bwrap')\n"
        "except OSError:\n"
        "    pass\n"
    )
    import subprocess as sp
    run = sp.run(cmd + ["-c", probe], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr[-300:]
    # fuori da /work non si scrive: /usr e' read-only
    run2 = sp.run(cmd + ["-c", "import pathlib; "
                  "pathlib.Path('/usr/x').write_text('1')"],
                  capture_output=True, text=True, timeout=60)
    assert run2.returncode != 0
