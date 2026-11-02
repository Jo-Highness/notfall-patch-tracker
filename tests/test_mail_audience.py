"""Zielgruppen-Selektoren und Empfaengerbildung (Auftrag 6.3; Pflichtfaelle aus 10)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from npt.domain import fields, filters
from npt.mail import audience as aud

VECTORS = json.loads((Path(__file__).parent / "vectors" / "filter-vectors.json").read_text())


def row(
    sid: str, owner: str | None, affected: str | None = None, action: str | None = None, **kw: Any
) -> dict[str, Any]:
    base: dict[str, Any] = dict.fromkeys(fields.LIST_FIELDS)
    base.update(
        system_id=sid,
        system_name=f"Name {sid}",
        app_owner=owner,
        affected=affected,
        action_taken=action,
        origin="stammdaten",
    )
    base.update(kw)
    return base


ROWS = [
    row("S-01", "anna@firma.test"),  # leer
    row("S-02", "Anna@Firma.test ", "ja"),  # ja ohne Aktion, gleiche Adresse anders geschrieben
    row("S-03", "ben@firma.test; team@firma.test"),  # leer, zwei Adressen (;)
    row("S-04", "ben@firma.test, Clara <clara@firma.test>", "ja", "isoliert"),  # ja mit Aktion
    row("S-05", "kaputt at firma", None),  # leer, ungueltig
    row("S-06", None, None),  # leer, ohne Angabe
    row("S-07", "", "ja"),  # ja ohne Aktion, leer
    row("S-08", "dora@firma.test; nicht-gueltig", "ja"),  # ja ohne Aktion, eine gueltig
    row("S-09", "erik@firma.test", "nein"),  # weder noch
    row("S-10", "anna@firma.test;anna@firma.test", "in_pruefung"),  # weder noch
    row("S-11", "  team@firma.test  ", None),  # leer
]


def build(key: str, query: str = "") -> aud.Audience:
    return aud.build_audience(ROWS, aud.selector_state(key, query))


def mapping(a: aud.Audience) -> dict[str, list[str]]:
    return {r.email: [s.system_id for s in r.systems] for r in a.recipients}


def test_registry_contains_required_selectors() -> None:
    assert {"betroffen_leer", "ja_ohne_aktion", aud.CUSTOM} <= set(aud.SELECTORS)
    for sel in aud.SELECTORS.values():
        assert sel.name
        assert sel.description
    with pytest.raises(ValueError, match="existiert"):
        aud.register(aud.SELECTORS["betroffen_leer"])


def test_registry_is_extensible() -> None:
    sel = aud.Selector("test_in_pruefung", "In Prüfung", "Test", (("f.affected", "in_pruefung"),))
    aud.register(sel)
    try:
        assert mapping(build("test_in_pruefung")) == {"anna@firma.test": ["S-10"]}
    finally:
        del aud.SELECTORS["test_in_pruefung"]


def test_selector_1_betroffen_leer() -> None:
    a = build("betroffen_leer")
    assert mapping(a) == {
        "anna@firma.test": ["S-01"],  # S-02 ist "ja" -> nicht im [LINK]
        "ben@firma.test": ["S-03"],
        "team@firma.test": ["S-03", "S-11"],  # Mehrfachadresse + getrimmt -> eine Mail
    }
    assert a.system_count == 5  # S-01, S-03, S-05, S-06, S-11
    assert a.systems_without_recipient == 2  # S-05 (ungueltig), S-06 (leer)
    skipped = {(e.reason, e.value): e.system_ids for e in a.skipped}
    assert skipped == {("leer", ""): ["S-06"], ("ungueltig", "kaputt at firma"): ["S-05"]}


def test_selector_2_ja_ohne_aktion() -> None:
    a = build("ja_ohne_aktion")
    assert mapping(a) == {
        "anna@firma.test": ["S-02"],  # Grossschreibung/Leerzeichen normalisiert
        "dora@firma.test": ["S-08"],  # gueltiger Teil bekommt die Mail
    }
    assert a.system_count == 3  # S-02, S-07, S-08 (S-04 hat eine Aktion)
    skipped = {(e.reason, e.value): e.system_ids for e in a.skipped}
    assert skipped == {("leer", ""): ["S-07"], ("ungueltig", "nicht-gueltig"): ["S-08"]}
    assert a.systems_without_recipient == 1


def test_exactly_one_recipient_per_normalized_address() -> None:
    a = aud.build_audience(ROWS, filters.FilterState())
    emails = [r.email for r in a.recipients]
    assert len(emails) == len(set(emails))
    assert emails == sorted(emails)
    m = mapping(a)
    # alle Schreibweisen von anna landen in einer Mail, doppelte Angabe im Feld zaehlt einmal
    assert m["anna@firma.test"] == ["S-01", "S-02", "S-10"]
    assert m["ben@firma.test"] == ["S-03", "S-04"]
    assert m["clara@firma.test"] == ["S-04"]
    for r in a.recipients:
        assert r.email == r.email.strip().lower()


def test_custom_selector_accepts_url_query_or_params() -> None:
    url = "https://npt.test/p/TOKEN123/?f.affected=ja&x.digital_access_zone=DAZ+1&sort=system_name"
    assert aud.custom_params(url) == [("f.affected", "ja"), ("x.digital_access_zone", "DAZ 1")]
    assert aud.custom_params("?f.affected=ja") == [("f.affected", "ja")]
    assert aud.custom_params("f.affected=ja&dir=desc") == [("f.affected", "ja")]
    assert aud.custom_params("http://npt.test/p/T") == []
    assert aud.custom_params("unbekannt=1&f.nichtda=2") == []
    with pytest.raises(aud.SelectorError):
        aud.custom_params("q=" + "x" * 30_000)
    with pytest.raises(aud.SelectorError):
        aud.selector_state("gibt_es_nicht")


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["name"])
def test_custom_selector_matches_filter_grammar(case: dict[str, Any]) -> None:
    """Benutzerdefiniert trifft exakt die Systeme der Hauptlisten-Sicht (gemeinsame Vektoren)."""
    state = aud.selector_state(aud.CUSTOM, "https://npt.test/p/tok/?" + case["query"])
    a = aud.build_audience(VECTORS["rows"], state)
    expected = set(case["expect"])
    hit = {s.system_id for r in a.recipients for s in r.systems}
    hit |= {sid for e in a.skipped for sid in e.system_ids}
    assert hit == expected
    assert a.system_count == len(expected)
