"""Pivot-Uebersicht eines Projekts (Auftrag 5.4, NPT-03).

Zeilen: Digital Access Zone (alphabetisch, leere DAZ als „(ohne DAZ)" am Ende).
Spalten: „System betroffen" in der Reihenfolge (leer), In Pruefung, Ja, Nein.
Zaehlung serverseitig per ``GROUP BY``. Jede Zelle (inkl. Summen) traegt einen
Drilldown-Link auf die Hauptliste, ausgedrueckt in der Filter-Grammatik
(``npt.domain.filters``): DAZ exakt per ``x.digital_access_zone`` (leer = ohne DAZ),
Status per ``f.affected`` (``leer`` = „(leer)"). Ein Test prueft, dass die Python-
Filterlogik fuer jeden Link exakt die Zellzahl liefert.

Kennzahlen: Anteil bearbeiteter Systeme („System betroffen" nicht leer) und Anzahl
``Ja`` ohne „Ausgefuehrte Aktion" – beide ebenfalls als Drilldown.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final, cast
from urllib.parse import urlencode

from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session

from npt.db.models import ProjectSystem
from npt.domain import filters, labels

NO_DAZ_LABEL: Final = "(ohne DAZ)"
# Spaltenreihenfolge (Auftrag 5.4): None = (leer)
AFFECTED_COLUMNS: Final[tuple[str | None, ...]] = (None, "in_pruefung", "ja", "nein")

_ps = cast(Table, ProjectSystem.__table__)


def column_label(key: str | None) -> str:
    return labels.EMPTY_LABEL if key is None else labels.AFFECTED[key]


def drilldown_params(
    daz: str | None = None, *, any_daz: bool = True, **enums: Iterable[str | None]
) -> list[tuple[str, str]]:
    """Filter-Parameter in kanonischer Form (``filters.to_params``)."""
    exact: dict[str, tuple[str, ...]] = {}
    if not any_daz:
        exact["digital_access_zone"] = (daz or "",)
    enum_sets = {
        name: frozenset(filters.EMPTY_KEY if v is None else v for v in values)
        for name, values in enums.items()
    }
    return filters.to_params(filters.FilterState(enums=enum_sets, exact=exact))


def list_href(base: str, params: list[tuple[str, str]]) -> str:
    return f"{base}/?{urlencode(params)}" if params else f"{base}/"


@dataclass(frozen=True)
class Cell:
    key: str | None
    count: int
    href: str


@dataclass(frozen=True)
class PivotRow:
    daz: str | None
    label: str
    cells: list[Cell]
    total: int
    href: str


@dataclass(frozen=True)
class Kpi:
    total: int
    processed: int
    processed_href: str
    yes_without_action: int
    yes_without_action_href: str

    @property
    def processed_pct(self) -> float:
        return round(100 * self.processed / self.total, 1) if self.total else 0.0


@dataclass(frozen=True)
class Pivot:
    seq: int
    rows: list[PivotRow]
    columns: list[Cell]  # Spaltensummen
    total: int
    total_href: str
    kpi: Kpi

    def as_json(self) -> dict[str, Any]:
        def cell(c: Cell) -> dict[str, Any]:
            return {"key": c.key, "count": c.count, "href": c.href}

        return {
            "seq": self.seq,
            "columns": [{**cell(c), "label": column_label(c.key)} for c in self.columns],
            "rows": [
                {
                    "daz": r.daz,
                    "label": r.label,
                    "cells": [cell(c) for c in r.cells],
                    "total": r.total,
                    "href": r.href,
                }
                for r in self.rows
            ],
            "total": self.total,
            "total_href": self.total_href,
            "kpi": {
                "total": self.kpi.total,
                "processed": self.kpi.processed,
                "processed_pct": self.kpi.processed_pct,
                "processed_href": self.kpi.processed_href,
                "yes_without_action": self.kpi.yes_without_action,
                "yes_without_action_href": self.kpi.yes_without_action_href,
            },
        }


def daz_sort_key(daz: str | None) -> tuple[int, str, str]:
    """Alphabetisch (ohne Gross-/Kleinschreibung), leere DAZ zuletzt."""
    if not daz:
        return (1, "", "")
    return (0, daz.casefold(), daz)


Counts = dict[tuple[str | None, str | None], int]


def count_rows(session: Session, project_id: int) -> tuple[Counts, int]:
    """``{(daz, affected): n}`` per GROUP BY plus Anzahl ``Ja`` ohne Aktion."""
    daz = func.nullif(_ps.c.digital_access_zone, "")
    stmt = (
        select(daz.label("daz"), _ps.c.affected, func.count().label("n"))
        .where(_ps.c.project_id == project_id)
        .group_by(daz, _ps.c.affected)
    )
    counts = {(r.daz, r.affected): int(r.n) for r in session.execute(stmt)}
    yes_no_action = session.execute(
        select(func.count()).where(
            _ps.c.project_id == project_id,
            _ps.c.affected == "ja",
            _ps.c.action_taken.is_(None),
        )
    ).scalar_one()
    return counts, int(yes_no_action)


def build(counts: Counts, yes_no_action: int, *, base: str, seq: int) -> Pivot:
    zones = sorted({daz for daz, _ in counts}, key=daz_sort_key)
    rows: list[PivotRow] = []
    col_totals = dict.fromkeys(AFFECTED_COLUMNS, 0)
    for zone in zones:
        cells = []
        for key in AFFECTED_COLUMNS:
            n = counts.get((zone, key), 0)
            col_totals[key] += n
            href = list_href(base, drilldown_params(zone, any_daz=False, affected=[key]))
            cells.append(Cell(key, n, href))
        rows.append(
            PivotRow(
                zone,
                zone or NO_DAZ_LABEL,
                cells,
                sum(c.count for c in cells),
                list_href(base, drilldown_params(zone, any_daz=False)),
            )
        )
    columns = [
        Cell(key, col_totals[key], list_href(base, drilldown_params(affected=[key])))
        for key in AFFECTED_COLUMNS
    ]
    total = sum(col_totals.values())
    processed = total - col_totals[None]
    kpi = Kpi(
        total=total,
        processed=processed,
        processed_href=list_href(
            base, drilldown_params(affected=[k for k in AFFECTED_COLUMNS if k is not None])
        ),
        yes_without_action=yes_no_action,
        yes_without_action_href=list_href(
            base, drilldown_params(affected=["ja"], action_taken=[None])
        ),
    )
    return Pivot(seq, rows, columns, total, list_href(base, []), kpi)


class PivotCache:
    """Ergebnis je Projekt und ``change_seq`` (pro Worker-Prozess).

    Viele offene Uebersichten laden nach einer Aenderung gleichzeitig neu; je Worker
    wird dann nur einmal gezaehlt. Der Cursor wird vor der Zaehlung gelesen (wie beim
    Delta): ein Ergebnis kann juenger sein als sein Schluessel, nie aelter.
    """

    def __init__(self, max_projects: int = 64) -> None:
        self._max = max_projects
        self._data: dict[int, tuple[int, Counts, int]] = {}

    def get(self, session: Session, project_id: int, seq: int, base: str) -> Pivot:
        hit = self._data.get(project_id)
        if hit is None or hit[0] != seq:
            counts, yes_no_action = count_rows(session, project_id)
            if len(self._data) >= self._max and project_id not in self._data:
                self._data.pop(next(iter(self._data)))
            hit = (seq, counts, yes_no_action)
            self._data[project_id] = hit
        return build(hit[1], hit[2], base=base, seq=seq)
