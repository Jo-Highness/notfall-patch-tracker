"""Mailversand ueber die Admin-App (Auftrag 6.3): Editor, Vorschau, Testmail, Versand, Status."""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from npt.db.models import AdminAuditLog, MailCampaign, MailDelivery, MailRun
from tests.admin_helpers import AdminClient, bootstrap
from tests.certs import CertFactory
from tests.conftest import ADMIN_ORIGIN, GATEWAY_IP, make_settings, with_peer
from tests.mail_helpers import MAIL_SETTINGS, FakeTransport, temp_error
from tests.project_helpers import make_project, seed_master
from tests.routes import iter_routes

BODY = "<h2>Notfall</h2><p>Bitte <strong>prüfen</strong>:</p><p>[LINK]</p><script>x()</script>"


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def admin(session_factory, certs: CertFactory, transport) -> AdminClient:
    from npt.web.admin import create_admin_app

    bootstrap(session_factory)
    app = create_admin_app(
        make_settings(**MAIL_SETTINGS), session_factory=session_factory, enable_monitor=False
    )
    app.state.mail_transport_factory = transport.factory
    client = TestClient(with_peer(app, GATEWAY_IP), base_url=ADMIN_ORIGIN, follow_redirects=False)
    return AdminClient(client, certs.issue())


@pytest.fixture
def proj(session_factory) -> Any:
    seed_master(session_factory, 10)
    return make_project(session_factory)


def create(admin: AdminClient, pid: int, **form: str) -> int:
    data = {"subject": "Notfall CVE", "body_html": BODY, "selector": "betroffen_leer", **form}
    r = admin.post(f"/admin/projekte/{pid}/mail", data)
    assert r.status_code == 303, r.text
    m = re.search(r"/mail/(\d+)\?ok=mail_gespeichert$", r.headers["location"])
    assert m, r.headers["location"]
    return int(m.group(1))


def test_project_page_links_mail(admin: AdminClient, proj: Any) -> None:
    page = admin.get(f"/admin/projekte/{proj.id}").text
    assert f'href="/admin/projekte/{proj.id}/mail"' in page
    r = admin.get(f"/admin/projekte/{proj.id}/mail")
    assert r.status_code == 200
    assert "Noch keine Kampagnen." in r.text


def test_editor_page_loads_quill_locally(admin: AdminClient, proj: Any) -> None:
    r = admin.get(f"/admin/projekte/{proj.id}/mail/neu")
    assert r.status_code == 200
    html = r.text
    assert '<script src="/admin/static/vendor/quill/quill.min.js" defer></script>' in html
    assert '<script src="/admin/static/js/mail.js" defer></script>' in html
    assert "script-src 'self'" in r.headers["content-security-policy"]
    # kein Inline-Script, kein Inline-Handler, kein CDN
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"\son[a-z]+=", html)
    assert "cdn" not in html.lower()
    for sel in ("betroffen_leer", "ja_ohne_aktion", "benutzerdefiniert"):
        assert f'data-testid="mail-selector-{sel}"' in html
    js = admin.get("/admin/static/vendor/quill/quill.min.js")
    assert js.status_code == 200
    assert "Quill" in js.text[:2000] + js.text[-2000:]
    assert admin.get("/admin/static/vendor/quill/quill.snow.css").status_code == 200
    assert admin.get("/admin/static/js/mail.js").status_code == 200


def test_create_sanitizes_and_previews(admin: AdminClient, proj: Any, session_factory) -> None:
    cid = create(admin, proj.id)
    with session_factory() as s:
        c = s.get(MailCampaign, cid)
        assert c is not None
        assert "<script" not in c.body_html
        assert "x()" not in c.body_html
        assert "<strong>prüfen</strong>" in c.body_html
        assert s.scalars(select(AdminAuditLog.action)).all().count("mail_kampagne_angelegt") == 1
    page = admin.get(f"/admin/projekte/{proj.id}/mail/{cid}").text
    assert re.search(r'data-testid="mail-recipient-count">5<', page)
    assert re.search(r'data-testid="mail-system-count">10<', page)
    assert 'data-testid="mail-sample"' in page
    assert "owner0@firma.test" in page
    assert f"http://npt.test/p/{proj.token}/?x.system_id=APP-00005" in page
    assert "Alle meine Systeme" in page
    assert 'data-testid="mail-no-placeholder"' not in page
    # anderer Beispiel-Empfaenger
    page = admin.get(f"/admin/projekte/{proj.id}/mail/{cid}?empfaenger=owner3@firma.test").text
    sample = page.split('data-testid="mail-sample-body"', 1)[1].split("</div>", 1)[0]
    assert "APP-00003" in sample
    assert "APP-00008" in sample
    assert "APP-00005" not in sample


def test_validation_errors_keep_input(admin: AdminClient, proj: Any, session_factory) -> None:
    r = admin.post(
        f"/admin/projekte/{proj.id}/mail",
        {"subject": "", "body_html": "<p>Text bleibt</p>", "selector": "betroffen_leer"},
    )
    assert r.status_code == 422
    assert "Bitte einen Betreff angeben." in r.text
    assert "Text bleibt" in r.text
    r = admin.post(
        f"/admin/projekte/{proj.id}/mail",
        {"subject": "x", "body_html": "<p>t</p>", "selector": "unbekannt"},
    )
    assert r.status_code == 422
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(MailCampaign)) == 0


def test_custom_selector_and_skipped(admin: AdminClient, proj: Any, session_factory) -> None:
    from sqlalchemy import update

    from npt.db.models import ProjectSystem

    with session_factory() as s:
        s.execute(
            update(ProjectSystem)
            .where(ProjectSystem.system_id == "APP-00002")
            .values(app_owner="kaputt; owner9@firma.test", affected="ja")
        )
        s.commit()
    url = f"http://npt.test/p/{proj.token}/?f.affected=ja&sort=system_id"
    cid = create(admin, proj.id, selector="benutzerdefiniert", custom_query=url)
    with session_factory() as s:
        c = s.get(MailCampaign, cid)
        assert c is not None
        assert c.selector_params == {"query": [["f.affected", "ja"]]}
    page = admin.get(f"/admin/projekte/{proj.id}/mail/{cid}").text
    assert re.search(r'data-testid="mail-recipient-count">1<', page)
    assert re.search(r'data-testid="mail-system-count">1<', page)
    assert 'data-testid="mail-skipped"' in page
    assert "<code>kaputt</code>" in page
    assert f'href="http://npt.test/p/{proj.token}/?f.affected=ja"' in page
    # Vorbelegung aus der Hauptlisten-Sicht per Link
    r = admin.get(
        f"/admin/projekte/{proj.id}/mail/neu?selektor=benutzerdefiniert&filter=f.affected%3Dja"
    )
    assert 'value="f.affected=ja"' in r.text


def test_test_mail_goes_to_given_address_only(
    admin: AdminClient, proj: Any, session_factory, transport: FakeTransport
) -> None:
    cid = create(admin, proj.id)
    base = f"/admin/projekte/{proj.id}/mail/{cid}"
    r = admin.post(f"{base}/test", {"to": "Pruefer@Npt.Test", "sample": "owner2@firma.test"})
    assert r.headers["location"].endswith("ok=mail_test_gesendet")
    assert len(transport.sent) == 1
    to, msg = transport.sent[0]
    assert to == "pruefer@npt.test"
    assert msg["To"] == "pruefer@npt.test"
    assert msg["Subject"] == "[TEST] Notfall CVE"
    html = msg.get_body(("html",)).get_content()  # type: ignore[union-attr]
    assert "APP-00002" in html
    assert "APP-00007" in html
    r = admin.post(f"{base}/test", {"to": "kein mail"})
    assert "fehler=mail_test_adresse_ungueltig" in r.headers["location"]
    transport.script = lambda _to, _n: temp_error(421)
    r = admin.post(f"{base}/test", {"to": "a@npt.test"})
    assert "fehler=mail_test_fehlgeschlagen" in r.headers["location"]
    with session_factory() as s:
        actions = s.scalars(select(AdminAuditLog.action)).all()
        assert actions.count("mail_testmail") == 1
        assert actions.count("mail_testmail_fehlgeschlagen") == 1
        assert s.scalar(select(func.count()).select_from(MailRun)) == 0


def test_send_flow_confirmation_and_double_submit(
    admin: AdminClient, proj: Any, session_factory
) -> None:
    cid = create(admin, proj.id)
    base = f"/admin/projekte/{proj.id}/mail/{cid}"
    page = admin.get(base).text
    assert 'data-confirm="Mail jetzt an 5 Empfänger senden?"' in page
    form = {"mode": "alle", "expected_run": "0", "expected_count": "5"}
    r = admin.post(f"{base}/senden", form)  # ohne Bestaetigung
    assert "fehler=mail_unbestaetigt" in r.headers["location"]
    r = admin.post(f"{base}/senden", {**form, "expected_count": "7", "confirm": "ja"})
    assert "fehler=mail_empfaenger_geaendert" in r.headers["location"]
    r = admin.post(f"{base}/senden", {**form, "confirm": "ja"})
    assert r.headers["location"] == f"{base}?lauf=1&ok=mail_versand_gestartet"
    assert "Der Versand wurde gestartet" in admin.get(r.headers["location"]).text
    r = admin.post(f"{base}/senden", {**form, "confirm": "ja"})  # doppelt abgeschickt
    assert "fehler=mail_bereits_gestartet" in r.headers["location"]
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(MailDelivery)) == 5
    page = admin.get(base).text
    assert 'data-testid="mail-running"' in page
    assert "data-status-url=" in page
    assert 'data-testid="mail-send-form"' not in page
    status = admin.get(f"{base}/status.json").json()
    assert status["active"] is True
    assert status["total"] == 5
    assert status["counts"]["ausstehend"] == 5
    assert {d["email"] for d in status["deliveries"]} == {f"owner{i}@firma.test" for i in range(5)}
    r = admin.post(f"{base}/abbrechen", {})
    assert r.headers["location"].endswith("ok=mail_versand_abgebrochen")
    status = admin.get(f"{base}/status.json").json()
    assert status["active"] is False
    assert status["counts"]["fehlgeschlagen"] == 5
    page = admin.get(base).text
    assert "Erneut an alle senden" in page
    assert 'data-testid="mail-resend-failed"' in page
    r = admin.post(
        f"{base}/senden",
        {
            "mode": "nur_fehlgeschlagene",
            "expected_run": "1",
            "expected_count": "5",
            "confirm": "ja",
        },
    )
    assert "ok=mail_versand_gestartet" in r.headers["location"]
    with session_factory() as s:
        actions = s.scalars(select(AdminAuditLog.action)).all()
    assert actions.count("mail_versand_gestartet") == 2
    assert actions.count("mail_versand_abgebrochen") == 1


def test_send_requires_smtp_config(
    admin_app_factory, session_factory, certs: CertFactory, proj: Any
) -> None:
    bootstrap(session_factory)
    admin = AdminClient(admin_app_factory(SMTP_HOST=""), certs.issue())
    cid = create(admin, proj.id)
    page = admin.get(f"/admin/projekte/{proj.id}/mail/{cid}").text
    assert 'data-testid="mail-smtp-error"' in page
    assert 'data-testid="mail-send-form"' not in page
    r = admin.post(
        f"/admin/projekte/{proj.id}/mail/{cid}/senden",
        {"mode": "alle", "expected_run": "0", "expected_count": "5", "confirm": "ja"},
    )
    assert "fehler=smtp_nicht_konfiguriert" in r.headers["location"]


def test_update_delete_and_csrf(admin: AdminClient, proj: Any, session_factory) -> None:
    cid = create(admin, proj.id)
    base = f"/admin/projekte/{proj.id}/mail/{cid}"
    r = admin.post(
        base, {"subject": "Neu", "body_html": "<p>neu [LINK]</p>", "selector": "ja_ohne_aktion"}
    )
    assert r.headers["location"].endswith("ok=mail_gespeichert")
    with session_factory() as s:
        c = s.get(MailCampaign, cid)
        assert c is not None
        assert (c.subject, c.selector) == ("Neu", "ja_ohne_aktion")
    r = admin.c.post(f"{base}/loeschen", headers=admin.headers, data={"csrf_token": "falsch"})
    assert r.status_code == 403
    r = admin.post(f"{base}/loeschen", {})
    assert r.headers["location"].endswith("ok=mail_kampagne_geloescht")
    assert "fehler=mail_kampagne_nicht_gefunden" in admin.get(base).headers["location"]
    other = make_project(session_factory, "Anderes")
    cid2 = create(admin, other.id)
    assert (
        "fehler=mail_kampagne_nicht_gefunden"
        in admin.get(f"/admin/projekte/{proj.id}/mail/{cid2}").headers["location"]
    )


def test_public_app_has_no_mail_routes(session_factory) -> None:
    from npt.web.public import create_public_app

    app = create_public_app(make_settings(), session_factory=session_factory, enable_monitor=False)
    paths = [p for p, _ in iter_routes(app.routes)]
    assert "/p/{token}/api/systems" in paths
    assert not [p for p in paths if "mail" in p]
