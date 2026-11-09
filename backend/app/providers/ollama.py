"""Provider Ollama (spec 1): locale (http://localhost:11434, nessuna chiave)
e cloud (https://ollama.com, Bearer). Stesso endpoint /api/chat per entrambi;
cambia solo base_url e presenza della chiave.

Tool-call: Ollama supporta il formato nativo {type: function, function: {...}}
per i modelli capaci; le chiamate in uscita vengono normalizzate in ToolCall.
I modelli piccoli senza tool-calling nativo useranno il formato JSON nel testo
(fallback del loop agente, stage 3).
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
    translate_tool_messages,
)

DEFAULT_LOCAL_BASE = "http://localhost:11434"
CLOUD_BASE = "https://ollama.com"

_NETWORK_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                   httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError)


class OllamaProvider(LLMProvider):
    type = "ollama"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    def _url(self, path: str) -> str:
        base = self.base_url or DEFAULT_LOCAL_BASE
        return f"{base}{path}"

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        try:
            resp.read()  # in streaming il corpo non e' ancora stato letto
        except Exception:
            pass
        detail = ""
        try:
            detail = str(resp.json().get("error") or "").strip()
        except Exception:
            detail = (resp.text or "").strip()[:200]
        detail = detail[:200]
        # La chiave non fa mai parte del messaggio: solo stato e dettaglio provider.
        if resp.status_code in (401, 403):
            raise AuthenticationError(self.name, "chiave API rifiutata dal provider")
        if resp.status_code == 404:
            raise ModelNotFound(self.name, detail or f"modello '{self.model}' non trovato")
        if resp.status_code == 429:
            raise RateLimitError(self.name, detail or "rate limit raggiunto")
        if resp.status_code >= 500:
            raise ProviderUnavailable(self.name, detail or f"HTTP {resp.status_code}")
        raise ProviderError(self.name, detail or f"HTTP {resp.status_code}")

    @staticmethod
    def _normalize_tools(tools: list[ToolSpec] | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in tools or []:
            out.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return out

    @staticmethod
    def _tool_calls(message: dict[str, Any]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):  # alcuni modelli emettono JSON testuale
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            calls.append(ToolCall(name=str(fn.get("name", "")), arguments=args))
        return calls

    def _payload(self, messages: list[Message], tools: list[ToolSpec] | None,
                 stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": translate_tool_messages(messages, arguments_as_json=False),
            "stream": bool(stream),
        }
        normalized = self._normalize_tools(tools)
        if normalized:
            payload["tools"] = normalized
        # Tetto opzionale: un modello che non si ferma bloccherebbe la chat.
        if self.max_tokens > 0:
            payload["options"] = {"num_predict": self.max_tokens}
        return payload

    def chat(self, messages: list[Message], tools: list[ToolSpec] | None = None,
             stream: bool = False) -> ProviderResponse | ChatStream:
        if stream:
            return self._stream(self._payload(messages, tools, True))
        try:
            resp = httpx.post(self._url("/api/chat"), headers=self._headers(),
                              json=self._payload(messages, tools, False),
                              timeout=self.timeout)
            self._raise_for_status(resp)
            data = resp.json()
        except ProviderError as exc:
            # Modelli senza tool-calling nativo (ollama risponde 400
            # "does not support tools"): UNA sola ritirata senza tools, poi
            # il loop agente ripiega sul formato JSON nel testo (stage 3).
            # Senza questo, un modello custom inserito dal pannello puo'
            # rendere il provider inutilizzabile.
            if tools and "does not support tools" in str(exc):
                return self.chat(messages, None, False)
            raise
        except _NETWORK_ERRORS as exc:
            raise ProviderUnavailable(self.name, f"irraggiungibile ({type(exc).__name__})") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"errore HTTP ({type(exc).__name__})") from exc
        message = data.get("message") or {}
        return ProviderResponse(
            text=str(message.get("content") or ""),
            tool_calls=self._tool_calls(message),
            provider=self.name,
            model=self.model,
            finish_reason=str(data.get("done_reason") or ""),
            raw=data,
        )

    def _stream(self, payload: dict[str, Any]) -> ChatStream:
        def events() -> Iterator[Any]:
            final: dict[str, Any] | None = None
            try:
                with httpx.stream("POST", self._url("/api/chat"), headers=self._headers(),
                                  json=payload, timeout=self.timeout) as resp:
                    self._raise_for_status(resp)
                    for line in resp.iter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        message = chunk.get("message") or {}
                        if chunk.get("done"):
                            final = chunk
                        delta = message.get("content") or ""
                        if delta:
                            yield delta
            except ProviderError:
                raise
            except _NETWORK_ERRORS as exc:
                raise ProviderUnavailable(self.name, f"irraggiungibile ({type(exc).__name__})") from exc
            except httpx.HTTPError as exc:
                raise ProviderError(self.name, f"errore HTTP ({type(exc).__name__})") from exc
            if final is not None:
                yield ProviderResponse(
                    text="",  # riempito da ChatStream con i delta accumulati
                    tool_calls=self._tool_calls(final.get("message") or {}),
                    provider=self.name,
                    model=self.model,
                    finish_reason=str(final.get("done_reason") or ""),
                    raw=final,
                )

        return ChatStream(events(), self.name, self.model)

    def list_models(self) -> list[str]:
        try:
            resp = httpx.get(self._url("/api/tags"), headers=self._headers(), timeout=10.0)
            resp.raise_for_status()
            return [m.get("name", "") for m in resp.json().get("models", []) if m.get("name")]
        except Exception:
            return []

    def ping(self) -> bool:
        try:
            resp = httpx.get(self._url("/api/tags"), headers=self._headers(), timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False
