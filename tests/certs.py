"""Erzeugt Test-Zertifikate in-memory (cryptography), unabhaengig von gen-test-pki.sh."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from npt.identity import UPN_OID


@dataclass
class IssuedCert:
    cert: x509.Certificate
    pem: str


def _name(parts: list[tuple[x509.ObjectIdentifier, str]]) -> x509.Name:
    return x509.Name([x509.NameAttribute(oid, value) for oid, value in parts])


def _der_utf8(value: str) -> bytes:
    raw = value.encode()
    assert len(raw) < 128
    return bytes([0x0C, len(raw)]) + raw


class CertFactory:
    def __init__(self, ca_cn: str = "NPT Test CA") -> None:
        self.ca_key = ec.generate_private_key(ec.SECP256R1())
        self.ca_name = _name(
            [
                (NameOID.COUNTRY_NAME, "DE"),
                (NameOID.ORGANIZATION_NAME, "NPT Test"),
                (NameOID.COMMON_NAME, ca_cn),
            ]
        )
        now = dt.datetime.now(dt.UTC)
        self.ca_cert = (
            x509.CertificateBuilder()
            .subject_name(self.ca_name)
            .issuer_name(self.ca_name)
            .public_key(self.ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(self.ca_key, hashes.SHA256())
        )

    def issue(
        self,
        cn: str = "Test Admin",
        ou: str = "Admins",
        emails: tuple[str, ...] = (),
        upns: tuple[str, ...] = (),
    ) -> IssuedCert:
        key = ec.generate_private_key(ec.SECP256R1())
        now = dt.datetime.now(dt.UTC)
        builder = (
            x509.CertificateBuilder()
            .subject_name(
                _name(
                    [
                        (NameOID.COUNTRY_NAME, "DE"),
                        (NameOID.ORGANIZATION_NAME, "NPT Test"),
                        (NameOID.ORGANIZATIONAL_UNIT_NAME, ou),
                        (NameOID.COMMON_NAME, cn),
                    ]
                )
            )
            .issuer_name(self.ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30))
        )
        sans: list[x509.GeneralName] = [x509.RFC822Name(e) for e in emails]
        sans += [x509.OtherName(UPN_OID, _der_utf8(u)) for u in upns]
        if sans:
            builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
        cert = builder.sign(self.ca_key, hashes.SHA256())
        pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        return IssuedCert(cert, pem)
