"""Gesundheitsmonitor je Worker (NPT-09).

``/healthz`` und ``/readyz`` fassen die Datenbank nie selbst an, sondern liefern
nur den Schnappschuss dieses Monitors. Der Monitor schreibt alle 10 s eine Probe
in ``health_probe`` (eigene Mini-Engine, belastet den Request-Pool nicht). Steht
er laenger als 120 s still, beendet sich der Worker selbst (Gunicorn startet neu).
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from dataclasses import dataclass

from sqlalchemy import Engine, text

from npt import limits

log = logging.getLogger("npt.health")


@dataclass(frozen=True)
class HealthSnapshot:
    alive: bool
    ready: bool
    last_tick_age_s: float | None
    last_ok_age_s: float | None
    error: str | None


class HealthMonitor:
    def __init__(self, role: str = "web") -> None:
        self._engine: Engine | None = None
        self._role = role
        self._stop = threading.Event()
        self._last_tick: float | None = None
        self._last_ok: float | None = None
        self._error: str | None = None
        self._threads: list[threading.Thread] = []
        self.worker_id = f"{socket.gethostname()}:{role}:{os.getpid()}"

    # -- Lebenszyklus ------------------------------------------------------
    def start(self) -> None:
        from npt.db.session import make_engine

        self._engine = make_engine(pool_size=1)
        self._last_tick = time.monotonic()
        for target, name in ((self._probe_loop, "health-probe"), (self._watchdog, "health-dog")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=limits.HEALTH_PROBE_TIMEOUT_S + 1)
        if self._engine is not None:
            self._engine.dispose()

    # -- Probe ------------------------------------------------------------
    def probe_once(self) -> None:
        if self._engine is None:
            raise RuntimeError("HealthMonitor nicht gestartet")
        started = time.monotonic()
        try:
            with self._engine.begin() as conn:
                timeout_ms = int(limits.HEALTH_PROBE_TIMEOUT_S * 1000)
                conn.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
                latency = (time.monotonic() - started) * 1000
                conn.execute(
                    text(
                        "INSERT INTO health_probe (worker_id, probed_at, latency_ms) "
                        "VALUES (:w, now(), :l) ON CONFLICT (worker_id) "
                        "DO UPDATE SET probed_at = now(), latency_ms = :l"
                    ),
                    {"w": self.worker_id, "l": latency},
                )
            self._last_ok = time.monotonic()
            self._error = None
        except Exception as exc:  # Probe darf nie den Monitor beenden
            self._error = type(exc).__name__
            log.warning("Health-Probe fehlgeschlagen", extra={"error": self._error})
        finally:
            self._last_tick = time.monotonic()

    def _probe_loop(self) -> None:
        while not self._stop.is_set():
            self.probe_once()
            self._stop.wait(limits.HEALTH_PROBE_INTERVAL_S)

    def _watchdog(self) -> None:
        while not self._stop.wait(5.0):
            tick = self._last_tick
            if tick is not None and time.monotonic() - tick > limits.HEALTH_STALL_EXIT_S:
                log.critical("Health-Monitor steht still, Worker beendet sich")
                os.kill(os.getpid(), signal.SIGTERM)
                return

    # -- Abfrage ----------------------------------------------------------
    def snapshot(self) -> HealthSnapshot:
        now = time.monotonic()
        tick_age = None if self._last_tick is None else now - self._last_tick
        ok_age = None if self._last_ok is None else now - self._last_ok
        alive = tick_age is not None and tick_age <= limits.HEALTH_STALL_EXIT_S
        ready = alive and ok_age is not None and ok_age <= limits.HEALTH_READY_MAX_AGE_S
        return HealthSnapshot(alive, ready, tick_age, ok_age, self._error)
