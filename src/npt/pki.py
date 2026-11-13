"""Pruefung hochgeladener Vertrauensanker und Zertifikate (NPT-15).

- :func:`parse_ca_bundle`   CA-Bundle (Root + Intermediates) fuer die mTLS-Pruefung
- :func:`parse_crl`         Sperrliste, muss von einer CA des Bundles signiert sein
- :func:`parse_client_cert` oeffentliches Admin-Zertifikat (nie privater Schluessel)
- :func:`verify_chain`      Kettenpruefung Client-Zertifikat -> selbstsignierte Root
- :func:`generate_self_signed` Rueckfall-Serverzertifikat des Gateways

Bewusst streng: alles, was nginx spaeter nicht als Vertrauensanker akzeptieren
wuerde oder was nicht in ein CA-Bundle gehoert, wird mit festem Fehlerschluessel
abgewiesen (``PkiError.code``), nie mit Bibliothekstext.
"""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from npt import limits

_KEY_MARKERS = (b"PRIVATE KEY", b"ENCRYPTED", b"PKCS12")
MAX_CHAIN = 6
_CRL_BEGIN = b"-----BEGIN X509 CRL-----"
# Bibliotheksfehler bei defekten Zertifikaten (doppelte/kaputte Erweiterungen, unbekannte
# Algorithmen); Aufrufer bilden sie auf eine feste Meldung ab statt auf HTTP 500.
CRYPTO_ERRORS: tuple[type[Exception], ...] = (
    ValueError,
    TypeError,
    UnsupportedAlgorithm,
    x509.DuplicateExtension,
    x509.UnsupportedGeneralNameType,
)


class PkiError(ValueError):
    """Fachlicher Fehler; ``code`` ist ein fester Meldungsschluessel."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CertInfo:
    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime
    sha256: str
    is_ca: bool
    self_signed: bool


def _now() -> datetime:
    return datetime.now(tz=UTC)


def sha256_hex(cert: x509.Certificate) -> str:
    return hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()


def fingerprint_display(hex_digest: str) -> str:
    return ":".join(hex_digest[i : i + 2] for i in range(0, len(hex_digest), 2)).upper()


def _is_ca(cert: x509.Certificate) -> bool:
    try:
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        return False
    except CRYPTO_ERRORS:
        raise PkiError("zertifikat_defekt") from None
    return bool(bc.ca)


def _issued_by(cert: x509.Certificate, issuer: x509.Certificate) -> bool:
    if cert.issuer != issuer.subject:
        return False
    try:
        cert.verify_directly_issued_by(issuer)
    except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm):
        return False
    return True


def _self_signed(cert: x509.Certificate) -> bool:
    return _issued_by(cert, cert)


def info(cert: x509.Certificate) -> CertInfo:
    return CertInfo(
        subject=cert.subject.rfc4514_string(),
        issuer=cert.issuer.rfc4514_string(),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        sha256=sha256_hex(cert),
        is_ca=_is_ca(cert),
        self_signed=_self_signed(cert),
    )


def _reject_keys(data: bytes) -> None:
    upper = data.upper()
    if any(m in upper for m in _KEY_MARKERS):
        raise PkiError("privater_schluessel")


def _load_certs(data: bytes) -> list[x509.Certificate]:
    """PEM (ein oder mehrere Zertifikate) oder ein einzelnes DER-Zertifikat."""
    if b"-----BEGIN" in data:
        if b"-----BEGIN CERTIFICATE-----" not in data:
            raise PkiError("kein_zertifikat")
        try:
            return x509.load_pem_x509_certificates(data)
        except ValueError:
            raise PkiError("kein_zertifikat") from None
    try:
        return [x509.load_der_x509_certificate(data)]
    except ValueError:
        # Auch PKCS#12 (.p12/.pfx) landet hier: enthaelt den privaten Schluessel.
        raise PkiError("kein_zertifikat") from None


def to_pem(certs: list[x509.Certificate]) -> str:
    return "".join(c.public_bytes(Encoding.PEM).decode("ascii") for c in certs)


def parse_ca_bundle(data: bytes, now: datetime | None = None) -> list[x509.Certificate]:
    """CA-Bundle pruefen; liefert die Zertifikate (Duplikate entfernt, Reihenfolge stabil)."""
    if not data.strip():
        raise PkiError("datei_leer")
    if len(data) > limits.CA_BUNDLE_MAX_BYTES:
        raise PkiError("datei_zu_gross")
    _reject_keys(data)
    certs = _load_certs(data)
    if len(certs) > limits.CA_BUNDLE_MAX_CERTS:
        raise PkiError("zu_viele_zertifikate")
    current = now or _now()
    seen: set[str] = set()
    result: list[x509.Certificate] = []
    for cert in certs:
        if not _is_ca(cert):
            raise PkiError("keine_ca")
        if cert.not_valid_after_utc <= current:
            raise PkiError("ca_abgelaufen")
        fp = sha256_hex(cert)
        if fp not in seen:
            seen.add(fp)
            result.append(cert)
    if not any(_self_signed(c) for c in result):
        # nginx (OpenSSL ohne PARTIAL_CHAIN) braucht die Root im Bundle.
        raise PkiError("keine_root")
    return result


def load_bundle_pem(pem: str | None) -> list[x509.Certificate]:
    """Gespeichertes (bereits geprueftes) Bundle laden; leer bei None."""
    if not pem:
        return []
    return x509.load_pem_x509_certificates(pem.encode("ascii"))


def _crl_signed_by(crl: x509.CertificateRevocationList, ca: x509.Certificate) -> bool:
    try:
        return bool(crl.is_signature_valid(ca.public_key()))  # type: ignore[arg-type]
    except (TypeError, ValueError, UnsupportedAlgorithm):
        return False


def _split_crls(data: bytes) -> list[bytes]:
    if _CRL_BEGIN not in data:
        return [data]  # DER (genau eine CRL)
    parts = data.split(_CRL_BEGIN)[1:]
    return [_CRL_BEGIN + p for p in parts]


def parse_crls(
    data: bytes, bundle: list[x509.Certificate], now: datetime | None = None
) -> list[x509.CertificateRevocationList]:
    """Eine oder mehrere CRLs (PEM hintereinander, oder eine DER-CRL).

    Jede muss von einer CA des Bundles signiert und noch gueltig sein.
    """
    if not data.strip():
        raise PkiError("datei_leer")
    if len(data) > limits.CRL_MAX_BYTES:
        raise PkiError("datei_zu_gross")
    _reject_keys(data)
    if b"-----BEGIN" in data and _CRL_BEGIN not in data:
        raise PkiError("keine_crl")
    crls: list[x509.CertificateRevocationList] = []
    for chunk in _split_crls(data):
        try:
            if chunk.startswith(_CRL_BEGIN):
                crl = x509.load_pem_x509_crl(chunk)
            else:
                crl = x509.load_der_x509_crl(chunk)
        except CRYPTO_ERRORS:
            raise PkiError("keine_crl") from None
        if not any(c.subject == crl.issuer and _crl_signed_by(crl, c) for c in bundle):
            raise PkiError("crl_fremd")
        nxt = crl.next_update_utc
        if nxt is not None and nxt <= (now or _now()):
            # nginx lehnt mit abgelaufener CRL ALLE Client-Zertifikate ab.
            raise PkiError("crl_abgelaufen")
        crls.append(crl)
    return crls


def parse_crl(
    data: bytes, bundle: list[x509.Certificate], now: datetime | None = None
) -> x509.CertificateRevocationList:
    """Genau die erste CRL (Kompatibilitaet/Tests)."""
    return parse_crls(data, bundle, now)[0]


def load_crls_pem(pem: str | None) -> list[x509.CertificateRevocationList]:
    if not pem:
        return []
    return [x509.load_pem_x509_crl(c) for c in _split_crls(pem.encode("ascii"))]


def crls_missing_for(
    bundle: list[x509.Certificate], crls: list[x509.CertificateRevocationList]
) -> list[str]:
    """CAs des Bundles ohne passende CRL.

    nginx prueft mit gesetzter CRL (CRL_CHECK_ALL) JEDE CA der Kette; fehlt eine CRL,
    werden alle Zertifikate dieser Kette abgewiesen.
    """
    if not crls:
        return []
    return [
        c.subject.rfc4514_string()
        for c in bundle
        if not any(crl.issuer == c.subject and _crl_signed_by(crl, c) for crl in crls)
    ]


def is_revoked(cert: x509.Certificate, crls: list[x509.CertificateRevocationList]) -> bool:
    return any(
        crl.issuer == cert.issuer
        and crl.get_revoked_certificate_by_serial_number(cert.serial_number) is not None
        for crl in crls
    )


def crl_pem(crl: x509.CertificateRevocationList | list[x509.CertificateRevocationList]) -> str:
    items = crl if isinstance(crl, list) else [crl]
    return "".join(c.public_bytes(Encoding.PEM).decode("ascii") for c in items)


def parse_client_cert(data: bytes, now: datetime | None = None) -> x509.Certificate:
    """Oeffentliches Client-Zertifikat (erstes der Datei); Schluessel/PKCS#12 abgewiesen."""
    if not data.strip():
        raise PkiError("datei_leer")
    if len(data) > limits.CERT_UPLOAD_MAX_BYTES:
        raise PkiError("datei_zu_gross")
    _reject_keys(data)
    cert = _load_certs(data)[0]
    if _is_ca(cert):
        raise PkiError("ist_ca")
    current = now or _now()
    if cert.not_valid_after_utc <= current or cert.not_valid_before_utc > current:
        raise PkiError("zertifikat_abgelaufen")
    return cert


def verify_chain(
    cert: x509.Certificate, bundle: list[x509.Certificate], now: datetime | None = None
) -> int | None:
    """Kettenlaenge bis zu einer selbstsignierten Root im Bundle, sonst ``None``.

    Laenge 1 = direkt von der Root ausgestellt (entspricht ``ssl_verify_depth``).
    Alle Glieder muessen zeitlich gueltig sein.
    """
    current = now or _now()
    node = cert
    for depth in range(1, MAX_CHAIN + 1):
        parent = next((c for c in bundle if _issued_by(node, c)), None)
        if parent is None:
            return None
        if not parent.not_valid_before_utc <= current < parent.not_valid_after_utc:
            return None
        if _self_signed(parent):
            return depth
        node = parent
    return None


def generate_self_signed(hostnames: list[str], now: datetime | None = None) -> tuple[str, str, str]:
    """Selbstsigniertes Serverzertifikat (EC P-256); liefert (cert_pem, key_pem, sha256)."""
    current = now or _now()
    key = ec.generate_private_key(ec.SECP256R1())
    names = [h for h in dict.fromkeys(hostnames) if h] or ["localhost"]
    san: list[x509.GeneralName] = []
    for h in names:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            san.append(x509.DNSName(h))
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, names[0][:64]),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Notfall-Patch-Tracker (selbstsigniert)"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(current - timedelta(minutes=5))
        .not_valid_after(current + timedelta(days=limits.SELF_SIGNED_DAYS))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode("ascii")
    return cert.public_bytes(Encoding.PEM).decode("ascii"), key_pem, sha256_hex(cert)
