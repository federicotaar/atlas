"""Generatori di risposta intercambiabili dietro la stessa interfaccia (spec 5G).

ExtractiveGenerator risponde componendo le memorie recuperate, con citazioni.
Non inventa: senza memorie pertinenti il Core risponde "Non lo so" prima di
chiamare qualsiasi generatore.
"""
import re
from typing import Protocol

import httpx

from ..config import Settings
from .embeddings import ModelUnavailable


_CITATION = re.compile(r"\[M(\d+)\]")


def cited_indexes(answer: str) -> list[int]:
    """Numeri delle memorie citate come [M1], [M2], in ordine di prima comparsa."""
    seen: list[int] = []
    for match in _CITATION.finditer(answer):
        n = int(match.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def strip_unknown_citations(answer: str, memory_count: int) -> str:
    """Toglie le citazioni a memorie che non esistono (es. [M9] con 3 memorie):
    un modello puo' inventarle e l'interfaccia non deve mostrare fonti false."""
    def keep(match: re.Match) -> str:
        return match.group(0) if 1 <= int(match.group(1)) <= memory_count else ""
    cleaned = _CITATION.sub(keep, answer)
    return re.sub(r"[ \t]{2,}", " ", cleaned).replace(" .", ".").replace(" ,", ",").strip()


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

    def __init__(self, base_url: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def answer(self, question: str, memories: list[dict]) -> str:
        context = "\n".join(f"[M{i}] {m['content']}" for i, m in enumerate(memories, start=1))
        prompt = (
            "Rispondi alla domanda SOLO usando le memorie elencate. "
            "Cita le memorie usate con il loro identificativo [M1], [M2], ecc. "
            "Se le memorie non bastano, rispondi che non lo sai.\n\n"
            f"Memorie:\n{context}\n\nDomanda: {question}\nRisposta:"
        )
        try:
            resp = httpx.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return strip_unknown_citations(str(resp.json()["response"]).strip(), len(memories))
        except Exception as exc:
            raise ModelUnavailable(f"Ollama generate non disponibile: {exc}") from exc


def build_generator(settings: Settings) -> Generator:
    if settings.llm_provider == "ollama":
        return OllamaGenerator(settings.ollama_base_url, settings.ollama_llm_model, settings.model_call_timeout_s)
    return ExtractiveGenerator()
