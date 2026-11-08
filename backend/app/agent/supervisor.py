"""Watchdog con backoff esponenziale e freno anti crash-loop (spec sez. 8).

due usi:
- run_supervised(cmd, ...): sorveglia un PROCESSO (uso standalone, quando
  l'agente non gira come servizio di sistema);
- RestartPolicy: la stessa logica (backoff + limite) applicata ai thread
  dei canali dentro l'agente: un canale che cade viene riesumato con
  backoff; esauriti i tentativi, si notifica sugli altri canali e si
  registra l'incidente.
"""
from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable


class RestartPolicy:
    """Backoff esponenziale con limite di riavvii in una finestra."""

    def __init__(self, max_restarts: int = 5, window_seconds: float = 600.0,
                 backoff_base: float = 2.0, backoff_max: float = 300.0):
        self.max_restarts = max_restarts
        self.window = window_seconds
        self.base = backoff_base
        self.cap = backoff_max
        self._restarts: list[float] = []
        self._lock = threading.Lock()

    def next_delay(self, now: float | None = None) -> float | None:
        """Secondi da attendere prima del prossimo riavvio, o None se il
        limite di riavvii nella finestra e' esaurito (crash-loop)."""
        now = time.time() if now is None else now
        with self._lock:
            self._restarts = [t for t in self._restarts
                              if now - t < self.window]
            if len(self._restarts) >= self.max_restarts:
                return None
            delay = min(self.base ** (len(self._restarts) + 1), self.cap)
            self._restarts.append(now)
            return delay

    def reset(self) -> None:
        with self._lock:
            self._restarts.clear()


def run_supervised(cmd: list[str], *, policy: RestartPolicy | None = None,
                   on_incident: Callable[[str], None] | None = None,
                   cwd: str | None = None) -> int:
    """Esegue cmd; se cade lo riavvia con backoff. Ritorna l'ultimo exit
    code quando il processo esce pulito o quando scatta il freno
    anti crash-loop."""
    policy = policy or RestartPolicy()
    note = on_incident or (lambda msg: print(f"[supervisor] {msg}", flush=True))
    while True:
        proc = subprocess.run(cmd, cwd=cwd)
        if proc.returncode == 0:
            return 0
        delay = policy.next_delay()
        if delay is None:
            note(f"crash-loop: {policy.max_restarts} riavvii in "
                 f"{policy.window}s, mi fermo (ultimo exit {proc.returncode})")
            return proc.returncode
        note(f"processo uscito con codice {proc.returncode}, "
             f"riavvio tra {delay:.0f}s")
        time.sleep(delay)


def supervise_channel(name: str, start_fn: Callable[[], None],
                      *, policy: RestartPolicy | None = None,
                      on_incident: Callable[[str], None] | None = None,
                      on_give_up: Callable[[str], None] | None = None,
                      stop: threading.Event | None = None) -> threading.Thread:
    """Avvia start_fn in un thread che la riesuma se cade (canale caduto ->
    riconnessione con backoff). on_give_up(name) scatta a tentativi
    esauriti: l'agente avvisa Fred su un altro canale."""
    policy = policy or RestartPolicy()
    stop = stop or threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                start_fn()
                return  # uscita pulita: niente riavvio
            except Exception as exc:
                delay = policy.next_delay()
                if delay is None:
                    if on_incident:
                        on_incident(f"canale {name}: crash-loop, rinuncio "
                                    f"({exc})")
                    if on_give_up:
                        on_give_up(name)
                    return
                if on_incident:
                    on_incident(f"canale {name} caduto: {exc}; "
                                f"riavvio tra {delay:.0f}s")
                stop.wait(delay)

    thread = threading.Thread(target=loop, daemon=True, name=f"ch-{name}")
    thread.start()
    return thread
