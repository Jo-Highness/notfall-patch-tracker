"""Zeitgesteuerte Projekt-Backups im jobs-Prozess (Auftrag 6.2, NPT-05).

Jeder Tick (15 s) prueft alle Projekte; gesichert wird nur bei Datenaenderung seit dem
letzten Backup und abgelaufenem Intervall. ``pg_try_advisory_xact_lock`` je Projekt
sorgt dafuer, dass bei mehreren Instanzen genau eine sichert (``npt.backups.run_due``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from npt import backups
from npt.config import Settings

log = logging.getLogger("npt.jobs.backups")


def make_job(
    settings: Settings,
    session_factory: Callable[[], Session],
    progress: Callable[[], None] | None = None,
) -> Callable[[], None]:
    """``progress`` wird je Projekt aufgerufen (Heartbeat bei langen Laeufen)."""
    backup_dir = Path(settings.BACKUP_DIR)

    def job() -> None:
        created = backups.run_due(session_factory, backup_dir, progress=progress)
        if created:
            log.info("Zeitgesteuerte Backups erstellt", extra={"anzahl": len(created)})

    return job
