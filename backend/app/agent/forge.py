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
2. esecuzione SEMPRE e solo sotto bubblewrap (bwrap): namespace senza
   rete, filesystem di sistema in sola lettura, scrittura possibile solo
   nella dir delle proposte, ambiente pulito dai segreti. Se bwrap non e'
   disponibile l'esecuzione e' DISABILITATA (fail-closed): la proposta
   resta registrata con isolation="unavailable" e non si attiva. Il gate
   statico non e' una boundary di sicurezza: l'unico perimetro e' bwrap.

Il codice generato non viene MAI eseguito nel processo dell'agente: ne'
ai test, ne' all'approvazione (il controllo dell'interfaccia gira in
bwrap), ne' a runtime (ogni chiamata di un tool approvato e' un processo
bwrap che risponde via JSON su stdout). Niente segreti o percorsi
protetti nei mount: dentro si vedono solo /usr /lib /bin in lettura,
l'interprete e tools_generated.
"""
from __future__ import annotations

import json
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
    """Comando bubblewrap per l'isolamento vero, o None se bwrap manca.

    Mount visibili nel namespace: /usr /lib /lib64 /bin (read-only),
    l'interprete Python e le sue site-packages (read-only), il runner
    (file, read-only) e workdir montata su /work (UNICA parte scrivibile).
    Niente home, workspace dell'utente, .env, data/ o codice protetto:
    semplicemente non esistono nel namespace. Rete assente (--unshare-all).
    """
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
    runner = Path(__file__).with_name("_forge_runner.py")
    if runner.exists():
        binds += ["--ro-bind", str(runner), "/runner.py"]
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


def _run_in_bwrap(workdir: Path, argv: list[str],
                  timeout: int = 60,
                  stdin: str = "") -> subprocess.CompletedProcess | None:
    """Esegue argv dentro bwrap; None se bwrap non e' disponibile."""
    cmd_prefix = _bwrap_cmd(workdir)
    if cmd_prefix is None:
        return None
    return subprocess.run(cmd_prefix + argv, timeout=timeout, input=stdin,
                          capture_output=True, text=True)


def inspect_interface(workdir: Path, name: str,
                      timeout: int = 30) -> dict | None:
    """Carica il modulo in bwrap e ritorna l'interfaccia come dict.

    None se bwrap non e' disponibile (attivazione disabilitata);
    {"error": ...} se il modulo non si carica.
    """
    try:
        proc = _run_in_bwrap(workdir,
                             ["/runner.py", "inspect", name],
                             timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {"error": str(exc)}
    if proc is None:
        return None
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (proc.stdout + proc.stderr).strip()[-300:]}


def invoke_isolated(workdir: Path, name: str, args: dict,
                    timeout: int = 60) -> str:
    """Esegue run(args, None) del tool approvato in un processo bwrap.

    Il tool non ha accesso al contesto dell'agente, alla rete, ai segreti
    o ai file fuori da tools_generated: risponde via JSON su stdout.
    """
    try:
        proc = _run_in_bwrap(workdir, ["/runner.py", "invoke", name],
                             timeout=timeout,
                             stdin=json.dumps(args, ensure_ascii=False))
    except subprocess.TimeoutExpired:
        return f"errore: il tool '{name}' ha superato il timeout"
    if proc is None:
        return (f"errore: isolamento non disponibile, il tool '{name}' "
                "non puo' essere eseguito su questo host")
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return f"errore: output non valido dal tool: {proc.stderr[-200:]}"
    if not payload.get("ok"):
        return f"errore nel tool '{name}': {payload.get('error', '?')}"
    result = payload.get("result")
    return result if isinstance(result, str) else json.dumps(
        result, ensure_ascii=False)


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
        if cmd_prefix is None:
            # fail-closed: senza isolamento reale NON si esegue nulla
            return {"ok": False, "isolation": "unavailable",
                    "out": ("esecuzione disabilitata: bubblewrap (bwrap) non "
                            "disponibile su questo host; la proposta resta "
                            "registrata ma non viene eseguita ne' attivata")}
        cmd = cmd_prefix + ["-m", "pytest", f"/work/test_{name}.py",
                            "-q", "--no-header", "-p", "no:cacheprovider"]
        try:
            proc = subprocess.run(
                cmd, timeout=timeout, capture_output=True, text=True)
            out = (proc.stdout + proc.stderr).strip()
            return {"ok": proc.returncode == 0, "out": out,
                    "isolation": "bwrap"}
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return {"ok": False, "out": str(exc), "isolation": "bwrap"}

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
        # Il controllo dell'interfaccia carica il modulo DENTRO bwrap:
        # mai eseguire codice generato nel processo dell'agente.
        iface = inspect_interface(self._dir, name)
        if iface is None:
            return {"ok": False,
                    "error": ("isolamento non disponibile: attivazione "
                              "disabilitata finche' bwrap non c'e'")}
        if "error" in iface:
            return {"ok": False,
                    "error": f"il modulo non si carica: {iface['error']}"}
        missing = [attr for attr in REQUIRED_INTERFACE if attr not in iface]
        if missing:
            return {"ok": False,
                    "error": f"interfaccia incompleta: manca {missing}"}
        info["status"] = "approved"
        info["tests_ok"] = True
        info["interface"] = {k: iface[k] for k in
                             ("name", "description", "parameters")}
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
        """I tool approvati come DESCRITTORI per la registrazione.

        Il modulo generato non viene importato nel processo: l'interfaccia
        (name/description/parameters) e' quella registrata all'approvazione
        e run e' una chiusura che esegue il tool in un processo bwrap.
        """
        loaded = {}
        for item in self.list():
            if item["status"] != "approved":
                continue
            iface = item.get("interface") or {}
            if not all(k in iface for k in
                       ("name", "description", "parameters")):
                continue  # approvato prima del controllo in bwrap: inattivo
            tool_name = item["name"]

            def _run(args: dict, ctx: object,
                     _name: str = tool_name) -> object:
                return invoke_isolated(self._dir, _name, args)

            loaded[tool_name] = {"name": iface["name"],
                                 "description": iface["description"],
                                 "parameters": iface["parameters"],
                                 "run": _run}
        return loaded
