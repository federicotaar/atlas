"""Provider OpenAI-compatibile (spec 1): copre OpenAI, Groq, OpenRouter,
Gemini (endpoint OpenAI-compatibile di Google) e qualunque servizio custom
con base_url + chiave. Un solo codice, tanti provider: cambia la config.

Endpoint: POST {base_url}/chat/completions, Bearer key (se richiesta).
Streaming: SSE con righe "data: {...}" e terminatore "data: [DONE]".
Tool-call: nativi OpenAI; in streaming gli arguments arrivano a frammenti
JSON e vengono riassemblati per index prima di diventare ToolCall.
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

_NETWORK_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                   httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError)


class OpenAICompatProvider(LLMProvider):
    type = "openai_compat"

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        try:
            resp.read()
        except Exception:
            pass
        detail = ""
        try:
            parsed = resp.json()
            if isinstance(parsed, list) and parsed:  # Gemini risponde con un array
                parsed = parsed[0]
            err = parsed.get("error") if isinstance(parsed, dict) else None
            if isinstance(err, dict):
                detail = str(err.get("message") or "")
            elif err:
                detail = str(err)
        except Exception:
            detail = (resp.text or "").strip()
        detail = " ".join(detail.split())[:200]  # una riga sola nei messaggi
        if resp.status_code in (401, 403):
            raise AuthenticationError(self.name, "chiave API rifiutata dal provider")
        # Gemini (endpoint OpenAI-compatibile) risponde 400 INVALID_ARGUMENT
        # per una chiave non valida: e' un errore di autenticazione.
        if resp.status_code == 400 and "api key" in detail.lower():
            raise AuthenticationError(self.name, f"chiave API rifiutata ({detail})")
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

    def _payload(self, messages: list[Message], tools: list[ToolSpec] | None,
                 stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": translate_tool_messages(messages, arguments_as_json=True),
            "stream": bool(stream),
        }
        normalized = self._normalize_tools(tools)
        if normalized:
            payload["tools"] = normalized
        if self.max_tokens > 0:
            payload["max_tokens"] = self.max_tokens
        return payload

    @staticmethod
    def _tool_calls_from_message(message: dict[str, Any]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or "{}"
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            calls.append(ToolCall(name=str(fn.get("name", "")), arguments=args))
        return calls

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
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return ProviderResponse(
            text=str(message.get("content") or ""),
            tool_calls=self._tool_calls_from_message(message),
            provider=self.name,
            model=str(data.get("model") or self.model),
            finish_reason=str(choice.get("finish_reason") or ""),
            raw=data,
        )

    def _stream(self, payload: dict[str, Any]) -> ChatStream:
        def events() -> Iterator[Any]:
            tool_parts: dict[int, dict[str, str]] = {}
            finish = ""
            try:
                with httpx.stream("POST", self._url(), headers=self._headers(),
                                  json=payload, timeout=self.timeout) as resp:
                    self._raise_for_status(resp)
                    for line in resp.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        choice = (chunk.get("choices") or [{}])[0]
                        finish = str(choice.get("finish_reason") or finish)
                        delta = choice.get("delta") or {}
                        for tc in delta.get("tool_calls") or []:
                            idx = int(tc.get("index", 0))
                            part = tool_parts.setdefault(idx, {"name": "", "arguments": ""})
                            fn = tc.get("function") or {}
                            part["name"] += str(fn.get("name") or "")
                            part["arguments"] += str(fn.get("arguments") or "")
                        text = delta.get("content") or ""
                        if text:
                            yield text
            except ProviderError:
                raise
            except _NETWORK_ERRORS as exc:
                raise ProviderUnavailable(self.name, f"irraggiungibile ({type(exc).__name__})") from exc
            except httpx.HTTPError as exc:
                raise ProviderError(self.name, f"errore HTTP ({type(exc).__name__})") from exc
            calls: list[ToolCall] = []
            for idx in sorted(tool_parts):
                part = tool_parts[idx]
                try:
                    args = json.loads(part["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": part["arguments"]}
                calls.append(ToolCall(name=part["name"], arguments=args))
            yield ProviderResponse(text="", tool_calls=calls, provider=self.name,
                                   model=self.model, finish_reason=finish)

        return ChatStream(events(), self.name, self.model)

    def list_models(self) -> list[str]:
        try:
            resp = httpx.get(f"{self.base_url}/models", headers=self._headers(), timeout=10.0)
            resp.raise_for_status()
            return [m.get("id", "") for m in resp.json().get("data", []) if m.get("id")]
        except Exception:
            return []
