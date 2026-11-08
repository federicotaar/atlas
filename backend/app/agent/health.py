"""Self-test periodico e registro incidenti (spec sez. 8, build order 12).

HealthChecker.self_test() verifica: provider raggiungibili, canali connessi,
spazio disco, integrita' dei database SQLite. Scrive il risultato in
logs/health.log; ogni fallimento finisce anche in logs/errors.log, il
registro unico degli incidenti usato dal riepilogo giornaliero.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path


class IncidentLog:
    """logs/errors.log: una riga JSON per incidente."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()

    def record(self, kind: str, detail: str) -> None:
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 "kind": kind, "detail": detail}
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def since(self, day: str) -> list[dict]:
        """Incidenti di un giorno ("YYYY-MM-DD"), per il riepilogo."""
        if not self._path.exists():
            return []
        out = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("ts", "").startswith(day):
                out.append(entry)
        return out


class HealthChecker:
    """Esegue il self-test e registra esito e incidenti."""

    def __init__(self, workspace: Path, incidents: IncidentLog | None = None):
        self._workspace = workspace
        self._health_log = workspace / "logs" / "health.log"
        self._health_log.parent.mkdir(parents=True, exist_ok=True)
        self.incidents = incidents or IncidentLog(workspace / "logs" / "errors.log")
        self._lock = threading.Lock()

    def check_disk(self) -> dict:
        usage = shutil.disk_usage(self._workspace)
        free_mb = usage.free // (1024 * 1024)
        ok = free_mb >= 100
        return {"ok": ok, "free_mb": free_mb}

    def check_db(self, path: Path) -> dict:
        if not path.exists():
            return {"ok": True, "detail": "assente (verra' creato)"}
        try:
            db = sqlite3.connect(str(path))
            result = db.execute("PRAGMA integrity_check").fetchone()[0]
            db.close()
            ok = result == "ok"
            return {"ok": ok, "detail": result}
        except sqlite3.Error as exc:
            return {"ok": False, "detail": str(exc)}

    def check_provider(self, registry, name: str) -> dict:
        """Ping al provider: una chat minima. MAI con key finte."""
        try:
            provider = registry.get(name)
        except Exception as exc:
            return {"ok": False, "detail": f"non registrato: {exc}"}
        try:
            response = provider.chat(
                [{"role": "user", "content": "rispondi solo: ok"}], max_tokens=8)
            return {"ok": True, "detail": (response.text or "")[:60]}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)[:200]}

    def self_test(self, registry=None, active: str | None = None,
                  channels: dict | None = None) -> dict:
        """Self-test completo; registra e ritorna il report."""
        report: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        report["disk"] = self.check_disk()
        report["databases"] = {
            name: self.check_db(self._workspace / rel)
            for name, rel in (("memory", "memory.db"),
                              ("jobs", "data/jobs.db"),
                              ("proactive", "data/proactive.db"),
                              ("habits", "data/habits.db"))}
        if registry is not None and active:
            report["provider"] = self.check_provider(registry, active)
        if channels:
            report["channels"] = {
                name: {"ok": bool(getattr(ch, "is_connected", lambda: True)())}
                for name, ch in channels.items()}

        failures = []
        if not report["disk"]["ok"]:
            failures.append(f"disco quasi pieno: {report['disk']['free_mb']}MB")
        for name, check in report["databases"].items():
            if not check["ok"]:
                failures.append(f"database {name}: {check['detail']}")
        if "provider" in report and not report["provider"]["ok"]:
            failures.append(f"provider {active}: {report['provider']['detail']}")
        for name, check in report.get("channels", {}).items():
            if not check["ok"]:
                failures.append(f"canale {name} disconnesso")
        report["ok"] = not failures
        report["failures"] = failures

        with self._lock:
            with self._health_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(report, ensure_ascii=False) + "\n")
        for failure in failures:
            self.incidents.record("self-test", failure)
        return report
