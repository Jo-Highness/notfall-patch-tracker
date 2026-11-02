"""Projekt-Backups (Auftrag 6.2, NPT-05).

Ausloeser: ``zeitgesteuert`` (Scheduler im jobs-Prozess, :func:`run_due`), ``manuell``,
``vor_import`` (Projekt-Upload) und ``vor_restore`` (``npt.restore``).

Erzeugungskern:

- Schnappschuss in **einer** SQL-Anweisung (Projekt + alle Systeme): unter
  READ COMMITTED sieht eine Anweisung genau einen Stand, ohne Schreiber zu blockieren.
- JSON (vollstaendig fuer den Restore, **ohne** ``access_token``/``token_generation``)
  und .xlsx (fuer Menschen, gleicher Formel-Injection-Schutz wie der Export).
- Ablage atomar im Backup-Volume: tmp-Datei, ``fsync``, ``rename``, ``fsync`` des
  Verzeichnisses. In ``backups`` stehen nur Pfade **relativ** zu ``BACKUP_DIR``;
  heruntergeladen wird ausschliesslich ueber die Backup-ID (kein Pfad aus dem Request).

Zeitgesteuert wird nur gesichert, wenn sich seit dem letzten Backup **Daten** geaendert
haben: ``GREATEST(max(row_seq), reset_seq) > last_backup_seq`` (Status- oder
Token-Wechsel erhoehen zwar ``change_seq``, aendern aber keine Daten). Exklusivitaet je
Projekt ueber ``pg_try_advisory_xact_lock`` – bei mehreren Instanzen sichert genau eine,
die anderen ueberspringen; nach dem Erwerb wird die Bedingung erneut geprueft.

Aufbewahrung: nach jedem zeitgesteuerten/manuellen Backup, nach einem Restore und beim
Speichern der Einstellungen bleiben die neuesten ``backup_retention`` Backups (alle
Ausloeser zaehlen); Dateien werden erst **nach** dem Commit geloescht.

Alle Funktionen arbeiten in der Transaktion der uebergebenen Session; Commit macht
der Aufrufer.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from sqlalchemy import ColumnElement, Table, delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from npt import export
from npt.admins import Actor, audit
from npt.db.models import Backup, Project, ProjectSystem
from npt.domain import fields, labels

log = logging.getLogger("npt.backups")

FORMAT: Final = "npt-projekt-backup"
FORMAT_VERSION: Final = 1
# Spalten eines Systems im JSON (alles, was fuer den Restore noetig ist).
SYSTEM_COLUMNS: Final[tuple[str, ...]] = (
    *fields.LIST_FIELDS,
    "version",
    "created_at",
    "updated_at",
)
PROJECT_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "name",
    "description",
    "status",
    "created_at",
    "created_by",
    "change_seq",
    "backup_interval_min",
    "backup_retention",
)

# Namensraum fuer die Advisory Locks je Projekt ("NPTB" << 32 | Projekt-ID).
BACKUP_LOCK_NS: Final = 0x4E50_5442
SCHEDULER_ACTOR: Final = "system:backup-scheduler"
RETENTION_MIN: Final = 1
RETENTION_MAX: Final = 10_000

_ps = cast(Table, ProjectSystem.__table__)
_pj = cast(Table, Project.__table__)


class BackupError(Exception):
    """Fachlicher Fehler; ``code`` ist ein fester Meldungsschluessel fuer die UI."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


@dataclass(frozen=True)
class Snapshot:
    project: dict[str, Any]
    systems: list[dict[str, Any]]

    @property
    def change_seq(self) -> int:
        return int(self.project["change_seq"])


def snapshot(session: Session, project_id: int) -> Snapshot:
    """Projekt + Systeme in einer Anweisung (konsistenter Stand)."""
    pcols = [_pj.c[c].label(f"p_{c}") for c in PROJECT_COLUMNS]
    scols = [_ps.c[c].label(f"s_{c}") for c in SYSTEM_COLUMNS]
    stmt = (
        select(*pcols, *scols)
        .select_from(_pj.outerjoin(_ps, _ps.c.project_id == _pj.c.id))
        .where(_pj.c.id == project_id)
        .order_by(_ps.c.system_id)
    )
    result = list(session.execute(stmt).mappings())
    if not result:
        raise BackupError("projekt_nicht_gefunden")
    project = {c: result[0][f"p_{c}"] for c in PROJECT_COLUMNS}
    systems = [
        {c: r[f"s_{c}"] for c in SYSTEM_COLUMNS} for r in result if r["s_system_id"] is not None
    ]
    return Snapshot(project, systems)


def backup_document(snap: Snapshot, trigger: str, created_at: datetime) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "created_at": created_at.isoformat(),
        "trigger": trigger,
        "project": {k: _iso(v) for k, v in snap.project.items()},
        "systems": [{k: _iso(v) for k, v in s.items()} for s in snap.systems],
    }


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover - z. B. Plattformen ohne Verzeichnis-fsync
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """Datei atomar schreiben: tmp im selben Verzeichnis, fsync, rename, fsync(dir)."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def project_dir(backup_dir: Path, project_id: int) -> Path:
    return backup_dir / str(int(project_id))


def resolve_path(backup_dir: Path, project_id: int, relative: str) -> Path:
    """Gespeicherten relativen Pfad aufloesen; muss im Projektverzeichnis liegen."""
    base = project_dir(backup_dir, project_id).resolve()
    candidate = (backup_dir / relative).resolve()
    if not candidate.is_relative_to(base) or candidate == base:
        raise BackupError("backup_nicht_gefunden")
    return candidate


@dataclass(frozen=True)
class CreatedBackup:
    backup: Backup
    files: tuple[Path, ...]


def create_backup(
    session: Session,
    project_id: int,
    trigger: str,
    *,
    backup_dir: Path,
    created_by: str | None = None,
    change_seq: int | None = None,
) -> CreatedBackup:
    """Backup schreiben und in ``backups`` eintragen.

    ``change_seq`` ueberschreibt die Sequenz des Schnappschusses – noetig, wenn der
    Aufrufer die Projektzeile bereits gesperrt und ``change_seq`` in seiner
    Transaktion erhoeht hat (``vor_import``/``vor_restore``: gesichert wird der Stand
    davor).
    """
    snap = snapshot(session, project_id)
    seq = snap.change_seq if change_seq is None else change_seq
    snap.project["change_seq"] = seq
    now = _now()
    doc = backup_document(snap, trigger, now)
    json_bytes = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()
    xlsx_bytes = export.systems_workbook(snap.systems)
    target = project_dir(backup_dir, project_id)
    target.mkdir(parents=True, exist_ok=True)
    stem = f"{now:%Y%m%dT%H%M%S}Z-{trigger}-{secrets.token_hex(4)}"
    json_file = target / f"{stem}.json"
    xlsx_file = target / f"{stem}.xlsx"
    written: list[Path] = []
    try:
        atomic_write(json_file, json_bytes)
        written.append(json_file)
        atomic_write(xlsx_file, xlsx_bytes)
        written.append(xlsx_file)
        backup = Backup(
            project_id=project_id,
            created_at=now,
            trigger=trigger,
            change_seq=seq,
            json_path=str(json_file.relative_to(backup_dir)),
            xlsx_path=str(xlsx_file.relative_to(backup_dir)),
            size_bytes=len(json_bytes) + len(xlsx_bytes),
            created_by=created_by,
        )
        session.add(backup)
        session.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(
                last_backup_seq=func.greatest(Project.last_backup_seq, seq),
                last_backup_at=now,
            )
        )
        session.flush()
    except BaseException:
        remove_files(written)
        raise
    log.info(
        "Backup erstellt",
        extra={"project_id": project_id, "trigger": trigger, "change_seq": seq},
    )
    return CreatedBackup(backup, tuple(written))


def remove_project_dir(backup_dir: Path, project_id: int) -> None:
    """Backup-Verzeichnis eines geloeschten Projekts entfernen (best effort)."""
    target = project_dir(backup_dir, project_id)
    if not target.is_dir() or target.is_symlink():
        return
    for path in target.iterdir():
        if path.is_file() and not path.is_symlink():
            remove_files([path])
    try:
        target.rmdir()
    except OSError:
        log.warning("Backup-Verzeichnis nicht leer", extra={"project_id": project_id})


def remove_files(paths: list[Path] | tuple[Path, ...]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - nur Protokoll
            log.warning("Backup-Datei nicht loeschbar", extra={"file": path.name})


# ---------------------------------------------------------------- Sperren, Faelligkeit
def lock_key(project_id: int) -> int:
    return (BACKUP_LOCK_NS << 32) | int(project_id)


def try_lock(session: Session, project_id: int) -> bool:
    """Transaktions-Advisory-Lock je Projekt ohne Warten (Scheduler)."""
    got = session.execute(
        text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": lock_key(project_id)}
    ).scalar()
    return bool(got)


def lock(session: Session, project_id: int) -> None:
    """Wie :func:`try_lock`, aber wartend (manuelles Backup, Einstellungen)."""
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key(project_id)})


def _data_seq() -> ColumnElement[Any]:
    latest_row = (
        select(func.coalesce(func.max(_ps.c.row_seq), 0))
        .where(_ps.c.project_id == _pj.c.id)
        .scalar_subquery()
    )
    return func.greatest(latest_row, _pj.c.reset_seq)


def due_projects(
    session: Session, now: datetime | None = None, project_id: int | None = None
) -> list[int]:
    """Projekte mit Intervall, Datenaenderung seit dem letzten Backup und abgelaufener Frist."""
    current: Any = now if now is not None else func.now()
    next_due = _pj.c.last_backup_at + func.make_interval(0, 0, 0, 0, 0, _pj.c.backup_interval_min)
    stmt = select(_pj.c.id).where(
        _pj.c.backup_interval_min > 0,
        _data_seq() > _pj.c.last_backup_seq,
        or_(_pj.c.last_backup_at.is_(None), next_due <= current),
    )
    if project_id is not None:
        stmt = stmt.where(_pj.c.id == project_id)
    return [int(pid) for pid in session.scalars(stmt.order_by(_pj.c.id))]


def has_changes(session: Session, project_id: int) -> bool:
    stmt = select(_data_seq() > _pj.c.last_backup_seq).where(_pj.c.id == project_id)
    return bool(session.scalar(stmt))


# ---------------------------------------------------------------- Aufbewahrung, Liste
def apply_retention(
    session: Session, project_id: int, *, backup_dir: Path, protect: Iterable[int] = ()
) -> list[Path]:
    """Aeltere Backups ueber ``backup_retention`` entfernen; liefert die zu loeschenden Dateien.

    Die Dateien loescht der Aufrufer erst nach dem Commit (:func:`remove_files`).
    """
    keep = session.scalar(select(Project.backup_retention).where(Project.id == project_id))
    if keep is None:
        return []
    protected = set(protect)
    ids = [
        bid
        for bid in session.scalars(
            select(Backup.id)
            .where(Backup.project_id == project_id)
            .order_by(Backup.created_at.desc(), Backup.id.desc())
            .offset(int(keep))
        )
        if bid not in protected
    ]
    if not ids:
        return []
    deleted = session.execute(
        delete(Backup).where(Backup.id.in_(ids)).returning(Backup.json_path, Backup.xlsx_path)
    ).all()
    paths: list[Path] = []
    for json_path, xlsx_path in deleted:
        for rel in (json_path, xlsx_path):
            if rel:
                try:
                    paths.append(resolve_path(backup_dir, project_id, rel))
                except BackupError:
                    log.warning("Backup-Pfad ausserhalb des Projektverzeichnisses ignoriert")
    return paths


def list_backups(session: Session, project_id: int, limit: int = 500) -> list[Backup]:
    stmt = (
        select(Backup)
        .where(Backup.project_id == project_id)
        .order_by(Backup.created_at.desc(), Backup.id.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def get_backup(session: Session, project_id: int, backup_id: int) -> Backup:
    backup = session.get(Backup, backup_id)
    if backup is None or backup.project_id != project_id:
        raise BackupError("backup_nicht_gefunden")
    return backup


def file_of(backup_dir: Path, backup: Backup, fmt: str) -> Path:
    """Datei eines Backups (``json``/``xlsx``); Pfad muss im Projektverzeichnis liegen."""
    rel = backup.json_path if fmt == "json" else backup.xlsx_path if fmt == "xlsx" else None
    if not rel:
        raise BackupError("backup_nicht_gefunden")
    path = resolve_path(backup_dir, backup.project_id, rel)
    if not path.is_file():
        raise BackupError("backup_datei_fehlt")
    return path


# ---------------------------------------------------------------- Einstellungen, manuell
def update_settings(
    session: Session,
    actor: Actor,
    project_id: int,
    interval_min: int,
    retention: int,
    *,
    backup_dir: Path,
) -> list[Path]:
    """Intervall/Aufbewahrung speichern; liefert ggf. zu loeschende Dateien (Aufbewahrung)."""
    if interval_min not in labels.BACKUP_INTERVALS_MIN:
        raise BackupError("backup_intervall_ungueltig")
    if not RETENTION_MIN <= retention <= RETENTION_MAX:
        raise BackupError("backup_aufbewahrung_ungueltig")
    lock(session, project_id)
    done = session.execute(
        update(Project)
        .where(Project.id == project_id)
        .values(backup_interval_min=interval_min, backup_retention=retention)
        .returning(Project.name)
    ).scalar()
    if done is None:
        raise BackupError("projekt_nicht_gefunden")
    audit(
        session,
        actor,
        "backup_einstellungen",
        done,
        {"project_id": project_id, "intervall_min": interval_min, "aufbewahrung": retention},
    )
    return apply_retention(session, project_id, backup_dir=backup_dir)


def manual_backup(
    session: Session, actor: Actor, project_id: int, *, backup_dir: Path
) -> tuple[CreatedBackup, list[Path]]:
    lock(session, project_id)
    created = create_backup(
        session, project_id, "manuell", backup_dir=backup_dir, created_by=actor.identifier
    )
    audit(
        session,
        actor,
        "backup_manuell",
        str(created.backup.id),
        {"project_id": project_id, "change_seq": created.backup.change_seq},
    )
    return created, apply_retention(session, project_id, backup_dir=backup_dir)


# ---------------------------------------------------------------- Scheduler (jobs-Prozess)
def backup_if_due(
    session: Session, project_id: int, *, backup_dir: Path, now: datetime | None = None
) -> tuple[CreatedBackup | None, list[Path]]:
    """Ein Projekt sichern, falls faellig und die Sperre frei ist (sonst ueberspringen)."""
    if not try_lock(session, project_id):
        return None, []
    if not due_projects(session, now, project_id):  # erneut pruefen (andere Instanz?)
        return None, []
    created = create_backup(
        session, project_id, "zeitgesteuert", backup_dir=backup_dir, created_by=SCHEDULER_ACTOR
    )
    return created, apply_retention(session, project_id, backup_dir=backup_dir)


def run_due(
    session_factory: Callable[[], Session],
    backup_dir: Path,
    now: datetime | None = None,
    progress: Callable[[], None] | None = None,
) -> list[int]:
    """Ein Scheduler-Tick: jedes faellige Projekt in eigener Transaktion; liefert Backup-IDs.

    ``progress`` (optional) wird vor jedem Projekt aufgerufen – der jobs-Prozess beruehrt
    damit seinen Heartbeat, solange der Lauf vorankommt (NPT-12 K5).
    """
    with session_factory() as session:
        candidates = due_projects(session, now)
    created_ids: list[int] = []
    for project_id in candidates:
        if progress is not None:
            progress()
        session = session_factory()
        try:
            created, obsolete = backup_if_due(session, project_id, backup_dir=backup_dir, now=now)
            session.commit()
        except Exception:
            session.rollback()
            log.exception("Zeitgesteuertes Backup fehlgeschlagen", extra={"project_id": project_id})
            continue
        finally:
            session.close()
        remove_files(obsolete)
        if created is not None:
            created_ids.append(created.backup.id)
    return created_ids
