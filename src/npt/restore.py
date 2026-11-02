"""Wiederherstellung eines Projekts aus einem Backup (Auftrag 6.2, NPT-05).

Ablauf in **einer** Transaktion:

1. Projektzeile sperren (``lock_project``, nur aktive Projekte) – Sperrreihenfolge wie
   ueberall: zuerst Projekt, dann Systeme.
2. Backup-Datei (JSON) lesen und mit der einen Domain-Validierung pruefen; ungueltig ->
   Abbruch ohne Aenderung.
3. Backup ``vor_restore`` des aktuellen Stands.
4. Systeme angleichen: nach dem Backup entstandene loeschen, fehlende wieder anlegen
   (gleiche System-ID, Herkunft aus dem Backup), abweichende Felder schreiben;
   jede Aenderung im ``change_log`` mit Quelle ``restore``.
5. ``reset_seq = change_seq`` und ``NOTIFY`` (``k=reload``): alle Clients laden die Liste
   ueber den Phase-4-Pfad komplett neu (``full_reload``/SSE ``reload``).

Projektname, Status, Token und Backup-Einstellungen bleiben unveraendert. Danach gilt
die Aufbewahrung (das wiederhergestellte und das ``vor_restore``-Backup sind dabei
geschuetzt); die Dateien entfernt der Aufrufer nach dem Commit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, cast

from sqlalchemy import Table, delete, insert, select, text, update
from sqlalchemy.orm import Session

from npt import backups
from npt.admins import Actor, audit
from npt.db.models import ChangeLog, Project, ProjectSystem
from npt.domain import fields, labels
from npt.domain.validation import ValidationError, clean_editable_value, clean_master_value
from npt.editing import lock_project, notify

# Felder, die ein Restore angleicht (alle ausser dem Schluessel, plus Herkunft).
RESTORE_FIELDS: Final[tuple[str, ...]] = (*fields.SYSTEM_FIELDS[1:], "origin")
MAX_BACKUP_BYTES: Final = 512 * 1024 * 1024

_ps = cast(Table, ProjectSystem.__table__)


class RestoreError(backups.BackupError):
    pass


@dataclass(frozen=True)
class RestoreResult:
    seq: int
    updated: int
    created: int
    deleted: int
    fields_changed: int
    before_backup_id: int
    obsolete_files: tuple[Path, ...] = ()


def load_document(path: Path, project_id: int) -> dict[str, Any]:
    if path.stat().st_size > MAX_BACKUP_BYTES:
        raise RestoreError("backup_ungueltig")
    try:
        doc = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise RestoreError("backup_ungueltig") from exc
    if (
        not isinstance(doc, dict)
        or doc.get("format") != backups.FORMAT
        or doc.get("format_version") != backups.FORMAT_VERSION
        or not isinstance(doc.get("project"), dict)
        or doc["project"].get("id") != project_id
        or not isinstance(doc.get("systems"), list)
    ):
        raise RestoreError("backup_ungueltig")
    return doc


def validate_systems(raw: list[Any]) -> dict[str, dict[str, str | None]]:
    """Systeme aus dem Backup mit der Domain-Validierung pruefen (wie UI/Upload)."""
    out: dict[str, dict[str, str | None]] = {}
    try:
        for item in raw:
            if not isinstance(item, dict):
                raise RestoreError("backup_ungueltig")
            sid = clean_master_value("system_id", item.get("system_id"))
            if sid is None or sid in out:
                raise RestoreError("backup_ungueltig")
            values: dict[str, str | None] = {}
            for name in fields.SYSTEM_FIELDS[1:]:
                value = item.get(name)
                if value is not None and not isinstance(value, str):
                    raise RestoreError("backup_ungueltig")
                if name in fields.EDITABLE_FIELDS:
                    values[name] = clean_editable_value(name, value)
                else:
                    values[name] = clean_master_value(name, value)
            origin = item.get("origin")
            if origin not in labels.ORIGIN:
                raise RestoreError("backup_ungueltig")
            values["origin"] = origin
            out[sid] = values
    except ValidationError as exc:
        raise RestoreError("backup_ungueltig") from exc
    return out


def restore(
    session: Session, actor: Actor, project_id: int, backup_id: int, *, backup_dir: Path
) -> RestoreResult:
    seq = lock_project(session, project_id)
    source = backups.get_backup(session, project_id, backup_id)
    doc = load_document(backups.file_of(backup_dir, source, "json"), project_id)
    target = validate_systems(doc["systems"])
    before = backups.create_backup(
        session,
        project_id,
        "vor_restore",
        backup_dir=backup_dir,
        created_by=actor.identifier,
        change_seq=seq - 1,
    )
    try:
        result = _apply(session, actor, project_id, seq, target, before.backup.id)
        session.execute(update(Project).where(Project.id == project_id).values(reset_seq=seq))
        notify(session, project_id, seq, "reload")
        audit(
            session,
            actor,
            "backup_wiederhergestellt",
            str(backup_id),
            {
                "project_id": project_id,
                "backup_change_seq": source.change_seq,
                "geaendert": result.updated,
                "angelegt": result.created,
                "geloescht": result.deleted,
                "vor_restore_backup_id": before.backup.id,
            },
        )
        obsolete = backups.apply_retention(
            session, project_id, backup_dir=backup_dir, protect={backup_id, before.backup.id}
        )
        session.flush()
    except BaseException:
        backups.remove_files(before.files)
        raise
    return replace(result, obsolete_files=tuple(obsolete))


def _apply(
    session: Session,
    actor: Actor,
    project_id: int,
    seq: int,
    target: dict[str, dict[str, str | None]],
    before_id: int,
) -> RestoreResult:
    cols = [_ps.c[c] for c in ("id", "system_id", *RESTORE_FIELDS)]
    current = {
        r["system_id"]: dict(r)
        for r in session.execute(
            select(*cols).where(_ps.c.project_id == project_id).with_for_update()
        ).mappings()
    }
    common: dict[str, Any] = {
        "project_id": project_id,
        "seq": seq,
        "source": "restore",
        "client_ip": actor.client_ip,
        "admin_identifier": actor.identifier,
    }
    log_rows: list[dict[str, Any]] = []
    removed = sorted(set(current) - set(target))
    if removed:
        session.execute(delete(_ps).where(_ps.c.id.in_([current[s]["id"] for s in removed])))
        log_rows.extend(
            {
                **common,
                "system_id": sid,
                "action": "geloescht",
                "field": None,
                "old_value": current[sid]["system_name"],
                "new_value": None,
            }
            for sid in removed
        )
    added = sorted(set(target) - set(current))
    for sid in added:
        values = target[sid]
        session.execute(
            insert(_ps).values(
                project_id=project_id, system_id=sid, version=1, row_seq=seq, **values
            )
        )
        log_rows.append(
            {
                **common,
                "system_id": sid,
                "action": "angelegt",
                "field": None,
                "old_value": None,
                "new_value": values["system_name"],
            }
        )
    updated = changed_fields = 0
    for sid in sorted(set(target) & set(current)):
        row = current[sid]
        diff = {n: target[sid][n] for n in RESTORE_FIELDS if row[n] != target[sid][n]}
        if not diff:
            continue
        updated += 1
        changed_fields += len(diff)
        session.execute(
            update(_ps)
            .where(_ps.c.id == row["id"])
            .values(
                {**diff, "version": _ps.c.version + 1, "row_seq": seq, "updated_at": text("now()")}
            )
        )
        log_rows.extend(
            {
                **common,
                "system_id": sid,
                "action": "geaendert",
                "field": name,
                "old_value": row[name],
                "new_value": value,
            }
            for name, value in diff.items()
        )
    if log_rows:
        session.execute(insert(ChangeLog), log_rows)
    return RestoreResult(seq, updated, len(added), len(removed), changed_fields, before_id)
