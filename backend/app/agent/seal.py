"""Sigillo dei file protetti (spec sez. 8 hard limit): permission modes,
blocklist, regole di sandbox, logica di approvazione, .env e sistema di log
NON sono modificabili dagli strumenti dell'agente - in NESSUN modo, yolo
compreso.

Un filtro lessicale sui comandi non basta (variabili, subshell, path
indiretti): questo modulo rende il limite EFFETTIVO. All'avvio fotografa
(contenuto in memoria + hash) i file protetti; dopo OGNI esecuzione di uno
strumento li riverifica e li RIPRISTINA se modificati/rimossi, loggando
l'incidente. Il ripristino e' garantito perche' i byte originali vivono nel
processo dell'agente, non nel filesystem che il comando puo' toccare.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

# File sigillati se esistono all'avvio (relativi al root del repo).
SEALED_REPO_FILES = ("backend/app/agent/safety.py", "backend/app/agent/permissions.py")


class SealGuard:
    def __init__(self, repo_root: Path, extra: list[Path] | None = None,
                 on_incident: Callable[[str], None] | None = None):
        self._snapshots: dict[Path, bytes] = {}
        self._on_incident = on_incident or (lambda msg: None)
        targets = [repo_root / rel for rel in SEALED_REPO_FILES]
        targets.append(repo_root / ".env")
        targets.extend(extra or [])
        for path in targets:
            try:
                if path.is_file():
                    self._snapshots[path.resolve()] = path.read_bytes()
            except OSError:
                continue

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def verify_and_restore(self) -> list[str]:
        """Controlla ogni file sigillato; ripristina se manomesso.
        Ritorna le descrizioni degli incidenti (vuota = tutto integro)."""
        incidents: list[str] = []
        for path, original in self._snapshots.items():
            try:
                current = path.read_bytes() if path.is_file() else None
            except OSError:
                current = None
            if current is not None and self._digest(current) == self._digest(original):
                continue
            stato = "rimosso" if current is None else "modificato"
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(original)
            except OSError as exc:
                incidents.append(f"{path}: {stato} e ripristino FALLITO ({exc})")
                continue
            incident = f"{path.name}: {stato} da uno strumento, ripristinato dal sigillo"
            incidents.append(incident)
            self._on_incident(incident)
        return incidents
