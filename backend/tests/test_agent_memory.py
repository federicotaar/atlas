"""Stage 4 (spec sez. 5): memoria persistente dell'agente - store SQLite,
/remember, /forget, iniezione di note pertinenti nel contesto."""
from __future__ import annotations

from pathlib import Path

from app.agent.memory import MemoryStore, memories_context
from app.agent_cli import run_cli
from app.providers.base import ChatStream, LLMProvider, ProviderResponse
from app.providers.registry import ProviderRegistry, ProviderSpec


def test_store_history_and_notes(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    store.add_message("locale", "cli", "user", "ciao")
    store.add_message("locale", "cli", "assistant", "salve")
    store.add_message("altro", "cli", "user", "non deve vedersi")
    hist = store.history("locale", "cli")
    assert [m["content"] for m in hist] == ["ciao", "salve"]
    store.close()
    # riapertura: i dati sopravvivono al processo
    store2 = MemoryStore(tmp_path / "memory.db")
    assert len(store2.history("locale", "cli")) == 2
    store2.close()


def test_remember_forget_relevant(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    store.remember("il gatto si chiama Briciola")
    store.remember("preferisco risposte corte")
    hits = store.relevant("come si chiama il gatto?")
    assert hits and "Briciola" in hits[0]
    removed = store.forget("gatto")
    assert removed == 1
    assert all("Briciola" not in t for _, t in store.all_memories())
    store.close()


def test_memories_context_marks_uncertainty():
    block = memories_context(["nota uno"])
    assert "nota uno" in block and "potrebbe non essere aggiornata" in block
    assert memories_context([]) == ""


class _CapturingProvider(LLMProvider):
    def __init__(self):
        super().__init__(name="fake", model="m")
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools=None, stream=False):
        self.seen.append(list(messages))
        if stream:
            return ChatStream(iter(["ricevuto"]), self.name, "m")
        return ProviderResponse(text="ricevuto", provider=self.name, model="m")


def _cli_registry(fake):
    reg = ProviderRegistry([ProviderSpec(name="fake", type="ollama")], env={})
    reg.get = lambda name: fake  # type: ignore[method-assign]
    return reg


def _run(tmp_path: Path, fake, lines: list[str]) -> str:
    import time
    buf: list[str] = []
    it = iter(lines)
    expected = 0

    def feed(prompt: str) -> str:
        nonlocal expected
        # il REPL e' asincrono: prima di QUALUNQUE riga nuova, attendi che le
        # domande gia' inviate abbiano avuto risposta e il task si sia chiuso
        deadline = time.time() + 5
        while "".join(buf).count("ricevuto") < expected and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.3)
        line = next(it)
        if not line.startswith("/"):
            expected += 1
        return line

    run_cli(_cli_registry(fake), active="fake", workspace=tmp_path,
            input_fn=feed,
            out=lambda s: buf.append(str(s)), write=lambda s: buf.append(str(s)))
    return "".join(buf)


def test_cli_remember_injects_and_forget_removes(tmp_path):
    fake = _CapturingProvider()
    out = _run(tmp_path, fake, [
        "/remember il gatto si chiama Briciola",
        "come si chiama il gatto?",
        "/memories",
        "/forget Briciola",
        "come si chiama il gatto?",
        "/quit",
    ])
    assert "Ricordato (#1)" in out
    # la domanda porta la nota come contesto di sistema
    call = fake.seen[0]
    assert call[0]["role"] == "system" and "Briciola" in call[0]["content"]
    assert "Briciola" in out  # listata da /memories
    assert "Dimenticate 1 note." in out
    # dopo /forget niente system block
    call2 = fake.seen[1]
    assert call2[0]["role"] == "user"


def test_cli_history_survives_restart(tmp_path):
    fake = _CapturingProvider()
    _run(tmp_path, fake, ["primo messaggio", "/quit"])
    fake2 = _CapturingProvider()
    _run(tmp_path, fake2, ["secondo", "/quit"])
    # il secondo avvio vede la cronologia della sessione precedente
    contents = [m["content"] for m in fake2.seen[0]]
    assert "primo messaggio" in contents and "secondo" in contents


def test_cli_remember_empty_rejected(tmp_path):
    fake = _CapturingProvider()
    out = _run(tmp_path, fake, ["/remember", "/quit"])
    assert "Errore" in out
