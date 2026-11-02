"""Projektverwaltung (Admin, Auftrag 5.1/6.4/7).

Anlegen = Snapshot-Kopie des aktuellen Stammdatenbestands; spaetere Stammdaten-
Aenderungen wirken nicht auf bestehende Projekte. Alle Funktionen arbeiten in der
Transaktion der uebergebenen Session und schreiben das Audit-Log in derselben
Transaktion; Commit macht der Aufrufer.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import BigInteger, case, delete, func, insert, literal, select, update
from sqlalchemy.orm import Session

from npt.admins import Actor, audit
from npt.db.models import MasterSystem, Project, ProjectSystem
from npt.domain import fields
from npt.domain.validation import clean_single_line
from npt.editing import lock_project, notify

NAME_MAX: Final = 200
DESCRIPTION_MAX: Final = 2000
TOKEN_BYTES: Final = 32  # token_urlsafe(32): 256 Bit, 43 Zeichen (NPT-10)


class ProjectError(Exception):
    """Fachlicher Fehler; ``code`` ist ein fester Meldungsschluessel fuer die UI."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def create_project(session: Session, actor: Actor, name: str, description: str) -> Project:
    clean_name = clean_single_line(name)
    if not clean_name:
        raise ProjectError("projektname_fehlt")
    if len(clean_name) > NAME_MAX:
        raise ProjectError("projektname_zu_lang")
    desc = description.strip() or None
    if desc is not None and len(desc) > DESCRIPTION_MAX:
        raise ProjectError("beschreibung_zu_lang")
    project = Project(
        name=clean_name,
        description=desc,
        status="aktiv",
        access_token=new_token(),
        created_by=actor.identifier,
    )
    session.add(project)
    session.flush()
    cols = list(fields.MASTER_FIELDS)
    src = select(
        *[getattr(MasterSystem, c) for c in cols],
        literal(project.id, BigInteger),
        literal("stammdaten"),
    ).order_by(MasterSystem.system_id)
    result = session.execute(
        insert(ProjectSystem).from_select([*cols, "project_id", "origin"], src)
    )
    count = int(result.rowcount or 0)  # type: ignore[attr-defined]
    audit(
        session,
        actor,
        "projekt_angelegt",
        clean_name,
        {"project_id": project.id, "systeme": count},
    )
    return project


@dataclass(frozen=True)
class ProjectSummary:
    project: Project
    total: int
    processed: int  # System betroffen != leer
    manual: int

    @property
    def progress_pct(self) -> int:
        return round(100 * self.processed / self.total) if self.total else 0


def list_projects(session: Session) -> list[ProjectSummary]:
    ps = ProjectSystem
    counts = (
        select(
            ps.project_id,
            func.count().label("total"),
            func.count(ps.affected).label("processed"),
            func.sum(case((ps.origin == "manuell_angelegt", 1), else_=0)).label("manual"),
        )
        .group_by(ps.project_id)
        .subquery()
    )
    status_order = case(
        (Project.status == "aktiv", 0), (Project.status == "abgeschlossen", 1), else_=2
    )
    stmt = (
        select(Project, counts.c.total, counts.c.processed, counts.c.manual)
        .outerjoin(counts, counts.c.project_id == Project.id)
        .order_by(status_order, Project.created_at.desc())
    )
    return [
        ProjectSummary(p, int(t or 0), int(d or 0), int(m or 0))
        for p, t, d, m in session.execute(stmt)
    ]


def get_summary(session: Session, project_id: int) -> ProjectSummary:
    for item in list_projects(session):
        if item.project.id == project_id:
            return item
    raise ProjectError("projekt_nicht_gefunden")


def get_project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise ProjectError("projekt_nicht_gefunden")
    return project


def regenerate_token(session: Session, actor: Actor, project_id: int) -> Project:
    seq = lock_project(session, project_id, require_active=False)
    project = get_project(session, project_id)
    session.refresh(project)
    project.access_token = new_token()
    project.token_generation = project.token_generation + 1
    # Offene Live-Verbindungen mit dem alten Token werden beendet (NPT-03).
    notify(session, project_id, seq, "token")
    audit(
        session,
        actor,
        "projekt_token_neu",
        project.name,
        {"project_id": project.id, "token_generation": project.token_generation},
    )
    return project


STATUS_TRANSITIONS: Final[dict[str, set[str]]] = {
    "aktiv": {"abgeschlossen", "archiviert"},
    "abgeschlossen": {"aktiv", "archiviert"},
    "archiviert": {"abgeschlossen"},
}


def set_status(session: Session, actor: Actor, project_id: int, status: str) -> Project:
    seq = lock_project(session, project_id, require_active=False)
    project = get_project(session, project_id)
    session.refresh(project)
    if status not in STATUS_TRANSITIONS.get(project.status, set()):
        raise ProjectError("status_ungueltig")
    old = project.status
    now = _now()
    values: dict[str, Any] = {"status": status, "updated_at": now}
    if status == "abgeschlossen":
        values["closed_at"] = now
    elif status == "archiviert":
        values["archived_at"] = now
    elif status == "aktiv":
        values["closed_at"] = None
    session.execute(update(Project).where(Project.id == project_id).values(values))
    session.refresh(project)
    notify(session, project_id, seq, "status")
    audit(
        session,
        actor,
        "projekt_status",
        project.name,
        {"project_id": project_id, "von": old, "nach": status},
    )
    return project


def delete_project(session: Session, actor: Actor, project_id: int, confirm_name: str) -> str:
    seq = lock_project(session, project_id, require_active=False)
    project = get_project(session, project_id)
    if confirm_name.strip() != project.name:
        raise ProjectError("name_bestaetigung_falsch")
    name = project.name
    session.execute(delete(Project).where(Project.id == project_id))
    notify(session, project_id, seq, "deleted")
    audit(session, actor, "projekt_geloescht", name, {"project_id": project_id})
    return name


def find_systems(
    session: Session, project_id: int, query: str, limit: int = 200
) -> list[ProjectSystem]:
    stmt = select(ProjectSystem).where(ProjectSystem.project_id == project_id)
    q = query.strip()
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            ProjectSystem.system_id.ilike(pattern, escape="\\")
            | ProjectSystem.system_name.ilike(pattern, escape="\\")
        )
    return list(session.scalars(stmt.order_by(ProjectSystem.system_id).limit(limit)))


def get_system(session: Session, project_id: int, system_id: str) -> ProjectSystem:
    row = session.scalars(
        select(ProjectSystem).where(
            ProjectSystem.project_id == project_id, ProjectSystem.system_id == system_id
        )
    ).first()
    if row is None:
        raise ProjectError("system_nicht_gefunden")
    return row


def manual_systems(session: Session, project_id: int) -> list[dict[str, Any]]:
    cols = [getattr(ProjectSystem, c) for c in fields.LIST_FIELDS]
    stmt = (
        select(*cols)
        .where(ProjectSystem.project_id == project_id, ProjectSystem.origin == "manuell_angelegt")
        .order_by(ProjectSystem.system_id)
    )
    return [dict(r) for r in session.execute(stmt).mappings()]
