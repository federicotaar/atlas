"""Superficie comandi ristretta del tool shell (enforcement di PREVENZIONE).

NESSUNA shell: il comando viene spezzato in argv (shlex) ed eseguito
direttamente con stdin chiuso. Senza interprete non esistono pipe, redirect,
variabili, subshell o comandi calcolati: la classe intera di bypass lessicali
non e' filtrata, e' ASSENTE. Non esiste un modo "shell grezza": nessun flag,
nessuna scappatoia - quello che non e' enforcement affidabile e' rimosso,
non affidato a euristiche piu' grandi.

Regole (tutte a monte dell'esecuzione):
- eseguibile in ALLOWED, risolto da un PATH fidato fisso (/usr/local/bin,
  /usr/bin, /bin): un binario dentro il workspace non viene mai eseguito;
- niente flag che trasformano la lettura in scrittura/esecuzione
  (find -exec, git -c/--output, sort -o, ...) e niente forme ricorsive:
  grep/rg/find non possono attraversare il workspace e leggere discendenti
  protetti (.env, app/agent/) - le ricerche vanno solo su file espliciti;
- git solo sottocomandi di lettura, senza patch/diff impliciti, senza
  revision:path (esfiltrerebbe contenuto protetto tracciato), con --no-pager
  forzato e pager esterno mai ereditato (l'ambiente figlio e' gia' minimale);
- ogni argomento che e' un percorso viene risolto (seguendo i symlink) PRIMA
  dell'esecuzione: i percorsi protetti (.env e tutto app/agent/ + agent_cli)
  sono SEMPRE negati; il fuori-workspace richiede approvazione esplicita del
  SINGOLO comando in ogni modo (yolo compreso) e non sblocca nient'altro.

Limite dichiarato: singolo processo sequenziale, nessuno scrittore concorrente
nel workspace da parte dell'agente: la finestra validate->exec non e'
sfruttabile dall'agente stesso. Un processo esterno ostile resta fuori dal
modello di minaccia dello strumento.
"""
from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path

TRUSTED_PATH = "/usr/local/bin:/usr/bin:/bin"

# Sola lettura/ispezione su argomenti espliciti. Niente find/rg ricorsivi:
# attraverserebbero discendenti protetti (esfiltrazione indiretta).
ALLOWED: frozenset[str] = frozenset({
    "ls", "cat", "head", "tail", "pwd", "echo", "grep", "wc",
    "sort", "uniq", "jq", "file", "stat", "du", "df", "date", "which",
    "git", "diff", "basename", "dirname", "realpath", "true", "false",
    "env", "printenv", "hostname", "uname", "id", "whoami",
})
GIT_READONLY: frozenset[str] = frozenset({
    "status", "log", "branch", "rev-parse", "describe", "diff",
})

SHELL_OPERATORS = ("|", ";", "&", ">", "<", "`", "$", "(", ")", "\n")

# Flag che trasformano un comando "di lettura" in scrittura, esecuzione o
# lettura indiretta di discendenti protetti: presenza => non disponibile.
DANGEROUS_FLAGS: dict[str, frozenset[str]] = {
    "ls": frozenset({"-R", "--recursive"}),
    "grep": frozenset({"-r", "-R", "--recursive", "-d", "--directories"}),
    "sort": frozenset({"-o", "--output"}),
    "date": frozenset({"-s", "--set"}),
    "git": frozenset({
        "-c", "-d", "-D", "-m", "-M", "-C", "-f", "-p", "-u",
        "--force", "--delete", "--move", "--copy", "--patch", "--output",
        "--git-dir", "--exec-path", "--work-tree", "--unset-upstream",
        "--set-upstream-to", "--edit-description", "--ext-diff",
    }),
}


@dataclass
class Validation:
    ok: bool
    reason: str = ""
    argv: list[str] = field(default_factory=list)
    outside_paths: list[str] = field(default_factory=list)


def _looks_like_path(token: str, workspace: Path) -> bool:
    if token.startswith(("/", "~", ".")):
        return True
    if "/" in token:
        return True
    return (workspace / token).exists()


def validate(command: str, workspace: Path, resolve, is_protected) -> Validation:
    """resolve(path)->(assoluto, dentro); is_protected(path)->bool.
    Nessuna esecuzione qui: solo classificazione e costruzione argv."""
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        return Validation(ok=False, reason=f"comando non parsabile: {exc}")
    if not argv:
        return Validation(ok=False, reason="comando vuoto")

    if any(op in command for op in SHELL_OPERATORS):
        return Validation(
            ok=False,
            reason=("superficie ristretta: pipe, redirect, variabili ($...) e "
                    "subshell non esistono qui (nessuna shell)"))

    exe_name = Path(argv[0]).name
    if exe_name not in ALLOWED:
        return Validation(
            ok=False,
            reason=f"eseguibile '{exe_name}' non disponibile nella superficie ristretta")
    exe = shutil.which(exe_name, path=TRUSTED_PATH)
    if exe is None:
        return Validation(ok=False, reason=f"'{exe_name}' non presente nei percorsi fidati")
    real = Path(exe).resolve()
    if real == workspace or workspace in real.parents:
        return Validation(ok=False, reason=f"'{exe_name}' risolverebbe dentro il workspace: rifiutato")

    rest = argv[1:]
    if exe_name == "git":
        sub = next((a for a in rest if not a.startswith("-")), "")
        if sub not in GIT_READONLY:
            return Validation(
                ok=False,
                reason=f"git {sub or '?'} non disponibile: solo lettura "
                       f"({', '.join(sorted(GIT_READONLY))})")
        if any(":" in a for a in rest):
            return Validation(
                ok=False,
                reason="git con sintassi revisione:percorso non disponibile: "
                       "leggerebbe contenuto protetto tracciato")
        if sub == "diff" and not any(not a.startswith("-") and a != "diff"
                                     for a in rest):
            return Validation(
                ok=False,
                reason="git diff senza pathspec esplicito non disponibile: "
                       "mostrerebbe anche file protetti")
        rest = ["--no-pager", *rest]  # mai pager esterni

    dangerous = DANGEROUS_FLAGS.get(exe_name, frozenset())
    for token in rest:
        base = token.split("=", 1)[0]
        if base in dangerous or any(
                token.startswith(f + "=") or
                (len(f) == 2 and token.startswith(f) and token != f)
                for f in dangerous if f.startswith("-")):
            return Validation(
                ok=False,
                reason=f"flag '{token}' non disponibile per {exe_name} "
                       f"nella superficie ristretta")

    # grep/ricerche: solo file espliciti (niente ricorsione ne' stdin implicito
    # come sorgente dati di file arbitrari).
    if exe_name == "grep":
        files = [a for a in rest if not a.startswith("-")]
        if len(files) < 2:
            return Validation(
                ok=False,
                reason="grep richiede pattern e almeno un file esplicito")

    outside: list[str] = []
    for token in rest:
        if token.startswith("-"):
            continue
        if token.startswith("~"):
            target = Path(token).expanduser().resolve()
            inside = target == workspace or workspace in target.parents
        else:
            if not _looks_like_path(token, workspace):
                continue
            target, inside = resolve(workspace, token)
        if is_protected(target):
            return Validation(
                ok=False, reason=f"percorso protetto ({target.name}): negato sempre")
        if not inside:
            outside.append(str(target))
    return Validation(ok=True, argv=[str(real), *rest], outside_paths=outside)
