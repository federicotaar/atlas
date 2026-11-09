"""Messaggi proattivi con guardrail (spec sez. 8, build order 11).

ProactiveGate decide se un messaggio puo' partire ORA verso un canale:
- ore silenziose (config): passa solo "urgent", il resto finisce nel digest;
- tetto giornaliero ai messaggi NON richiesti (unsolicited): l'eccedenza va
  nel digest;
- le categorie a priorita' bassa vanno sempre nel digest, mai una alla volta;
- ogni categoria ha un punteggio di importanza persistente: "meno di questo"
  lo abbassa, "piu'" lo alza; se l'utente ignora una categoria (3 consegne
  consecutive senza nessun messaggio dell'utente in mezzo) scende di un
  passo; sotto soglia minima la categoria non parte proprio.

I messaggi richiesti dall'utente (promemoria e job creati da Fred) sono
sollecitati e NON sono ping non richiesti: partono sempre e subito - mai
soppressi da una categoria silenziata, mai ritardati dalle ore silenziose,
mai finiscono nel digest, mai contati nel tetto. Un promemoria chiesto per
le 7:00 arriva alle 7:00, anche in ore silenziose e anche se la sua
categoria e' stata abbassata dal feedback sul traffico NON richiesto.

Il digest si svuota al primo heartbeat fuori dalle ore silenziose.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .localzone import FALLBACK_ZONE, detect_system_zone, load_timezone_name

URGENT = "urgent"
HIGH = "high"
LOW = "low"

MIN_IMPORTANCE = 0
MAX_IMPORTANCE = 5
DEFAULT_IMPORTANCE = 3
SEND_THRESHOLD = 1          # sotto questa importanza la categoria non parte
IGNORE_DECAY_AFTER = 3      # consegne ignorate consecutive prima del -1

# Categorie note; altre stringhe sono accettate con importanza di default.
DEFAULT_IMPORTANCES = {
    "reminder": 5,     # promemoria richiesti: quasi mai filtrati
    "job": 3,          # output dei job schedulati
    "brief": 3,        # brief mattutino
    "heads-up": 3,     # avvisi dell'agente
    "digest": 3,       # il digest stesso
}


@dataclass
class ProactiveConfig:
    quiet_start: str = "23:00"     # "HH:MM" nel fuso dell'installazione
    quiet_end: str = "07:30"
    max_unsolicited_per_day: int = 8
    heartbeat_minutes: int = 15
    batch_digest: bool = True
    habits_local_only: bool = False
    timezone: str = ""             # nome IANA; vuoto = risolto da localzone

    @classmethod
    def load(cls, workspace: Path) -> "ProactiveConfig":
        path = workspace / "config" / "proactive.json"
        cfg = cls()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                for key in ("quiet_start", "quiet_end",
                            "max_unsolicited_per_day", "heartbeat_minutes",
                            "batch_digest", "habits_local_only"):
                    if key in data:
                        setattr(cfg, key, data[key])
            except (ValueError, TypeError):
                pass  # config rotta: default, mai bloccare l'avvio
        # Il fuso e' unico per tutta l'installazione (config/app.json via
        # localzone), mai un'impostazione a parte della proattivita'.
        cfg.timezone = load_timezone_name(workspace)
        return cfg


def _hhmm_to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _zone_of(cfg: ProactiveConfig) -> ZoneInfo:
    name = (cfg.timezone or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return ZoneInfo(detect_system_zone() or FALLBACK_ZONE)


def in_quiet_hours(cfg: ProactiveConfig, now: float | None = None) -> bool:
    t = datetime.fromtimestamp(time.time() if now is None else now,
                               _zone_of(cfg))
    minute = t.hour * 60 + t.minute
    start, end = _hhmm_to_minutes(cfg.quiet_start), _hhmm_to_minutes(cfg.quiet_end)
    if start == end:
        return False
    if start < end:                    # es. 13:00-15:00
        return start <= minute < end
    return minute >= start or minute < end  # es. 23:00-07:30 (attraversa mezzanotte)


class ProactiveStore:
    """Importanza per categoria, conteggio giornaliero, digest pendente."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._db.executescript("""
        CREATE TABLE IF NOT EXISTS importances(
            category TEXT PRIMARY KEY, score INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS deliveries(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL, category TEXT NOT NULL,
            solicited INTEGER NOT NULL DEFAULT 0, ts REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS digest(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL, sender TEXT NOT NULL,
            text TEXT NOT NULL, category TEXT NOT NULL, ts REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS ignore_counts(
            category TEXT PRIMARY KEY, consecutive INTEGER NOT NULL DEFAULT 0);
        """)

    def importance(self, category: str) -> int:
        row = self._db.execute(
            "SELECT score FROM importances WHERE category=?",
            (category,)).fetchone()
        if row:
            return row["score"]
        return DEFAULT_IMPORTANCES.get(category, DEFAULT_IMPORTANCE)

    def set_importance(self, category: str, score: int) -> int:
        score = max(MIN_IMPORTANCE, min(MAX_IMPORTANCE, score))
        with self._lock:
            self._db.execute(
                "INSERT INTO importances(category,score) VALUES(?,?)"
                " ON CONFLICT(category) DO UPDATE SET score=excluded.score",
                (category, score))
            self._db.commit()
        return score

    def adjust_importance(self, category: str, delta: int) -> int:
        return self.set_importance(category, self.importance(category) + delta)

    def importances(self) -> dict[str, int]:
        rows = self._db.execute("SELECT category,score FROM importances").fetchall()
        merged = dict(DEFAULT_IMPORTANCES)
        merged.update({r["category"]: r["score"] for r in rows})
        return merged

    def record_delivery(self, category: str, solicited: bool,
                        now: float | None = None) -> None:
        now = time.time() if now is None else now
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        with self._lock:
            self._db.execute(
                "INSERT INTO deliveries(day,category,solicited,ts)"
                " VALUES(?,?,?,?)", (day, category, int(solicited), now))
            self._db.commit()

    def unsolicited_today(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        row = self._db.execute(
            "SELECT COUNT(*) AS n FROM deliveries WHERE day=? AND solicited=0",
            (day,)).fetchone()
        return row["n"]

    def ignored_streak(self, category: str) -> int:
        row = self._db.execute(
            "SELECT consecutive FROM ignore_counts WHERE category=?",
            (category,)).fetchone()
        return row["consecutive"] if row else 0

    def note_delivered_unanswered(self, category: str) -> int:
        with self._lock:
            self._db.execute(
                "INSERT INTO ignore_counts(category,consecutive) VALUES(?,1)"
                " ON CONFLICT(category) DO UPDATE SET"
                " consecutive=consecutive+1", (category,))
            self._db.commit()
        return self.ignored_streak(category)

    def note_user_message(self) -> None:
        """Un messaggio dell'utente azzera il conteggio 'ignorato' ovunque."""
        with self._lock:
            self._db.execute("DELETE FROM ignore_counts")
            self._db.commit()

    def queue_digest(self, channel: str, sender: str, text: str,
                     category: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._db.execute(
                "INSERT INTO digest(channel,sender,text,category,ts)"
                " VALUES(?,?,?,?,?)", (channel, sender, text, category, now))
            self._db.commit()

    def pop_digest(self) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM digest ORDER BY id").fetchall()
            self._db.execute("DELETE FROM digest")
            self._db.commit()
        return rows

    def pending_digest_count(self) -> int:
        return self._db.execute("SELECT COUNT(*) AS n FROM digest").fetchone()["n"]


@dataclass
class DeliveryDecision:
    send: bool
    reason: str = ""
    queued_digest: bool = False


@dataclass
class ProactiveGate:
    """Decide e instrada i messaggi proattivi. deliver_fn(channel, sender,
    text) e' l'invio reale verso il canale."""

    cfg: ProactiveConfig
    store: ProactiveStore
    deliver_fn: object = None
    on_digest_flush: object = None  # chiamato con (channel, sender, testo)

    def decide(self, category: str, priority: str, solicited: bool,
               now: float | None = None) -> DeliveryDecision:
        now = time.time() if now is None else now
        # Un messaggio RICHIESTO non e' un ping non sollecitato: parte sempre
        # e subito, qualunque sia lo stato della categoria, le ore silenziose
        # o il batching di bassa priorita'.
        if solicited:
            return DeliveryDecision(True, "richiesto dall'utente")
        if self.store.importance(category) < SEND_THRESHOLD:
            return DeliveryDecision(False, "categoria sotto soglia")
        if in_quiet_hours(self.cfg, now):
            if priority == URGENT:
                return DeliveryDecision(True, "urgente in ore silenziose")
            return DeliveryDecision(False, "ore silenziose", queued_digest=True)
        if priority == LOW and self.cfg.batch_digest:
            return DeliveryDecision(False, "bassa priorita'", queued_digest=True)
        if not solicited and self.store.unsolicited_today(now) >= \
                self.cfg.max_unsolicited_per_day:
            return DeliveryDecision(False, "tetto giornaliero",
                                    queued_digest=True)
        return DeliveryDecision(True)

    def deliver(self, channel: str, sender: str, text: str, *,
                category: str = "job", priority: str = HIGH,
                solicited: bool = False,
                now: float | None = None) -> DeliveryDecision:
        decision = self.decide(category, priority, solicited, now)
        if decision.send:
            if self.deliver_fn is not None:
                self.deliver_fn(channel, sender, text)
            self.store.record_delivery(category, solicited, now)
            if not solicited:
                streak = self.store.note_delivered_unanswered(category)
                if streak >= IGNORE_DECAY_AFTER:
                    self.store.adjust_importance(category, -1)
                    self.store.note_user_message()  # azzera la streak
        elif decision.queued_digest:
            self.store.queue_digest(channel, sender, text, category, now)
        return decision

    def heartbeat(self, now: float | None = None) -> int:
        """Giro di heartbeat: fuori dalle ore silenziose svuota il digest
        (un messaggio per destinazione). Ritorna i digest inviati."""
        if in_quiet_hours(self.cfg, now):
            return 0
        rows = self.store.pop_digest()
        by_target: dict[tuple[str, str], list] = {}
        for row in rows:
            by_target.setdefault((row["channel"], row["sender"]), []).append(row)
        sent = 0
        for (channel, sender), items in by_target.items():
            body = "Nel frattempo, in sintesi:\n" + "\n".join(
                f"- {item['text']}" for item in items)
            if self.on_digest_flush is not None:
                self.on_digest_flush(channel, sender, body)
            elif self.deliver_fn is not None:
                self.deliver_fn(channel, sender, body)
            self.store.record_delivery("digest", solicited=False, now=now)
            sent += 1
        return sent

    def user_message_seen(self) -> None:
        self.store.note_user_message()

    def feedback(self, category: str, more: bool) -> int:
        return self.store.adjust_importance(category, 1 if more else -1)
