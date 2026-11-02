"""Zentrales Label-Mapping (NPT-10).

Enums werden als ``text`` mit CHECK und ASCII-Schluesseln gespeichert; „Leer"
ist ``NULL`` und hat keinen Schluessel. Die Anzeigenamen stehen nur hier.
"""

from __future__ import annotations

from typing import Final

EMPTY_LABEL: Final = "(leer)"

AFFECTED: Final[dict[str, str]] = {
    "ja": "Ja",
    "nein": "Nein",
    "in_pruefung": "In Prüfung",
}

ACTION_TAKEN: Final[dict[str, str]] = {
    "update_installiert": "Update installiert",
    "mitigiert": "Mitigiert (siehe Bemerkung)",
    "isoliert": "Isoliert",
    "abgeschaltet": "Abgeschaltet",
}

# Kurz-Tooltips an den *-Feldern (Auftrag 11.2): Spaltenkopf der Hauptliste und
# Formular „Neues System". Ausfuehrlich: Bearbeiter-Handbuch.
FIELD_HINTS: Final[dict[str, str]] = {
    "affected": (
        "Ist das System von der Schwachstelle betroffen? Ja / Nein / In Prüfung "
        "(Prüfung läuft). Leer = noch nicht bearbeitet."
    ),
    "installed_version": (
        "Aktuell installierte Version der betroffenen Komponente, z. B. 2.17.1 "
        "(eine Zeile, max. 255 Zeichen)."
    ),
    "action_taken": (
        "Was wurde getan? Update installiert / Mitigiert (Details in Bemerkung) / "
        "Isoliert / Abgeschaltet. Leer = noch keine Maßnahme."
    ),
    "remark": (
        "Freitext (max. 4000 Zeichen), z. B. Begründung, Mitigation, Termin, "
        "Ansprechpartner. Wird als reiner Text gespeichert."
    ),
}

ORIGIN: Final[dict[str, str]] = {
    "stammdaten": "Stammdaten",
    "manuell_angelegt": "Manuell angelegt",
    "admin_upload": "Admin-Upload",
}

PROJECT_STATUS: Final[dict[str, str]] = {
    "aktiv": "aktiv",
    "abgeschlossen": "abgeschlossen",
    "archiviert": "archiviert",
}

MASTER_SOURCE: Final[dict[str, str]] = {"excel": "Excel", "api": "API"}

CHANGE_SOURCE: Final[dict[str, str]] = {
    "ui": "Bearbeiter",
    "admin_upload": "Admin-Upload",
    "admin": "Admin",
    "restore": "Wiederherstellung",
}

CHANGE_ACTION: Final[dict[str, str]] = {
    "geaendert": "geändert",
    "angelegt": "angelegt",
    "geloescht": "gelöscht",
}

BACKUP_TRIGGER: Final[dict[str, str]] = {
    "zeitgesteuert": "zeitgesteuert",
    "manuell": "manuell",
    "vor_import": "vor Import",
    "vor_restore": "vor Wiederherstellung",
}

# Backup-Intervall in Minuten; 0 = aus (Auftrag 6.2).
BACKUP_INTERVALS_MIN: Final[dict[int, str]] = {
    0: "aus",
    15: "15 min",
    30: "30 min",
    60: "1 h",
    240: "4 h",
    720: "12 h",
    1440: "24 h",
}

MAIL_DELIVERY_STATUS: Final[dict[str, str]] = {
    "ausstehend": "ausstehend",
    "in_versand": "in Versand",
    "gesendet": "gesendet",
    "fehlgeschlagen": "fehlgeschlagen",
}

ACCESS_REQUEST_STATUS: Final[dict[str, str]] = {
    "offen": "offen",
    "bestaetigt": "bestätigt",
    "abgelehnt": "abgelehnt",
}

ID_KIND: Final[dict[str, str]] = {
    "subject_dn": "Subject-DN",
    "san_email": "SAN E-Mail",
    "san_upn": "SAN UPN",
    "fingerprint_sha256": "SHA-256-Fingerprint",
}


def label(mapping: dict[str, str], key: str | None) -> str:
    """Anzeigename zu einem Schluessel; ``None`` -> „(leer)"."""
    if key is None:
        return EMPTY_LABEL
    return mapping.get(key, key)


def check_in(column: str, mapping: dict[str, str] | dict[int, str]) -> str:
    """SQL-CHECK-Ausdruck ``column IN (...)`` aus einem Mapping (nur feste ASCII-Schluessel)."""
    values = ", ".join(f"'{k}'" if isinstance(k, str) else str(k) for k in mapping)
    return f"{column} IN ({values})"
