"""Optionaler zeitgesteuerter Stammdaten-API-Abruf (Stub, Auftrag 4 / ARCHITECTURE A3).

Legt ausschliesslich eine **Vorschau** an; die Uebernahme bestaetigt ein Admin
in der Stammdatenpflege. Ohne ``MASTER_API_ENABLED`` und
``MASTER_API_SCHEDULE_MINUTES > 0`` wird der Job nicht registriert.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from npt.admins import Actor, audit
from npt.config import Settings
from npt.masterdata import service
from npt.masterdata.api import ApiMasterDataSource, ApiPullSchedule

log = logging.getLogger("npt.jobs.master_api")

API_ACTOR = "stammdaten-api"


def pull_preview(session: Session, settings: Settings) -> str | None:
    """Einmal abrufen und als Vorschau ablegen (genau ein Prozess per Advisory Lock)."""
    got = session.execute(
        text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": service.MASTER_LOCK_KEY + 1}
    ).scalar()
    if not got:
        return None
    result = ApiMasterDataSource(settings).load()
    digest = service.file_sha256(
        json.dumps([r.to_json() for r in result.records], sort_keys=True).encode()
    )
    stage_id = service.stage(
        session, result, created_by=f"system:{API_ACTOR}", file_name="API-Abruf", file_hash=digest
    )
    audit(session, Actor.system(API_ACTOR), "stammdaten_api_vorschau", stage_id[:8])
    return stage_id


def make_job(
    settings: Settings, session_factory: Callable[[], Session]
) -> Callable[[], None] | None:
    if not settings.MASTER_API_ENABLED or settings.MASTER_API_SCHEDULE_MINUTES <= 0:
        return None
    schedule = ApiPullSchedule(settings)

    def job() -> None:
        if not schedule.due():
            return
        with session_factory() as session:
            stage_id = pull_preview(session, settings)
            session.commit()
        if stage_id:
            log.info("Stammdaten-API: Vorschau angelegt (Bestätigung durch Admin nötig)")

    return job
