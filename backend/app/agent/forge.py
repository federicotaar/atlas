"""Tool auto-generati (spec sez. 8, build order 14).

L'agente puo' PROPORRE nuovi tool in tools_generated/: scrive codice e
test, li esegue in una sotto-directory isolata con ambiente scrubbato dai
segreti, e manda il diff a Fred. Il tool resta INATTIVO finche' Fred non
lo approva esplicitamente (/tools approve <nome>): nessuna attivazione
automatica, mai. Le proposte possono toccare SOLO tools_generated/ -
mai app/, la sicurezza, .env o i log (perimetro protetto, spec sez. 8
hard limits).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
REQUIRED_INTERFACE = ("name", "description", "parameters", "run")


class ToolForge:
    def __init__(self, workspace: Path):
        self._dir = workspace / "tools_generated"
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / "__init__.py").touch(exist_ok=True)
        self._meta = self._dir / ".proposals.json"

    def _load_meta(self) -> dict:
        if self._meta.exists():
            try:
                return json.loads(self._meta.read_text())
            except ValueError:
                return {}
        return {}

    def _save_meta(self, meta: dict) -> None:
        self._meta.write_text(json.dumps(meta, indent=1, ensure_ascii=False))

    def propose(self, name: str, code: str, tests: str,
                now: float | None = None) -> dict:
        """Crea una proposta: file + test, suite di test nel sandbox.
        Ritorna lo stato con l'esito dei test."""
        if not NAME_RE.match(name):
            raise ValueError(f"nome tool non valido: {name!r}")
        now = time.time() if now is None else now
        tool_path = self._dir / f"{name}.py"
        test_path = self._dir / f"test_{name}.py"
        if tool_path.exists() and self._load_meta().get(name, {}).get(
                "status") == "approved":
            raise ValueError(f"tool {name} gia' approvato: non sovrascrivo")
        tool_path.write_text(code)
        test_path.write_text(tests)
        result = self.run_tests(name)
        meta = self._load_meta()
        meta[name] = {"status": "pending", "ts": now,
                      "tests_ok": result["ok"], "tests_out": result["out"][-800:]}
        self._save_meta(meta)
        return {"name": name, "tests_ok": result["ok"],
                "tests_out": result["out"], "tool_path": str(tool_path),
                "test_path": str(test_path)}

    def run_tests(self, name: str, timeout: int = 60) -> dict:
        """Test in sandbox: processo separato, cwd dentro tools_generated,
        ambiente senza segreti, timeout duro."""
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "PYTHONPATH": str(self._dir), "HOME": str(self._dir)}
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", f"test_{name}.py", "-q",
                 "--no-header", "-p", "no:cacheprovider"],
                cwd=self._dir, env=env, timeout=timeout,
                capture_output=True, text=True)
            out = (proc.stdout + proc.stderr).strip()
            return {"ok": proc.returncode == 0, "out": out}
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return {"ok": False, "out": str(exc)}

    def list(self) -> list[dict]:
        return [{"name": name, **info}
                for name, info in sorted(self._load_meta().items())]

    def diff(self, name: str) -> str:
        """Il testo della proposta da mostrare per l'approvazione."""
        tool_path = self._dir / f"{name}.py"
        if not tool_path.exists():
            return ""
        return tool_path.read_text()

    def approve(self, name: str) -> dict:
        """Attiva un tool DOPO approvazione esplicita di Fred. Ricarica i
        test: se non passano, niente attivazione."""
        meta = self._load_meta()
        info = meta.get(name)
        if info is None:
            return {"ok": False, "error": f"nessuna proposta '{name}'"}
        result = self.run_tests(name)
        if not result["ok"]:
            info["tests_ok"] = False
            info["tests_out"] = result["out"][-800:]
            self._save_meta(meta)
            return {"ok": False,
                    "error": f"i test non passano: {result['out'][-200:]}"}
        namespace: dict = {}
        try:
            code = (self._dir / f"{name}.py").read_text()
            exec(compile(code, f"<tools_generated/{name}.py>", "exec"),
                 namespace)
        except Exception as exc:
            return {"ok": False, "error": f"il modulo non si carica: {exc}"}
        missing = [attr for attr in REQUIRED_INTERFACE
                   if attr not in namespace]
        if missing:
            return {"ok": False,
                    "error": f"interfaccia incompleta: manca {missing}"}
        info["status"] = "approved"
        info["tests_ok"] = True
        self._save_meta(meta)
        return {"ok": True, "name": name}

    def reject(self, name: str) -> bool:
        meta = self._load_meta()
        if name not in meta:
            return False
        meta[name]["status"] = "rejected"
        self._save_meta(meta)
        return True

    def load_approved(self) -> dict[str, dict]:
        """I tool approvati come namespace caricati (per la registrazione)."""
        loaded = {}
        for item in self.list():
            if item["status"] != "approved":
                continue
            namespace: dict = {}
            try:
                code = (self._dir / f"{item['name']}.py").read_text()
                exec(compile(code, f"<{item['name']}.py>", "exec"), namespace)
                if all(attr in namespace for attr in REQUIRED_INTERFACE):
                    loaded[item["name"]] = namespace
            except Exception:
                continue  # un tool che non si carica resta inattivo
        return loaded
