"""Filter-Grammatik der Hauptliste – **maßgebliche Implementierung** (NPT-01).

Der Filter- und Suchzustand steht in der URL (Query-Parameter), damit jede Sicht
als Link teilbar ist (Pivot-Drilldown, Mail-Deep-Links). Dieselbe Semantik
implementiert ``static/js/npt-filter.js`` fuer den Browser; beide laufen gegen
``tests/vectors/filter-vectors.json``. Python ist maßgeblich (Export „gefilterte
Sicht", Mail-Selektor „Benutzerdefiniert").

Grammatik (alle Parameter optional, unbekannte Parameter/Felder werden ignoriert):

``q=<text>``
    Globale Volltextsuche: der getrimmte Text (lowercase) muss in mindestens
    einer Spalte der Liste (``LIST_FIELDS``) enthalten sein. Enum-Spalten werden
    ueber ihren Anzeigenamen durchsucht, leere Werte nie. Nur das erste ``q`` zaehlt.

``f.<feld>=<text>`` (Textfelder)
    „enthaelt", ohne Beachtung von Gross-/Kleinschreibung; Text getrimmt, leer =
    inaktiv. Nur das erste Vorkommen je Feld zaehlt.

``f.<feld>=<schluessel>`` (Enum-Felder ``affected``, ``action_taken``, ``origin``)
    Mehrfachauswahl durch Wiederholen des Parameters (ODER innerhalb des Felds).
    Werte sind die ASCII-Schluessel (z. B. ``ja``, ``in_pruefung``), ``leer``
    steht fuer „(leer)". Unbekannte Schluessel treffen nichts. Nur leere Werte =
    inaktiv.

``x.<feld>=<wert>`` (alle Felder)
    Exakter Vergleich (Gross-/Kleinschreibung beachtet), wiederholbar (ODER).
    Ein leerer Wert trifft leere Felder („(ohne DAZ)"). Bei Enum-Feldern gilt
    dieselbe Bedeutung wie bei ``f.`` (Schluessel, ``leer``).

``sort=<feld>`` und ``dir=asc|desc``
    Sortierung nach dem Anzeigetext (lowercase, Vergleich nach Codepoints), leere
    Werte bei ``asc`` am Ende; Gleichstand nach System-ID. ``desc`` kehrt die
    gesamte Reihenfolge um. Ohne gueltiges ``sort``: System-ID aufsteigend (``dir``
    wirkt dann nicht).

Alle aktiven Filter werden UND-verknuepft. „lowercase" ist ``str.lower()`` bzw.
``String.prototype.toLowerCase()``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from npt.domain import fields, labels

EMPTY_KEY: Final = "leer"
MAX_VALUES_PER_FIELD: Final = 5000
MAX_TEXT_LEN: Final = 500
TEXT_FIELDS: Final = tuple(f for f in fields.LIST_FIELDS if f not in fields.ENUM_FIELDS)

Row = Mapping[str, Any]


@dataclass(frozen=True)
class FilterState:
    q: str | None = None
    contains: dict[str, str] = field(default_factory=dict)
    enums: dict[str, frozenset[str]] = field(default_factory=dict)
    exact: dict[str, tuple[str, ...]] = field(default_factory=dict)
    sort: str | None = None
    desc: bool = False

    @property
    def active(self) -> bool:
        return bool(self.q or self.contains or self.enums or self.exact)


def parse(params: Iterable[tuple[str, str]]) -> FilterState:  # noqa: PLR0912 - Grammatik
    """Query-Parameter (Reihenfolge erhalten, Wiederholungen erlaubt) -> Zustand."""
    q: str | None = None
    contains: dict[str, str] = {}
    enum_values: dict[str, list[str]] = {}
    exact: dict[str, list[str]] = {}
    sort: str | None = None
    desc = False
    seen_q = False
    seen_contains: set[str] = set()
    seen_sort = seen_dir = False
    for key, raw in params:
        value = raw[: MAX_TEXT_LEN * 4]
        if key == "q":
            if not seen_q:
                seen_q = True
                q = value.strip()[:MAX_TEXT_LEN] or None
        elif key == "sort":
            if not seen_sort:
                seen_sort = True
                sort = value if value in fields.LIST_FIELDS else None
        elif key == "dir":
            if not seen_dir:
                seen_dir = True
                desc = value == "desc"
        elif key.startswith("f."):
            name = key[2:]
            if name in fields.ENUM_FIELDS:
                v = value.strip()
                if v:
                    enum_values.setdefault(name, []).append(v)
            elif name in TEXT_FIELDS and name not in seen_contains:
                seen_contains.add(name)
                text = value.strip()[:MAX_TEXT_LEN]
                if text:
                    contains[name] = text
        elif key.startswith("x."):
            name = key[2:]
            if name in fields.ENUM_FIELDS:
                enum_values.setdefault(name, []).append(value.strip() or EMPTY_KEY)
            elif name in TEXT_FIELDS:
                exact.setdefault(name, []).append(value)
    return FilterState(
        q=q,
        contains=contains,
        enums={k: frozenset(v[:MAX_VALUES_PER_FIELD]) for k, v in enum_values.items()},
        exact={k: tuple(dict.fromkeys(v[:MAX_VALUES_PER_FIELD])) for k, v in exact.items()},
        sort=sort,
        desc=desc and sort is not None,
    )


def to_params(state: FilterState) -> list[tuple[str, str]]:
    """Kanonische Darstellung (Reihenfolge: q, f.* in Feldreihenfolge, x.*, sort, dir)."""
    out: list[tuple[str, str]] = []
    if state.q:
        out.append(("q", state.q))
    for name in fields.LIST_FIELDS:
        if name in state.contains:
            out.append((f"f.{name}", state.contains[name]))
        if name in state.enums:
            out.extend((f"f.{name}", v) for v in sorted(state.enums[name]))
    for name in fields.LIST_FIELDS:
        if name in state.exact:
            out.extend((f"x.{name}", v) for v in state.exact[name])
    if state.sort:
        out.append(("sort", state.sort))
        if state.desc:
            out.append(("dir", "desc"))
    return out


def display_text(row: Row, name: str) -> str:
    """Anzeigetext einer Zelle (Enum -> Anzeigename, leer -> "")."""
    value = row.get(name)
    if value is None or value == "":
        return ""
    mapping = fields.ENUM_FIELDS.get(name)
    if mapping is not None:
        return mapping.get(str(value), str(value))
    return str(value)


def _enum_hit(row: Row, name: str, wanted: frozenset[str]) -> bool:
    value = row.get(name)
    key = EMPTY_KEY if value is None or value == "" else str(value)
    return key in wanted


def matches(row: Row, state: FilterState) -> bool:
    if state.q:
        needle = state.q.lower()
        if not any(needle in display_text(row, n).lower() for n in fields.LIST_FIELDS):
            return False
    for name, text in state.contains.items():
        if text.lower() not in display_text(row, name).lower():
            return False
    for name, wanted in state.enums.items():
        if not _enum_hit(row, name, wanted):
            return False
    for name, values in state.exact.items():
        value = row.get(name)
        if (value if value is not None else "") not in values:
            return False
    return True


def sort_key(row: Row, name: str) -> tuple[int, str, str]:
    text = display_text(row, name).lower()
    return (1 if text == "" else 0, text, str(row.get("system_id") or ""))


def apply(rows: Sequence[Row], state: FilterState) -> list[Row]:
    """Filtern und sortieren wie die Hauptliste."""
    hit = [r for r in rows if matches(r, state)]
    name = state.sort or "system_id"
    return sorted(hit, key=lambda r: sort_key(r, name), reverse=state.desc)


def empty_label(name: str) -> str:
    return "(ohne DAZ)" if name == "digital_access_zone" else labels.EMPTY_LABEL
