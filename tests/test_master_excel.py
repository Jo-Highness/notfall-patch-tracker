"""Excel-Quelle: Kopfzeilen-Toleranz, Abbrueche, Vorlage, Zellwerte (Auftrag 4)."""

from __future__ import annotations

import datetime as dt
import io
import zipfile

import pytest

from npt.domain import fields
from npt.domain.validation import cell_to_text
from npt.masterdata.excel import ExcelMasterDataSource, build_template, looks_like_xlsx
from npt.masterdata.source import MasterDataError
from tests.xlsx import MASTER_HEADER, master_row, master_xlsx, read_xlsx, xlsx_bytes


def test_header_tolerant_case_whitespace_and_order() -> None:
    header = [
        "  system-id ",
        "SYSTEM-NAME",
        "digital   access zone",
        "anwendungsverantwortlicher  ",
    ]
    data = xlsx_bytes([header, ["A1", "Name", "Z1", "a@b.de"]])
    src = ExcelMasterDataSource(data, "x.xlsx")
    recs = src.fetch()
    assert src.columns == ["system_name", "digital_access_zone", "app_owner"]
    assert recs[0].system_id == "A1"
    assert recs[0].values == {
        "system_name": "Name",
        "digital_access_zone": "Z1",
        "app_owner": "a@b.de",
    }


def test_header_accepts_internal_names_and_star_suffix() -> None:
    data = xlsx_bytes([["system_id", "System betroffen*", "fqdn"], ["A1", "Ja", "h.test"]])
    src = ExcelMasterDataSource(data)
    recs = src.fetch()
    assert src.columns == ["fqdn"]
    assert recs[0].values == {"fqdn": "h.test"}
    assert any("kein Stammdatenfeld" in n.message for n in src.notes)


def test_header_may_follow_title_rows() -> None:
    data = xlsx_bytes([["Export vom 1.1."], [], MASTER_HEADER, master_row("A1")])
    recs = ExcelMasterDataSource(data).fetch()
    assert [r.system_id for r in recs] == ["A1"]
    assert recs[0].row == 4


def test_missing_system_id_column_aborts() -> None:
    data = xlsx_bytes([["System-Name", "SBF"], ["x", "y"]])
    with pytest.raises(MasterDataError, match="Pflichtspalte „System-ID“ nicht gefunden"):
        ExcelMasterDataSource(data).fetch()


def test_duplicate_header_aborts() -> None:
    data = xlsx_bytes([["System-ID", "SBF", " sbf "], ["A", "x", "y"]])
    with pytest.raises(MasterDataError, match="mehrfach"):
        ExcelMasterDataSource(data).fetch()


def test_unknown_and_missing_columns_are_reported() -> None:
    data = xlsx_bytes([["System-ID", "System-Name", "Kostenstelle"], ["A", "x", "1"]])
    src = ExcelMasterDataSource(data)
    src.fetch()
    msgs = [n.message for n in src.notes]
    assert any("Unbekannte Spalte „Kostenstelle“" in m for m in msgs)
    assert any("„FQDN“ fehlt" in m for m in msgs)


@pytest.mark.parametrize(
    "payload",
    [b"", b"kein excel", b"PK\x03\x04kaputt", b"<html></html>"],
)
def test_non_xlsx_rejected(payload: bytes) -> None:
    with pytest.raises(MasterDataError, match=r"\.xlsx|gelesen"):
        ExcelMasterDataSource(payload).fetch()


def test_zip_without_workbook_is_not_xlsx() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", "x")
    assert not looks_like_xlsx(buf.getvalue())


def test_empty_rows_skipped_and_values_trimmed() -> None:
    data = master_xlsx(master_row("A1", name="  Name  "), [None] * 11, master_row(" A2 "))
    recs = ExcelMasterDataSource(data).fetch()
    assert [r.system_id for r in recs] == ["A1", "A2"]
    assert recs[0].values["system_name"] == "Name"


def test_formulas_are_not_evaluated() -> None:
    data = master_xlsx(master_row("A1", name="=1+1"))
    rec = ExcelMasterDataSource(data).fetch()[0]
    # data_only: ohne gespeicherten Ergebniswert bleibt die Zelle leer – keine Auswertung.
    assert rec.values["system_name"] is None


def test_template_has_master_headers() -> None:
    rows = read_xlsx(build_template())
    assert list(rows[0]) == MASTER_HEADER
    assert len(rows) == 1
    # Vorlage ist selbst wieder importierbar (keine Datensaetze)
    assert ExcelMasterDataSource(build_template()).fetch() == []


def test_cell_to_text_conversions() -> None:
    assert cell_to_text(None) is None
    assert cell_to_text("  ") is None
    assert cell_to_text(123.0) == "123"
    assert cell_to_text(12.5) == "12.5"
    assert cell_to_text(7) == "7"
    assert cell_to_text(dt.datetime(2026, 1, 2)) == "2026-01-02"
    assert cell_to_text(dt.date(2026, 1, 2)) == "2026-01-02"
    assert cell_to_text("a\r\nb\x07") == "a\nb"


def test_display_names_cover_all_fields() -> None:
    assert set(fields.DISPLAY_NAMES) == set(fields.LIST_FIELDS)
    assert len(fields.SYSTEM_FIELDS) == 15
