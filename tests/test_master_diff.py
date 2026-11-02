"""Import-Diff-Logik (reine Funktion, Auftrag 4/10)."""

from __future__ import annotations

from npt.masterdata.diff import CurrentSystem, compute_diff
from npt.masterdata.source import MasterRecord

COLS = ["system_name", "sbf"]


def cur(name: str, sbf: str | None = "S", missing: str | None = None) -> CurrentSystem:
    return CurrentSystem({"system_name": name, "sbf": sbf, "fqdn": "keep"}, missing)


def rec(sid: str, name: str, sbf: str | None = "S") -> MasterRecord:
    return MasterRecord(sid, {"system_name": name, "sbf": sbf})


def test_new_changed_missing_unchanged() -> None:
    current = {"A": cur("a"), "B": cur("b"), "C": cur("c")}
    diff = compute_diff(current, [rec("A", "a"), rec("B", "b2", None), rec("D", "d")], COLS)
    assert [r.system_id for r in diff.new] == ["D"]
    assert [c.system_id for c in diff.changed] == ["B"]
    changes = {(f.field, f.old, f.new) for f in diff.changed[0].changes}
    assert changes == {("system_name", "b", "b2"), ("sbf", "S", None)}
    assert [m.system_id for m in diff.missing] == ["C"]
    assert diff.unchanged == 1
    assert diff.summary() == {"new": 1, "changed": 1, "missing": 1, "unchanged": 1}


def test_fields_not_delivered_are_not_compared() -> None:
    # fqdn ist nicht in COLS -> keine Aenderung, obwohl der Datensatz kein fqdn hat
    diff = compute_diff({"A": cur("a")}, [rec("A", "a")], COLS)
    assert diff.changed == []
    assert diff.unchanged == 1


def test_reappeared_system_counts_as_changed() -> None:
    diff = compute_diff({"A": cur("a", missing="2026-01-01T00:00:00+00:00")}, [rec("A", "a")], COLS)
    assert diff.changed[0].reappeared
    assert diff.changed[0].changes == ()


def test_already_missing_stays_missing_with_marker() -> None:
    diff = compute_diff({"A": cur("a", missing="2026-01-01")}, [], COLS)
    assert diff.missing[0].missing_since == "2026-01-01"


def test_fingerprint_is_stable_and_sensitive() -> None:
    current = {"A": cur("a"), "B": cur("b")}
    records = [rec("A", "a2"), rec("C", "c")]
    fp1 = compute_diff(current, records, COLS).fingerprint()
    assert fp1 == compute_diff(dict(reversed(current.items())), records, COLS).fingerprint()
    current["B"] = cur("b", missing="2026-01-01")
    assert compute_diff(current, records, COLS).fingerprint() != fp1
    current = {"A": cur("a-anders"), "B": cur("b")}
    assert compute_diff(current, records, COLS).fingerprint() != fp1


def test_duplicate_records_count_once() -> None:
    diff = compute_diff({}, [rec("A", "a"), rec("A", "zweit")], COLS)
    assert len(diff.new) == 1
    assert diff.new[0].values["system_name"] == "a"
