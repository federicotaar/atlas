"""Apprendimento dalle abitudini (spec sez. 8, build order 13).

HabitStore osserva i messaggi dell'utente (quando scrive, cosa chiede,
quanto scrive) e accumula evidenze locali in SQLite. Un giro notturno le
riassume in habits.md leggibile: ogni elemento ha conteggio delle evidenze
e data dell'ultima osservazione. Le abitudini sono SUGGERIMENTI, non fatti:
una routine appena inferita va confermata prima di agirci sopra.

Privacy:
- tutto resta in locale;
- niente inferenze sensibili (salute, soldi, relazioni, politica): i
  messaggi che toccano quei temi non lasciano traccia di contenuto;
- /habits off spegne l'apprendimento; /habits forget rimuove un elemento;
- modalita' local-only: il contesto abitudini e' usato solo con modelli
  Ollama locali, mai inviato a provider cloud.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

# Temi sensibili (spec): nessuna inferenza su questi, mai.
SENSITIVE = re.compile(
    r"\b(salute|dottor|medicin|malattia|sintomo|psicolog|soldi|stipendio|"
    r"mutuo|debito|fidanzat|ragazza|moglie|marito|partito|elezioni|voto|"
    r"religione|diagnosi)\b", re.IGNORECASE)

STOPWORDS = {
    "che", "per", "con", "una", "non", "del", "della", "dei", "gli", "alla",
    "dalle", "nel", "nella", "sul", "sulla", "come", "dove", "quando", "mi",
    "ti", "ci", "lo", "la", "il", "le", "un", "di", "da", "in", "su", "e",
    "o", "ma", "se", "puoi", "puo", "fai", "fare", "dammi", "dimmi", "vorrei",
    "questo", "questa", "sono", "hai", "ho", "al", "ai", "alle", "the", "and",
    "for", "you", "can", "me", "my", "please", "what", "when", "how",
}

WEEKDAYS_IT = ("lunedi'", "martedi'", "mercoledi'", "giovedi'",
               "venerdi'", "sabato", "domenica")


class HabitStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL, hour INTEGER NOT NULL, weekday INTEGER NOT NULL,
            msg_len INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS topics(
            word TEXT NOT NULL, weekday INTEGER NOT NULL,
            ts REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS lengths(
            ts REAL NOT NULL, chars INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS feedback(
            ts REAL NOT NULL, kind TEXT NOT NULL);  -- shorter/longer
        """)

    # --- osservazione -----------------------------------------------------

    def enabled(self) -> bool:
        row = self._db.execute(
            "SELECT value FROM meta WHERE key='enabled'").fetchone()
        return row is None or row["value"] != "0"

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO meta(key,value) VALUES('enabled',?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("1" if on else "0",))
            if not on:  # /habits off: dimentica tutto, non solo smetti
                for table in ("events", "topics", "lengths", "feedback"):
                    self._db.execute(f"DELETE FROM {table}")
            self._db.commit()

    def observe_message(self, text: str, now: float | None = None) -> None:
        """Registra un messaggio dell'utente. I messaggi su temi sensibili
        lasciano SOLO l'impronta oraria, mai il contenuto."""
        if not self.enabled():
            return
        now = time.time() if now is None else now
        t = time.localtime(now)
        with self._lock:
            self._db.execute(
                "INSERT INTO events(ts,hour,weekday,msg_len) VALUES(?,?,?,?)",
                (now, t.tm_hour, t.tm_wday, len(text)))
            if not SENSITIVE.search(text):
                for word in self._keywords(text):
                    self._db.execute(
                        "INSERT INTO topics(word,weekday,ts) VALUES(?,?,?)",
                        (word, t.tm_wday, now))
            self._db.commit()

    def observe_reply_length(self, chars: int, now: float | None = None) -> None:
        if not self.enabled():
            return
        with self._lock:
            self._db.execute("INSERT INTO lengths(ts,chars) VALUES(?,?)",
                             (now or time.time(), chars))
            self._db.commit()

    def observe_length_feedback(self, kind: str,
                                now: float | None = None) -> None:
        """kind = shorter | longer: l'utente ha chiesto risposte piu'
        corte o piu' lunghe."""
        if not self.enabled() or kind not in ("shorter", "longer"):
            return
        with self._lock:
            self._db.execute("INSERT INTO feedback(ts,kind) VALUES(?,?)",
                             (now or time.time(), kind))
            self._db.commit()

    @staticmethod
    def _keywords(text: str) -> list[str]:
        words = re.findall(r"[a-zA-Z][a-zA-Z'0-9]{2,}", text.lower())
        counts = Counter(w for w in words if w not in STOPWORDS)
        return [w for w, _ in counts.most_common(3)]

    # --- sintesi ------------------------------------------------------------

    def summarize(self, now: float | None = None) -> list[dict]:
        """Elementi appresi con evidenze e ultima osservazione."""
        now = time.time() if now is None else now
        items: list[dict] = []

        hours = [r["hour"] for r in self._db.execute(
            "SELECT hour FROM events").fetchall()]
        if len(hours) >= 5:
            first_seen = min(hours)
            last_seen = max(hours)
            items.append({
                "key": "orari attivi",
                "text": f"scrive in genere tra le {first_seen:02d}:00 "
                        f"e le {last_seen:02d}:59",
                "evidence": len(hours),
                "last_seen": self._last_ts("events")})

        rows = self._db.execute(
            "SELECT word, COUNT(*) AS n, MAX(ts) AS last FROM topics"
            " GROUP BY word HAVING n >= 3 ORDER BY n DESC LIMIT 10").fetchall()
        for row in rows:
            items.append({
                "key": f"argomento frequente: {row['word']}",
                "text": f"chiede spesso di \"{row['word']}\"",
                "evidence": row["n"], "last_seen": row["last"]})

        # Routine: stessa parola, stesso giorno della settimana, >= 3 volte.
        rows = self._db.execute(
            "SELECT word, weekday, COUNT(*) AS n, MAX(ts) AS last FROM topics"
            " GROUP BY word, weekday HAVING n >= 3"
            " ORDER BY n DESC LIMIT 5").fetchall()
        for row in rows:
            day = WEEKDAYS_IT[row["weekday"]]
            items.append({
                "key": f"routine: {row['word']} il {day}",
                "text": f"di {day} chiede spesso di \"{row['word']}\" "
                        f"(possibile routine da proporre)",
                "evidence": row["n"], "last_seen": row["last"]})

        shorter = self._db.execute(
            "SELECT COUNT(*) AS n FROM feedback WHERE kind='shorter'"
            ).fetchone()["n"]
        longer = self._db.execute(
            "SELECT COUNT(*) AS n FROM feedback WHERE kind='longer'"
            ).fetchone()["n"]
        if shorter >= 3 and shorter > longer:
            items.append({"key": "lunghezza risposte",
                          "text": "preferisce risposte brevi",
                          "evidence": shorter,
                          "last_seen": self._last_ts("feedback")})
        elif longer >= 3 and longer > shorter:
            items.append({"key": "lunghezza risposte",
                          "text": "preferisce risposte lunghe",
                          "evidence": longer,
                          "last_seen": self._last_ts("feedback")})
        return items

    def _last_ts(self, table: str) -> float:
        row = self._db.execute(f"SELECT MAX(ts) AS last FROM {table}").fetchone()
        return row["last"] or 0.0

    def forget(self, keyword: str) -> int:
        """Rimuove gli elementi che contengono la parola: topic e routine."""
        with self._lock:
            cur = self._db.execute("DELETE FROM topics WHERE word=?",
                                   (keyword.lower(),))
            self._db.commit()
        return cur.rowcount

    def write_habits_md(self, path: Path, now: float | None = None) -> list[dict]:
        """Il riepilogo notturno leggibile: habits.md."""
        now = time.time() if now is None else now
        items = self.summarize(now)
        lines = ["# Abitudini apprese (suggerimenti, non fatti)",
                 "",
                 f"_Aggiornato: {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}_",
                 ""]
        if not items:
            lines.append("Ancora nessuna abitudine con abbastanza evidenze.")
        for item in items:
            seen = time.strftime("%Y-%m-%d", time.localtime(item["last_seen"]))
            lines.append(f"- **{item['key']}**: {item['text']} "
                         f"(evidenze: {item['evidence']}, ultima: {seen})")
        path.write_text("\n".join(lines) + "\n")
        return items

    def routines_to_propose(self, min_evidence: int = 3) -> list[dict]:
        """Routine inferite da proporre (MAI attivare senza conferma)."""
        return [item for item in self.summarize()
                if item["key"].startswith("routine:")
                and item["evidence"] >= min_evidence]


def habits_context(items: list[dict]) -> str:
    """Blocco breve per il contesto del modello (tono/lunghezza adattivi)."""
    notes = [item["text"] for item in items
             if item["key"] == "lunghezza risposte"]
    if not notes:
        return ""
    return "Abitudini note dell'utente: " + "; ".join(notes)


def habits_allowed_for_provider(provider_name: str, local_only: bool) -> bool:
    """Modalita' local-only: i dati sulle abitudini non lasciano la
    macchina, quindi con provider cloud non si iniettano."""
    if not local_only:
        return True
    return provider_name.startswith("ollama")
