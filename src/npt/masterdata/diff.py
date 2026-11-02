"""Import-Diff fuer Stammdaten – identisch fuer alle Quellen (Auftrag 4).

Reine Funktionen ohne Datenbankzugriff: Bestand (``system_id`` -> Felder) und
validierte Datensaetze rein, Diff raus. Der Fingerprint bindet die Vorschau an
den Bestand zum Vorschauzeitpunkt; die Uebernahme rechnet unter Sperre neu und
lehnt bei abweichendem Fingerprint ab (neue Vorschau).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from npt.domain import fields
from npt.masterdata.source import MasterRecord


@dataclass(frozen=True)
class CurrentSystem:
    values: dict[str, str | None]
    missing_since: str | None = None  # ISO-Zeitstempel oder None


@dataclass(frozen=True)
class FieldChange:
    field: str
    old: str | None
    new: str | None


@dataclass(frozen=True)
class ChangedSystem:
    system_id: str
    system_name: str | None
    changes: tuple[FieldChange, ...]
    reappeared: bool = False  # war als fehlend markiert und ist wieder enthalten


@dataclass(frozen=True)
class MissingSystem:
    system_id: str
    system_name: str | None
    missing_since: str | None  # bereits markiert seit


@dataclass
class MasterDiff:
    new: list[MasterRecord] = field(default_factory=list)
    changed: list[ChangedSystem] = field(default_factory=list)
    missing: list[MissingSystem] = field(default_factory=list)
    unchanged: int = 0
    columns: list[str] = field(default_factory=list)

    def fingerprint(self) -> str:
        """Stabiler Hash ueber den vollstaendigen Diff."""
        data = {
            "columns": self.columns,
            "new": [[r.system_id, sorted(r.values.items())] for r in self.new],
            "changed": [
                [c.system_id, c.reappeared, [[f.field, f.old, f.new] for f in c.changes]]
                for c in self.changed
            ],
            "missing": [[m.system_id, m.missing_since] for m in self.missing],
            "unchanged": self.unchanged,
        }
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def summary(self) -> dict[str, Any]:
        return {
            "new": len(self.new),
            "changed": len(self.changed),
            "missing": len(self.missing),
            "unchanged": self.unchanged,
        }


def compute_diff(
    current: dict[str, CurrentSystem], records: list[MasterRecord], columns: list[str]
) -> MasterDiff:
    """Neu / geaendert / fehlend / unveraendert.

    Verglichen werden nur Felder, die die Quelle liefert (``columns``); fehlt eine
    Spalte ganz, bleibt das Feld im Bestand unveraendert. Doppelte IDs in
    ``records`` (nur bei Validierungsfehlern) zaehlen einmal (erster Datensatz).
    """
    diff = MasterDiff(columns=[c for c in fields.MASTER_DATA_FIELDS if c in columns])
    seen: set[str] = set()
    for rec in records:
        if rec.system_id in seen:
            continue
        seen.add(rec.system_id)
        existing = current.get(rec.system_id)
        if existing is None:
            diff.new.append(rec)
            continue
        changes = tuple(
            FieldChange(name, existing.values.get(name), rec.values.get(name))
            for name in diff.columns
            if existing.values.get(name) != rec.values.get(name)
        )
        reappeared = existing.missing_since is not None
        if changes or reappeared:
            name = rec.values.get("system_name", existing.values.get("system_name"))
            diff.changed.append(ChangedSystem(rec.system_id, name, changes, reappeared))
        else:
            diff.unchanged += 1
    for sid in sorted(current):
        if sid not in seen:
            cur = current[sid]
            diff.missing.append(
                MissingSystem(sid, cur.values.get("system_name"), cur.missing_since)
            )
    diff.new.sort(key=lambda r: r.system_id)
    diff.changed.sort(key=lambda c: c.system_id)
    return diff
