"""Aufraeumen im jobs-Prozess (NPT-12 C6).

``health_probe`` enthaelt je Worker eine Zeile (``hostname:rolle:pid``). In Kubernetes
bekommt jede Pod-Generation neue Hostnamen; Zeilen beendeter Worker werden nach 24 h
geloescht. Laeuft hoechstens einmal je Stunde und Prozess; mehrere Instanzen sind
unkritisch (idempotentes DELETE).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from npt import limits

log = logging.getLogger("npt.jobs.housekeeping")


def delete_stale_probes(
    session: Session, max_age_s: float = limits.HEALTH_PROBE_RETENTION_S
) -> int:
    result = session.execute(
        text("DELETE FROM health_probe WHERE probed_at < now() - make_interval(secs => :s)"),
        {"s": max_age_s},
    )
    return int(getattr(result, "rowcount", 0) or 0)


def make_job(
    session_factory: Callable[[], Session],
    interval_s: float = limits.HEALTH_PROBE_CLEANUP_INTERVAL_S,
) -> Callable[[], None]:
    last: float | None = None

    def job() -> None:
        nonlocal last
        now = time.monotonic()
        if last is not None and now - last < interval_s:
            return
        with session_factory() as session:
            deleted = delete_stale_probes(session)
            session.commit()
        last = now
        if deleted:
            log.info("Alte Health-Proben geloescht", extra={"anzahl": deleted})

    return job
