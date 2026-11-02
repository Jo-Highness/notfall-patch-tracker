"""Felder eines Systems: Reihenfolge, Anzeigenamen, Kopfzeilen-Zuordnung (Auftrag 3.1).

Eine Quelle fuer Liste, Export, Import und Vorlage. Die Spaltenreihenfolge ist
exakt die der Tabelle in Auftrag 3.1; ``origin`` („Herkunft") wird in Liste und
Export zusaetzlich am Ende gefuehrt.
"""

from __future__ import annotations

import unicodedata
from typing import Final

from npt.domain import labels

# Alle 15 Felder in Anzeige-Reihenfolge (Auftrag 3.1).
SYSTEM_FIELDS: Final[tuple[str, ...]] = (
    "system_id",
    "system_name",
    "sbf",
    "digital_access_zone",
    "affected",
    "installed_version",
    "action_taken",
    "remark",
    "system_description",
    "app_owner",
    "app_type",
    "lifecycle",
    "fqdn",
    "cio_unit",
    "foreign_key",
)

# Stammdatenfelder (ohne *-Felder), in Anzeige-Reihenfolge.
MASTER_FIELDS: Final[tuple[str, ...]] = (
    "system_id",
    "system_name",
    "sbf",
    "digital_access_zone",
    "system_description",
    "app_owner",
    "app_type",
    "lifecycle",
    "fqdn",
    "cio_unit",
    "foreign_key",
)
# Stammdatenfelder ohne den Schluessel.
MASTER_DATA_FIELDS: Final[tuple[str, ...]] = MASTER_FIELDS[1:]

# Durch Bearbeiter schreibbare *-Felder.
EDITABLE_FIELDS: Final[tuple[str, ...]] = (
    "affected",
    "installed_version",
    "action_taken",
    "remark",
)

# Enum-Felder und ihre Label-Mappings (Schluessel ASCII, „Leer" = NULL).
ENUM_FIELDS: Final[dict[str, dict[str, str]]] = {
    "affected": labels.AFFECTED,
    "action_taken": labels.ACTION_TAKEN,
    "origin": labels.ORIGIN,
}

# Liste/Export: alle Felder + Herkunft.
LIST_FIELDS: Final[tuple[str, ...]] = (*SYSTEM_FIELDS, "origin")

DISPLAY_NAMES: Final[dict[str, str]] = {
    "system_id": "System-ID",
    "system_name": "System-Name",
    "sbf": "SBF",
    "digital_access_zone": "Digital Access Zone",
    "affected": "System betroffen*",
    "installed_version": "Installierte Version*",
    "action_taken": "Ausgeführte Aktion*",
    "remark": "Bemerkung*",
    "system_description": "System Beschreibung",
    "app_owner": "Anwendungsverantwortlicher",
    "app_type": "Art der Anwendung",
    "lifecycle": "Lifecycle",
    "fqdn": "FQDN",
    "cio_unit": "CIO-Einheit",
    "foreign_key": "Fremdschlüssel",
    "origin": "Herkunft",
}

# Laengengrenzen (serverseitige Validierung).
INSTALLED_VERSION_MAX: Final = 255
REMARK_MAX: Final = 4000
SYSTEM_ID_MAX: Final = 100
MASTER_TEXT_MAX: Final = 1000
DESCRIPTION_MAX: Final = 4000
MASTER_FIELD_MAX: Final[dict[str, int]] = {
    f: (DESCRIPTION_MAX if f == "system_description" else MASTER_TEXT_MAX)
    for f in MASTER_DATA_FIELDS
} | {"system_id": SYSTEM_ID_MAX}

# Reserviertes Praefix fuer manuell angelegte Systeme (Auftrag 5.6).
NEW_ID_PREFIX: Final = "NEW-"


def normalize_header(text: object) -> str:
    """Kopfzeilen-Text tolerant vergleichbar machen.

    NFKC, Whitespace-Folgen zu einem Leerzeichen, trimmen, casefold. Ein
    abschliessendes ``*`` (Kennzeichnung der *-Felder) ist optional.
    """
    if text is None:
        return ""
    value = unicodedata.normalize("NFKC", str(text))
    value = " ".join(value.split()).casefold()
    return value.removesuffix("*").rstrip()


# Kopfzeile -> internes Feld. Akzeptiert Anzeigenamen (mit/ohne *) und interne Namen.
HEADER_LOOKUP: Final[dict[str, str]] = {
    **{normalize_header(name): field for field, name in DISPLAY_NAMES.items()},
    **{normalize_header(field): field for field in DISPLAY_NAMES},
}


def format_new_id(number: int) -> str:
    """``NEW-0001`` …; ab 10000 ohne Kuerzung weiter (Auftrag 5.6)."""
    return f"{NEW_ID_PREFIX}{number:04d}"


def is_reserved_id(system_id: str) -> bool:
    return system_id.upper().startswith(NEW_ID_PREFIX)
