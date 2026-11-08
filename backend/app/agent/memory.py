"""Memoria dell'agente (spec sez. 5): SQLite per cronologia conversazioni e
note a lungo termine.

- Cronologia separata per mittente e canale: (sender, channel) identifica una
  conversazione; la CLI usa ("locale", "cli"), i canali chat useranno il
  numero/id del mittente.
- /remember <testo>: nota persistente. /forget <testo>: rimuove le note che
  contengono il testo dato (rimozione vera, non soft-delete).
- A ogni richiesta le note pertinenti (match sulle parole del messaggio,
  poi le piu' recenti) vengono iniettate come contesto di sistema.

Niente SQLAlchemy qui: l'agente locale deve girare da solo con la stdlib.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT NOT NULL,
    channel TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(sender, channel, id);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_HISTORY_LIMIT = 40        # messaggi recenti per conversazione
_MEMORIES_INJECT = 8       # note massime iniettate per richiesta
_WORD = re.compile(r"[a-zA-Z0-9àèéìòù]{4,}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryStore:
    """Accesso SQLite thread-safe (un lock per connessione: i task dell'agente
    girano in thread separati dal REPL)."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- cronologia ---------------------------------------------------------

    def add_message(self, sender: str, channel: str, role: str, content: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO messages(sender, channel, role, content, ts) VALUES (?,?,?,?,?)",
                (sender, channel, role, content, _now()))
            self._db.commit()

    def history(self, sender: str, channel: str,
                limit: int = _HISTORY_LIMIT) -> list[dict]:
        """Ultimi `limit` messaggi della conversazione, in ordine cronologico.
        I messaggi con tool_calls del loop non vengono persistiti: la
        cronologia a lungo termine tiene solo testo utente/assistente."""
        with self._lock:
            rows = self._db.execute(
                "SELECT role, content FROM messages WHERE sender=? AND channel=? "
                "ORDER BY id DESC LIMIT ?", (sender, channel, limit)).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    # --- note a lungo termine -----------------------------------------------

    def remember(self, text: str) -> int:
        text = text.strip()
        if not text:
            raise ValueError("niente da ricordare: /remember <testo>")
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO memories(text, created_at) VALUES (?,?)", (text, _now()))
            self._db.commit()
            return int(cur.lastrowid)

    def forget(self, fragment: str) -> int:
        """Rimuove le note che contengono `fragment` (case-insensitive).
        Ritorna quante ne ha rimosse."""
        fragment = fragment.strip()
        if not fragment:
            raise ValueError("cosa devo dimenticare? /forget <testo>")
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM memories WHERE lower(text) LIKE ?",
                (f"%{fragment.lower()}%",))
            self._db.commit()
            return cur.rowcount

    def all_memories(self) -> list[tuple[int, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, text FROM memories ORDER BY id").fetchall()
        return [(int(r["id"]), str(r["text"])) for r in rows]

    def relevant(self, message: str, limit: int = _MEMORIES_INJECT) -> list[str]:
        """Note pertinenti: prima quelle che condividono una parola (4+ lettere)
        con il messaggio, in ordine di recentita'; poi le piu' recenti a
        riempire, senza duplicati."""
        words = {w.lower() for w in _WORD.findall(message)}
        notes = self.all_memories()
        scored: list[tuple[int, int, str]] = []  # (-hits, -id, text)
        for note_id, text in notes:
            note_words = {w.lower() for w in _WORD.findall(text)}
            hits = len(words & note_words)
            scored.append((-hits, -note_id, text))
        scored.sort()
        picked = [text for neg_hits, _, text in scored if neg_hits < 0]
        for _, _, text in scored:
            if len(picked) >= limit:
                break
            if text not in picked:
                picked.append(text)
        return picked[:limit]


def memories_context(notes: list[str]) -> str:
    """Blocco di sistema con le note: marcato come memoria, non come fatto
    corrente, e senza segreti (il SecretFilter del loop lo copre a monte)."""
    if not notes:
        return ""
    lines = "\n".join(f"- {note}" for note in notes)
    return ("Cose che l'utente ti ha chiesto di ricordare (memoria persistente, "
            "potrebbe non essere aggiornata):\n" + lines)
