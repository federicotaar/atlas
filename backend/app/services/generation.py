"""Generatori di risposta intercambiabili dietro la stessa interfaccia (spec 5G).

ExtractiveGenerator risponde componendo le memorie recuperate, con citazioni.
Non inventa: senza memorie pertinenti il Core risponde "Non lo so" prima di
chiamare qualsiasi generatore.
"""
from typing import Protocol

import httpx

from ..config import Settings
from .embeddings import ModelUnavailable


class Generator(Protocol):
    name: str

    def answer(self, question: str, memories: list[dict]) -> str: ...


class ExtractiveGenerator:
    """Risposta estrattiva: elenca le memorie pertinenti, niente testo inventato."""

    name = "extractive"

    def answer(self, question: str, memories: list[dict]) -> str:
        lines = ["Dalle tue memorie:"]
        for i, m in enumerate(memories, start=1):
            lines.append(f"{i}. {m['content']} [M{i}]")
        return "\n".join(lines)


class OllamaGenerator:
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float = 30.0, max_tokens: int = 0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # 0 = illimitato (comportamento storico). Un tetto evita che un
        # modello che non emette EOS tenga la richiesta aperta fino al timeout.
        self.max_tokens = max_tokens

    def answer(self, question: str, memories: list[dict]) -> str:
        context = "\n".join(f"[M{i}] {m['content']}" for i, m in enumerate(memories, start=1))
        prompt = (
            "Rispondi alla domanda SOLO usando le memorie elencate. "
            "Cita le memorie usate con il loro identificativo [M1], [M2], ecc. "
            "Se le memorie non bastano, rispondi che non lo sai.\n\n"
            f"Memorie:\n{context}\n\nDomanda: {question}\nRisposta:"
        )
        try:
            payload: dict = {"model": self.model, "prompt": prompt, "stream": False}
            if self.max_tokens > 0:
                payload["options"] = {"num_predict": self.max_tokens}
            resp = httpx.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return str(resp.json()["response"]).strip()
        except Exception as exc:
            raise ModelUnavailable(f"Ollama generate non disponibile: {exc}") from exc


def build_generator(settings: Settings) -> Generator:
    if settings.llm_provider == "ollama":
        return OllamaGenerator(
            settings.ollama_base_url,
            settings.ollama_llm_model,
            settings.model_call_timeout_s,
            settings.ollama_generate_max_tokens,
        )
    return ExtractiveGenerator()
