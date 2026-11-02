"""Identifier-Normalisierung (NPT-07)."""

from __future__ import annotations

import pytest

from npt.identity import (
    ANY_ISSUER,
    IdentifierError,
    identity_from_cert,
    normalize_dn,
    normalize_identifier,
    parse_bootstrap_entry,
)
from tests.certs import CertFactory

CANON = "C=de,CN=test admin,O=npt test,OU=admins"


@pytest.mark.parametrize(
    "dn",
    [
        "CN=Test Admin,OU=Admins,O=NPT Test,C=DE",  # RFC 4514 (Blatt zuerst)
        "C=DE,O=NPT Test,OU=Admins,CN=Test Admin",  # umgekehrte Reihenfolge
        "/C=DE/O=NPT Test/OU=Admins/CN=Test Admin",  # OpenSSL-Slash-Format
        "cn = test  admin , ou=ADMINS,  o=npt   test,c=de",  # Whitespace + Case
        "commonName=Test Admin,organizationalUnitName=Admins,"
        "organizationName=NPT Test,countryName=DE",
        "2.5.4.3=Test Admin,2.5.4.11=Admins,2.5.4.10=NPT Test,2.5.4.6=DE",  # OIDs
        "OID.2.5.4.3=Test Admin;OU=Admins;O=NPT Test;C=DE",  # RFC 2253 ';'
        'CN="Test Admin",OU=Admins,O=NPT Test,C=DE',  # Anfuehrungszeichen
        "CN=\\54est Admin,OU=Admins,O=NPT Test,C=DE",  # Hex-Escape (T)
        "CN=Ｔｅｓｔ Admin,OU=Admins,O=NPT Test,C=DE",  # Vollbreite -> NFKC
        "  /C=DE/O=NPT Test/OU=Admins/CN=Test Admin/  ",
    ],
)
def test_dn_variants_normalize_identically(dn: str) -> None:
    assert normalize_identifier("subject_dn", dn) == CANON


def test_dn_from_certificate_equals_string_form(certs: CertFactory) -> None:
    issued = certs.issue()
    assert normalize_dn(issued.cert.subject) == CANON
    assert normalize_dn(issued.cert.subject.rfc4514_string()) == CANON


def test_escaped_comma_matches_slash_format() -> None:
    a = normalize_identifier("subject_dn", "CN=Muster\\, Max,O=Firma")
    b = normalize_identifier("subject_dn", "/O=Firma/CN=Muster, Max")
    assert a == b == "CN=muster\\, max,O=firma"


def test_multivalued_rdn_order_irrelevant() -> None:
    a = normalize_identifier("subject_dn", "CN=a+UID=b,O=x")
    b = normalize_identifier("subject_dn", "UID=b+CN=a,O=x")
    assert a == b


def test_distinct_dns_stay_distinct() -> None:
    a = normalize_identifier("subject_dn", "CN=Test Admin,O=NPT Test")
    b = normalize_identifier("subject_dn", "CN=Test Admin2,O=NPT Test")
    c = normalize_identifier("subject_dn", "CN=Test Admin,O=NPT Test,OU=x")
    assert len({a, b, c}) == 3


def test_value_with_plus_is_not_split_when_escaped() -> None:
    a = normalize_identifier("subject_dn", "CN=a\\+b,O=x")
    assert a == "CN=a\\+b,O=x"
    assert a != normalize_identifier("subject_dn", "CN=a+B=b,O=x")


@pytest.mark.parametrize("bad", ["", "   ", "CN", "CN=a,,O=b", 'CN="a,O=b', "C N=x", "/"])
def test_invalid_dn_rejected(bad: str) -> None:
    with pytest.raises(IdentifierError):
        normalize_identifier("subject_dn", bad)


def test_email_upn_fingerprint_normalization() -> None:
    assert normalize_identifier("san_email", "  Admin@NPT.Test ") == "admin@npt.test"
    assert normalize_identifier("san_email", "email:Admin@npt.test") == "admin@npt.test"
    assert normalize_identifier("san_upn", "Admin@CORP.Example") == "admin@corp.example"
    fp = "AB:" * 31 + "AB"
    assert normalize_identifier("fingerprint_sha256", fp) == "ab" * 32
    assert normalize_identifier("fingerprint_sha256", "sha256:" + "0F" * 32) == "0f" * 32
    for kind, bad in [
        ("san_email", "kein-at"),
        ("san_upn", "a b"),
        ("fingerprint_sha256", "abc"),
        ("fingerprint_sha256", "g" * 64),
    ]:
        with pytest.raises(IdentifierError):
            normalize_identifier(kind, bad)  # type: ignore[arg-type]


def test_identity_from_cert_all_kinds(certs: CertFactory) -> None:
    issued = certs.issue(emails=("B@npt.test", "a@NPT.test"), upns=("Admin@Corp.Example",))
    ident = identity_from_cert(issued.cert, "subject_dn")
    assert ident.candidates == (CANON,)
    assert ident.issuer_norm == "C=de,CN=npt test ca,O=npt test"
    assert identity_from_cert(issued.cert, "san_email").candidates == ("a@npt.test", "b@npt.test")
    assert identity_from_cert(issued.cert, "san_upn").candidates == ("admin@corp.example",)
    fp = identity_from_cert(issued.cert, "fingerprint_sha256")
    assert fp.candidates == (fp.fingerprint_sha256,)
    assert len(fp.fingerprint_sha256) == 64


def test_identity_without_san_has_no_candidates(certs: CertFactory) -> None:
    issued = certs.issue()
    assert identity_from_cert(issued.cert, "san_email").candidates == ()
    assert identity_from_cert(issued.cert, "san_email").primary == ""


def test_bootstrap_entry_with_and_without_issuer() -> None:
    assert parse_bootstrap_entry("subject_dn", "CN=Test Admin,OU=Admins,O=NPT Test,C=DE") == (
        ANY_ISSUER,
        CANON,
    )
    issuer, ident = parse_bootstrap_entry(
        "subject_dn", "/C=DE/O=NPT Test/CN=NPT Test CA::/C=DE/O=NPT Test/OU=Admins/CN=Test Admin"
    )
    assert issuer == "C=de,CN=npt test ca,O=npt test"
    assert ident == CANON
