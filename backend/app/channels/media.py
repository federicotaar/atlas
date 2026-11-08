"""Stage 6 (spec sez. 6): media dei canali - note vocali, foto, file.

- Note vocali/audio: trascritte con Groq (whisper-large-v3-turbo) quando
  GROQ_API_KEY e' disponibile (spec: "local Whisper or Groq"; qui Groq).
  Senza chiave il file resta salvato e la nota lo segnala.
- Foto: descritte con un provider vision (Gemini) quando GEMINI_API_KEY e'
  disponibile; senza chiave resta il file nel workspace.
- File: salvati nella sandbox; quelli di testo piccoli entrano nel contesto
  come contenuto inline, gli altri restano a disposizione di read_file.

Tutto resta dentro la sandbox del workspace (data/inbound/<canale>/<mittente>/)
e nessun contenuto binario finisce in log: il log canali vede solo tipo, nome
e percorso. Le chiavi non compaiono mai in note, log o messaggi d'errore.
"""
from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass
from pathlib import Path

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_TRANSCRIBE_MODEL = "whisper-large-v3-turbo"
GEMINI_DESCRIBE_URL = ("https://generativelanguage.googleapis.com/v1beta/"
                       "models/gemini-3.5-flash:generateContent")
MAX_MEDIA_BYTES = 20 * 1024 * 1024  # limite download della Bot API
TEXT_INLINE_MAX = 4000              # caratteri oltre i quali resta solo il path

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_TEXT_EXT = (".txt", ".md", ".py", ".json", ".yaml", ".yml", ".csv", ".log",
             ".toml", ".ini", ".html", ".css", ".js", ".ts")


@dataclass(frozen=True)
class Attachment:
    """Un allegato in arrivo da un canale: gia' scaricato (solo se il
    mittente e' autorizzato) ma non ancora salvato ne' lavorato."""

    kind: str  # "voice" | "audio" | "photo" | "file"
    name: str
    mime: str
    data: bytes


@dataclass(frozen=True)
class ProcessedMedia:
    path: Path
    note: str  # annotazione in italiano che entra nel testo per l'agente


def _safe_name(name: str) -> str:
    cleaned = _SAFE.sub("_", (name or "").strip()).strip("._")
    return cleaned or "allegato.bin"


def save_inbound(root: Path, channel: str, sender: str, name: str,
                 data: bytes) -> Path:
    """Salva l'allegato nella sandbox, con nome ripulito e univoco."""
    folder = root / "data" / "inbound" / channel / sender
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = folder / f"{stamp}-{_safe_name(name)}"
    n = 1
    while path.exists():
        path = folder / f"{stamp}-{n}-{_safe_name(name)}"
        n += 1
    path.write_bytes(data)
    return path


def _httpx_post(url: str, *, headers=None, files=None, data=None,
                json=None, timeout: float = 60.0):
    """POST normalizzata: ritorna (status_code, json). Isolata per i test."""
    import httpx  # import pigro: i test non richiedono la libreria
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, files=files, data=data,
                           json=json)
    return resp.status_code, resp.json()


class MediaProcessor:
    """Salva e lavora gli allegati per conto dell'hub. `post` e' iniettabile
    nei test: stessa firma di _httpx_post."""

    def __init__(self, workspace: Path, *, groq_key: str = "",
                 gemini_key: str = "", post=None,
                 inline_max: int = TEXT_INLINE_MAX):
        self._workspace = workspace
        self._groq_key = groq_key
        self._gemini_key = gemini_key
        self._post = post or _httpx_post
        self._inline_max = inline_max

    def process(self, att: Attachment, channel: str, sender: str) -> ProcessedMedia:
        path = save_inbound(self._workspace, channel, sender, att.name, att.data)
        rel = path.relative_to(self._workspace)
        if att.kind in ("voice", "audio"):
            text = self._transcribe(att)
            if text:
                return ProcessedMedia(path, f'[nota vocale trascritta: "{text}"]')
            return ProcessedMedia(
                path, f"[nota vocale salvata in {rel} - trascrizione non disponibile]")
        if att.kind == "photo":
            desc = self._describe(att)
            if desc:
                return ProcessedMedia(
                    path, f"[foto ricevuta: {desc} (file: {rel})]")
            return ProcessedMedia(
                path, f"[foto salvata in {rel} - descrizione non disponibile]")
        inline = self._inline_text(att)
        if inline is not None:
            return ProcessedMedia(
                path, f"[file {att.name} salvato in {rel}; contenuto:\n{inline}]")
        return ProcessedMedia(path, f"[file {att.name} salvato in {rel}]")

    # --- lavorazioni --------------------------------------------------------

    def _transcribe(self, att: Attachment) -> str:
        if not self._groq_key:
            return ""
        try:
            status, body = self._post(
                GROQ_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {self._groq_key}"},
                files={"file": (_safe_name(att.name), att.data,
                                att.mime or "application/octet-stream")},
                data={"model": GROQ_TRANSCRIBE_MODEL})
        except Exception:
            return ""
        if status != 200 or not isinstance(body, dict):
            return ""
        return str(body.get("text") or "").strip()

    def _describe(self, att: Attachment) -> str:
        if not self._gemini_key:
            return ""
        payload = {"contents": [{"parts": [
            {"text": "Descrivi in una frase che cosa mostra questa immagine, "
                     "in italiano, per un assistente che non puo' vederla."},
            {"inline_data": {"mime_type": att.mime or "image/jpeg",
                             "data": base64.b64encode(att.data).decode()}}]}]}
        try:
            status, body = self._post(
                GEMINI_DESCRIBE_URL,
                headers={"x-goog-api-key": self._gemini_key},
                json=payload)
        except Exception:
            return ""
        if status != 200 or not isinstance(body, dict):
            return ""
        try:
            parts = body["candidates"][0]["content"]["parts"]
            return " ".join(str(p.get("text", "")) for p in parts).strip()
        except (KeyError, IndexError, TypeError):
            return ""

    def _inline_text(self, att: Attachment) -> str | None:
        looks_text = att.mime.startswith("text/") or att.name.lower().endswith(_TEXT_EXT)
        if not looks_text or len(att.data) > self._inline_max:
            return None
        return att.data.decode("utf-8", errors="replace")
