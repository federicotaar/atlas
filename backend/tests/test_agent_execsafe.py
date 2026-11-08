"""Superficie ristretta shell: PREVENZIONE, non ripristino.

Ogni test dimostra che un comando pericoloso NON PARTE (deny a monte o esito
esplicito "non disponibile"), non che i danni vengono riparati dopo. Non
esiste una modalita' shell grezza: nessun flag puo' riaprirla.
"""
from __future__ import annotations

from pathlib import Path

from app.agent.execsafe import validate
from app.agent.permissions import Decision, Mode, decide
from app.agent.safety import SecretFilter, resolve_in_workspace
from app.agent.tools import ToolContext, _is_protected_path, _shell


def v(ws: Path, cmd: str):
    return validate(cmd, ws, resolve_in_workspace, _is_protected_path)


def ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws, secrets=SecretFilter())


# --- assenza strutturale di pipe/redirect/variabili/subshell ---

def test_redirect_to_safety_file_never_executes(tmp_path):
    assert not v(tmp_path, "echo hacked > app/agent/safety.py").ok


def test_computed_paths_never_execute(tmp_path):
    for cmd in ("cat $F/.env", "cat $(pwd)/x", "cat `which sh`", "ls $HOME"):
        assert not v(tmp_path, cmd).ok, cmd


def test_pipes_and_separators_never_execute(tmp_path):
    for cmd in ("ls | sh", "echo a; rm -rf /", "echo a && cat .env"):
        assert not v(tmp_path, cmd).ok, cmd


def test_decide_denies_shell_operators(tmp_path):
    decision, reason = decide(Mode.YOLO, "shell",
                              {"command": "echo x > /tmp/evil"}, tmp_path)
    assert decision is Decision.DENY
    assert "nessuna shell" in reason


# --- eseguibili fuori allowlist ---

def test_dangerous_executables_unavailable(tmp_path):
    for cmd in ("rm -rf /tmp", "kill 1", "pkill python", "curl http://x",
                "python3 -c 'import os'", "chmod 777 /", "sh -c id",
                "./script.sh", "/bin/rm x", "find . -name x", "rg pattern"):
        check = v(tmp_path, cmd)
        assert not check.ok, cmd


def test_trusted_path_resolution(tmp_path):
    # un binario 'ls' nel workspace non viene mai eseguito
    fake = tmp_path / "ls"
    fake.write_text("#!/bin/sh\necho FAKE", encoding="utf-8")
    fake.chmod(0o755)
    check = v(tmp_path, "ls")
    assert check.ok and not str(check.argv[0]).startswith(str(tmp_path))
    out = _shell({"command": "ls"}, ctx(tmp_path))
    assert "FAKE" not in out


# --- accesso indiretto a contenuto protetto ---

def test_recursive_forms_unavailable(tmp_path):
    assert not v(tmp_path, "ls -R").ok
    assert not v(tmp_path, "grep -r KEY .").ok
    assert not v(tmp_path, "grep --recursive KEY src").ok


def test_grep_requires_explicit_files(tmp_path):
    assert not v(tmp_path, "grep KEY").ok
    (tmp_path / "a.txt").write_text("KEY=1", encoding="utf-8")
    check = v(tmp_path, "grep KEY a.txt")
    assert check.ok


def test_git_indirect_reads_narrowed(tmp_path):
    # show/blame/ls-files non accettati; patch impliciti negati; rev:path negato
    for cmd in ("git show HEAD", "git blame app/x.py", "git log -p",
                "git log --patch", "git diff", "git show HEAD:app/agent/safety.py",
                "git log HEAD:.env", "git -c alias.st=!rm status",
                "git branch -D main", "git commit -m x"):
        assert not v(tmp_path, cmd).ok, cmd
    assert v(tmp_path, "git status").ok
    assert v(tmp_path, "git log --oneline").ok
    assert "--no-pager" in v(tmp_path, "git status").argv


def test_sort_output_and_rg_style_flags_blocked(tmp_path):
    assert not v(tmp_path, "sort -o /tmp/out x").ok
    assert not v(tmp_path, "sort --output=/tmp/out x").ok


# --- path validation con symlink risolti ---

def test_outside_path_asks_in_every_mode(tmp_path):
    for mode in (Mode.ASK, Mode.AUTO_SAFE, Mode.YOLO):
        decision, reason = decide(mode, "shell",
                                  {"command": "cat /etc/hostname"}, tmp_path)
        assert decision is Decision.ASK, mode
        assert "/etc/hostname" in reason


def test_outside_path_via_dotdot_resolves(tmp_path):
    ws = tmp_path / "sub"
    ws.mkdir()
    check = v(ws, "cat ../../etc/hostname")
    assert check.ok and check.outside_paths


def test_symlink_escape_resolves_and_asks(tmp_path):
    (tmp_path / "link").symlink_to(Path("/etc"))
    check = v(tmp_path, "cat link/hostname")
    assert check.ok and any("/etc" in p for p in check.outside_paths)


def test_protected_layer_denied_always(tmp_path):
    # .env, app/agent/*, agent_cli.py: mai leggibili/scrivibili via shell
    for mode in (Mode.ASK, Mode.YOLO):
        for cmd in ("cat .env", "cat app/agent/safety.py",
                    "cat app/agent/execsafe.py", "cat app/agent/seal.py",
                    "cat app/agent_cli.py", "head app/agent/tools.py"):
            d, _ = decide(mode, "shell", {"command": cmd}, tmp_path)
            assert d is Decision.DENY, (mode, cmd)


def test_symlink_to_protected_denied(tmp_path):
    target = tmp_path / "backend" / "app" / "agent"
    target.mkdir(parents=True)
    (target / "safety.py").write_text("x", encoding="utf-8")
    (tmp_path / "sc").symlink_to(target / "safety.py")
    assert not v(tmp_path, "cat sc").ok


# --- superficie funzionante ---

def test_benign_commands_allowed_and_run(tmp_path):
    (tmp_path / "nota.txt").write_text("ciao mondo", encoding="utf-8")
    assert "ciao mondo" in _shell({"command": "cat nota.txt"}, ctx(tmp_path))
    assert "nota.txt" in _shell({"command": "ls"}, ctx(tmp_path))


def test_unavailable_command_returns_explicit_message(tmp_path):
    assert "NON DISPONIBILE" in _shell({"command": "curl http://x"}, ctx(tmp_path))
    assert "NON DISPONIBILE" in _shell({"command": "echo x | sh"}, ctx(tmp_path))


def test_guard_files_unreadable_via_shell(tmp_path):
    out = _shell({"command": "cat .env"}, ctx(tmp_path))
    assert "NON DISPONIBILE" in out or "BLOCCATO" in out or "negat" in out.lower()


def test_blocklist_still_first(tmp_path):
    d, reason = decide(Mode.YOLO, "shell", {"command": "rm -rf /"}, tmp_path)
    assert d is Decision.DENY and "blocklist" in reason


def test_env_scrub_keeps_secrets_out_of_child_env(tmp_path):
    import os
    os.environ["TEST_ATLAS_API_KEY_SCRUB"] = "segretissimo"
    try:
        out = _shell({"command": "env"}, ctx(tmp_path))
        assert "segretissimo" not in out
    finally:
        del os.environ["TEST_ATLAS_API_KEY_SCRUB"]


# --- git senza configurazione: helper configurati non partono MAI ---

def _init_repo(ws: Path) -> None:
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True,
                   capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=ws, check=True, capture_output=True)


def test_git_configured_external_diff_never_runs(tmp_path):
    """Un .git/config malevolo (anche piantato prima) configura un helper che
    scrive un marker: la superficie lo ignora perche' TUTTE le sorgenti di
    config sono neutralizzate nell'ambiente figlio."""
    import subprocess
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("uno", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "a"], cwd=tmp_path, check=True,
                   capture_output=True)
    (tmp_path / "a.txt").write_text("due", encoding="utf-8")  # diff reale
    marker = tmp_path / "PWNED"
    (tmp_path / ".git" / "config").write_text(
        '[diff]\n\texternal = /usr/bin/touch ' + str(marker) + '\n',
        encoding="utf-8")
    out = _shell({"command": "git diff -- a.txt"}, ctx(tmp_path))
    assert not marker.exists(), out


def test_git_configured_alias_never_runs(tmp_path):
    _init_repo(tmp_path)
    marker = tmp_path / "PWNED"
    (tmp_path / ".git" / "config").write_text(
        '[alias]\n\tstatus = !/usr/bin/touch ' + str(marker) + '\n',
        encoding="utf-8")
    out = _shell({"command": "git status"}, ctx(tmp_path))
    assert not marker.exists(), out


def test_git_textconv_never_runs(tmp_path):
    import subprocess
    _init_repo(tmp_path)
    (tmp_path / "b.bin").write_bytes(b"\x00\x01")
    subprocess.run(["git", "add", "b.bin"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "b"], cwd=tmp_path, check=True,
                   capture_output=True)
    (tmp_path / "b.bin").write_bytes(b"\x00\x02")
    marker = tmp_path / "PWNED"
    (tmp_path / ".git" / "config").write_text(
        '[diff "x"]\n\ttextconv = /usr/bin/touch ' + str(marker) + '\n',
        encoding="utf-8")
    (tmp_path / ".gitattributes").write_text("*.bin diff=x\n", encoding="utf-8")
    out = _shell({"command": "git diff -- b.bin"}, ctx(tmp_path))
    assert not marker.exists(), out


def test_git_hook_never_runs_via_tools(tmp_path):
    _init_repo(tmp_path)
    marker = tmp_path / "PWNED"
    hook = tmp_path / ".git" / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\ntouch " + str(marker) + "\n", encoding="utf-8")
    hook.chmod(0o755)
    _shell({"command": "git status"}, ctx(tmp_path))
    _shell({"command": "git log --oneline"}, ctx(tmp_path))
    assert not marker.exists()


def test_write_file_cannot_touch_git_internals(tmp_path):
    d, reason = decide(Mode.YOLO, "write_file",
                       {"path": ".git/config", "content": "x"}, tmp_path)
    assert d is Decision.DENY
    d, _ = decide(Mode.YOLO, "write_file",
                  {"path": ".git/hooks/pre-commit", "content": "x"}, tmp_path)
    assert d is Decision.DENY
    out = _shell({"command": "cat .git/config"}, ctx(tmp_path))
    assert "NON DISPONIBILE" in out or "protetto" in out or "negat" in out.lower()


def test_git_works_on_normal_repo(tmp_path):
    _init_repo(tmp_path)
    out = _shell({"command": "git status"}, ctx(tmp_path))
    assert "exit 0" in out
