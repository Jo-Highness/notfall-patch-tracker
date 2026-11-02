"""Vorschau und Uebernahme eines Projekt-Uploads (Auftrag 6.1, NPT-04).

- ``stage``: geparste Datei -> ``staged_uploads`` (kind=project, 2 h gueltig).
- ``preview``: Diff gegen den aktuellen Projektbestand + Fingerprint.
- ``apply``: Projektzeile sperren (Sperrreihenfolge: zuerst Projekt, dann Systeme),
  Diff neu berechnen; weicht der Fingerprint ab, wird **nichts** geschrieben
  (:class:`StaleUploadError`, der Aufrufer zeigt die aktualisierte Vorschau).
  Sonst Backup ``vor_import`` und alle Aenderungen in derselben Transaktion,
  ``change_log`` Quelle ``admin_upload`` mit Admin-Identifier, ``NOTIFY`` an alle Clients.

Commit macht der Aufrufer; bei Fehlern rollt er zurueck (die Backup-Dateien eines
zurueckgerollten Versuchs werden hier entfernt).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast

from sqlalchemy import Table, delete, func, insert, select, text, update
from sqlalchemy.orm import Session

from npt import backups
from npt.admins import Actor, audit
from npt.db.models import ChangeLog, ProjectSystem, StagedUpload
from npt.domain import fields
from npt.editing import lock_project, notify
from npt.masterdata.diff import FieldChange
from npt.masterdata.source import ValidationReport
from npt.project_upload.diff import UploadDiff, compute_diff, conflict_key
from npt.project_upload.parse import ParsedUpload, UploadRecord

STAGE_TTL: Final = timedelta(hours=2)
STAGE_KIND: Final = "project"

_ps = cast(Table, ProjectSystem.__table__)


class UploadError(Exception):
    """Fachlicher Fehler; ``code`` ist ein fester Meldungsschluessel fuer die UI."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StaleUploadError(UploadError):
    """Der Projektbestand hat sich an betroffenen Feldern geaendert."""

    def __init__(self) -> None:
        super().__init__("upload_vorschau_veraltet")


@dataclass
class Staged:
    id: str
    project_id: int
    created_at: datetime
    created_by: str
    file_name: str | None
    file_hash: str
    records: list[UploadRecord]
    columns: list[str]
    report: ValidationReport


@dataclass
class Preview:
    staged: Staged
    diff: UploadDiff
    fingerprint: str

    @property
    def can_apply(self) -> bool:
        return self.staged.report.ok


@dataclass(frozen=True)
class Selection:
    """Auswahl des Admins: Konflikte (alle oder einzeln) und anzulegende IDs."""

    accept_all: bool = False
    conflicts: frozenset[str] = frozenset()
    create: frozenset[str] = frozenset()

    def accepts(self, system_id: str, change: FieldChange) -> bool:
        return self.accept_all or conflict_key(system_id, change) in self.conflicts


@dataclass(frozen=True)
class ApplyResult:
    seq: int | None
    systems_changed: int
    fields_filled: int
    conflicts_taken: int
    conflicts_skipped: int
    created: int
    backup_id: int | None
    changed_ids: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def purge_expired(session: Session) -> None:
    session.execute(delete(StagedUpload).where(StagedUpload.expires_at < func.now()))


def stage(
    session: Session,
    project_id: int,
    parsed: ParsedUpload,
    *,
    created_by: str,
    file_name: str | None,
    file_hash: str,
) -> str:
    purge_expired(session)
    stage_id = secrets.token_urlsafe(24)
    session.add(
        StagedUpload(
            id=stage_id,
            kind=STAGE_KIND,
            project_id=project_id,
            created_by=created_by,
            file_name=file_name,
            file_hash=file_hash,
            payload={
                "columns": parsed.columns,
                "records": [r.to_json() for r in parsed.records],
                "report": parsed.report.to_json(),
            },
            expires_at=_now() + STAGE_TTL,
        )
    )
    session.flush()
    return stage_id


def load_staged(session: Session, project_id: int, stage_id: str) -> Staged:
    row = session.get(StagedUpload, stage_id)
    if (
        row is None
        or row.kind != STAGE_KIND
        or row.project_id != project_id
        or row.expires_at < _now()
    ):
        raise UploadError("upload_nicht_gefunden")
    payload = row.payload
    return Staged(
        id=row.id,
        project_id=project_id,
        created_at=row.created_at,
        created_by=row.created_by,
        file_name=row.file_name,
        file_hash=row.file_hash,
        records=[UploadRecord.from_json(r) for r in payload["records"]],
        columns=list(payload["columns"]),
        report=ValidationReport.from_json(payload["report"]),
    )


def list_open_stages(session: Session, project_id: int) -> list[StagedUpload]:
    stmt = (
        select(StagedUpload)
        .where(
            StagedUpload.kind == STAGE_KIND,
            StagedUpload.project_id == project_id,
            StagedUpload.expires_at >= func.now(),
        )
        .order_by(StagedUpload.created_at.desc())
    )
    return list(session.scalars(stmt))


def discard(session: Session, actor: Actor, project_id: int, stage_id: str) -> None:
    session.execute(
        delete(StagedUpload).where(
            StagedUpload.id == stage_id,
            StagedUpload.kind == STAGE_KIND,
            StagedUpload.project_id == project_id,
        )
    )
    audit(session, actor, "projekt_upload_verworfen", stage_id[:8], {"project_id": project_id})


def current_rows(
    session: Session, project_id: int, *, for_update: bool = False
) -> dict[str, dict[str, Any]]:
    cols = [_ps.c[c] for c in ("id", *fields.SYSTEM_FIELDS)]
    stmt = select(*cols).where(_ps.c.project_id == project_id)
    if for_update:
        stmt = stmt.with_for_update()
    return {r["system_id"]: dict(r) for r in session.execute(stmt).mappings()}


def preview(session: Session, project_id: int, stage_id: str) -> Preview:
    staged = load_staged(session, project_id, stage_id)
    diff = compute_diff(current_rows(session, project_id), staged.records, staged.columns)
    return Preview(staged, diff, diff.fingerprint())


def apply(
    session: Session,
    actor: Actor,
    project_id: int,
    stage_id: str,
    fingerprint: str,
    selection: Selection,
    *,
    backup_dir: Path,
) -> ApplyResult:
    """Uebernahme in einer Transaktion; Diff unter Projektsperre neu berechnet."""
    seq = lock_project(session, project_id)
    staged = load_staged(session, project_id, stage_id)
    if not staged.report.ok:
        raise UploadError("upload_hat_fehler")
    current = current_rows(session, project_id, for_update=True)
    diff = compute_diff(current, staged.records, staged.columns)
    if diff.fingerprint() != fingerprint or not selection.create <= diff.creatable_ids:
        raise StaleUploadError

    plan: list[tuple[str, list[FieldChange]]] = []
    taken = skipped = filled = 0
    for sysdiff in diff.systems:
        changes = list(sysdiff.fills)
        filled += len(sysdiff.fills)
        for change in sysdiff.conflicts:
            if selection.accepts(sysdiff.system_id, change):
                changes.append(change)
                taken += 1
            else:
                skipped += 1
        if changes:
            plan.append((sysdiff.system_id, changes))
    to_create = [u for u in diff.unknown if u.system_id in selection.create]

    if not plan and not to_create:
        # Nichts zu schreiben: change_seq nicht verbrauchen, kein Backup.
        session.rollback()
        session.execute(delete(StagedUpload).where(StagedUpload.id == stage_id))
        audit(
            session,
            actor,
            "projekt_upload_uebernommen",
            staged.file_name,
            {"project_id": project_id, "geaendert": 0, "konflikte_abgelehnt": skipped},
        )
        return ApplyResult(None, 0, 0, 0, skipped, 0, None)

    created_backup = backups.create_backup(
        session,
        project_id,
        "vor_import",
        backup_dir=backup_dir,
        created_by=actor.identifier,
        change_seq=seq - 1,
    )
    try:
        log_rows = _write(session, actor, project_id, seq, current, plan, to_create)
        if log_rows:
            session.execute(insert(ChangeLog), log_rows)
        notify(session, project_id, seq, "upload")
        session.execute(delete(StagedUpload).where(StagedUpload.id == stage_id))
        result = ApplyResult(
            seq=seq,
            systems_changed=len(plan),
            fields_filled=filled,
            conflicts_taken=taken,
            conflicts_skipped=skipped,
            created=len(to_create),
            backup_id=created_backup.backup.id,
            changed_ids=[sid for sid, _ in plan],
        )
        audit(
            session,
            actor,
            "projekt_upload_uebernommen",
            staged.file_name,
            {
                "project_id": project_id,
                "file_hash": staged.file_hash,
                "systeme_geaendert": result.systems_changed,
                "neubefuellungen": filled,
                "konflikte_uebernommen": taken,
                "konflikte_abgelehnt": skipped,
                "angelegt": result.created,
                "backup_id": result.backup_id,
            },
        )
        session.flush()
    except BaseException:
        backups.remove_files(created_backup.files)
        raise
    return result


def _write(
    session: Session,
    actor: Actor,
    project_id: int,
    seq: int,
    current: dict[str, dict[str, Any]],
    plan: list[tuple[str, list[FieldChange]]],
    to_create: list[Any],
) -> list[dict[str, Any]]:
    common: dict[str, Any] = {
        "project_id": project_id,
        "seq": seq,
        "source": "admin_upload",
        "client_ip": actor.client_ip,
        "admin_identifier": actor.identifier,
    }
    log_rows: list[dict[str, Any]] = []
    for system_id, changes in plan:
        values: dict[str, Any] = {c.field: c.new for c in changes}
        values.update(version=_ps.c.version + 1, row_seq=seq, updated_at=text("now()"))
        session.execute(update(_ps).where(_ps.c.id == current[system_id]["id"]).values(values))
        log_rows.extend(
            {
                **common,
                "system_id": system_id,
                "action": "geaendert",
                "field": c.field,
                "old_value": c.old,
                "new_value": c.new,
            }
            for c in changes
        )
    for unknown in to_create:
        values = {f: unknown.values.get(f) for f in fields.SYSTEM_FIELDS[1:]}
        session.execute(
            insert(_ps).values(
                project_id=project_id,
                system_id=unknown.system_id,
                origin="admin_upload",
                version=1,
                row_seq=seq,
                **values,
            )
        )
        log_rows.append(
            {
                **common,
                "system_id": unknown.system_id,
                "action": "angelegt",
                "field": None,
                "old_value": None,
                "new_value": values.get("system_name"),
            }
        )
        log_rows.extend(
            {
                **common,
                "system_id": unknown.system_id,
                "action": "geaendert",
                "field": name,
                "old_value": None,
                "new_value": values[name],
            }
            for name in fields.SYSTEM_FIELDS[1:]
            if values[name] is not None
        )
    return log_rows
