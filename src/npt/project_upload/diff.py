"""Diff eines Projekt-Uploads gegen den Projektbestand (Auftrag 6.1) – reine Funktionen.

Je geliefertem, nicht-leerem Feld einer bekannten System-ID:

- gleicher Wert -> unveraendert
- Zielfeld leer -> **Neubefuellung** (wird bei Bestaetigung uebernommen)
- Zielfeld nicht leer und abweichend -> **Konflikt** (nur nach expliziter Auswahl)

Unbekannte System-IDs werden aufgelistet (Default: ignorieren). IDs mit dem
reservierten Praefix ``NEW-`` lassen sich nicht anlegen (die Vergabe gehoert der
Sequenz, Auftrag 5.6). Der Fingerprint bindet die Vorschau an die alten Werte
**der betroffenen Felder**; Aenderungen an anderen Feldern machen sie nicht ungueltig.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from npt.domain import fields
from npt.masterdata.diff import FieldChange
from npt.project_upload.parse import UploadRecord


def is_empty(value: str | None) -> bool:
    return value is None or value == ""


def conflict_key(system_id: str, change: FieldChange) -> str:
    """Formularwert eines Konflikts: ``<feld>:<hash(alt,neu)>:<System-ID>``.

    Der Hash sorgt dafuer, dass eine Auswahl nur fuer genau diese alt/neu-Kombination
    gilt; aendert sich der alte Wert zwischenzeitlich, ist die Auswahl hinfaellig.
    """
    raw = json.dumps([system_id, change.field, change.old, change.new], ensure_ascii=False)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{change.field}:{digest}:{system_id}"


@dataclass(frozen=True)
class SystemDiff:
    system_id: str
    system_name: str | None
    row: int | None
    fills: tuple[FieldChange, ...]
    conflicts: tuple[FieldChange, ...]

    def keyed_conflicts(self) -> list[tuple[str, FieldChange]]:
        return [(conflict_key(self.system_id, c), c) for c in self.conflicts]


@dataclass(frozen=True)
class UnknownSystem:
    system_id: str
    row: int | None
    values: dict[str, str]
    creatable: bool


@dataclass
class UploadDiff:
    systems: list[SystemDiff] = field(default_factory=list)
    unknown: list[UnknownSystem] = field(default_factory=list)
    unchanged: int = 0
    columns: list[str] = field(default_factory=list)

    @property
    def conflict_systems(self) -> list[SystemDiff]:
        return [s for s in self.systems if s.conflicts]

    @property
    def fill_systems(self) -> list[SystemDiff]:
        return [s for s in self.systems if s.fills]

    @property
    def conflict_count(self) -> int:
        return sum(len(s.conflicts) for s in self.systems)

    @property
    def fill_count(self) -> int:
        return sum(len(s.fills) for s in self.systems)

    @property
    def creatable_ids(self) -> set[str]:
        return {u.system_id for u in self.unknown if u.creatable}

    def conflict_keys(self) -> set[str]:
        return {k for s in self.systems for k, _ in s.keyed_conflicts()}

    def fingerprint(self) -> str:
        data = {
            "columns": self.columns,
            "systems": [
                [
                    s.system_id,
                    [[c.field, c.old, c.new] for c in s.fills],
                    [[c.field, c.old, c.new] for c in s.conflicts],
                ]
                for s in self.systems
            ],
            "unknown": [[u.system_id, u.creatable, sorted(u.values.items())] for u in self.unknown],
            "unchanged": self.unchanged,
        }
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def summary(self) -> dict[str, Any]:
        return {
            "konflikte": self.conflict_count,
            "konflikt_systeme": len(self.conflict_systems),
            "neubefuellungen": self.fill_count,
            "unbekannt": len(self.unknown),
            "unveraendert": self.unchanged,
        }


def compute_diff(
    current: dict[str, dict[str, str | None]], records: list[UploadRecord], columns: list[str]
) -> UploadDiff:
    """``current``: System-ID -> aktuelle Feldwerte des Projekts."""
    diff = UploadDiff(columns=[c for c in fields.SYSTEM_FIELDS if c in columns])
    for rec in records:
        existing = current.get(rec.system_id)
        if existing is None:
            diff.unknown.append(
                UnknownSystem(
                    rec.system_id,
                    rec.row,
                    dict(rec.values),
                    not fields.is_reserved_id(rec.system_id),
                )
            )
            continue
        fills: list[FieldChange] = []
        conflicts: list[FieldChange] = []
        for name in diff.columns:
            if name not in rec.values:
                continue  # leere Zelle: vorhandener Wert bleibt
            new = rec.values[name]
            old = existing.get(name)
            if old == new:
                continue
            (fills if is_empty(old) else conflicts).append(FieldChange(name, old, new))
        if fills or conflicts:
            diff.systems.append(
                SystemDiff(
                    rec.system_id,
                    rec.values.get("system_name") or existing.get("system_name"),
                    rec.row,
                    tuple(fills),
                    tuple(conflicts),
                )
            )
        else:
            diff.unchanged += 1
    diff.systems.sort(key=lambda s: s.system_id)
    diff.unknown.sort(key=lambda u: u.system_id)
    return diff
