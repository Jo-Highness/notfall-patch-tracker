"""Schreibpfad der Projektsysteme (NPT-03/04, Auftrag 5.2/5.5/5.6).

Jede Schreibtransaktion beginnt mit ``UPDATE projects SET change_seq = change_seq + 1
… RETURNING`` (Sperrreihenfolge: zuerst Projekt, dann Systemzeile). Geaenderte
Zeilen erhalten ``row_seq`` = neue ``change_seq``; ``NOTIFY npt_changes`` wird
erst beim Commit versendet.

Konflikterkennung: Vergleich des gesendeten ``expected`` mit dem aktuellen
Feldwert (``IS DISTINCT FROM``-Semantik). Unterschiedliche Felder derselben Zeile
werden ohne Konflikt zusammengefuehrt; es gibt kein force-Flag – Ueberschreiben
heisst erneut senden mit dem gezeigten aktuellen Wert.

Die Funktionen committen nicht; der Aufrufer schliesst die Transaktion immer ab
(Commit bzw. Rollback im ``finally``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Final, cast

from sqlalchemy import Row, Table, insert, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from npt.db.models import ChangeLog, Project, ProjectSystem
from npt.domain import fields
from npt.domain.validation import (
    ValidationError,
    clean_editable_value,
    clean_master_value,
    clean_single_line,
    parse_emails,
)

NOTIFY_CHANNEL: Final = "npt_changes"
CLIENT_NAME_MAX: Final = 100
# Spalten, die die Bearbeiter-API ausliefert (Allowlist, NPT-11 PII).
PUBLIC_COLUMNS: Final[tuple[str, ...]] = (*fields.LIST_FIELDS, "version", "row_seq")
# SQLSTATEs: lock_not_available, query_canceled (statement_timeout)
_BUSY_STATES: Final = {"55P03", "57014"}

_ps = cast(Table, ProjectSystem.__table__)


class WriteError(Exception):
    """Fachlicher Fehler des Schreibpfads mit festem Grund und HTTP-Status."""

    status: int = 400
    reason: str = "fehler"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.reason)
        self.message = message


class NotFoundError(WriteError):
    status = 404
    reason = "nicht_gefunden"


class ReadOnlyError(WriteError):
    status = 403
    reason = "schreibgeschuetzt"


class BusyError(WriteError):
    status = 503
    reason = "gesperrt"


class InputError(WriteError):
    status = 422
    reason = "validierung"

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


class ConflictError(WriteError):
    status = 409
    reason = "konflikt"

    def __init__(self, field: str, current: Any, row: dict[str, Any]) -> None:
        super().__init__(f"{field} wurde zwischenzeitlich geändert")
        self.field = field
        self.current = current
        self.row = row


@contextmanager
def busy_as_error() -> Iterator[None]:
    """Lock-/Statement-Timeout -> :class:`BusyError` (503 mit Retry-After)."""
    try:
        yield
    except OperationalError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        if state in _BUSY_STATES:
            raise BusyError("Datensatz ist gerade gesperrt") from exc
        raise


def row_dto(row: Mapping[str, Any] | Row[Any]) -> dict[str, Any]:
    data = row._mapping if isinstance(row, Row) else row
    return {c: data[c] for c in PUBLIC_COLUMNS}


def clean_client_name(value: str | None) -> str | None:
    text_value = clean_single_line(value)
    return text_value[:CLIENT_NAME_MAX] if text_value else None


def lock_project(session: Session, project_id: int, *, require_active: bool = True) -> int:
    """Projektzeile sperren und ``change_seq`` erhoehen; liefert die neue Sequenz."""
    stmt = (
        update(Project)
        .where(Project.id == project_id)
        .values(change_seq=Project.change_seq + 1, updated_at=text("now()"))
        .returning(Project.change_seq)
    )
    if require_active:
        stmt = stmt.where(Project.status == "aktiv")
    with busy_as_error():
        seq = session.execute(stmt).scalar()
    if seq is None:
        exists = session.execute(select(Project.id).where(Project.id == project_id)).first()
        if exists is None:
            raise NotFoundError
        raise ReadOnlyError("Projekt ist abgeschlossen oder archiviert")
    return int(seq)


def notify(session: Session, project_id: int, seq: int, kind: str) -> None:
    payload = json.dumps({"p": project_id, "s": seq, "k": kind}, separators=(",", ":"))
    session.execute(text("SELECT pg_notify(:c, :p)"), {"c": NOTIFY_CHANNEL, "p": payload})


def _select_row(session: Session, project_id: int, system_id: str) -> dict[str, Any] | None:
    stmt = (
        select(_ps)
        .where(_ps.c.project_id == project_id, _ps.c.system_id == system_id)
        .with_for_update()
    )
    with busy_as_error():
        row = session.execute(stmt).mappings().first()
    return dict(row) if row is not None else None


def list_rows(session: Session, project_id: int) -> list[dict[str, Any]]:
    cols = [_ps.c[c] for c in PUBLIC_COLUMNS]
    stmt = select(*cols).where(_ps.c.project_id == project_id).order_by(_ps.c.system_id)
    return [dict(r) for r in session.execute(stmt).mappings()]


def _log(
    session: Session,
    *,
    project_id: int,
    seq: int,
    system_id: str,
    source: str,
    action: str = "geaendert",
    field: str | None = None,
    old: str | None = None,
    new: str | None = None,
    client_ip: str | None = None,
    client_name: str | None = None,
    admin_identifier: str | None = None,
) -> None:
    session.execute(
        insert(ChangeLog).values(
            project_id=project_id,
            seq=seq,
            system_id=system_id,
            action=action,
            field=field,
            old_value=old,
            new_value=new,
            source=source,
            client_ip=client_ip,
            client_name=client_name,
            admin_identifier=admin_identifier,
        )
    )


@dataclass(frozen=True)
class UpdateResult:
    row: dict[str, Any]
    changed: bool
    seq: int | None


def update_field(
    session: Session,
    project_id: int,
    system_id: str,
    field: str,
    value: str | None,
    expected: str | None,
    *,
    client_ip: str | None,
    client_name: str | None,
) -> UpdateResult:
    """Ein *-Feld schreiben (Bearbeiter, Quelle ``ui``)."""
    if field not in fields.EDITABLE_FIELDS:
        raise InputError({field: "Feld ist nicht schreibbar"})
    try:
        new_value = clean_editable_value(field, value)
    except ValidationError as exc:
        raise InputError({field: exc.message}) from exc
    expected_value = expected or None
    seq = lock_project(session, project_id)
    row = _select_row(session, project_id, system_id)
    if row is None:
        raise NotFoundError("System nicht gefunden")
    current = row[field]
    if current != expected_value:
        raise ConflictError(field, current, row_dto(row))
    if current == new_value:
        session.rollback()  # change_seq nicht verbrauchen
        return UpdateResult(row_dto(row), False, None)
    updated = (
        session.execute(
            update(_ps)
            .where(_ps.c.id == row["id"])
            .values(
                {
                    field: new_value,
                    "version": _ps.c.version + 1,
                    "row_seq": seq,
                    "updated_at": text("now()"),
                }
            )
            .returning(*[_ps.c[c] for c in PUBLIC_COLUMNS])
        )
        .mappings()
        .one()
    )
    _log(
        session,
        project_id=project_id,
        seq=seq,
        system_id=system_id,
        source="ui",
        field=field,
        old=current,
        new=new_value,
        client_ip=client_ip,
        client_name=clean_client_name(client_name),
    )
    notify(session, project_id, seq, "row")
    return UpdateResult(dict(updated), True, seq)


def validate_new_system(data: Mapping[str, Any]) -> dict[str, str | None]:
    """Pflichtfelder einer Neuanlage pruefen (Auftrag 5.6); Fehler je Feld."""
    errors: dict[str, str] = {}
    out: dict[str, str | None] = {}
    for name in fields.MASTER_DATA_FIELDS:
        raw = data.get(name)
        if raw is not None and not isinstance(raw, str):
            errors[name] = "Text erwartet"
            continue
        try:
            value = clean_master_value(name, raw)
        except ValidationError as exc:
            errors[name] = exc.message
            continue
        if value is None:
            errors[name] = "Pflichtfeld"
            continue
        out[name] = value
    owner = out.get("app_owner")
    if owner is not None and not parse_emails(owner).ok:
        errors["app_owner"] = "muss eine gültige E-Mail-Adresse enthalten"
    for name in fields.EDITABLE_FIELDS:
        try:
            out[name] = clean_editable_value(name, data.get(name))
        except ValidationError as exc:
            errors[name] = exc.message
    if errors:
        raise InputError(errors)
    return out


def next_new_system_id(session: Session) -> str:
    number = session.execute(text("SELECT nextval('new_system_seq')")).scalar_one()
    return fields.format_new_id(int(number))


def create_system(
    session: Session,
    project_id: int,
    data: Mapping[str, Any],
    *,
    client_ip: str | None,
    client_name: str | None,
) -> dict[str, Any]:
    """Neues System ``NEW-xxxx`` anlegen (Bearbeiter, nur aktive Projekte)."""
    values = validate_new_system(data)
    seq = lock_project(session, project_id)
    system_id = next_new_system_id(session)
    created = (
        session.execute(
            insert(_ps)
            .values(
                project_id=project_id,
                system_id=system_id,
                origin="manuell_angelegt",
                version=1,
                row_seq=seq,
                **values,
            )
            .returning(*[_ps.c[c] for c in PUBLIC_COLUMNS])
        )
        .mappings()
        .one()
    )
    name = clean_client_name(client_name)
    common: dict[str, Any] = {
        "project_id": project_id,
        "seq": seq,
        "system_id": system_id,
        "source": "ui",
        "client_ip": client_ip,
        "client_name": name,
    }
    _log(session, action="angelegt", new=values["system_name"], **common)
    for field in fields.EDITABLE_FIELDS:
        if values[field] is not None:
            _log(session, field=field, new=values[field], **common)
    notify(session, project_id, seq, "new")
    return dict(created)


def admin_update_system(
    session: Session,
    project_id: int,
    system_id: str,
    data: Mapping[str, Any],
    expected_version: int,
    *,
    admin_identifier: str,
    client_ip: str | None,
) -> list[str]:
    """Stammdatenfelder eines Projektsystems korrigieren (Admin, Auftrag 5.6).

    Liefert die geaenderten Felder. Optimistic Locking ueber ``version``.
    """
    errors: dict[str, str] = {}
    values: dict[str, str | None] = {}
    for name in fields.MASTER_DATA_FIELDS:
        try:
            values[name] = clean_master_value(name, data.get(name))
        except ValidationError as exc:
            errors[name] = exc.message
    if errors:
        raise InputError(errors)
    seq = lock_project(session, project_id)
    row = _select_row(session, project_id, system_id)
    if row is None:
        raise NotFoundError("System nicht gefunden")
    if row["version"] != expected_version:
        raise ConflictError("version", row["version"], row_dto(row))
    changed = [n for n in fields.MASTER_DATA_FIELDS if row[n] != values[n]]
    if not changed:
        session.rollback()
        return []
    session.execute(
        update(_ps)
        .where(_ps.c.id == row["id"])
        .values(
            {n: values[n] for n in changed}
            | {"version": _ps.c.version + 1, "row_seq": seq, "updated_at": text("now()")}
        )
    )
    for name in changed:
        _log(
            session,
            project_id=project_id,
            seq=seq,
            system_id=system_id,
            source="admin",
            field=name,
            old=row[name],
            new=values[name],
            client_ip=client_ip,
            admin_identifier=admin_identifier,
        )
    notify(session, project_id, seq, "row")
    return changed
