"""Excel-Quelle (.xlsx) fuer Stammdaten und die herunterladbare Vorlage (Auftrag 4).

Parsing mit ``openpyxl`` im ``read_only``-Modus und ``data_only`` (Formeln werden
nicht ausgewertet, es zaehlt der gespeicherte Wert; Makros werden nie geladen).
Die Kopfzeilen-Zuordnung (tolerant gegenueber Gross-/Kleinschreibung und
Leerzeichen) ist auch fuer den Projekt-Upload (Phase 6) gedacht.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from npt.domain import fields
from npt.domain.validation import cell_to_text
from npt.masterdata.source import Issue, MasterDataError, MasterDataSource, MasterRecord

XLSX_MIME: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Die Kopfzeile wird in den ersten Zeilen gesucht (Leerzeilen/Titel davor sind erlaubt).
HEADER_SEARCH_ROWS: Final = 10
MAX_COLUMNS: Final = 200
MAX_ROWS: Final = 100_000


@dataclass(frozen=True)
class SheetMapping:
    header_row: int  # 1-basiert
    columns: dict[int, str]  # Spaltenindex (0-basiert) -> internes Feld
    ignored: list[str]  # nicht zuordenbare Kopfzeilen (Originaltext)


def looks_like_xlsx(data: bytes) -> bool:
    """.xlsx ist ein ZIP-Container mit ``[Content_Types].xml`` und ``xl/workbook.xml``."""
    if not data.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        return False
    return "[Content_Types].xml" in names and "xl/workbook.xml" in names


def map_header(
    rows: list[tuple[Any, ...]], allowed: Iterable[str], required: str = "system_id"
) -> SheetMapping:
    """Kopfzeile finden und Spalten den Feldern zuordnen.

    Gesucht wird die erste Zeile (innerhalb ``HEADER_SEARCH_ROWS``), in der die
    Pflichtspalte steht. Doppelte Zuordnung desselben Felds ist ein Abbruch.
    """
    allowed_set = set(allowed)
    for idx, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        normalized = [fields.normalize_header(c) for c in row[:MAX_COLUMNS]]
        if fields.normalize_header(fields.DISPLAY_NAMES[required]) not in normalized and (
            fields.normalize_header(required) not in normalized
        ):
            continue
        columns: dict[int, str] = {}
        ignored: list[str] = []
        for col, norm in enumerate(normalized):
            if not norm:
                continue
            name = fields.HEADER_LOOKUP.get(norm)
            if name is None or name not in allowed_set:
                ignored.append(str(row[col]).strip()[:100])
                continue
            if name in columns.values():
                raise MasterDataError(
                    f"Die Spalte „{fields.DISPLAY_NAMES[name]}“ kommt in der Kopfzeile "
                    "mehrfach vor."
                )
            columns[col] = name
        return SheetMapping(idx + 1, columns, ignored)
    raise MasterDataError(
        f"Pflichtspalte „{fields.DISPLAY_NAMES[required]}“ nicht gefunden. Die Kopfzeile "
        f"muss die Spaltennamen der Vorlage enthalten (in den ersten {HEADER_SEARCH_ROWS} "
        "Zeilen)."
    )


def read_rows(data: bytes) -> list[tuple[Any, ...]]:
    """Alle Zeilen des ersten Arbeitsblatts (Werte, keine Formeln)."""
    if not looks_like_xlsx(data):
        raise MasterDataError("Die Datei ist keine gültige Excel-Datei (.xlsx).")
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise MasterDataError("Die Excel-Datei kann nicht gelesen werden.") from exc
    try:
        ws = wb.worksheets[0] if wb.worksheets else None
        if ws is None:
            raise MasterDataError("Die Excel-Datei enthält kein Arbeitsblatt.")
        rows: list[tuple[Any, ...]] = []
        for row in ws.iter_rows(values_only=True, max_col=MAX_COLUMNS):
            rows.append(tuple(row))
            if len(rows) > MAX_ROWS + HEADER_SEARCH_ROWS:
                raise MasterDataError(f"Zu viele Zeilen (mehr als {MAX_ROWS}).")
        return rows
    finally:
        wb.close()


class ExcelMasterDataSource(MasterDataSource):
    """Stammdaten aus einer hochgeladenen .xlsx-Datei."""

    source_kind = "excel"

    def __init__(self, data: bytes, file_name: str | None = None) -> None:
        super().__init__()
        self._data = data
        self.file_name = file_name

    def fetch(self) -> list[MasterRecord]:
        rows = read_rows(self._data)
        mapping = map_header(rows, fields.MASTER_FIELDS)
        provided = set(mapping.columns.values())
        self.columns = [f for f in fields.MASTER_DATA_FIELDS if f in provided]
        self.notes = []
        for name in mapping.ignored:
            known = fields.HEADER_LOOKUP.get(fields.normalize_header(name))
            if known is not None:
                msg = f"Spalte „{name}“ ist kein Stammdatenfeld und wird ignoriert."
            else:
                msg = f"Unbekannte Spalte „{name}“ wird ignoriert."
            self.notes.append(Issue(msg))
        for name in fields.MASTER_DATA_FIELDS:
            if name not in provided:
                self.notes.append(
                    Issue(
                        f"Spalte „{fields.DISPLAY_NAMES[name]}“ fehlt – vorhandene Werte "
                        "bleiben unverändert, neue Systeme erhalten hier keinen Wert."
                    )
                )
        id_col = next(c for c, f in mapping.columns.items() if f == "system_id")
        records: list[MasterRecord] = []
        for offset, row in enumerate(rows[mapping.header_row :]):
            excel_row = mapping.header_row + offset + 1
            if all(cell_to_text(c) is None for c in row):
                continue  # Leerzeile
            values: dict[str, str | None] = {}
            for col, name in mapping.columns.items():
                if name == "system_id":
                    continue
                values[name] = cell_to_text(row[col]) if col < len(row) else None
            sid = cell_to_text(row[id_col]) if id_col < len(row) else None
            records.append(MasterRecord(sid or "", values, excel_row))
        return records


def build_template() -> bytes:
    """Leere Vorlage mit den Spaltenkoepfen der Stammdaten (Auftrag 4)."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None  # noqa: S101 - neue Arbeitsmappe hat immer ein aktives Blatt
    ws.title = "Stammdaten"
    ws.append([fields.DISPLAY_NAMES[f] for f in fields.MASTER_FIELDS])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(fields.MASTER_FIELDS))}1"
    for idx in range(1, len(fields.MASTER_FIELDS) + 1):
        ws.column_dimensions[get_column_letter(idx)].width = 24
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
