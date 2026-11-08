"""Libreria di skill riusabili (spec sez. 8, build order 14).

Dopo un task a piu' passi riuscito, l'agente puo' salvare la procedura come
skill in skills/<nome>.md. Per decisione condivisa le skill salvate nascono
PROPOSTE: diventano attive (iniettate come contesto nelle richieste
pertinenti) solo dopo "/skills approve <nome>". Niente codice eseguibile:
una skill e' testo procedurale, l'attivazione riguarda solo il suo riuso.

Le skill sono versionate con git insieme alle altre auto-modifiche
(vedi selfmods.py): ogni salvataggio e' un commit, /rollback annulla.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


class SkillStore:
    def __init__(self, workspace: Path):
        self._dir = workspace / "skills"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._meta = self._dir / ".skills.json"

    def _load_meta(self) -> dict:
        if self._meta.exists():
            try:
                return json.loads(self._meta.read_text())
            except ValueError:
                return {}
        return {}

    def _save_meta(self, meta: dict) -> None:
        self._meta.write_text(json.dumps(meta, indent=1, ensure_ascii=False))

    def save(self, name: str, procedure: str, source_task: str = "",
             now: float | None = None) -> Path:
        """Salva una skill come PROPOSTA (non attiva finche' Fred approva)."""
        if not NAME_RE.match(name):
            raise ValueError(f"nome skill non valido: {name!r}")
        now = time.time() if now is None else now
        path = self._dir / f"{name}.md"
        path.write_text(
            f"# Skill: {name}\n\n"
            f"_Imparata da: {source_task or 'task riuscito'} "
            f"({time.strftime('%Y-%m-%d', time.localtime(now))})_\n\n"
            + procedure.strip() + "\n")
        meta = self._load_meta()
        meta[name] = {"status": "proposed", "ts": now,
                      "source_task": source_task[:200]}
        self._save_meta(meta)
        return path

    def list(self) -> list[dict]:
        meta = self._load_meta()
        return [{"name": name, **info} for name, info in sorted(meta.items())]

    def get(self, name: str) -> dict | None:
        path = self._dir / f"{name}.md"
        info = self._load_meta().get(name)
        if info is None or not path.exists():
            return None
        return {"name": name, "text": path.read_text(), **info}

    def approve(self, name: str) -> bool:
        meta = self._load_meta()
        if name not in meta:
            return False
        meta[name]["status"] = "approved"
        self._save_meta(meta)
        return True

    def reject(self, name: str) -> bool:
        meta = self._load_meta()
        if name not in meta:
            return False
        meta[name]["status"] = "rejected"
        self._save_meta(meta)
        return True

    def approved_skills(self) -> list[dict]:
        out = []
        for item in self.list():
            if item["status"] == "approved":
                full = self.get(item["name"])
                if full:
                    out.append(full)
        return out


def skills_context(store: SkillStore, query: str) -> str:
    """Blocchi delle skill APPROVATE pertinenti alla richiesta: match per
    parole del nome nel testo richiesto (piu' il contrario)."""
    words = {w for w in re.findall(r"[a-z0-9]{4,}", query.lower())}
    blocks = []
    for skill in store.approved_skills():
        name_words = set(skill["name"].split("-"))
        if words & name_words:
            blocks.append(f"Procedura nota ({skill['name']}):\n"
                          + skill["text"].strip())
    return "\n\n".join(blocks)
