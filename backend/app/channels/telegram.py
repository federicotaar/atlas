"""Canale Telegram (spec sez. 6, build order 5): long polling, nessun server
pubblico. Token da .env (TELEGRAM_BOT_TOKEN), mai in config ne' nei log.

Il client reale usa python-telegram-bot; i test iniettano un client finto con
la stessa interfaccia (start_polling/on_update + send_message), quindi la
logica del canale e' coperta senza rete.
"""
from __future__ import annotations

import threading
from typing import Callable

from .base import Channel, ChannelMessage


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, token: str, client_factory: Callable | None = None,
                 drop_pending_updates: bool = True):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN mancante")
        self._token = token
        self._client_factory = client_factory or _PTBClient
        self._drop_pending = drop_pending_updates
        self._client = None
        self._handler: Callable[[ChannelMessage], None] | None = None

    def on_message(self, handler: Callable[[ChannelMessage], None]) -> None:
        self._handler = handler

    def start(self) -> None:
        self._client = self._client_factory(self._token, self._drop_pending)
        self._client.start_polling(self._on_update)

    def send(self, to: str, text: str) -> None:
        if self._client is None:
            raise RuntimeError("canale non avviato")
        self._client.send_message(to, text)

    def _on_update(self, chat_id: str, text: str, display: str = "") -> None:
        if self._handler is not None:
            self._handler(ChannelMessage(sender=str(chat_id),
                                         channel=self.name, text=text,
                                         display=display))


class _PTBClient:
    """Adattatore python-telegram-bot: polling in thread daemon, invio via
    event loop dell'app. Import pigro: i test non richiedono la libreria."""

    def __init__(self, token: str, drop_pending_updates: bool = True):
        from telegram.ext import ApplicationBuilder  # import pigro
        self._app = ApplicationBuilder().token(token).build()
        self._drop_pending = drop_pending_updates
        self._loop = None

    def start_polling(self, on_update: Callable[..., None]) -> None:
        import asyncio

        from telegram.ext import MessageHandler, filters

        async def _recv(update, context) -> None:
            msg = update.message
            if msg is not None and msg.text:
                user = msg.from_user
                display = ""
                if user is not None:
                    parts = [p for p in (user.first_name, user.last_name) if p]
                    display = " ".join(parts)
                    if user.username:
                        display += f" (@{user.username})"
                on_update(str(msg.chat_id), msg.text, display.strip())

        async def _post_init(app) -> None:
            self._loop = asyncio.get_running_loop()

        self._app.add_handler(MessageHandler(filters.TEXT, _recv))
        self._app.post_init = _post_init
        # stop_signals=None: run_polling gira in un thread NON principale
        # (la CLI resta interattiva); asyncio vieta i signal handler fuori
        # dal main thread. Lo stop resta del processo/REPL.
        threading.Thread(target=self._app.run_polling,
                         kwargs={"drop_pending_updates": self._drop_pending,
                                 "stop_signals": None},
                         daemon=True).start()

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
