"""Excel-Export: Spalten, gefilterte Sicht, Formel-Injection-Schutz (Auftrag 5.3/8.2)."""

from __future__ import annotations

import io
import zipfile

import pytest
from openpyxl import load_workbook

from npt import export
from npt.domain import fields
from tests.project_helpers import Editor, make_project, seed_master

DANGEROUS = [
    '=HYPERLINK("http://evil.example","klick")',
    "+1+1",
    "-2+3",
    "@SUM(A1:A2)",
    "=cmd|' /C calc'!A0",
    "\tTAB",
]


def sheet_xml(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read("xl/worksheets/sheet1.xml").decode()


@pytest.mark.parametrize("value", DANGEROUS)
def test_formula_like_values_are_written_as_text(value: str) -> None:
    row = dict.fromkeys(fields.LIST_FIELDS)
    row.update(system_id="A", remark=value, system_name="=1+1", origin="stammdaten")
    data = export.systems_workbook([row])
    xml = sheet_xml(data)
    assert "<f>" not in xml  # keine Formelzellen
    assert "<f " not in xml
    ws = load_workbook(io.BytesIO(data)).active
    assert ws is not None
    header = [c.value for c in ws[1]]
    col = header.index("Bemerkung*") + 1
    cell = ws.cell(row=2, column=col)
    assert cell.data_type == "s"
    assert cell.value == value  # Wert unveraendert (verlustfreier Re-Import)
    assert cell.quotePrefix is True
    name_cell = ws.cell(row=2, column=header.index("System-Name") + 1)
    assert name_cell.data_type == "s"
    assert name_cell.value == "=1+1"


def test_harmless_values_have_no_quote_prefix() -> None:
    row = dict.fromkeys(fields.LIST_FIELDS)
    row.update(system_id="A", remark="Patch 1-2", affected="ja", origin="manuell_angelegt")
    ws = load_workbook(io.BytesIO(export.systems_workbook([row]))).active
    assert ws is not None
    values = [c.value for c in ws[2]]
    assert "Ja" in values  # Enum als Anzeigename
    assert "Manuell angelegt" in values
    remark = ws.cell(row=2, column=fields.LIST_FIELDS.index("remark") + 1)
    assert remark.quotePrefix is False


def test_layout_header_freeze_autofilter() -> None:
    rows = [dict.fromkeys(fields.LIST_FIELDS) | {"system_id": f"S{i}"} for i in range(3)]
    ws = load_workbook(io.BytesIO(export.systems_workbook(rows))).active
    assert ws is not None
    assert [c.value for c in ws[1]] == [fields.DISPLAY_NAMES[f] for f in fields.LIST_FIELDS]
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref == "A1:P4"


def test_illegal_control_chars_removed() -> None:
    row = dict.fromkeys(fields.LIST_FIELDS) | {"system_id": "A", "remark": "a\x01b"}
    ws = load_workbook(io.BytesIO(export.systems_workbook([row]))).active
    assert ws is not None
    assert ws.cell(row=2, column=fields.LIST_FIELDS.index("remark") + 1).value == "ab"


def test_download_name_ascii() -> None:
    from datetime import datetime

    name = export.download_name("Größte Lücke: CVE/2026", "sicht", datetime(2026, 1, 2, 3, 4))
    assert name == "Groesste-Luecke-CVE-2026-sicht-20260102-0304.xlsx"


def test_public_export_all_and_filtered(session_factory, public_app_factory) -> None:
    seed_master(session_factory, 6)
    proj = make_project(session_factory, "Export Ä")
    ed = Editor(public_app_factory(), proj)
    assert ed.patch("APP-00002", "affected", "ja", None).status_code == 200
    assert ed.patch("APP-00004", "affected", "ja", None).status_code == 200
    assert ed.patch("APP-00004", "remark", "=1+2", None).status_code == 200
    r = ed.c.get(f"{proj.base}/export.xlsx?umfang=alle")
    assert r.status_code == 200
    assert r.headers["content-type"] == export.XLSX_MIME
    assert "attachment" in r.headers["content-disposition"]
    ws = load_workbook(io.BytesIO(r.content)).active
    assert ws is not None
    assert ws.max_row == 7
    r = ed.c.get(f"{proj.base}/export.xlsx?umfang=sicht&f.affected=ja&sort=system_id&dir=desc")
    ws = load_workbook(io.BytesIO(r.content)).active
    assert ws is not None
    ids = [ws.cell(row=i, column=1).value for i in range(2, ws.max_row + 1)]
    assert ids == ["APP-00004", "APP-00002"]
    assert "<f>" not in sheet_xml(r.content)


def test_export_all_cached_per_change_seq(session_factory, public_app_factory) -> None:
    """NPT-18: Excel-Datenverbindungen fragen periodisch ab – gleiche Sequenz, gleiche Bytes,
    jede Aenderung liefert sofort den neuen Stand (auch ueber Projekte getrennt)."""
    seed_master(session_factory, 4)
    proj = make_project(session_factory, "Cache A")
    other = make_project(session_factory, "Cache B")
    ed = Editor(public_app_factory(), proj)
    first = ed.c.get(f"{proj.base}/export.xlsx?umfang=alle")
    again = ed.c.get(f"{proj.base}/export.xlsx")  # umfang=alle ist der Standard
    assert first.status_code == again.status_code == 200
    assert first.content == again.content
    assert first.headers["cache-control"] == "no-store"
    assert ed.patch("APP-00001", "remark", "neu nach Cache", None).status_code == 200
    after = ed.c.get(f"{proj.base}/export.xlsx?umfang=alle")
    assert after.content != first.content
    ws = load_workbook(io.BytesIO(after.content)).active
    assert ws is not None
    values = [c.value for row in ws.iter_rows() for c in row]
    assert "neu nach Cache" in values
    # anderes Projekt bekommt nie den Eintrag des ersten
    ws_b = load_workbook(io.BytesIO(ed.c.get(f"{other.base}/export.xlsx").content)).active
    assert ws_b is not None
    assert "neu nach Cache" not in [c.value for row in ws_b.iter_rows() for c in row]


def test_workbook_cache_rebuilds_on_new_seq_and_evicts() -> None:
    cache = export.WorkbookCache(max_projects=2)
    calls: list[int] = []

    def build(n: int) -> bytes:
        calls.append(n)
        return str(n).encode()

    assert cache.get(1, 5, lambda: build(1)) == b"1"
    assert cache.get(1, 5, lambda: build(99)) == b"1"  # Treffer, kein Neubau
    assert cache.get(1, 6, lambda: build(2)) == b"2"  # neue Sequenz
    cache.get(2, 1, lambda: build(3))
    cache.get(3, 1, lambda: build(4))  # verdraengt den aeltesten Eintrag
    assert cache.get(1, 6, lambda: build(5)) == b"5"
    assert calls == [1, 2, 3, 4, 5]
