"""Excel-Export (.xlsx) mit Schutz gegen Formel-Injection (Auftrag 5.3, 8.2).

- Spaltenkoepfe = Anzeigenamen, Reihenfolge exakt Auftrag 3.1 (+ Herkunft).
- Kopfzeile fixiert, Autofilter ueber den gesamten Bereich.
- Jede Textzelle wird ausdruecklich als **Text** geschrieben (``data_type = "s"``),
  nie als Formel. Beginnt ein Wert mit ``=``, ``+``, ``-``, ``@`` (oder Tab/CR),
  wird zusaetzlich das Excel-Attribut ``quotePrefix`` gesetzt – Excel zeigt den
  Wert dann wie mit vorangestelltem ``'`` als Text, auch nach erneutem Bearbeiten.
  Der gespeicherte Wert bleibt unveraendert (verlustfreier Re-Import).
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from npt.domain import fields
from npt.domain.filters import display_text

XLSX_MIME: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FORMULA_TRIGGERS: Final = ("=", "+", "-", "@", "\t", "\r")
EXCEL_CELL_MAX: Final = 32767
_FILENAME_RE: Final = re.compile(r"[^A-Za-z0-9._-]+")


def is_formula_like(value: str) -> bool:
    return value.startswith(FORMULA_TRIGGERS)


def write_text(ws: Worksheet, row: int, col: int, value: str | None) -> None:
    """Zelle sicher als Text schreiben (nie als Formel)."""
    if value is None or value == "":
        return
    text = ILLEGAL_CHARACTERS_RE.sub("", value)[:EXCEL_CELL_MAX]
    cell = ws.cell(row=row, column=col)
    cell.value = text
    cell.data_type = "s"  # openpyxl wuerde "=..." sonst als Formel speichern
    if is_formula_like(text):
        cell.quotePrefix = True


def write_sheet(
    ws: Worksheet,
    header: Sequence[str],
    rows: Iterable[Sequence[str | None]],
    widths: Sequence[int] | None = None,
) -> None:
    bold = Font(bold=True)
    for col, title in enumerate(header, start=1):
        write_text(ws, 1, col, title)
        ws.cell(row=1, column=col).font = bold
    count = 1
    for r, values in enumerate(rows, start=2):
        count = r
        for col, value in enumerate(values, start=1):
            write_text(ws, r, col, value)
    last_col = get_column_letter(len(header))
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{last_col}{max(count, 1)}"
    for col in range(1, len(header) + 1):
        width = widths[col - 1] if widths else 20
        ws.column_dimensions[get_column_letter(col)].width = width


_WIDTHS: Final[dict[str, int]] = {
    "system_id": 14,
    "system_name": 28,
    "remark": 40,
    "system_description": 40,
    "app_owner": 32,
}


def systems_workbook(
    rows: Iterable[Mapping[str, Any]], *, columns: Sequence[str] = fields.LIST_FIELDS
) -> bytes:
    """Systeme eines Projekts als .xlsx (Enum-Werte als Anzeigenamen, leer = leer)."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None  # noqa: S101 - neue Arbeitsmappe hat immer ein aktives Blatt
    ws.title = "Systeme"
    write_sheet(
        ws,
        [fields.DISPLAY_NAMES[c] for c in columns],
        ([display_text(r, c) or None for c in columns] for r in rows),
        [_WIDTHS.get(c, 20) for c in columns],
    )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def safe_name(base: str) -> str:
    """ASCII-Bestandteil eines Dateinamens (Umlaute transkribiert, max. 60 Zeichen)."""
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})
    table |= str.maketrans({"ß": "ss"})
    return _FILENAME_RE.sub("-", base.translate(table)).strip("-._")[:60] or "projekt"


def download_name(base: str, suffix: str, now: datetime, ext: str = "xlsx") -> str:
    """ASCII-Dateiname fuer Content-Disposition."""
    return f"{safe_name(base)}-{suffix}-{now.strftime('%Y%m%d-%H%M')}.{ext}"


def attachment_headers(file_name: str) -> dict[str, str]:
    return {
        "content-disposition": f'attachment; filename="{file_name}"',
        "cache-control": "no-store",
    }


class WorkbookCache:
    """Export „alle“ je Projekt und ``change_seq`` (pro Worker-Prozess, NPT-18).

    Excel-Datenverbindungen (Power Query) rufen den Export periodisch ab, oft viele
    Arbeitsmappen gleichzeitig. Jede Schreibtransaktion eines Projekts erhoeht
    ``change_seq`` (NPT-05, auch Upload und Wiederherstellung); solange er gleich bleibt,
    ist die Arbeitsmappe byte-identisch und wird nur einmal je Worker gebaut. Wie beim
    ``PivotCache`` wird die Sequenz vor dem Lesen der Zeilen ermittelt: ein Eintrag kann
    juenger sein als sein Schluessel, nie aelter.
    """

    def __init__(self, max_projects: int = 32) -> None:
        self._max = max_projects
        self._data: dict[int, tuple[int, bytes]] = {}

    def get(self, project_id: int, seq: int, build: Callable[[], bytes]) -> bytes:
        hit = self._data.get(project_id)
        if hit is not None and hit[0] == seq:
            return hit[1]
        data = build()
        if len(self._data) >= self._max and project_id not in self._data:
            self._data.pop(next(iter(self._data)))
        self._data[project_id] = (seq, data)
        return data
