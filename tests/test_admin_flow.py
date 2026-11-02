"""Admin-Verwaltung end-to-end gegen die App (mit Wegwerf-Postgres).

Abgedeckt: Bootstrap, Anfrage-Seite fuer Nicht-Admins, Anfrage bestaetigen/
ablehnen, Hinzufuegen per Identifier, Deaktivieren/Entfernen, letzter-Admin-
Schutz, CSRF (Token, Scope, Origin), Audit-Log.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from npt import admins
from npt.db.models import AdminAccessRequest, AdminAuditLog, AdminUser
from tests.certs import CertFactory, IssuedCert
from tests.conftest import gateway_headers

ADMIN_DN = "CN=Test Admin,OU=Admins,O=NPT Test,C=DE"

ClientFactory = Callable[..., TestClient]


def csrf_of(html: str) -> str:
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    assert m, "csrf-Meta-Tag fehlt"
    return m.group(1)


def bootstrap(sf: sessionmaker[Session], *entries: str) -> None:
    with sf() as s:
        admins.bootstrap_admins(s, "subject_dn", list(entries))
        s.commit()


def get(c: TestClient, path: str, who: IssuedCert):
    return c.get(path, headers=gateway_headers(who.pem))


def post(
    c: TestClient,
    path: str,
    who: IssuedCert,
    data: dict[str, str] | None = None,
    token: str | None = None,
    origin: str | None = None,
):
    headers = gateway_headers(who.pem)
    if origin is not None:
        headers["Origin"] = origin
    form = dict(data or {})
    if token is not None:
        form["csrf_token"] = token
    return c.post(path, headers=headers, data=form)


def token_for(c: TestClient, who: IssuedCert, path: str = "/admin/benutzer") -> str:
    return csrf_of(get(c, path, who).text)


@pytest.fixture
def admin_cert(certs: CertFactory) -> IssuedCert:
    return certs.issue()


@pytest.fixture
def other_cert(certs: CertFactory) -> IssuedCert:
    return certs.issue(cn="Test Nichtadmin", ou="Fachbereich")


def test_bootstrap_is_idempotent(session_factory: sessionmaker[Session]) -> None:
    bootstrap(session_factory, ADMIN_DN, "/C=DE/O=NPT Test/OU=Admins/CN=Test Admin")
    bootstrap(session_factory, ADMIN_DN)
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(AdminUser)) == 1
        assert s.scalar(select(func.count()).select_from(AdminAuditLog)) == 1


def test_admin_gets_dashboard(
    admin_app_factory: ClientFactory, session_factory, admin_cert
) -> None:
    bootstrap(session_factory, ADMIN_DN)
    r = get(admin_app_factory(), "/admin/", admin_cert)
    assert r.status_code == 200
    assert "Admin-Bereich" in r.text
    assert r.headers["referrer-policy"] == "same-origin"


def test_non_admin_sees_request_page(admin_app_factory, session_factory, other_cert) -> None:
    bootstrap(session_factory, ADMIN_DN)
    r = get(admin_app_factory(), "/admin/benutzer", other_cert)
    assert r.status_code == 403
    assert "Freischaltung anfragen" in r.text
    assert "C=de,CN=test nichtadmin,O=npt test,OU=fachbereich" in r.text


def test_request_and_approve_flow(
    admin_app_factory, session_factory, admin_cert, other_cert
) -> None:
    bootstrap(session_factory, ADMIN_DN)
    c = admin_app_factory()
    tok = csrf_of(get(c, "/admin/zugang", other_cert).text)
    r = post(c, "/admin/zugang/anfrage", other_cert, token=tok)
    assert r.status_code == 303
    assert "ok=angefragt" in r.headers["location"]
    # zweite Anfrage erzeugt keinen Duplikat-Eintrag
    r = post(c, "/admin/zugang/anfrage", other_cert, token=tok)
    assert "bereits_angefragt" in r.headers["location"]
    page = get(c, "/admin/zugang", other_cert)
    assert "ist offen" in page.text

    users = get(c, "/admin/benutzer", admin_cert)
    assert "test nichtadmin" in users.text
    with session_factory() as s:
        req = s.scalars(select(AdminAccessRequest)).one()
    r = post(c, f"/admin/anfragen/{req.id}/bestaetigen", admin_cert, token=csrf_of(users.text))
    assert r.status_code == 303
    assert "ok=bestaetigt" in r.headers["location"]
    assert get(c, "/admin/", other_cert).status_code == 200
    with session_factory() as s:
        actions = set(s.scalars(select(AdminAuditLog.action)))
    assert {"zugang_angefragt", "anfrage_bestaetigt"} <= actions


def test_reject_request(admin_app_factory, session_factory, admin_cert, other_cert) -> None:
    bootstrap(session_factory, ADMIN_DN)
    c = admin_app_factory()
    post(c, "/admin/zugang/anfrage", other_cert, token=token_for(c, other_cert, "/admin/zugang"))
    with session_factory() as s:
        req = s.scalars(select(AdminAccessRequest)).one()
    r = post(c, f"/admin/anfragen/{req.id}/ablehnen", admin_cert, token=token_for(c, admin_cert))
    assert "ok=abgelehnt" in r.headers["location"]
    assert get(c, "/admin/", other_cert).status_code == 403
    # erneutes Entscheiden einer erledigten Anfrage
    r = post(c, f"/admin/anfragen/{req.id}/bestaetigen", admin_cert, token=token_for(c, admin_cert))
    assert "fehler=anfrage_nicht_offen" in r.headers["location"]


def test_add_admin_by_identifier_any_format(
    admin_app_factory, session_factory, admin_cert, other_cert
) -> None:
    bootstrap(session_factory, ADMIN_DN)
    c = admin_app_factory()
    tok = token_for(c, admin_cert)
    r = post(
        c,
        "/admin/benutzer/neu",
        admin_cert,
        {
            "identifier": "/c=de/o=NPT  Test/ou=FACHBEREICH/cn=test nichtadmin",
            "display_name": "Neu",
        },
        token=tok,
    )
    assert "ok=hinzugefuegt" in r.headers["location"]
    assert get(c, "/admin/", other_cert).status_code == 200
    r = post(
        c,
        "/admin/benutzer/neu",
        admin_cert,
        {"identifier": "CN=Test Nichtadmin,OU=Fachbereich,O=NPT Test,C=DE"},
        token=tok,
    )
    assert "fehler=existiert_bereits" in r.headers["location"]
    r = post(c, "/admin/benutzer/neu", admin_cert, {"identifier": "kein dn"}, token=tok)
    assert "fehler=identifier_ungueltig" in r.headers["location"]


def test_issuer_restriction(admin_app_factory, session_factory, admin_cert) -> None:
    foreign_issuer = "CN=Andere CA,O=Woanders"
    bootstrap(session_factory, f"{foreign_issuer}::{ADMIN_DN}")
    assert get(admin_app_factory(), "/admin/", admin_cert).status_code == 403
    bootstrap(session_factory, f"CN=NPT Test CA,O=NPT Test,C=DE::{ADMIN_DN}")
    assert get(admin_app_factory(), "/admin/", admin_cert).status_code == 200


def _admin_id(sf: sessionmaker[Session], fragment: str) -> int:
    with sf() as s:
        return s.scalars(
            select(AdminUser.id).where(AdminUser.identifier_norm.contains(fragment))
        ).one()


def test_last_admin_cannot_remove_or_deactivate_self(
    admin_app_factory, session_factory, admin_cert
) -> None:
    bootstrap(session_factory, ADMIN_DN)
    c = admin_app_factory()
    me = _admin_id(session_factory, "test admin")
    tok = token_for(c, admin_cert)
    for action in ("deaktivieren", "entfernen"):
        r = post(c, f"/admin/benutzer/{me}/{action}", admin_cert, token=tok)
        assert "fehler=letzter_admin" in r.headers["location"], action
    assert get(c, "/admin/", admin_cert).status_code == 200


def test_deactivate_and_remove_with_second_admin(
    admin_app_factory, session_factory, admin_cert, other_cert
) -> None:
    bootstrap(session_factory, ADMIN_DN, "CN=Test Nichtadmin,OU=Fachbereich,O=NPT Test,C=DE")
    c = admin_app_factory()
    other = _admin_id(session_factory, "nichtadmin")
    me = _admin_id(session_factory, "test admin")
    tok = token_for(c, admin_cert)
    r = post(c, f"/admin/benutzer/{other}/deaktivieren", admin_cert, token=tok)
    assert "ok=deaktiviert" in r.headers["location"]
    assert get(c, "/admin/", other_cert).status_code == 403
    # jetzt ist "me" der letzte aktive Admin
    r = post(c, f"/admin/benutzer/{me}/entfernen", admin_cert, token=tok)
    assert "fehler=letzter_admin" in r.headers["location"]
    r = post(c, f"/admin/benutzer/{other}/aktivieren", admin_cert, token=tok)
    assert "ok=aktiviert" in r.headers["location"]
    r = post(c, f"/admin/benutzer/{me}/entfernen", admin_cert, token=tok)
    assert "ok=entfernt" in r.headers["location"]
    assert get(c, "/admin/", admin_cert).status_code == 403
    with session_factory() as s:
        actions = list(s.scalars(select(AdminAuditLog.action).order_by(AdminAuditLog.id)))
    assert actions[-3:] == ["admin_deaktiviert", "admin_aktiviert", "admin_entfernt"]


def test_last_admin_guard_service_level(session_factory) -> None:
    bootstrap(session_factory, ADMIN_DN)
    actor = admins.Actor("x", "y")
    with session_factory() as s:
        aid = s.scalars(select(AdminUser.id)).one()
        with pytest.raises(admins.AdminError, match="letzter_admin"):
            admins.remove_admin(s, actor, "subject_dn", aid)
        s.rollback()
        # Admin einer anderen (nicht wirksamen) Art zaehlt nicht als Ersatz
        admins.add_admin(s, actor, "san_email", "a@npt.test")
        s.commit()
        with pytest.raises(admins.AdminError, match="letzter_admin"):
            admins.set_admin_active(s, actor, "subject_dn", aid, False)


# ---------------------------------------------------------------- CSRF ueber die App
def test_post_without_csrf_token_rejected(admin_app_factory, session_factory, admin_cert) -> None:
    bootstrap(session_factory, ADMIN_DN)
    c = admin_app_factory()
    r = post(c, "/admin/benutzer/neu", admin_cert, {"identifier": "CN=x"})
    assert r.status_code == 403
    assert "CSRF" in r.text
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(AdminUser)) == 1


def test_post_with_wrong_origin_rejected(admin_app_factory, session_factory, admin_cert) -> None:
    bootstrap(session_factory, ADMIN_DN)
    c = admin_app_factory()
    tok = token_for(c, admin_cert)
    for origin in ("https://evil.example", "null", ""):
        r = post(
            c, "/admin/benutzer/neu", admin_cert, {"identifier": "CN=x"}, token=tok, origin=origin
        )
        assert r.status_code == 403, origin


def test_csrf_token_bound_to_principal(
    admin_app_factory, session_factory, admin_cert, other_cert
) -> None:
    bootstrap(session_factory, ADMIN_DN)
    c = admin_app_factory()
    foreign_token = token_for(c, other_cert, "/admin/zugang")
    r = post(c, "/admin/benutzer/neu", admin_cert, {"identifier": "CN=x"}, token=foreign_token)
    assert r.status_code == 403


def test_csrf_header_variant_accepted(admin_app_factory, session_factory, admin_cert) -> None:
    bootstrap(session_factory, ADMIN_DN)
    c = admin_app_factory()
    headers = gateway_headers(admin_cert.pem) | {"X-CSRF-Token": token_for(c, admin_cert)}
    r = c.post("/admin/benutzer/neu", headers=headers, data={"identifier": "CN=Neu,O=X"})
    assert "ok=hinzugefuegt" in r.headers["location"]


def test_all_unsafe_admin_routes_require_csrf(
    admin_app_factory, session_factory, admin_cert
) -> None:
    """Jede zustandsaendernde Admin-Route lehnt Requests ohne Token ab."""
    from npt.web.admin import create_admin_app
    from tests.conftest import make_settings
    from tests.routes import iter_routes

    bootstrap(session_factory, ADMIN_DN)
    app = create_admin_app(make_settings(), session_factory=session_factory, enable_monitor=False)
    unsafe = [p for p, m in iter_routes(app.routes) if m & {"POST", "PUT", "PATCH", "DELETE"}]
    assert "/admin/benutzer/neu" in unsafe
    assert len(unsafe) >= 7
    c = admin_app_factory()
    for path in unsafe:
        concrete = path.replace("{admin_id}", "1").replace("{request_id}", "1")
        r = post(c, concrete, admin_cert, {"identifier": "CN=x"})
        assert r.status_code == 403, path
        assert "CSRF" in r.text, path


def test_audit_page_lists_actions(admin_app_factory, session_factory, admin_cert) -> None:
    bootstrap(session_factory, ADMIN_DN)
    r = get(admin_app_factory(), "/admin/audit", admin_cert)
    assert r.status_code == 200
    assert "admin_bootstrap" in r.text


def test_html_output_escapes_identifiers(
    admin_app_factory, session_factory, admin_cert, certs
) -> None:
    evil = certs.issue(cn="<script>alert(1)</script>")
    bootstrap(session_factory, ADMIN_DN)
    r = get(admin_app_factory(), "/admin/", evil)
    assert r.status_code == 403
    assert "<script>alert" not in r.text
    assert "&lt;script" in r.text
