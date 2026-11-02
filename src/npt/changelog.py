"""Aenderungsprotokoll je Projekt (Auftrag 6.4): Filter, Seiten, Export.

Filter: System-ID (enthaelt, ohne Gross-/Kleinschreibung), Feld (oder „Anlage"),
Quelle, Zeitraum (Kalendertage in der Anzeige-Zeitzone, ``bis`` einschliesslich).
Gespeichert ist UTC; Anzeige und Export in ``DISPLAY_TZ`` (Europe/Berlin).
Der Export schreibt jede Zelle als Text mit Formel-Injection-Schutz (``npt.export``).
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from npt import export
from npt.db.models import ChangeLog
from npt.domain import fields, labels
from npt.project_upload.parse import enum_display

PAGE_SIZE: Final = 100
EXPORT_MAX: Final = 200_000
FIELD_CREATED: Final = "_angelegt"  # Filterwert fuer Eintraege der Aktion „angelegt"
SYSTEM_FILTER_MAX: Final = 200
# Felder, die im Protokoll vorkommen koennen (Filterauswahl).
LOG_FIELDS: Final[tuple[str, ...]] = (*fields.SYSTEM_FIELDS[1:], "origin")


@dataclass(frozen=True)
class LogFilter:
    system_id: str = ""
    field: str = ""
    source: str = ""
    date_from: date | None = None
    date_to: date | None = None

    def params(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.system_id:
            out["sid"] = self.system_id
        if self.field:
            out["feld"] = self.field
        if self.source:
            out["quelle"] = self.source
        if self.date_from:
            out["von"] = self.date_from.isoformat()
        if self.date_to:
            out["bis"] = self.date_to.isoformat()
        return out

    @property
    def active(self) -> bool:
        return bool(self.params())


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def parse_filter(params: Mapping[str, str]) -> LogFilter:
    """Query-Parameter -> Filter; unbekannte/ungueltige Werte werden verworfen."""
    field = params.get("feld", "")
    if field not in (*LOG_FIELDS, FIELD_CREATED):
        field = ""
    source = params.get("quelle", "")
    if source not in labels.CHANGE_SOURCE:
        source = ""
    return LogFilter(
        system_id=" ".join(params.get("sid", "").split())[:SYSTEM_FILTER_MAX],
        field=field,
        source=source,
        date_from=_parse_date(params.get("von")),
        date_to=_parse_date(params.get("bis")),
    )


def _day_start(day: date, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def _filtered(
    stmt: Select[tuple[ChangeLog]], project_id: int, flt: LogFilter, tz: ZoneInfo
) -> Select[tuple[ChangeLog]]:
    stmt = stmt.where(ChangeLog.project_id == project_id)
    if flt.system_id:
        pattern = flt.system_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(ChangeLog.system_id.ilike(f"%{pattern}%", escape="\\"))
    if flt.field == FIELD_CREATED:
        stmt = stmt.where(ChangeLog.action == "angelegt")
    elif flt.field:
        stmt = stmt.where(ChangeLog.field == flt.field)
    if flt.source:
        stmt = stmt.where(ChangeLog.source == flt.source)
    if flt.date_from:
        stmt = stmt.where(ChangeLog.changed_at >= _day_start(flt.date_from, tz))
    if flt.date_to:
        stmt = stmt.where(ChangeLog.changed_at < _day_start(flt.date_to + timedelta(days=1), tz))
    return stmt


@dataclass(frozen=True)
class Page:
    entries: list[ChangeLog]
    total: int
    page: int
    pages: int


def query(session: Session, project_id: int, flt: LogFilter, tz: ZoneInfo, page: int = 1) -> Page:
    count_stmt = _filtered(select(ChangeLog), project_id, flt, tz)
    total = int(session.scalar(select(func.count()).select_from(count_stmt.subquery())) or 0)
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(1, page), pages)
    stmt = (
        _filtered(select(ChangeLog), project_id, flt, tz)
        .order_by(ChangeLog.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    return Page(list(session.scalars(stmt)), total, page, pages)


def export_entries(
    session: Session, project_id: int, flt: LogFilter, tz: ZoneInfo
) -> list[ChangeLog]:
    stmt = (
        _filtered(select(ChangeLog), project_id, flt, tz)
        .order_by(ChangeLog.id.desc())
        .limit(EXPORT_MAX)
    )
    return list(session.scalars(stmt))


def field_label(entry: ChangeLog) -> str:
    if entry.field is None:
        return ""
    return fields.DISPLAY_NAMES.get(entry.field, entry.field)


def value_text(entry: ChangeLog, value: str | None) -> str:
    """Alter/neuer Wert zur Anzeige (Enums als Anzeigename, leer = „(leer)")."""
    if entry.field is None:
        return value or ""
    return enum_display(entry.field, value)


def actor_text(entry: ChangeLog) -> str:
    if entry.admin_identifier:
        return entry.admin_identifier
    return entry.client_name or ""


HEADER: Final = (
    "Zeitpunkt",
    "System-ID",
    "Aktion",
    "Feld",
    "Alter Wert",
    "Neuer Wert",
    "Quelle",
    "Wer",
    "IP",
    "Sequenz",
)


def workbook(entries: Iterable[ChangeLog], tz: ZoneInfo) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None  # noqa: S101 - neue Arbeitsmappe hat immer ein aktives Blatt
    ws.title = "Änderungsprotokoll"
    rows = (
        [
            e.changed_at.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S"),
            e.system_id,
            labels.CHANGE_ACTION.get(e.action, e.action),
            field_label(e),
            value_text(e, e.old_value),
            value_text(e, e.new_value),
            labels.CHANGE_SOURCE.get(e.source, e.source),
            actor_text(e),
            e.client_ip or "",
            str(e.seq),
        ]
        for e in entries
    )
    export.write_sheet(ws, HEADER, rows, [20, 14, 11, 24, 30, 30, 16, 32, 16, 9])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
