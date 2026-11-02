"""Hintergrundjobs (Backups, Mailversand) – Prozess ``python -m npt.jobs``.

Rahmen: Signalbehandlung, Tick-Schleife (15 s) und Job-Registry. Registriert werden
zeitgesteuerte Backups (``npt.jobs.backups``), das Aufraeumen alter Health-Proben
(``npt.jobs.housekeeping``) und optional der Stammdaten-API-Abruf; der Mailversand
laeuft als eigener Thread (``npt.jobs.mail``). Exklusivitaet ueber PostgreSQL Advisory
Locks (NPT-05). Optional beruehrt die Schleife vor und nach jedem Job eine
Heartbeat-Datei (``JOBS_HEARTBEAT_FILE``, Liveness in Kubernetes, NPT-12 K5).

Lange Jobs melden Fortschritt mit ``beat()`` (z. B. der Backup-Lauf je Projekt). Bewusst
kein Hintergrund-Thread: der Heartbeat soll nur bei echtem Fortschritt weiterlaufen, damit
ein haengender Job die Liveness-Probe weiterhin ausloest.
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable
from pathlib import Path
from types import FrameType

from npt.db.session import DB_UNAVAILABLE_ERRORS

log = logging.getLogger("npt.jobs")

TICK_S = 15.0
Job = Callable[[], None]
JOBS: list[tuple[str, Job]] = []


class _HeartbeatState:
    """Von run_forever gesetzt; beat() beruehrt die Datei (None = Heartbeat aus)."""

    path: Path | None = None


_HEARTBEAT = _HeartbeatState()


def touch_heartbeat(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.touch()
    except OSError as exc:  # Heartbeat darf die Jobs nie beenden
        log.warning("Heartbeat-Datei nicht schreibbar", extra={"error": type(exc).__name__})


def beat() -> None:
    """Fortschritt innerhalb eines laufenden Jobs melden (Heartbeat beruehren)."""
    touch_heartbeat(_HEARTBEAT.path)


def run_forever(stop: threading.Event | None = None, heartbeat: Path | None = None) -> int:
    stop = stop or threading.Event()
    _HEARTBEAT.path = heartbeat

    def _handle(signum: int, _frame: FrameType | None) -> None:
        log.info("Jobs-Prozess beendet sich", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    log.info("Jobs-Prozess gestartet", extra={"jobs": [name for name, _ in JOBS]})
    while not stop.is_set():
        touch_heartbeat(heartbeat)
        for name, job in JOBS:
            try:
                job()
            except DB_UNAVAILABLE_ERRORS as exc:
                # Datenbank weg (z. B. Neustart): kein Traceback je Tick, der naechste
                # Tick versucht es erneut.
                log.warning(
                    "Job: Datenbank nicht erreichbar",
                    extra={"job": name, "error": type(exc).__name__},
                )
            except Exception:
                log.exception("Job fehlgeschlagen", extra={"job": name})
            touch_heartbeat(heartbeat)
        stop.wait(TICK_S)
    _HEARTBEAT.path = None
    return 0
