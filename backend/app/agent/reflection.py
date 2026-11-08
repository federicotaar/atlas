"""Riflessione post-task e revisione settimanale (spec sez. 8, stage 14).

Ogni task del loop agente lascia una riga in data/reflections.jsonl:
esito, passi, errori. La revisione settimanale conta i fallimenti,
individua i pattern e propone miglioramenti come MESSAGGIO approvabile:
mai applicazioni automatiche.
"""
from __future__ import annotations

import json
import threading
import time
from collections import Counter
from pathlib import Path


class ReflectionLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()

    def record(self, task: str, ok: bool, steps: int = 0, note: str = "",
               channel: str = "cli", now: float | None = None) -> None:
        now = time.time() if now is None else now
        entry = {"ts": now, "task": task[:200], "ok": ok, "steps": steps,
                 "note": note[:300], "channel": channel}
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def entries(self, since: float = 0.0) -> list[dict]:
        if not self._path.exists():
            return []
        out = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("ts", 0) >= since:
                out.append(entry)
        return out


def weekly_review(log: ReflectionLog, now: float | None = None) -> str | None:
    """Riepilogo della settimana con proposte. None se non c'e' materiale."""
    now = time.time() if now is None else now
    entries = log.entries(since=now - 7 * 86400)
    if not entries:
        return None
    done = [e for e in entries if e["ok"]]
    failed = [e for e in entries if not e["ok"]]
    lines = [f"Revisione settimanale ({len(entries)} task: "
             f"{len(done)} riusciti, {len(failed)} falliti)."]
    if failed:
        notes = Counter(e.get("note", "")[:80] for e in failed if e.get("note"))
        lines.append("Errori piu' frequenti:")
        for note, count in notes.most_common(3):
            lines.append(f"- {count}x {note}")
        lines.append("Proposta: guarda gli errori sopra; se vuoi posso "
                     "creare una skill o un promemoria per evitarli "
                     "(rispondi e lo preparo, nulla parte da solo).")
    slow = [e for e in done if e.get("steps", 0) >= 8]
    if slow:
        lines.append(f"{len(slow)} task hanno richiesto molti passi; se sono "
                     "ricorrenti posso salvarne la procedura come skill.")
    return "\n".join(lines)
