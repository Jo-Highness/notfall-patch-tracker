"""Admin-Middleware: Vertrauensmodell gegenueber dem Gateway (NPT-06).

Diese Tests brauchen keine Datenbank: alle Ablehnungen passieren vor jedem DB-Zugriff.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from npt.web.admin import create_admin_app
from npt.web.admin_auth import AuthRejectedError, authenticate
from tests.certs import CertFactory
from tests.conftest import (
    ADMIN_ORIGIN,
    GATEWAY_IP,
    OTHER_IP,
    gateway_headers,
    make_settings,
    with_peer,
)


def _no_db() -> Any:
    raise AssertionError("abgelehnte Requests duerfen die DB nicht anfassen")


def client(peer: str) -> TestClient:
    app = create_admin_app(make_settings(), session_factory=_no_db, enable_monitor=False)
    return TestClient(with_peer(app, peer), base_url=ADMIN_ORIGIN, follow_redirects=False)


def _scope(peer: str, headers: dict[str, str] | list[tuple[str, str]]) -> dict[str, Any]:
    items = headers.items() if isinstance(headers, dict) else headers
    return {
        "type": "http",
        "client": (peer, 1234),
        "headers": [(k.lower().encode(), v.encode()) for k, v in items],
    }


def test_spoofed_headers_from_non_gateway_ip_rejected(certs: CertFactory) -> None:
    issued = certs.issue()
    r = client(OTHER_IP).get("/admin/", headers=gateway_headers(issued.pem))
    assert r.status_code == 403
    assert "Zugriff verweigert" in r.text


def test_spoofed_xff_does_not_help(certs: CertFactory) -> None:
    issued = certs.issue()
    headers = gateway_headers(issued.pem) | {"X-Forwarded-For": GATEWAY_IP, "X-Real-IP": GATEWAY_IP}
    assert client(OTHER_IP).get("/admin/", headers=headers).status_code == 403


@pytest.mark.parametrize(
    "verify", ["FAILED:unable to get issuer certificate", "NONE", "success", ""]
)
def test_verify_not_success_rejected(certs: CertFactory, verify: str) -> None:
    issued = certs.issue()
    r = client(GATEWAY_IP).get("/admin/", headers=gateway_headers(issued.pem, verify=verify))
    assert r.status_code == 403


def test_missing_headers_rejected() -> None:
    assert client(GATEWAY_IP).get("/admin/").status_code == 403
    assert (
        client(GATEWAY_IP).get("/admin/", headers={"X-SSL-Client-Verify": "SUCCESS"}).status_code
        == 403
    )


def test_fingerprint_mismatch_rejected(certs: CertFactory) -> None:
    real = certs.issue()
    other = certs.issue(cn="Jemand Anders")
    # Zertifikat des Admins, Fingerprint eines anderen Zertifikats
    headers = gateway_headers(
        real.pem, fingerprint=gateway_headers(other.pem)["X-SSL-Client-Fingerprint"]
    )
    assert client(GATEWAY_IP).get("/admin/", headers=headers).status_code == 403


def test_garbage_certificate_rejected() -> None:
    headers = {
        "X-SSL-Client-Verify": "SUCCESS",
        "X-SSL-Client-Cert": "-----BEGIN%20CERTIFICATE-----%0Aabc%0A-----END%20CERTIFICATE-----",
        "X-SSL-Client-Fingerprint": "00" * 20,
    }
    assert client(GATEWAY_IP).get("/admin/", headers=headers).status_code == 403


def test_duplicate_trust_header_rejected(certs: CertFactory) -> None:
    issued = certs.issue()
    headers = [*list(gateway_headers(issued.pem).items()), ("X-SSL-Client-Verify", "SUCCESS")]
    with pytest.raises(AuthRejectedError, match="mehrfach"):
        authenticate(_scope(GATEWAY_IP, headers), make_settings())


def test_authenticate_accepts_valid_gateway_request(certs: CertFactory) -> None:
    issued = certs.issue()
    ident = authenticate(_scope(GATEWAY_IP, gateway_headers(issued.pem)), make_settings())
    assert ident.primary == "C=de,CN=test admin,O=npt test,OU=admins"


def test_fingerprint_with_colons_and_uppercase_accepted(certs: CertFactory) -> None:
    issued = certs.issue()
    fp = gateway_headers(issued.pem)["X-SSL-Client-Fingerprint"].upper()
    fp = ":".join(fp[i : i + 2] for i in range(0, len(fp), 2))
    headers = gateway_headers(issued.pem, fingerprint=fp)
    authenticate(_scope(GATEWAY_IP, headers), make_settings())


def test_security_headers_on_rejection() -> None:
    r = client(OTHER_IP).get("/admin/")
    assert r.status_code == 403
    assert "unsafe-inline" not in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
