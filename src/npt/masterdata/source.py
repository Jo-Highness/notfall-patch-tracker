"""Abstrakte Stammdatenquelle und Validierung der gelieferten Datensaetze (Auftrag 4).

``MasterDataSource.fetch()`` liefert Rohdatensaetze. Die fachliche Pruefung
(doppelte/leere IDs, reserviertes Praefix ``NEW-``, E-Mail-Adressen, Laengen)
macht ``validate_records`` – fuer alle Quellen gleich.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Final

from npt.domain import fields
from npt.domain.validation import ValidationError, clean_master_value, parse_emails

# Obergrenze fuer die Anzahl Datensaetze je Import (Schutz vor Fehlbedienung).
MAX_RECORDS: Final = 100_000
# Begrenzung der im Bericht aufgefuehrten Einzelmeldungen je Art.
MAX_ISSUES_LISTED: Final = 500


class MasterDataError(Exception):
    """Abbruch des Imports mit einer klaren, deutschsprachigen Meldung."""


class MasterDataSourceDisabledError(MasterDataError):
    """Quelle ist nicht aktiviert/konfiguriert."""


@dataclass(frozen=True)
class MasterRecord:
    """Ein Stammdatensatz aus einer Quelle.

    ``values`` enthaelt nur die von der Quelle gelieferten Felder (ohne
    ``system_id``); fehlt ein Feld ganz, bleibt es bei der Uebernahme unveraendert.
    ``row`` ist die Zeilennummer in der Quelle (Excel: 1-basiert), sonst ``None``.
    """

    system_id: str
    values: dict[str, str | None]
    row: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {"system_id": self.system_id, "values": self.values, "row": self.row}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MasterRecord:
        return cls(str(data["system_id"]), dict(data["values"]), data.get("row"))


@dataclass(frozen=True)
class Issue:
    message: str
    row: int | None = None
    system_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"message": self.message, "row": self.row, "system_id": self.system_id}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Issue:
        return cls(str(data["message"]), data.get("row"), data.get("system_id"))


@dataclass
class ValidationReport:
    """Fehler verhindern die Uebernahme, Warnungen nicht."""

    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    rejected_reserved: int = 0
    invalid_emails: int = 0

    def error(self, issue: Issue) -> None:
        self.error_count += 1
        if len(self.errors) < MAX_ISSUES_LISTED:
            self.errors.append(issue)

    def warn(self, issue: Issue) -> None:
        self.warning_count += 1
        if len(self.warnings) < MAX_ISSUES_LISTED:
            self.warnings.append(issue)

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def to_json(self) -> dict[str, Any]:
        return {
            "errors": [i.to_json() for i in self.errors],
            "warnings": [i.to_json() for i in self.warnings],
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "rejected_reserved": self.rejected_reserved,
            "invalid_emails": self.invalid_emails,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ValidationReport:
        return cls(
            errors=[Issue.from_json(i) for i in data.get("errors", [])],
            warnings=[Issue.from_json(i) for i in data.get("warnings", [])],
            error_count=int(data.get("error_count", 0)),
            warning_count=int(data.get("warning_count", 0)),
            rejected_reserved=int(data.get("rejected_reserved", 0)),
            invalid_emails=int(data.get("invalid_emails", 0)),
        )


@dataclass
class SourceResult:
    """Ergebnis von Abruf + Validierung, Grundlage der Vorschau."""

    records: list[MasterRecord]
    columns: list[str]  # gelieferte Felder (ohne system_id), Anzeige-Reihenfolge
    report: ValidationReport
    source: str  # 'excel' | 'api'


class MasterDataSource(abc.ABC):
    """Quelle fuer Stammdaten (Excel-Datei, perspektivisch API).

    ``fetch()`` liefert Rohdatensaetze; strukturelle Fehler (z. B. fehlende
    Spalte System-ID) brechen mit :class:`MasterDataError` ab.
    """

    #: Wert fuer ``master_systems.source`` / ``master_imports.source``.
    source_kind: str = "excel"
    #: Gelieferte Felder (ohne system_id); nach ``fetch()`` gesetzt.
    columns: list[str]
    #: Hinweise der Quelle selbst (z. B. ignorierte Spalten); nach ``fetch()`` gesetzt.
    notes: list[Issue]

    def __init__(self) -> None:
        self.columns = list(fields.MASTER_DATA_FIELDS)
        self.notes = []

    @abc.abstractmethod
    def fetch(self) -> list[MasterRecord]:
        """Alle Datensaetze der Quelle abrufen."""

    def load(self) -> SourceResult:
        """``fetch()`` + gemeinsame Validierung."""
        raw = self.fetch()
        records, report = validate_records(raw, self.columns)
        for note in self.notes:
            report.warn(note)
        return SourceResult(records, list(self.columns), report, self.source_kind)


def validate_records(
    raw: list[MasterRecord], columns: list[str]
) -> tuple[list[MasterRecord], ValidationReport]:
    """Pruefung laut Auftrag 4/5.6.

    - leere System-ID, doppelte System-ID: **Fehler** (Uebernahme gesperrt)
    - Praefix ``NEW-``: Zeile abgelehnt, **Warnung**
    - ungueltige/fehlende E-Mail im Anwendungsverantwortlichen: **Warnung**
    - zu lange Werte: gekuerzt, **Warnung**
    """
    report = ValidationReport()
    if len(raw) > MAX_RECORDS:
        raise MasterDataError(
            f"Zu viele Datensätze ({len(raw)}); erlaubt sind höchstens {MAX_RECORDS}."
        )
    seen: dict[str, int | None] = {}
    cleaned: list[MasterRecord] = []
    for rec in raw:
        sid_raw = rec.system_id
        try:
            sid = clean_master_value("system_id", sid_raw)
        except ValidationError:
            report.error(Issue("System-ID ist zu lang.", rec.row, sid_raw[:100]))
            continue
        if sid is None:
            report.error(Issue("System-ID ist leer.", rec.row))
            continue
        if fields.is_reserved_id(sid):
            report.rejected_reserved += 1
            report.warn(
                Issue(
                    "System-ID mit reserviertem Präfix „NEW-“ – Zeile wird nicht übernommen.",
                    rec.row,
                    sid,
                )
            )
            continue
        if sid in seen:
            first = seen[sid]
            where = f" (erstmals in Zeile {first})" if first is not None else ""
            report.error(Issue(f"System-ID ist doppelt{where}.", rec.row, sid))
            continue
        seen[sid] = rec.row
        values: dict[str, str | None] = {}
        for name in columns:
            raw_value = rec.values.get(name)
            try:
                values[name] = clean_master_value(name, raw_value)
            except ValidationError:
                limit = fields.MASTER_FIELD_MAX[name]
                text = str(raw_value)
                values[name] = text[:limit]
                report.warn(
                    Issue(
                        f"{fields.DISPLAY_NAMES[name]} länger als {limit} Zeichen – gekürzt.",
                        rec.row,
                        sid,
                    )
                )
        if "app_owner" in columns:
            parsed = parse_emails(values.get("app_owner"))
            if not parsed.ok:
                report.invalid_emails += 1
                if parsed.invalid:
                    detail = "ungültige Adresse(n): " + "; ".join(parsed.invalid)[:300]
                else:
                    detail = "keine E-Mail-Adresse"
                report.warn(Issue(f"Anwendungsverantwortlicher: {detail}.", rec.row, sid))
        cleaned.append(MasterRecord(sid, values, rec.row))
    # Doppelte IDs sind Fehler; der erste Datensatz bleibt in der Liste, die
    # Uebernahme ist ohnehin gesperrt.
    return cleaned, report
