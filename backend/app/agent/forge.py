"""Tool auto-generati (spec sez. 8, build order 14).

L'agente puo' PROPORRE nuovi tool in tools_generated/: scrive codice e
test, li esegue in isolamento, e manda il diff a Fred. Il tool resta
INATTIVO finche' Fred non lo approva esplicitamente (/tools approve
<nome>): nessuna attivazione automatica, mai. Le proposte possono toccare
SOLO tools_generated/ - mai app/, la sicurezza, .env o i log (perimetro
protetto, spec sez. 8 hard limits).

Isolamento REALE, a due livelli:
1. static gate (sempre): il codice che importa subprocess/socket/ctypes/
   multiprocessing/signal o usa __import__/eval/exec/compile non arriva
   nemmeno ai test;
2. esecuzione dei test: con bubblewrap (bwrap) i test girano in un
   namespace senza rete, filesystem di sistema in sola lettura, scrittura
   possibile solo nella dir della proposta, ambiente pulito dai segreti;
   senza bwrap si ripiega su rlimit + env scrubbed e la proposta resta
   marcata isolation="reduced" (MAI spacciata per sandbox completa).

Il codice NON approvato non viene mai eseguito con accesso a token, al
workspace reale o ai file dell'utente: i test vedono solo tools_generated.
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

# Static gate: moduli e primitive incompatibili con codice generato non
# verificato. Non e' una sandbox: e' il primo filtro, l'esecuzione isolata
# e' il secondo.
BLOCKED_IMPORTS = {"subprocess", "socket", "ctypes", "multiprocessing",
                   "signal", "pty", "shutil"}
BLOCKED_CALLS = {"__import__", "eval", "exec", "compile", "breakpoint"}


def static_gate(code: str) -> str | None:
    """None se il codice passa, altrimenti il motivo del rifiuto."""
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"sintassi non valida: {exc}"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([a.name.split(".")[0] for a in node.names]
                     if isinstance(node, ast.Import)
                     else [(node.module or "").split(".")[0]])
            for name in names:
                if name in BLOCKED_IMPORTS:
                    return f"import vietato nel codice generato: {name}"
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BLOCKED_CALLS:
                return f"chiamata vietata nel codice generato: {func.id}"
    return None


def _bwrap_cmd(workdir: Path) -> list[str] | None:
    """Comando bubblewrap per l'isolamento vero, o None se bwrap manca."""
    import shutil as _sh
    bwrap = _sh.which("bwrap")
    if bwrap is None:
        return None
    # Eseguiamo il binario reale (symlink risolti): nel namespace vuoto di
    # bwrap la catena di symlink del venv non esisterebbe. Il venv fornisce
    # solo le site-packages via PYTHONPATH. Layout POSIX del venv richiesto,
    # altrimenti si ricade sull'isolamento ridotto.
    venv_root = Path(sys.executable).parent.parent
    real = Path(sys.executable).resolve()
    ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    if not (venv_root / "lib" / ver / "site-packages").is_dir():
        return None
    binds = []
    for path in ("/usr", "/lib", "/lib64", "/bin"):
        if Path(path).exists():
            binds += ["--ro-bind", path, path]
    # L'interprete e il venv (anche sotto /tmp) vanno su percorsi propri:
    # la tmpfs /tmp qui sotto coprirebbe un bind dentro /tmp.
    binds += ["--ro-bind", str(venv_root), "/pyenv"]
    binds += ["--ro-bind", str(real.parent.parent), "/pyreal"]
    return [bwrap, "--unshare-all", "--die-with-parent", "--clearenv",
            "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "HOME", "/work",
            "--setenv", "PYTHONPATH", f"/work:/pyenv/lib/{ver}/site-packages",
            *binds,
            "--bind", str(workdir), "/work",
            "--tmpfs", "/tmp",
            "--dev", "/dev",
            "--chdir", "/work",
            "--", f"/pyreal/bin/{real.name}"]


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
        gate_error = static_gate(code)
        if gate_error is not None:
            # codice respinto: non lasciamo file eseguibili in giro
            meta = self._load_meta()
            meta[name] = {"status": "rejected", "ts": now,
                          "tests_ok": False, "tests_out": gate_error,
                          "isolation": "static-gate"}
            self._save_meta(meta)
            return {"name": name, "tests_ok": False, "tests_out": gate_error,
                    "tool_path": None, "test_path": None}
        tool_path.write_text(code)
        test_path.write_text(tests)
        result = self.run_tests(name)
        meta = self._load_meta()
        meta[name] = {"status": "pending", "ts": now,
                      "tests_ok": result["ok"], "tests_out": result["out"][-800:],
                      "isolation": result["isolation"]}
        self._save_meta(meta)
        return {"name": name, "tests_ok": result["ok"],
                "tests_out": result["out"], "tool_path": str(tool_path),
                "test_path": str(test_path),
                "isolation": result["isolation"]}

    def run_tests(self, name: str, timeout: int = 60) -> dict:
        """Test in isolamento: bwrap (senza rete, FS di sistema read-only,
        scrivibile solo la dir della proposta, env pulito) se disponibile;
        altrimenti rlimit + env scrubbed marcato isolation="reduced"."""
        cmd_prefix = _bwrap_cmd(self._dir)
        if cmd_prefix is not None:
            cmd = cmd_prefix + ["-m", "pytest", f"/work/test_{name}.py",
                                "-q", "--no-header", "-p", "no:cacheprovider"]
            env, cwd, preexec = None, None, None
            isolation = "bwrap"
        else:
            import resource

            def limits() -> None:
                resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
                resource.setrlimit(resource.RLIMIT_AS,
                                   (512 * 1024 * 1024,) * 2)
                resource.setrlimit(resource.RLIMIT_FSIZE,
                                   (10 * 1024 * 1024,) * 2)
                resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

            cmd = [sys.executable, "-m", "pytest", f"test_{name}.py", "-q",
                   "--no-header", "-p", "no:cacheprovider"]
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                   "PYTHONPATH": str(self._dir), "HOME": str(self._dir)}
            cwd, preexec, isolation = self._dir, limits, "reduced"
        try:
            proc = subprocess.run(
                cmd, cwd=cwd, env=env, timeout=timeout,
                preexec_fn=preexec, capture_output=True, text=True)
            out = (proc.stdout + proc.stderr).strip()
            return {"ok": proc.returncode == 0, "out": out,
                    "isolation": isolation}
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return {"ok": False, "out": str(exc), "isolation": isolation}

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
        if info.get("status") == "rejected":
            return {"ok": False,
                    "error": "proposta gia' scartata dallo static gate"}
        gate_error = static_gate((self._dir / f"{name}.py").read_text())
        if gate_error is not None:
            return {"ok": False, "error": gate_error}
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
