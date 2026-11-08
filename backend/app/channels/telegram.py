"""Canale Telegram (spec sez. 6, build order 5-6): long polling, nessun server
pubblico. Token da .env (TELEGRAM_BOT_TOKEN), mai in config ne' nei log.

Stage 6: oltre al testo il canale accetta note vocali/audio, foto e file.
Un allegato viene scaricato SOLO se il mittente e' gia' autorizzato
(allow_check): per gli sconosciuti non parte nemmeno il download e vale il
silenzio dell'hub. Il salvataggio e le lavorazioni (trascrizione Groq,
descrizione foto) sono di MediaProcessor, chiamato dall'hub nel thread del
task, mai nel loop di polling.

Il client reale usa python-telegram-bot; i test iniettano un client finto con
la stessa interfaccia (start_polling/on_update + send_message), quindi la
logica del canale e' coperta senza rete.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .base import Channel, ChannelMessage
from .media import MAX_MEDIA_BYTES, Attachment


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, token: str, client_factory: Callable | None = None,
                 drop_pending_updates: bool = True,
                 allow_check: Callable[[str], bool] | None = None):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN mancante")
        self._token = token
        self._client_factory = client_factory or _PTBClient
        self._drop_pending = drop_pending_updates
        self._allow_check = allow_check
        self._client = None
        self._handler: Callable[[ChannelMessage], None] | None = None

    def on_message(self, handler: Callable[[ChannelMessage], None]) -> None:
        self._handler = handler

    def start(self) -> None:
        self._client = self._client_factory(self._token, self._drop_pending)
        # Il gate del download vive nel client reale; i client finti dei test
        # non scaricano nulla e non hanno bisogno del metodo.
        if self._allow_check is not None and hasattr(self._client, "set_allow_check"):
            self._client.set_allow_check(self._allow_check)
        self._client.start_polling(self._on_update)

    def send(self, to: str, text: str) -> None:
        if self._client is None:
            raise RuntimeError("canale non avviato")
        self._client.send_message(to, text)

    def is_connected(self) -> bool:
        return bool(self._client is not None and
                    getattr(self._client, "is_connected", lambda: True)())

    def set_on_failure(self, fn) -> None:
        if self._client is not None and hasattr(self._client, "set_on_failure"):
            self._client.set_on_failure(fn)

    def _on_update(self, chat_id: str, text: str, display: str = "",
                   attachments: tuple[Attachment, ...] = ()) -> None:
        if self._handler is not None:
            self._handler(ChannelMessage(sender=str(chat_id),
                                         channel=self.name, text=text,
                                         display=display,
                                         attachments=tuple(attachments)))


def _display_of(user) -> str:
    if user is None:
        return ""
    parts = [p for p in (user.first_name, user.last_name) if p]
    display = " ".join(parts)
    if user.username:
        display += f" (@{user.username})"
    return display.strip()


class _PTBClient:
    """Adattatore python-telegram-bot: polling in thread daemon, invio via
    event loop dell'app. Import pigro: i test non richiedono la libreria."""

    def __init__(self, token: str, drop_pending_updates: bool = True):
        from telegram.ext import ApplicationBuilder  # import pigro
        self._app = ApplicationBuilder().token(token).build()
        self._drop_pending = drop_pending_updates
        self._loop = None
        self._alive = False
        self._on_failure: Callable[[str, Exception], None] | None = None
        self._allow_check: Callable[[str], bool] | None = None

    def set_allow_check(self, fn: Callable[[str], bool]) -> None:
        self._allow_check = fn

    def set_on_failure(self, fn) -> None:
        self._on_failure = fn

    def is_connected(self) -> bool:
        return self._alive

    def start_polling(self, on_update: Callable[..., None]) -> None:
        import asyncio

        from telegram.ext import MessageHandler, filters

        async def _recv(update, context) -> None:
            msg = update.message
            if msg is not None and msg.text:
                on_update(str(msg.chat_id), msg.text,
                          _display_of(msg.from_user))

        async def _recv_media(update, context) -> None:
            msg = update.message
            if msg is None:
                return
            chat_id = str(msg.chat_id)
            display = _display_of(msg.from_user)
            caption = (msg.caption or "").strip()
            # Mai scaricare contenuti da mittenti non autorizzati: l'hub
            # risponde con il silenzio anche a questo messaggio segnaposto.
            if self._allow_check is not None and not self._allow_check(chat_id):
                on_update(chat_id, caption, display, ())
                return
            try:
                attachments = await self._download(msg)
            except Exception:
                on_update(chat_id,
                          (caption + " [allegato non scaricabile]").strip(),
                          display, ())
                return
            if attachments is None:  # troppo grande: solo segnalazione
                on_update(chat_id, caption, display, ())
                return
            on_update(chat_id, caption, display, tuple(attachments))

        async def _post_init(app) -> None:
            self._loop = asyncio.get_running_loop()

        self._app.add_handler(MessageHandler(filters.TEXT, _recv))
        media_filter = (filters.VOICE | filters.AUDIO | filters.PHOTO
                        | filters.Document.ALL)
        self._app.add_handler(MessageHandler(media_filter, _recv_media))
        self._app.post_init = _post_init
        # stop_signals=None: run_polling gira in un thread NON principale
        # (la CLI resta interattiva); asyncio vieta i signal handler fuori
        # dal main thread. Lo stop resta del processo/REPL.
        # Watchdog (stage 12): se run_polling muore, il thread lo riesuma
        # con backoff esponenziale; a tentativi esauriti avvisa (on_failure)
        # invece di morire in silenzio.
        from ..agent.supervisor import RestartPolicy

        policy = RestartPolicy(max_restarts=5, window_seconds=600)

        def poll_forever() -> None:
            self._alive = True
            try:
                while True:
                    try:
                        self._app.run_polling(
                            drop_pending_updates=self._drop_pending,
                            stop_signals=None)
                        return  # uscita pulita
                    except Exception as exc:
                        delay = policy.next_delay()
                        if delay is None:
                            if self._on_failure is not None:
                                self._on_failure("telegram", exc)
                            return
                        time.sleep(delay)
            finally:
                self._alive = False

        self._alive = True
        threading.Thread(target=poll_forever, daemon=True).start()

    async def _download(self, msg) -> list[Attachment] | None:
        """Scarica l'allegato del messaggio. None = troppo grande (il
        chiamante manda solo una segnalazione testuale)."""
        stamp = msg.date.strftime("%Y%m%d-%H%M%S") if msg.date else "media"
        if msg.voice or msg.audio:
            media = msg.voice or msg.audio
            if media.file_size and media.file_size > MAX_MEDIA_BYTES:
                return None
            tgfile = await media.get_file()
            data = bytes(await tgfile.download_to_memory())
            name = getattr(media, "file_name", None) or f"voce-{stamp}.ogg"
            mime = getattr(media, "mime_type", "") or "audio/ogg"
            kind = "voice" if msg.voice else "audio"
            return [Attachment(kind, name, mime, data)]
        if msg.photo:
            tgfile = await msg.photo[-1].get_file()  # la versione piu' grande
            data = bytes(await tgfile.download_to_memory())
            if len(data) > MAX_MEDIA_BYTES:
                return None
            return [Attachment("photo", f"foto-{stamp}.jpg", "image/jpeg", data)]
        if msg.document:
            doc = msg.document
            if doc.file_size and doc.file_size > MAX_MEDIA_BYTES:
                return None
            tgfile = await doc.get_file()
            data = bytes(await tgfile.download_to_memory())
            return [Attachment("file", doc.file_name or "documento.bin",
                               doc.mime_type or "application/octet-stream", data)]
        return []

    def send_message(self, chat_id: str, text: str) -> None:
        import asyncio
        import time
        deadline = time.monotonic() + 10
        while self._loop is None:
            if time.monotonic() > deadline:
                raise RuntimeError("telegram: loop non pronto")
            time.sleep(0.05)
        fut = asyncio.run_coroutine_threadsafe(
            self._app.bot.send_message(chat_id=int(chat_id), text=text),
            self._loop)
        fut.result(timeout=30)
