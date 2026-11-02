"""Validierungsbericht des Stammdatenimports und E-Mail-Pruefung (Auftrag 4/5.6)."""

from __future__ import annotations

import pytest

from npt.domain import fields
from npt.domain.validation import parse_emails
from npt.masterdata.source import MasterRecord, validate_records

COLS = list(fields.MASTER_DATA_FIELDS)


def rec(sid: str, row: int, owner: str | None = "a@firma.test", **kw: str | None) -> MasterRecord:
    values: dict[str, str | None] = dict.fromkeys(COLS, "x")
    values["app_owner"] = owner
    values.update(kw)
    return MasterRecord(sid, values, row)


def test_duplicate_and_empty_ids_are_errors() -> None:
    records, report = validate_records([rec("A", 2), rec("", 3), rec("A", 4), rec("B", 5)], COLS)
    assert not report.ok
    assert report.error_count == 2
    msgs = {(i.row, i.message) for i in report.errors}
    assert (3, "System-ID ist leer.") in msgs
    assert (4, "System-ID ist doppelt (erstmals in Zeile 2).") in msgs
    assert [r.system_id for r in records] == ["A", "B"]


def test_reserved_prefix_rejected_with_warning() -> None:
    records, report = validate_records([rec("NEW-0001", 2), rec("new-7", 3), rec("A", 4)], COLS)
    assert report.ok
    assert report.rejected_reserved == 2
    assert [r.system_id for r in records] == ["A"]
    assert all("NEW-" in w.message for w in report.warnings)


def test_invalid_emails_are_warnings_only() -> None:
    records, report = validate_records(
        [
            rec("A", 2, "max at firma.test"),
            rec("B", 3, None),
            rec("C", 4, "ok@firma.test; kaputt@"),
            rec("D", 5, "Max <max@firma.test>, team@firma.test"),
        ],
        COLS,
    )
    assert report.ok
    assert report.invalid_emails == 3
    assert len(records) == 4
    flagged = {w.system_id for w in report.warnings}
    assert flagged == {"A", "B", "C"}


def test_no_email_check_without_owner_column() -> None:
    cols = [c for c in COLS if c != "app_owner"]
    _, report = validate_records([MasterRecord("A", {"system_name": "x"}, 2)], cols)
    assert report.warning_count == 0


def test_too_long_values_truncated_with_warning() -> None:
    long = "x" * (fields.MASTER_TEXT_MAX + 10)
    records, report = validate_records([rec("A", 2, system_name=long)], COLS)
    assert report.ok
    assert len(records[0].values["system_name"] or "") == fields.MASTER_TEXT_MAX
    assert "gekürzt" in report.warnings[0].message


def test_issue_list_is_capped_but_counted() -> None:
    raw = [rec("", i) for i in range(700)]
    _, report = validate_records(raw, COLS)
    assert report.error_count == 700
    assert len(report.errors) == 500


@pytest.mark.parametrize(
    ("text", "valid", "invalid"),
    [
        ("a@b.de", ("a@b.de",), ()),
        ("  A@B.DE ", ("a@b.de",), ()),
        ("a@b.de;c@d.de", ("a@b.de", "c@d.de"), ()),
        ("a@b.de, c@d.de", ("a@b.de", "c@d.de"), ()),
        ("a@b.de; a@B.de", ("a@b.de",), ()),
        ("Max Muster <m.muster@firma.de>", ("m.muster@firma.de",), ()),
        ("max@", (), ("max@",)),
        ("Max Muster", (), ("Max Muster",)),
        ("a@b", (), ("a@b",)),
        ("a b@c.de", (), ("a b@c.de",)),
        ("", (), ()),
        (None, (), ()),
        ("ok@x.de;;", ("ok@x.de",), ()),
    ],
)
def test_parse_emails(text: str | None, valid: tuple[str, ...], invalid: tuple[str, ...]) -> None:
    parsed = parse_emails(text)
    assert parsed.valid == valid
    assert parsed.invalid == invalid
