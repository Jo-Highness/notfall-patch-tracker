"""Vorschau und Uebernahme von Stammdatenimporten (Auftrag 4, NPT-04).

- ``stage``: Quelle abgerufen und validiert -> ``staged_uploads`` (kind=master).
- ``preview``: Diff gegen den aktuellen Bestand + Fingerprint.
- ``apply``: unter Advisory Lock neu berechnen; weicht der Fingerprint ab, wird
  nicht geschrieben (:class:`StalePreviewError`) und eine neue Vorschau gezeigt.
  Im Upload fehlende Systeme werden nur entfernt, wenn der Admin sie ausdruecklich
  ausgewaehlt hat; sonst bleiben sie erhalten und werden ueber ``missing_since``
  markiert.

Alle Funktionen arbeiten in der Transaktion der uebergebenen Session; Commit
macht der Aufrufer. Das Audit-Log wird in derselben Transaktion geschrieben.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from npt.admins import Actor, audit
from npt.db.models import MasterImport, MasterSystem, StagedUpload
from npt.domain import fields
from npt.masterdata.diff import CurrentSystem, MasterDiff, compute_diff
from npt.masterdata.source import MasterRecord, SourceResult, ValidationReport

STAGE_TTL: Final = timedelta(hours=2)
# Konstanter Schluessel fuer pg_advisory_xact_lock: Stammdaten-Uebernahmen seriell.
MASTER_LOCK_KEY: Final = 0x4E50_544D  # "NPTM"
DETAIL_IDS_MAX: Final = 2000


class MasterImportError(Exception):
    """Fachlicher Fehler; ``code`` ist ein fester Meldungsschluessel fuer die UI."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StalePreviewError(MasterImportError):
    def __init__(self) -> None:
        super().__init__("vorschau_veraltet")


@dataclass
class Staged:
    id: str
    created_at: datetime
    created_by: str
    file_name: str | None
    file_hash: str
    source: str
    records: list[MasterRecord]
    columns: list[str]
    report: ValidationReport


@dataclass
class Preview:
    staged: Staged
    diff: MasterDiff
    fingerprint: str

    @property
    def can_apply(self) -> bool:
        return self.staged.report.ok


@dataclass(frozen=True)
class ImportResult:
    import_id: int
    count_new: int
    count_changed: int
    count_removed: int
    count_missing_kept: int


def _now() -> datetime:
    return datetime.now(tz=UTC)


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def purge_expired(session: Session) -> None:
    session.execute(delete(StagedUpload).where(StagedUpload.expires_at < func.now()))


def stage(
    session: Session,
    result: SourceResult,
    *,
    created_by: str,
    file_name: str | None,
    file_hash: str,
) -> str:
    """Abgerufene und validierte Daten fuer die Vorschau ablegen; liefert die Stage-ID."""
    purge_expired(session)
    stage_id = secrets.token_urlsafe(24)
    session.add(
        StagedUpload(
            id=stage_id,
            kind="master",
            created_by=created_by,
            file_name=file_name,
            file_hash=file_hash,
            payload={
                "source": result.source,
                "columns": result.columns,
                "records": [r.to_json() for r in result.records],
                "report": result.report.to_json(),
            },
            expires_at=_now() + STAGE_TTL,
        )
    )
    session.flush()
    return stage_id


def load_staged(session: Session, stage_id: str) -> Staged:
    row = session.get(StagedUpload, stage_id)
    if row is None or row.kind != "master" or row.expires_at < _now():
        raise MasterImportError("vorschau_nicht_gefunden")
    payload = row.payload
    return Staged(
        id=row.id,
        created_at=row.created_at,
        created_by=row.created_by,
        file_name=row.file_name,
        file_hash=row.file_hash,
        source=str(payload.get("source", "excel")),
        records=[MasterRecord.from_json(r) for r in payload["records"]],
        columns=list(payload["columns"]),
        report=ValidationReport.from_json(payload["report"]),
    )


def discard(session: Session, actor: Actor, stage_id: str) -> None:
    session.execute(
        delete(StagedUpload).where(StagedUpload.id == stage_id, StagedUpload.kind == "master")
    )
    audit(session, actor, "stammdaten_vorschau_verworfen", stage_id[:8])


def current_master(session: Session, *, for_update: bool = False) -> dict[str, CurrentSystem]:
    cols = [getattr(MasterSystem, f) for f in fields.MASTER_FIELDS]
    stmt = select(*cols, MasterSystem.missing_since)
    if for_update:
        stmt = stmt.with_for_update()
    out: dict[str, CurrentSystem] = {}
    for row in session.execute(stmt):
        data = row._asdict()
        ms = data.pop("missing_since")
        sid = data.pop("system_id")
        out[sid] = CurrentSystem(data, ms.isoformat() if ms is not None else None)
    return out


def preview(session: Session, stage_id: str) -> Preview:
    staged = load_staged(session, stage_id)
    diff = compute_diff(current_master(session), staged.records, staged.columns)
    return Preview(staged, diff, diff.fingerprint())


def apply(
    session: Session,
    actor: Actor,
    stage_id: str,
    fingerprint: str,
    remove_ids: set[str],
) -> ImportResult:
    """Uebernahme in einer Transaktion; Diff unter Sperre neu berechnet."""
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": MASTER_LOCK_KEY})
    staged = load_staged(session, stage_id)
    if not staged.report.ok:
        raise MasterImportError("vorschau_hat_fehler")
    diff = compute_diff(current_master(session, for_update=True), staged.records, staged.columns)
    if diff.fingerprint() != fingerprint:
        raise StalePreviewError
    missing_ids = {m.system_id for m in diff.missing}
    if not remove_ids <= missing_ids:
        raise StalePreviewError
    now = _now()
    source = staged.source

    if diff.new:
        rows = []
        for rec in diff.new:
            row: dict[str, Any] = dict.fromkeys(fields.MASTER_DATA_FIELDS)
            row.update({k: v for k, v in rec.values.items() if k in fields.MASTER_DATA_FIELDS})
            row.update(system_id=rec.system_id, source=source, created_at=now, updated_at=now)
            rows.append(row)
        for start in range(0, len(rows), 1000):
            session.execute(insert(MasterSystem), rows[start : start + 1000])
    for changed in diff.changed:
        values: dict[str, Any] = {c.field: c.new for c in changed.changes}
        values.update(missing_since=None, updated_at=now, source=source)
        session.execute(
            update(MasterSystem).where(MasterSystem.system_id == changed.system_id).values(values)
        )
    if remove_ids:
        session.execute(delete(MasterSystem).where(MasterSystem.system_id.in_(remove_ids)))
    kept = sorted(missing_ids - remove_ids)
    newly_marked = [
        m.system_id for m in diff.missing if m.system_id in kept and not m.missing_since
    ]
    if newly_marked:
        session.execute(
            update(MasterSystem)
            .where(MasterSystem.system_id.in_(newly_marked))
            .values(missing_since=now)
        )
    record = MasterImport(
        admin_identifier=actor.identifier,
        source=source,
        file_name=staged.file_name,
        file_hash=staged.file_hash,
        count_new=len(diff.new),
        count_changed=len(diff.changed),
        count_removed=len(remove_ids),
        count_missing_kept=len(kept),
        details={
            "unchanged": diff.unchanged,
            "warnings": staged.report.warning_count,
            "rejected_reserved": staged.report.rejected_reserved,
            "invalid_emails": staged.report.invalid_emails,
            "new_ids": [r.system_id for r in diff.new][:DETAIL_IDS_MAX],
            "changed_ids": [c.system_id for c in diff.changed][:DETAIL_IDS_MAX],
            "removed_ids": sorted(remove_ids)[:DETAIL_IDS_MAX],
            "missing_kept_ids": kept[:DETAIL_IDS_MAX],
        },
    )
    session.add(record)
    session.execute(delete(StagedUpload).where(StagedUpload.id == stage_id))
    session.flush()
    audit(
        session,
        actor,
        "stammdaten_importiert",
        staged.file_name,
        {
            "import_id": record.id,
            "file_hash": staged.file_hash,
            "neu": len(diff.new),
            "geaendert": len(diff.changed),
            "entfernt": len(remove_ids),
            "fehlend_behalten": len(kept),
        },
    )
    return ImportResult(record.id, len(diff.new), len(diff.changed), len(remove_ids), len(kept))


def list_imports(session: Session, limit: int = 50) -> list[MasterImport]:
    stmt = select(MasterImport).order_by(MasterImport.id.desc()).limit(limit)
    return list(session.scalars(stmt))


def list_open_stages(session: Session) -> list[StagedUpload]:
    stmt = (
        select(StagedUpload)
        .where(StagedUpload.kind == "master", StagedUpload.expires_at >= func.now())
        .order_by(StagedUpload.created_at.desc())
    )
    return list(session.scalars(stmt))


@dataclass(frozen=True)
class MasterStats:
    total: int
    missing_marked: int
    last_update: datetime | None


def stats(session: Session) -> MasterStats:
    total, missing, last = session.execute(
        select(
            func.count(),
            func.count(MasterSystem.missing_since),
            func.max(MasterSystem.updated_at),
        ).select_from(MasterSystem)
    ).one()
    return MasterStats(int(total), int(missing), last)


def list_missing(session: Session, limit: int = 500) -> list[MasterSystem]:
    stmt = (
        select(MasterSystem)
        .where(MasterSystem.missing_since.is_not(None))
        .order_by(MasterSystem.system_id)
        .limit(limit)
    )
    return list(session.scalars(stmt))
