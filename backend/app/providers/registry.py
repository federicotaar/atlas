"""Registro dei provider da config/providers.yaml (spec 1).

Ogni voce dichiara name, type, base_url, model, api_key_env. Il valore della
chiave arriva SOLO da .env / variabili d'ambiente: mai nel file di config,
mai nei log, mai stampato. Il fallback di catena passa al provider
configurato successivo quando quello attivo fallisce o va in rate limit;
in streaming il cambio e' possibile solo prima del primo delta di testo
(dopo, il testo si mescolerebbe: l'errore risale all'utente).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from .anthropic import AnthropicProvider
from .base import (
    AllProvidersFailed,
    ChatStream,
    LLMProvider,
    Message,
    ProviderError,  # noqa: F401
    ProviderResponse,
    ToolSpec,
)
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider

# Tipi di provider implementati (spec 1). openai_compat copre OpenAI, Groq,
# OpenRouter, Gemini (endpoint OpenAI-compatibile) e qualunque custom
# base_url+chiave; anthropic usa la sua API nativa /v1/messages.
PROVIDER_TYPES: dict[str, type[LLMProvider]] = {
    "ollama": OllamaProvider,
    "openai_compat": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
}


class ConfigError(Exception):
    """Configurazione provider non valida (nessun segreto nel messaggio)."""


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    type: str
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""
    enabled: bool = True
    max_tokens: int = 0  # 0 = nessun tetto; >0 limita la risposta (num_predict)

    @property
    def requires_key(self) -> bool:
        return bool(self.api_key_env)


def load_dotenv(path: str | Path) -> dict[str, str]:
    """Carica KEY=VALUE da un file .env senza sovrascrivere l'ambiente.
    Ritorna le chiavi lette (mai i valori) per diagnostica senza segreti."""
    p = Path(path)
    if not p.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = "(impostata)"
    return loaded


class ProviderRegistry:
    """Costruisce e tiene i provider dichiarati in config/providers.yaml."""

    def __init__(self, specs: list[ProviderSpec], env: dict[str, str] | None = None):
        self._env = os.environ if env is None else env
        self._specs = {s.name: s for s in specs}
        self._order = [s.name for s in specs]
        self._instances: dict[str, LLMProvider] = {}
        self._model_overrides: dict[str, str] = {}

    @classmethod
    def from_file(cls, path: str | Path, env: dict[str, str] | None = None) -> "ProviderRegistry":
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"file di configurazione non trovato: {p}")
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{p}: YAML non valido ({exc})") from exc
        raw = data.get("providers")
        if not isinstance(raw, list) or not raw:
            raise ConfigError(f"{p}: manca la lista 'providers'")
        specs: list[ProviderSpec] = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict) or "name" not in item or "type" not in item:
                raise ConfigError(f"{p}: voce providers[{i}] senza 'name' o 'type'")
            specs.append(ProviderSpec(
                name=str(item["name"]),
                type=str(item["type"]),
                base_url=str(item.get("base_url") or ""),
                model=str(item.get("model") or ""),
                api_key_env=str(item.get("api_key_env") or ""),
                enabled=bool(item.get("enabled", True)),
                max_tokens=int(item.get("max_tokens") or 0),
            ))
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            raise ConfigError(f"{p}: nomi provider duplicati")
        return cls(specs, env=env)

    def has_key(self, name: str) -> bool:
        """La chiave del provider e' presente in ambiente (valore mai esposto)."""
        spec = self.spec(name)
        return bool(spec.api_key_env) and bool(self._env.get(spec.api_key_env, ""))

    def spec(self, name: str) -> ProviderSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise ConfigError(f"provider sconosciuto: {name}") from None

    def names(self, enabled_only: bool = True) -> list[str]:
        return [n for n in self._order if not enabled_only or self._specs[n].enabled]

    def usable(self, name: str) -> tuple[bool, str]:
        """(usabile, motivo_se_no): un provider che richiede una chiave senza
        averla in .env viene saltato dalla catena, con il motivo dichiarato."""
        spec = self.spec(name)
        if not spec.enabled:
            return False, "disabilitato in config"
        if spec.type not in PROVIDER_TYPES:
            return False, f"tipo '{spec.type}' non implementato"
        if spec.requires_key and not self._env.get(spec.api_key_env, ""):
            return False, f"chiave mancante ({spec.api_key_env} non impostata)"
        return True, ""

    def get(self, name: str) -> LLMProvider:
        spec = self.spec(name)
        cls = PROVIDER_TYPES.get(spec.type)
        if cls is None:
            raise ConfigError(f"tipo provider non implementato: {spec.type}")
        model = self._model_overrides.get(name) or spec.model
        key = self._env.get(spec.api_key_env, "") if spec.api_key_env else ""
        inst = self._instances.get(name)
        if inst is None or inst.model != model or inst.base_url != spec.base_url.rstrip("/"):
            inst = cls(name=spec.name, model=model, base_url=spec.base_url, api_key=key,
                       max_tokens=spec.max_tokens)
            self._instances[name] = inst
        return inst

    def set_model(self, name: str, model: str) -> None:
        self.spec(name)  # valida che esista
        self._model_overrides[name] = model

    def current_model(self, name: str) -> str:
        return self._model_overrides.get(name) or self.spec(name).model

    def fallback_order(self, active: str) -> list[str]:
        """Il provider attivo per primo, poi gli altri in ordine di config;
        quelli inutilizzabili (disabilitati o senza chiave) vengono saltati."""
        ordered = [active] + [n for n in self._order if n != active]
        return [n for n in ordered if self.usable(n)[0]]

    def chat_with_fallback(
        self,
        active: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        stream: bool = False,
        on_fallback: Callable[[str, str, str], None] | None = None,
    ) -> ProviderResponse | ChatStream:
        """Prova i provider in fallback_order; su ProviderError passa al
        successivo. In streaming il cambio avviene solo prima del primo delta:
        a meta' risposta l'errore risale, niente testo rimescolato."""
        order = self.fallback_order(active)
        attempts: list[tuple[str, str]] = []
        for name in order:
            try:
                result = self.get(name).chat(messages, tools=tools, stream=stream)
            except ProviderError as exc:
                attempts.append((name, str(exc)))
                continue
            if not stream:
                return result
            assert isinstance(result, ChatStream)
            try:
                first = next(result)
            except StopIteration:
                return result  # risposta vuota ma valida
            except ProviderError as exc:
                attempts.append((name, str(exc)))
                continue  # nessun delta emesso: cambio sicuro
            result._prepend(first)
            for prev, err in attempts:
                if on_fallback:
                    on_fallback(prev, name, err)
            return result
        raise AllProvidersFailed(attempts)
