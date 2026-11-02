"""Eine Domain-Validierung fuer UI, Import, Admin-Upload und Restore (NPT-04).

Je nach Herkunft unterscheidet sich nur die Folge (Fehler, Warnung, Abbruch),
nicht ob geprueft wird.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Final

from npt.domain import fields, labels

# Pragmatische Adresspruefung: lokaler Teil ohne Leer-/Steuerzeichen, Domain mit
# mindestens einem Punkt und einer TLD aus Buchstaben (>= 2). Bewusst keine
# vollstaendige RFC-5322-Grammatik (Quoted-Strings usw. kommen in Stammdaten nicht vor).
_EMAIL_RE: Final = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)
_ANGLE_RE: Final = re.compile(r"<([^<>]*)>")
_SPLIT_RE: Final = re.compile(r"[;,]")
# Steuerzeichen ausser Tab/Zeilenumbruch (fuer mehrzeilige Felder).
_CTRL_MULTILINE_RE: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CTRL_RE: Final = re.compile(r"[\x00-\x1f\x7f]")
EMAIL_MAX: Final = 254


class ValidationError(ValueError):
    """Ungueltiger Feldwert; ``field`` und eine deutschsprachige Meldung."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


@dataclass(frozen=True)
class EmailParse:
    valid: tuple[str, ...]
    invalid: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Mindestens eine gueltige Adresse und keine ungueltige."""
        return bool(self.valid) and not self.invalid


def normalize_email(address: str) -> str:
    return address.strip().lower()


def is_valid_email(address: str) -> bool:
    return len(address) <= EMAIL_MAX and _EMAIL_RE.fullmatch(address) is not None


def parse_emails(text: str | None) -> EmailParse:
    """Adressen aus dem Feld „Anwendungsverantwortlicher".

    Mehrere Adressen sind durch ``;`` oder ``,`` getrennt; je Teil wird eine
    Adresse in spitzen Klammern (``Name <a@b.de>``) bevorzugt. Gueltige Adressen
    werden normalisiert (lowercase, getrimmt) und dedupliziert.
    """
    valid: list[str] = []
    invalid: list[str] = []
    if not text:
        return EmailParse((), ())
    for raw in _SPLIT_RE.split(text):
        part = raw.strip()
        if not part:
            continue
        m = _ANGLE_RE.search(part)
        candidate = (m.group(1) if m else part).strip()
        if is_valid_email(candidate):
            norm = normalize_email(candidate)
            if norm not in valid:
                valid.append(norm)
        else:
            invalid.append(part)
    return EmailParse(tuple(valid), tuple(invalid))


def cell_to_text(value: object) -> str | None:
    """Excel-/API-Zellwert -> getrimmter Text; leer -> ``None``.

    Ganzzahlige Floats (``123.0``) werden ohne Nachkommastellen geschrieben,
    Datumswerte als ISO-Datum.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        text = "WAHR" if value else "FALSCH"
    elif isinstance(value, float):
        text = str(int(value)) if value.is_integer() else repr(value)
    elif isinstance(value, Decimal):
        text = format(value.normalize(), "f")
    elif isinstance(value, datetime):
        text = value.date().isoformat() if value.time() == time() else value.isoformat(" ")
    elif isinstance(value, date):
        text = value.isoformat()
    else:
        text = str(value)
    text = _CTRL_MULTILINE_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    return text or None


def clean_single_line(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(_CTRL_RE.sub(" ", value).split())
    return text or None


def clean_master_value(field: str, value: object) -> str | None:
    """Stammdatenfeld bereinigen; Laenge wird geprueft (``ValidationError``)."""
    text = cell_to_text(value)
    if text is None:
        return None
    if field != "system_description":
        text = clean_single_line(text)
        if text is None:
            return None
    limit = fields.MASTER_FIELD_MAX[field]
    if len(text) > limit:
        raise ValidationError(field, f"zu lang (max. {limit} Zeichen)")
    return text


def clean_editable_value(field: str, value: str | None) -> str | None:
    """*-Feld pruefen und normalisieren (Enum-Schluessel bzw. Plaintext mit Laenge).

    Leerer Text bzw. ``None`` bedeutet „Leer" (NULL).
    """
    if field not in fields.EDITABLE_FIELDS:
        raise ValidationError(field, "Feld ist nicht schreibbar")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(field, "Text erwartet")
    if field in fields.ENUM_FIELDS:
        key = value.strip()
        if not key:
            return None
        if key not in fields.ENUM_FIELDS[field]:
            raise ValidationError(field, "ungültiger Auswahlwert")
        return key
    if "\x00" in value:
        raise ValidationError(field, "ungültige Zeichen")
    if field == "installed_version":
        text = clean_single_line(value)
        limit = fields.INSTALLED_VERSION_MAX
    else:
        text = _CTRL_MULTILINE_RE.sub("", value.replace("\r\n", "\n").replace("\r", "\n"))
        text = text.strip() or None
        limit = fields.REMARK_MAX
    if text is not None and len(text) > limit:
        raise ValidationError(field, f"zu lang (max. {limit} Zeichen)")
    return text


def enum_key_from_label(field: str, value: str | None) -> str | None:
    """Excel-Wert eines Enum-Felds (Anzeigename oder Schluessel) -> Schluessel.

    ``Leer``/``(leer)``/leer -> ``None``; unbekannt -> ``ValidationError``.
    """
    if value is None:
        return None
    text = " ".join(value.split()).casefold()
    if text in ("", "leer", labels.EMPTY_LABEL.casefold()):
        return None
    for key, name in fields.ENUM_FIELDS[field].items():
        if text in (key.casefold(), name.casefold()):
            return key
    raise ValidationError(field, f"ungültiger Wert {value!r}")
