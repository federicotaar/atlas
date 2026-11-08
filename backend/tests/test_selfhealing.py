"""Stage 12 (spec sez. 8): watchdog con backoff e freno anti crash-loop,
self-test con registro incidenti, backup e ripristino dei database,
checkpoint dei task lunghi.
"""
from __future__ import annotations

import json
import sqlite3
import time

from app.agent.backup import BackupManager, integrity_ok
from app.agent.checkpoint import CheckpointStore
from app.agent.health import HealthChecker, IncidentLog
from app.agent.supervisor import RestartPolicy, supervise_channel

T0 = time.mktime((2026, 11, 8, 12, 0, 0, 0, 0, -1))


# --- RestartPolicy -----------------------------------------------------------

def test_restart_policy_backoff_and_crash_loop_limit():
    policy = RestartPolicy(max_restarts=3, window_seconds=600, backoff_base=2)
    assert policy.next_delay(T0) == 2
    assert policy.next_delay(T0 + 10) == 4
    assert policy.next_delay(T0 + 20) == 8
    assert policy.next_delay(T0 + 30) is None  # freno anti crash-loop


def test_restart_policy_window_expires():
    policy = RestartPolicy(max_restarts=1, window_seconds=60, backoff_base=2)
    assert policy.next_delay(T0) is not None
    assert policy.next_delay(T0 + 120) is not None  # finestra passata


def test_supervise_channel_retries_and_gives_up():
    incidents, given_up = [], []
    calls = {"n": 0}

    def always_crash():
        calls["n"] += 1
        raise RuntimeError("boom")

    policy = RestartPolicy(max_restarts=2, window_seconds=600,
                           backoff_base=0.01, backoff_max=0.01)
    thread = supervise_channel("telegram", always_crash, policy=policy,
                               on_incident=incidents.append,
                               on_give_up=given_up.append)
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert calls["n"] == 3  # tentativo iniziale + 2 riavvii
    assert given_up == ["telegram"]
    assert any("crash-loop" in msg for msg in incidents)


def test_supervise_channel_clean_exit_no_restart():
    calls = {"n": 0}

    def clean():
        calls["n"] += 1

    thread = supervise_channel("whatsapp", clean,
                               policy=RestartPolicy(backoff_base=0.01))
    thread.join(timeout=5)
    assert calls["n"] == 1


# --- IncidentLog e self-test ----------------------------------------------------

def test_incident_log_roundtrip(tmp_path):
    log = IncidentLog(tmp_path / "logs" / "errors.log")
    log.record("canale", "whatsapp caduto")
    day = time.strftime("%Y-%m-%d")
    assert log.since(day)[0]["detail"] == "whatsapp caduto"
    assert log.since("1999-01-01") == []


def test_self_test_detects_corrupt_db_and_full_disk(tmp_path):
    checker = HealthChecker(tmp_path)
    bad = tmp_path / "memory.db"
    bad.write_bytes(b"questo non e' sqlite")
    report = checker.self_test()
    assert report["ok"] is False
    assert any("memory" in f for f in report["failures"])
    # l'incidente e' finito nel registro
    day = time.strftime("%Y-%m-%d")
    assert any("memory" in e["detail"]
               for e in checker.incidents.since(day))


def test_self_test_logs_health_line(tmp_path):
    checker = HealthChecker(tmp_path)
    report = checker.self_test()
    assert report["ok"] is True
    lines = (tmp_path / "logs" / "health.log").read_text().splitlines()
    assert json.loads(lines[-1])["ok"] is True


# --- backup e ripristino -----------------------------------------------------------

def make_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE t (x INTEGER)")
    db.execute("INSERT INTO t VALUES (42)")
    db.commit()
    db.close()


def test_backup_and_rotation(tmp_path):
    manager = BackupManager(tmp_path, keep_days=7)
    make_db(tmp_path / "memory.db")
    written = manager.backup(now=T0)
    assert len(written) == 1
    # giorno oltre la rotazione: cancellato
    old_day = time.strftime("%Y-%m-%d", time.localtime(T0 - 8 * 86400))
    old_dir = tmp_path / "backups" / old_day
    old_dir.mkdir(parents=True)
    (old_dir / "memory.db").write_bytes(b"x")
    manager.backup(now=T0)
    assert not old_dir.exists()


def test_corrupt_db_restored_from_backup(tmp_path):
    manager = BackupManager(tmp_path)
    make_db(tmp_path / "data" / "jobs.db")
    manager.backup(now=T0)
    # corrompo il database
    (tmp_path / "data" / "jobs.db").write_bytes(b"corrotto")
    assert not integrity_ok(tmp_path / "data" / "jobs.db")
    messages = manager.verify_and_restore(now=T0 + 3600)
    assert len(messages) == 1
    # ripristino esplicito: eta' del backup, perdita, originale conservato
    assert "di oggi" in messages[0]
    assert "conservato" in messages[0]
    assert integrity_ok(tmp_path / "data" / "jobs.db")
    kept = list((tmp_path / "data").glob("jobs.db.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"corrotto"
    db = sqlite3.connect(str(tmp_path / "data" / "jobs.db"))
    assert db.execute("SELECT x FROM t").fetchone()[0] == 42
    db.close()


def test_corrupt_db_without_backup_reported(tmp_path):
    incidents = IncidentLog(tmp_path / "logs" / "errors.log")
    (tmp_path / "memory.db").write_bytes(b"corrotto")
    manager = BackupManager(tmp_path)
    messages = manager.verify_and_restore(incidents)
    assert "NESSUN backup integro" in messages[0]
    assert "resta al suo posto" in messages[0]
    assert integrity_ok(tmp_path / "memory.db") is False  # non toccato
    day = time.strftime("%Y-%m-%d")
    assert any("NESSUN backup" in e["detail"] for e in incidents.since(day))


# --- checkpoint -------------------------------------------------------------------

def test_checkpoint_save_load_expiry(tmp_path):
    store = CheckpointStore(tmp_path / "data" / "checkpoint.json")
    assert store.load() is None
    store.save([{"role": "user", "content": "ciao"}], note="ciao", now=T0)
    loaded = store.load(now=T0 + 60)
    assert loaded["history"][0]["content"] == "ciao"
    assert store.load(now=T0 + 25 * 3600) is None  # troppo vecchio
    store.clear()
    assert store.load(now=T0) is None


def test_checkpoint_keeps_only_tail(tmp_path):
    store = CheckpointStore(tmp_path / "data" / "checkpoint.json")
    history = [{"role": "user", "content": str(i)} for i in range(100)]
    store.save(history, now=T0)
    assert len(store.load(now=T0)["history"]) == 40
