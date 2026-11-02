"""Parsing und Validierung einer Projekt-Upload-Datei (Auftrag 6.1).

Gleiche Spaltenlogik wie der Stammdatenimport (``masterdata.excel``: Kopfzeile
tolerant, erste 10 Zeilen, nur erstes Blatt, ``read_only``/``data_only``), aber alle
15 Felder aus Auftrag 3.1 sind zulaessig. Geprueft wird mit der einen
Domain-Validierung (``domain.validation``); im Upload ist jeder ungueltige Wert ein
**Fehler mit Zeilenangabe** (die Uebernahme ist dann gesperrt), ungueltige
E-Mail-Adressen sind wie beim Stammdatenimport eine Warnung.

Leere Zellen (und der Enum-Wert „Leer"/„(leer)") bedeuten „kein Wert geliefert":
sie stehen nicht in ``UploadRecord.values`` und loeschen nie vorhandene Werte.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from npt.domain import fields, labels
from npt.domain.validation import (
    ValidationError,
    cell_to_text,
    clean_editable_value,
    clean_master_value,
    enum_key_from_label,
    parse_emails,
)
from npt.masterdata.excel import map_header, read_rows
from npt.masterdata.source import Issue, ValidationReport

# Alle Felder ausser dem Schluessel; ``origin`` (Herkunft) ist nicht ueberschreibbar.
UPLOAD_FIELDS: Final[tuple[str, ...]] = fields.SYSTEM_FIELDS[1:]
MAX_RECORDS: Final = 100_000


@dataclass(frozen=True)
class UploadRecord:
    """Eine Zeile des Uploads; ``values`` enthaelt nur nicht-leere, bereinigte Werte."""

    system_id: str
    values: dict[str, str]
    row: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {"system_id": self.system_id, "values": self.values, "row": self.row}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> UploadRecord:
        return cls(str(data["system_id"]), dict(data["values"]), data.get("row"))


@dataclass
class ParsedUpload:
    records: list[UploadRecord]
    columns: list[str]  # gelieferte Felder (ohne system_id), Anzeige-Reihenfolge
    report: ValidationReport


def _allowed_text(field: str) -> str:
    names = ", ".join(f"„{n}“" for n in fields.ENUM_FIELDS[field].values())
    return f"erlaubt: {names} oder leer"


def clean_upload_value(field: str, raw: str) -> str | None:
    """Ein Zellwert -> bereinigter Wert (``None`` = leer); ungueltig -> ValidationError."""
    if field in fields.ENUM_FIELDS:
        try:
            return enum_key_from_label(field, raw)
        except ValidationError as exc:
            raise ValidationError(
                field, f"ungültiger Wert „{raw[:100]}“ ({_allowed_text(field)})"
            ) from exc
    if field in fields.EDITABLE_FIELDS:
        return clean_editable_value(field, raw)
    return clean_master_value(field, raw)


def _ignored_notes(report: ValidationReport, ignored: list[str]) -> None:
    for name in ignored:
        known = fields.HEADER_LOOKUP.get(fields.normalize_header(name))
        if known is not None:
            report.warn(Issue(f"Spalte „{name}“ ist nicht über den Upload änderbar – ignoriert."))
        else:
            report.warn(Issue(f"Unbekannte Spalte „{name}“ wird ignoriert."))


def _row_values(
    row: tuple[Any, ...],
    columns: dict[int, str],
    excel_row: int,
    sid: str,
    report: ValidationReport,
) -> dict[str, str] | None:
    """Zellen einer Zeile pruefen; ``None``, wenn mindestens ein Wert ungueltig ist."""
    values: dict[str, str] = {}
    row_ok = True
    for col, name in columns.items():
        raw = cell_to_text(row[col]) if col < len(row) and name != "system_id" else None
        if raw is None:
            continue
        try:
            value = clean_upload_value(name, raw)
        except ValidationError as exc:
            report.error(Issue(f"{fields.DISPLAY_NAMES[name]}: {exc.message}.", excel_row, sid))
            row_ok = False
            continue
        if value is not None:
            values[name] = value
    if "app_owner" in values:
        parsed = parse_emails(values["app_owner"])
        if not parsed.ok:
            report.invalid_emails += 1
            detail = (
                "ungültige Adresse(n): " + "; ".join(parsed.invalid)[:300]
                if parsed.invalid
                else "keine E-Mail-Adresse"
            )
            report.warn(Issue(f"Anwendungsverantwortlicher: {detail}.", excel_row, sid))
    return values if row_ok else None


def _system_id(
    row: tuple[Any, ...],
    id_col: int,
    excel_row: int,
    seen: dict[str, int],
    report: ValidationReport,
) -> str | None:
    raw_sid = cell_to_text(row[id_col]) if id_col < len(row) else None
    try:
        sid = clean_master_value("system_id", raw_sid)
    except ValidationError:
        report.error(Issue("System-ID ist zu lang.", excel_row, (raw_sid or "")[:100]))
        return None
    if sid is None:
        report.error(Issue("System-ID ist leer.", excel_row))
        return None
    if sid in seen:
        report.error(
            Issue(f"System-ID ist doppelt (erstmals in Zeile {seen[sid]}).", excel_row, sid)
        )
        return None
    seen[sid] = excel_row
    return sid


def parse_upload(data: bytes) -> ParsedUpload:
    """Datei lesen, Spalten zuordnen, jede Zeile pruefen.

    Strukturelle Fehler (keine .xlsx, Pflichtspalte fehlt, Spalte doppelt) brechen
    mit ``MasterDataError`` ab, Zeilenfehler landen im Bericht.
    """
    rows = read_rows(data)
    mapping = map_header(rows, fields.SYSTEM_FIELDS)
    provided = set(mapping.columns.values())
    columns = [f for f in UPLOAD_FIELDS if f in provided]
    report = ValidationReport()
    _ignored_notes(report, mapping.ignored)
    id_col = next(c for c, f in mapping.columns.items() if f == "system_id")
    seen: dict[str, int] = {}
    records: list[UploadRecord] = []
    for offset, row in enumerate(rows[mapping.header_row :]):
        excel_row = mapping.header_row + offset + 1
        if all(cell_to_text(c) is None for c in row):
            continue
        sid = _system_id(row, id_col, excel_row, seen, report)
        if sid is None:
            continue
        values = _row_values(row, mapping.columns, excel_row, sid, report)
        if values is not None:
            records.append(UploadRecord(sid, values, excel_row))
        if len(records) > MAX_RECORDS:
            report.error(Issue(f"Zu viele Datensätze (mehr als {MAX_RECORDS})."))
            break
    return ParsedUpload(records, columns, report)


def enum_display(field: str, value: str | None) -> str:
    """Anzeige eines (alten/neuen) Feldwerts in Vorschau und Protokoll."""
    if field in fields.ENUM_FIELDS:
        return labels.label(fields.ENUM_FIELDS[field], value)
    return value if value is not None else labels.EMPTY_LABEL
