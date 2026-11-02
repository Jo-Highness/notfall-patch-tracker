"""Zertifikats-Identifier und deren Normalisierung (Auftrag 8.1, NPT-07).

Eine Funktion :func:`normalize_identifier` fuer alle Eingabewege (Bootstrap,
Admin-Formular, Zertifikat aus dem Gateway):

- ``subject_dn``: Attributtypen als kanonische Kurznamen in Grossbuchstaben,
  Werte NFKC + trim + Leerzeichen zusammenziehen + casefold, RDNs sortiert,
  Ausgabe RFC 4514. Eingabe RFC 4514/2253 oder OpenSSL-Slash-Format.
- ``san_email``: lowercase (alle SAN-Adressen eines Zertifikats sind Kandidaten).
- ``san_upn``: OID 1.3.6.1.4.1.311.20.2.3, casefold.
- ``fingerprint_sha256``: 64 Hex-Zeichen lowercase.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ObjectIdentifier

IdKind = Literal["subject_dn", "san_email", "san_upn", "fingerprint_sha256"]
ID_KINDS: tuple[IdKind, ...] = ("subject_dn", "san_email", "san_upn", "fingerprint_sha256")
ANY_ISSUER = "*"
UPN_OID = ObjectIdentifier("1.3.6.1.4.1.311.20.2.3")


class IdentifierError(ValueError):
    """Eingabe laesst sich nicht als Identifier der gewuenschten Art lesen."""


# Kanonische Kurznamen. Schluessel: Kleinbuchstaben-Name oder dotted OID.
_TYPE_ALIASES: dict[str, str] = {}
for _canon, _aliases in {
    "CN": ("cn", "commonname", "2.5.4.3"),
    "SN": ("sn", "surname", "2.5.4.4"),
    "SERIALNUMBER": ("serialnumber", "2.5.4.5"),
    "C": ("c", "countryname", "2.5.4.6"),
    "L": ("l", "localityname", "2.5.4.7"),
    "ST": ("st", "s", "stateorprovincename", "2.5.4.8"),
    "STREET": ("street", "streetaddress", "2.5.4.9"),
    "O": ("o", "organizationname", "2.5.4.10"),
    "OU": ("ou", "organizationalunitname", "2.5.4.11"),
    "TITLE": ("title", "2.5.4.12"),
    "GN": ("gn", "givenname", "2.5.4.42"),
    "INITIALS": ("initials", "2.5.4.43"),
    "GENERATIONQUALIFIER": ("generationqualifier", "2.5.4.44"),
    "DNQUALIFIER": ("dnqualifier", "2.5.4.46"),
    "PSEUDONYM": ("pseudonym", "2.5.4.65"),
    "ORGANIZATIONIDENTIFIER": ("organizationidentifier", "2.5.4.97"),
    "DC": ("dc", "domaincomponent", "0.9.2342.19200300.100.1.25"),
    "UID": ("uid", "userid", "0.9.2342.19200300.100.1.1"),
    "EMAILADDRESS": ("emailaddress", "email", "e", "1.2.840.113549.1.9.1"),
}.items():
    for _alias in _aliases:
        _TYPE_ALIASES[_alias] = _canon

_OID_RE = re.compile(r"^(?:oid\.)?([0-9]+(?:\.[0-9]+)+)$", re.IGNORECASE)
_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")
_WS_RE = re.compile(r"\s+")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

Ava = tuple[str, str]
Rdn = tuple[Ava, ...]


def canonical_type(attr_type: str) -> str:
    t = attr_type.strip()
    m = _OID_RE.match(t)
    if m:
        return _TYPE_ALIASES.get(m.group(1), m.group(1))
    if not _TYPE_RE.match(t):
        raise IdentifierError(f"ungueltiger Attributtyp: {attr_type!r}")
    return _TYPE_ALIASES.get(t.lower(), t.upper())


def normalize_value(value: str) -> str:
    v = unicodedata.normalize("NFKC", value)
    v = _WS_RE.sub(" ", v).strip()
    return v.casefold()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def _unescape_rfc4514_value(raw: str) -> str:
    """Wert gemaess RFC 4514 (bzw. 2253: auch in Anfuehrungszeichen) dekodieren."""
    s = raw.strip()
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        s = s[1:-1]
    if s.startswith("#"):
        # BER-kodierter Wert: unveraendert (Hex) vergleichen.
        return s.lower()
    out = bytearray()
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1 : i + 3]
            if len(nxt) == 2 and all(c in "0123456789abcdefABCDEF" for c in nxt):
                out.append(int(nxt, 16))
                i += 3
                continue
            out.extend(s[i + 1].encode())
            i += 2
            continue
        out.extend(ch.encode())
        i += 1
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IdentifierError("ungueltige Escape-Sequenz im DN") from exc


def _split_unescaped(s: str, seps: str) -> list[str]:
    """Teilt an Trennzeichen ausserhalb von Escapes und Anfuehrungszeichen."""
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    in_quotes = False
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            buf.append(s[i : i + 2])
            i += 2
            continue
        if ch == '"':
            in_quotes = not in_quotes
        if ch in seps and not in_quotes:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if in_quotes:
        raise IdentifierError("nicht geschlossene Anfuehrungszeichen im DN")
    parts.append("".join(buf))
    return parts


def _parse_ava(text_ava: str) -> Ava:
    if "=" not in text_ava:
        raise IdentifierError(f"Attribut ohne '=': {text_ava!r}")
    attr_type, raw_value = text_ava.split("=", 1)
    return canonical_type(attr_type), normalize_value(_unescape_rfc4514_value(raw_value))


def parse_dn(dn: str) -> list[Rdn]:
    """DN-String (RFC 4514/2253 oder OpenSSL-Slash-Format) in RDN-Liste."""
    s = dn.strip()
    if not s:
        raise IdentifierError("leerer DN")
    if s.startswith("/"):
        rdn_texts = [p for p in _split_unescaped(s[1:], "/") if p.strip()]
    else:
        # RFC 2253 erlaubt ';' als RDN-Trenner.
        rdn_texts = _split_unescaped(s, ",;")
    if not rdn_texts:
        raise IdentifierError("DN ohne RDN")
    rdns: list[Rdn] = []
    for rdn_text in rdn_texts:
        if not rdn_text.strip():
            raise IdentifierError("leeres RDN im DN")
        avas = tuple(sorted(_parse_ava(a) for a in _split_unescaped(rdn_text, "+")))
        rdns.append(avas)
    return rdns


def rdns_from_name(name: x509.Name) -> list[Rdn]:
    rdns: list[Rdn] = []
    for rdn in name.rdns:
        avas: list[Ava] = []
        for attr in rdn:
            value = attr.value
            if isinstance(value, bytes):
                value = "#" + value.hex()
            avas.append((canonical_type(attr.oid.dotted_string), normalize_value(value)))
        rdns.append(tuple(sorted(avas)))
    return rdns


# ---------------------------------------------------------------------------
# Serialisierung RFC 4514
# ---------------------------------------------------------------------------
_ESCAPE_CHARS = set('"+,;<>\\=')


def _escape_value(value: str) -> str:
    if value.startswith("#") and re.fullmatch(r"#[0-9a-f]+", value):
        return value
    out = []
    for i, ch in enumerate(value):
        if ch in _ESCAPE_CHARS or (i == 0 and ch in "# ") or (i == len(value) - 1 and ch == " "):
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ch == "\x7f":
            out.append(f"\\{ord(ch):02x}")
        else:
            out.append(ch)
    return "".join(out)


def serialize_rdns(rdns: list[Rdn]) -> str:
    ordered = sorted(rdns)
    return ",".join("+".join(f"{t}={_escape_value(v)}" for t, v in rdn) for rdn in ordered)


def normalize_dn(dn: str | x509.Name) -> str:
    rdns = rdns_from_name(dn) if isinstance(dn, x509.Name) else parse_dn(dn)
    return serialize_rdns(rdns)


# ---------------------------------------------------------------------------
# Oeffentliche API
# ---------------------------------------------------------------------------
def normalize_identifier(kind: IdKind, value: str) -> str:
    """Normalisiert eine textuelle Identifier-Eingabe der Art ``kind``."""
    if kind == "subject_dn":
        return normalize_dn(value)
    v = unicodedata.normalize("NFKC", value).strip()
    if kind == "san_email":
        if v.lower().startswith("email:"):
            v = v[6:].strip()
        if "@" not in v or any(c.isspace() for c in v):
            raise IdentifierError("ungueltige E-Mail-Adresse")
        return v.lower()
    if kind == "san_upn":
        if not v or any(c.isspace() for c in v):
            raise IdentifierError("ungueltiger UPN")
        return v.casefold()
    if kind == "fingerprint_sha256":
        v = v.lower()
        if v.startswith("sha256:") or v.startswith("sha256="):
            v = v[7:]
        v = re.sub(r"[\s:]", "", v)
        if not _HEX64_RE.match(v):
            raise IdentifierError("SHA-256-Fingerprint muss 64 Hex-Zeichen haben")
        return v
    raise IdentifierError(f"unbekannte Identifier-Art: {kind!r}")


def normalize_issuer(value: str) -> str:
    """Issuer fuer admin_users: normalisierter DN oder ``*`` (beliebig)."""
    v = value.strip()
    if not v or v == ANY_ISSUER:
        return ANY_ISSUER
    return normalize_dn(v)


def parse_bootstrap_entry(kind: IdKind, entry: str) -> tuple[str, str]:
    """``[issuer::]identifier`` -> (issuer_norm, identifier_norm)."""
    if "::" in entry:
        issuer_raw, ident_raw = entry.split("::", 1)
        return normalize_issuer(issuer_raw), normalize_identifier(kind, ident_raw)
    return ANY_ISSUER, normalize_identifier(kind, entry)


def _decode_der_utf8(data: bytes) -> str | None:
    """Minimaler DER-Dekoder fuer den UPN (UTF8String, Tag 0x0C)."""
    if len(data) < 2 or data[0] != 0x0C:
        return None
    length = data[1]
    offset = 2
    if length & 0x80:
        n = length & 0x7F
        if n == 0 or n > 4 or len(data) < 2 + n:
            return None
        length = int.from_bytes(data[2 : 2 + n], "big")
        offset = 2 + n
    body = data[offset : offset + length]
    if len(body) != length:
        return None
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return None


@dataclass(frozen=True)
class CertIdentity:
    """Aus einem verifizierten Client-Zertifikat abgeleitete Identitaet."""

    kind: IdKind
    issuer_norm: str
    candidates: tuple[str, ...]  # normalisierte Identifier; ein Treffer genuegt
    subject_dn: str  # Anzeige (RFC 4514 wie im Zertifikat)
    issuer_dn: str
    fingerprint_sha256: str
    serial_hex: str
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def primary(self) -> str:
        """Identifier fuer Anzeige, Audit und Anfrage ('' falls keiner ermittelbar)."""
        return self.candidates[0] if self.candidates else ""


def _san(cert: x509.Certificate) -> x509.SubjectAlternativeName | None:
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return None
    return ext.value


def identity_from_cert(cert: x509.Certificate, kind: IdKind) -> CertIdentity:
    der = cert.public_bytes(Encoding.DER)
    fp256 = hashlib.sha256(der).hexdigest()
    candidates: list[str] = []
    if kind == "subject_dn":
        if len(cert.subject.rdns) > 0:
            candidates.append(normalize_dn(cert.subject))
    elif kind == "san_email":
        san = _san(cert)
        if san is not None:
            emails = san.get_values_for_type(x509.RFC822Name)
            candidates.extend(sorted({e.strip().lower() for e in emails if e.strip()}))
    elif kind == "san_upn":
        san = _san(cert)
        if san is not None:
            for other in san.get_values_for_type(x509.OtherName):
                if other.type_id == UPN_OID:
                    decoded = _decode_der_utf8(other.value)
                    if decoded and decoded.strip():
                        candidates.append(decoded.strip().casefold())
            candidates = sorted(set(candidates))
    elif kind == "fingerprint_sha256":
        candidates.append(fp256)
    return CertIdentity(
        kind=kind,
        issuer_norm=normalize_dn(cert.issuer),
        candidates=tuple(candidates),
        subject_dn=cert.subject.rfc4514_string(),
        issuer_dn=cert.issuer.rfc4514_string(),
        fingerprint_sha256=fp256,
        serial_hex=format(cert.serial_number, "x"),
    )


def sha1_fingerprint(cert: x509.Certificate) -> str:
    """SHA-1 des DER, wie nginx ``$ssl_client_fingerprint`` (nur Integritaetsabgleich)."""
    der = cert.public_bytes(Encoding.DER)
    return hashlib.sha1(der, usedforsecurity=False).hexdigest()
