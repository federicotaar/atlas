"""Provider Anthropic (spec 1): API nativa /v1/messages (non ha un endpoint
OpenAI-compatibile ufficiale). Differenze gestite qui dentro, cosi' il loop
non se ne accorge: system estratto dai messaggi, max_tokens obbligatorio,
tool_use/input_schema, streaming SSE per eventi.

Errori: 401 auth, 429 rate limit, 529 overloaded -> irraggiungibile (retry),
5xx -> irraggiungibile. La chiave non finisce mai nei messaggi d'errore.
"""
from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

from .base import (
    AuthenticationError,
    ChatStream,
    LLMProvider,
    Message,
    ModelNotFound,
    ProviderError,
    ProviderResponse,
    ProviderUnavailable,
    RateLimitError,
    ToolCall,
    ToolSpec,
)

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_BASE = "https://api.anthropic.com"
# Anthropic richiede sempre max_tokens: tetto di sicurezza se non configurato.
DEFAULT_MAX_TOKENS = 4096

_NETWORK_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                   httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError)


class AnthropicProvider(LLMProvider):
    type = "anthropic"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _url(self) -> str:
        return f"{self.base_url or DEFAULT_BASE}/v1/messages"

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        try:
            resp.read()
        except Exception:
            pass
        detail = ""
        try:
            detail = str((resp.json().get("error") or {}).get("message") or "")
        except Exception:
            detail = (resp.text or "").strip()[:200]
        detail = detail[:200]
        if resp.status_code in (401, 403):
            raise AuthenticationError(self.name, "chiave API rifiutata dal provider")
        if resp.status_code == 404:
            raise ModelNotFound(self.name, detail or f"modello '{self.model}' non trovato")
        if resp.status_code == 429:
            raise RateLimitError(self.name, detail or "rate limit raggiunto")
        if resp.status_code == 529 or resp.status_code >= 500:
            raise ProviderUnavailable(self.name, detail or f"HTTP {resp.status_code}")
        raise ProviderError(self.name, detail or f"HTTP {resp.status_code}")

    def _payload(self, messages: list[Message], tools: list[ToolSpec] | None,
                 stream: bool) -> dict[str, Any]:
        system_parts = [str(m.get("content", "")) for m in messages if m.get("role") == "system"]
        chat_messages = [m for m in messages if m.get("role") != "system"]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages,
            "max_tokens": self.max_tokens or DEFAULT_MAX_TOKENS,
            "stream": bool(stream),
        }
        if system_parts:
            payload["system"] = "\n\n".join(p for p in system_parts if p)
        if tools:
            payload["tools"] = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]
        return payload

    def chat(self, messages: list[Message], tools: list[ToolSpec] | None = None,
             stream: bool = False) -> ProviderResponse | ChatStream:
        if stream:
            return self._stream(self._payload(messages, tools, True))
        try:
            resp = httpx.post(self._url(), headers=self._headers(),
                              json=self._payload(messages, tools, False),
                              timeout=self.timeout)
            self._raise_for_status(resp)
            data = resp.json()
        except ProviderError:
            raise
        except _NETWORK_ERRORS as exc:
            raise ProviderUnavailable(self.name, f"irraggiungibile ({type(exc).__name__})") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"errore HTTP ({type(exc).__name__})") from exc
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(name=str(block.get("name") or ""),
                                      arguments=block.get("input") or {}))
        return ProviderResponse(
            text="".join(text_parts),
            tool_calls=calls,
            provider=self.name,
            model=str(data.get("model") or self.model),
            finish_reason=str(data.get("stop_reason") or ""),
            raw=data,
        )

    def _stream(self, payload: dict[str, Any]) -> ChatStream:
        def events() -> Iterator[Any]:
            # blocco tool_use corrente: input arriva a frammenti JSON
            open_tools: dict[int, dict[str, Any]] = {}
            finish = ""
            try:
                with httpx.stream("POST", self._url(), headers=self._headers(),
                                  json=payload, timeout=self.timeout) as resp:
                    self._raise_for_status(resp)
                    for line in resp.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if not data_str:
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        etype = event.get("type")
                        if etype == "content_block_start":
                            block = event.get("content_block") or {}
                            if block.get("type") == "tool_use":
                                open_tools[int(event.get("index", 0))] = {
                                    "name": str(block.get("name") or ""), "json": ""}
                        elif etype == "content_block_delta":
                            delta = event.get("delta") or {}
                            if delta.get("type") == "text_delta":
                                text = str(delta.get("text") or "")
                                if text:
                                    yield text
                            elif delta.get("type") == "input_json_delta":
                                idx = int(event.get("index", 0))
                                if idx in open_tools:
                                    open_tools[idx]["json"] += str(delta.get("partial_json") or "")
                        elif etype == "message_delta":
                            finish = str((event.get("delta") or {}).get("stop_reason") or finish)
            except ProviderError:
                raise
            except _NETWORK_ERRORS as exc:
                raise ProviderUnavailable(self.name, f"irraggiungibile ({type(exc).__name__})") from exc
            except httpx.HTTPError as exc:
                raise ProviderError(self.name, f"errore HTTP ({type(exc).__name__})") from exc
            calls: list[ToolCall] = []
            for idx in sorted(open_tools):
                part = open_tools[idx]
                try:
                    args = json.loads(part["json"] or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": part["json"]}
                calls.append(ToolCall(name=part["name"], arguments=args))
            yield ProviderResponse(text="", tool_calls=calls, provider=self.name,
                                   model=self.model, finish_reason=finish)

        return ChatStream(events(), self.name, self.model)

    def list_models(self) -> list[str]:
        try:
            resp = httpx.get(f"{self.base_url or DEFAULT_BASE}/v1/models",
                             headers=self._headers(), timeout=10.0)
            resp.raise_for_status()
            return [m.get("id", "") for m in resp.json().get("data", []) if m.get("id")]
        except Exception:
            return []
