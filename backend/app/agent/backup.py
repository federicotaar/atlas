"""Backup automatici e ripristino dei database (spec sez. 8, build order 12).

BackupManager.backup() copia i database SQLite in backups/ (rotazione: si
tengono gli ultimi 7 giorni). All'avvio, se un database risulta corrotto
(PRAGMA integrity_check), viene ripristinato dall'ultimo backup integro
invece di ripartire da zero o peggio, lavorare su dati guasti.
"""
from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

DATABASES = ("memory.db", "data/jobs.db", "data/proactive.db", "data/habits.db")
KEEP_DAYS = 7


def integrity_ok(path: Path) -> bool:
    if not path.exists():
        return True  # assente: verra' creato nuovo, niente da ripristinare
    try:
        db = sqlite3.connect(str(path))
        result = db.execute("PRAGMA integrity_check").fetchone()[0]
        db.close()
        return result == "ok"
    except sqlite3.Error:
        return False


class BackupManager:
    def __init__(self, workspace: Path, keep_days: int = KEEP_DAYS):
        self._workspace = workspace
        self._dir = workspace / "backups"
        self._keep = keep_days

    def backup(self, now: float | None = None) -> list[Path]:
        """Copia i database esistenti in backups/<YYYY-MM-DD>/ e ruota."""
        now = time.time() if now is None else now
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        target = self._dir / day
        target.mkdir(parents=True, exist_ok=True)
        written = []
        for rel in DATABASES:
            src = self._workspace / rel
            if src.exists() and integrity_ok(src):
                dest = target / rel.replace("/", "_")
                shutil.copy2(src, dest)
                written.append(dest)
        self._rotate(now)
        return written

    def _rotate(self, now: float) -> None:
        cutoff = now - self._keep * 86400
        for child in self._dir.iterdir():
            if child.is_dir():
                try:
                    day_ts = time.mktime(time.strptime(child.name, "%Y-%m-%d"))
                except ValueError:
                    continue
                if day_ts < cutoff:
                    shutil.rmtree(child)

    def backups_for(self, db_rel: str) -> list[Path]:
        """Copie disponibili per un database, dalla piu' recente."""
        name = db_rel.replace("/", "_")
        found = sorted(self._dir.glob(f"*/{name}"), reverse=True)
        return found

    def restore_latest(self, db_rel: str) -> tuple[Path, Path] | None:
        """Ripristina l'ultimo backup INTEGRO di un database SENZA buttare
        l'originale: il file corrotto viene conservato accanto (suffisso
        .corrupt-<data>) per eventuale recupero manuale. Ritorna
        (backup usato, originale conservato) o None se nessun backup
        utilizzabile."""
        dest = self._workspace / db_rel
        for candidate in self.backups_for(db_rel):
            if integrity_ok(candidate):
                dest.parent.mkdir(parents=True, exist_ok=True)
                kept = dest.with_name(
                    dest.name + ".corrupt-"
                    + time.strftime("%Y%m%d-%H%M%S"))
                shutil.move(str(dest), kept)
                shutil.copy2(candidate, dest)
                return candidate, kept
        return None

    def verify_and_restore(self, incidents=None,
                           now: float | None = None) -> list[str]:
        """Controllo d'avvio: ogni database corrotto viene ripristinato
        dall'ultimo backup integro, con l'originale conservato e la
        perdita stimata ESPLICITA (eta' del backup = dati piu' recenti
        persi). Ritorna messaggi leggibili da mostrare all'utente."""
        now = time.time() if now is None else now
        messages = []
        for rel in DATABASES:
            path = self._workspace / rel
            if not path.exists() or integrity_ok(path):
                continue
            result = self.restore_latest(rel)
            if result is not None:
                source, kept = result
                backup_day = source.parent.name
                try:
                    age_days = int((now - time.mktime(
                        time.strptime(backup_day, "%Y-%m-%d"))) // 86400)
                except ValueError:
                    age_days = -1
                age_txt = ("di oggi" if age_days == 0 else
                           f"di {age_days} giorni fa")
                msg = (f"database {rel} corrotto: ripristinato il backup "
                       f"{age_txt} ({backup_day}); i record piu' recenti "
                       f"del backup andati persi; l'originale corrotto e' "
                       f"conservato in {kept.name} per recupero manuale")
                messages.append(msg)
                if incidents is not None:
                    incidents.record("database", msg)
            else:
                msg = (f"database {rel} corrotto e NESSUN backup integro "
                       f"disponibile: riparte vuoto; l'originale resta al "
                       f"suo posto per recupero manuale")
                messages.append(msg)
                if incidents is not None:
                    incidents.record("database", msg)
        return messages
