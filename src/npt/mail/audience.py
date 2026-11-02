"""Zielgruppen-Selektoren und Empfaengerbildung (Auftrag 6.3).

Ein Selektor ist ein Name plus ein Filterkriterium auf ``project_systems``. Das
Kriterium ist in der Filter-Grammatik der Hauptliste formuliert
(``npt.domain.filters``, maßgebliche Implementierung) – damit gelten fuer Selektoren,
Export, Pivot-Drilldown und den Selektor „Benutzerdefiniert" dieselben Regeln.
Weitere Selektoren werden mit :func:`register` ergaenzt, ohne Umbau.

Empfaenger: Gruppierung der getroffenen Systeme nach normalisierter Adresse
(lowercase, getrimmt) aus „Anwendungsverantwortlicher"; mehrere Adressen (``;``/``,``)
ergeben je Adresse eine Mail mit ihren Systemen. Leere und ungueltige Angaben werden
im Bericht aufgefuehrt und uebersprungen.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import parse_qsl, urlsplit

from npt.domain import filters
from npt.domain.validation import parse_emails

CUSTOM: Final = "benutzerdefiniert"
CUSTOM_QUERY_MAX: Final = 20_000


@dataclass(frozen=True)
class Selector:
    key: str
    name: str
    description: str
    # Filterkriterium als Query-Parameter der Hauptliste; ``None`` = aus der Kampagne
    # (Selektor „Benutzerdefiniert").
    params: tuple[tuple[str, str], ...] | None


SELECTORS: dict[str, Selector] = {}


def register(selector: Selector) -> Selector:
    if selector.key in SELECTORS:
        raise ValueError(f"Selektor {selector.key!r} existiert bereits")
    SELECTORS[selector.key] = selector
    return selector


register(
    Selector(
        "betroffen_leer",
        "„System betroffen“ noch leer",
        "Alle Anwendungsverantwortlichen mit mindestens einem System, bei dem „System "
        "betroffen“ noch leer ist. [LINK] enthält nur diese Systeme.",
        (("f.affected", filters.EMPTY_KEY),),
    )
)
register(
    Selector(
        "ja_ohne_aktion",
        "Betroffen = Ja, Aktion noch leer",
        "Alle Anwendungsverantwortlichen mit mindestens einem System mit „System betroffen“ "
        "= Ja und leerer „Ausgeführter Aktion“. [LINK] enthält nur diese Systeme.",
        (("f.affected", "ja"), ("f.action_taken", filters.EMPTY_KEY)),
    )
)
register(
    Selector(
        CUSTOM,
        "Benutzerdefiniert (Filter der Hauptliste)",
        "Übernimmt Filter und Suche einer Hauptlisten-Sicht: Adresse der gefilterten "
        "Hauptliste (oder nur den Teil nach „?“) einfügen. [LINK] enthält die getroffenen "
        "Systeme.",
        None,
    )
)


class SelectorError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def custom_params(raw: str) -> list[tuple[str, str]]:
    """Filter aus eingefuegter Hauptlisten-URL bzw. Query-String (kanonisch, ohne Sortierung).

    Akzeptiert ``https://…/p/<token>/?f.affected=leer``, ``?f.affected=leer`` und
    ``f.affected=leer``. Ein Projekt-Token in der URL wird verworfen (nur die Filter
    zaehlen, das Projekt bestimmt die Kampagne).
    """
    text = raw.strip()
    if len(text) > CUSTOM_QUERY_MAX:
        raise SelectorError("mail_filter_zu_lang")
    if "?" in text:
        text = text.split("?", 1)[1]
    elif text.lower().startswith(("http://", "https://")):
        text = urlsplit(text).query
    text = text.split("#", 1)[0]
    pairs = parse_qsl(text, keep_blank_values=True)
    state = filters.parse(pairs)
    canonical = filters.to_params(state)
    return [(k, v) for k, v in canonical if k not in ("sort", "dir")]


def selector_state(selector_key: str, custom_query: str | None = None) -> filters.FilterState:
    selector = SELECTORS.get(selector_key)
    if selector is None:
        raise SelectorError("mail_selektor_ungueltig")
    if selector.params is None:
        return filters.parse(custom_params(custom_query or ""))
    return filters.parse(selector.params)


def selector_params(selector_key: str, custom_query: str | None = None) -> list[tuple[str, str]]:
    """Kanonische Filterparameter des Selektors (fuer Anzeige und Hauptlisten-Link)."""
    return filters.to_params(selector_state(selector_key, custom_query))


# ---------------------------------------------------------------- Empfaenger
@dataclass(frozen=True)
class SystemRef:
    system_id: str
    system_name: str | None

    def as_json(self) -> dict[str, str | None]:
        return {"id": self.system_id, "name": self.system_name}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> SystemRef:
        name = data.get("name")
        return cls(str(data["id"]), str(name) if name is not None else None)


@dataclass
class Recipient:
    email: str
    systems: list[SystemRef] = field(default_factory=list)


@dataclass
class SkippedEntry:
    """Uebersprungene Angabe im Feld „Anwendungsverantwortlicher" (leer oder ungueltig)."""

    reason: str  # "leer" | "ungueltig"
    value: str
    system_ids: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {"reason": self.reason, "value": self.value, "system_ids": self.system_ids}


@dataclass
class Audience:
    recipients: list[Recipient]
    skipped: list[SkippedEntry]
    system_count: int  # vom Selektor getroffene Systeme
    systems_without_recipient: int  # getroffene Systeme ohne jede gueltige Adresse

    @property
    def recipient_count(self) -> int:
        return len(self.recipients)

    def find(self, email: str) -> Recipient | None:
        norm = email.strip().lower()
        return next((r for r in self.recipients if r.email == norm), None)


def build_audience(rows: Iterable[Mapping[str, Any]], state: filters.FilterState) -> Audience:
    """Systeme filtern und nach normalisierter Adresse gruppieren (eine Mail je Adresse)."""
    hit = sorted((r for r in rows if filters.matches(r, state)), key=lambda r: str(r["system_id"]))
    by_email: dict[str, Recipient] = {}
    skipped: dict[tuple[str, str], SkippedEntry] = {}
    without = 0
    for row in hit:
        sid = str(row["system_id"])
        ref = SystemRef(sid, row.get("system_name"))
        raw = row.get("app_owner")
        parsed = parse_emails(raw)
        if not parsed.valid:
            without += 1
        if not parsed.valid and not parsed.invalid:
            entry = skipped.setdefault(("leer", ""), SkippedEntry("leer", ""))
            entry.system_ids.append(sid)
        for bad in parsed.invalid:
            entry = skipped.setdefault(("ungueltig", bad), SkippedEntry("ungueltig", bad))
            entry.system_ids.append(sid)
        for email in parsed.valid:
            by_email.setdefault(email, Recipient(email)).systems.append(ref)
    recipients = [by_email[k] for k in sorted(by_email)]
    skipped_list = sorted(skipped.values(), key=lambda e: (e.reason != "leer", e.value.lower()))
    return Audience(recipients, skipped_list, len(hit), without)


def skipped_to_json(entries: Sequence[SkippedEntry]) -> list[dict[str, Any]]:
    return [e.as_json() for e in entries]
