"""News und FAQ je Projekt (NPT-13): Admin-CRUD, oeffentliche Seiten, Suche, XSS."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import func, select, update

from npt import content, limits
from npt.db.models import AdminAuditLog, ProjectFaq, ProjectNews
from tests.admin_helpers import AdminClient, bootstrap
from tests.certs import CertFactory
from tests.project_helpers import make_project

XSS = (
    '<p>Hallo <strong>fett</strong> <a href="https://example.test/x" onclick="alert(1)">Link</a>'
    "<script>alert(1)</script><img src=x onerror=alert(2)>"
    '<a href="javascript:alert(3)">js</a><span style="color:red">rot</span></p>'
)


@pytest.fixture
def admin(admin_app_factory, session_factory, certs: CertFactory) -> AdminClient:
    bootstrap(session_factory)
    return AdminClient(admin_app_factory(PUBLIC_BASE_URL="http://npt.test"), certs.issue())


def assert_clean(html: str) -> None:
    lowered = html.lower()
    for bad in ("<script", "onerror", "onclick", "javascript:", "<img", "style="):
        assert bad not in lowered, bad


# ---------------------------------------------------------------- Einheit
def test_sanitize_and_validation() -> None:
    data = content.validate_news("  Titel\n mit  Umbruch ", XSS)
    assert data.title == "Titel mit Umbruch"
    assert "<strong>fett</strong>" in data.body_html
    assert 'href="https://example.test/x"' in data.body_html
    assert 'rel="noopener noreferrer"' in data.body_html
    assert_clean(data.body_html)
    with pytest.raises(content.ContentError, match="news_titel_fehlt"):
        content.validate_news("   ", "<p>x</p>")
    with pytest.raises(content.ContentError, match="news_text_fehlt"):
        content.validate_news("T", "<script>nur skript</script><p> </p>")
    with pytest.raises(content.ContentError, match="news_titel_zu_lang"):
        content.validate_news("x" * 201, "<p>x</p>")
    faq = content.validate_faq("Frage?", "<p>Antwort mit <em>Betonung</em></p>", "")
    assert faq.answer_text == "Antwort mit Betonung"
    assert faq.sort_order == 0
    with pytest.raises(content.ContentError, match="faq_reihenfolge_ungueltig"):
        content.validate_faq("F", "<p>A</p>", "abc")
    with pytest.raises(content.ContentError, match="faq_reihenfolge_ungueltig"):
        content.validate_faq("F", "<p>A</p>", "100001")


def test_render_resanitizes_stored_html(session_factory) -> None:
    proj = make_project(session_factory)
    with session_factory() as s:
        s.add(ProjectNews(project_id=proj.id, title="T", body_html=XSS, created_by="db"))
        s.commit()
        item = content.list_news(s, proj.id)[0]
    assert_clean(content.render_html(item.body_html))


# ---------------------------------------------------------------- Admin-CRUD News
def test_news_crud_admin_and_public(
    admin: AdminClient, session_factory, public_app_factory
) -> None:
    proj = make_project(session_factory)
    base = f"/admin/projekte/{proj.id}/news"
    assert admin.get(base).status_code == 200
    page = admin.get(f"{base}/neu")
    assert page.status_code == 200
    assert "quill.min.js" in page.text
    assert "richtext.js" in page.text
    r = admin.post(base, {"title": "Erste Meldung", "body_html": XSS})
    assert r.status_code == 303
    assert "ok=news_gespeichert" in r.headers["location"]
    with session_factory() as s:
        item = s.scalars(select(ProjectNews)).one()
        assert "test admin" in item.created_by.lower()
        assert_clean(item.body_html)
        nid = item.id
        actions = s.scalars(select(AdminAuditLog.action)).all()
    assert "news_angelegt" in actions

    pub = public_app_factory()
    lst = pub.get(f"{proj.base}/news")
    assert lst.status_code == 200
    assert "Erste Meldung" in lst.text
    assert "<strong>fett</strong>" in lst.text
    assert_clean(lst.text.split('data-testid="news-list"')[1])
    assert lst.headers["cache-control"] == "no-store"
    assert "test admin" not in lst.text.lower()  # keine Admin-Identifier (PII) oeffentlich
    one = pub.get(f"{proj.base}/news/{nid}")
    assert one.status_code == 200
    assert "Erste Meldung" in one.text

    # bearbeiten
    assert admin.get(f"{base}/{nid}").status_code == 200
    r = admin.post(f"{base}/{nid}", {"title": "Geändert", "body_html": "<p>neu</p>"})
    assert r.status_code == 303
    assert "aktualisiert" in pub.get(f"{proj.base}/news/{nid}").text
    # ungueltig -> 422 mit Formular
    r = admin.post(f"{base}/{nid}", {"title": "", "body_html": "<p>x</p>"})
    assert r.status_code == 422
    assert "Bitte einen Titel angeben" in r.text
    # loeschen
    r = admin.post(f"{base}/{nid}/loeschen")
    assert r.status_code == 303
    assert "ok=news_geloescht" in r.headers["location"]
    assert pub.get(f"{proj.base}/news/{nid}").status_code == 404
    assert "noch keine News" in pub.get(f"{proj.base}/news").text


def test_news_requires_csrf_and_project_isolation(
    admin: AdminClient, session_factory, public_app_factory
) -> None:
    a = make_project(session_factory, "A")
    b = make_project(session_factory, "B")
    r = admin.c.post(
        f"/admin/projekte/{a.id}/news",
        headers=admin.headers,
        data={"title": "x", "body_html": "<p>x</p>", "csrf_token": "falsch"},
    )
    assert r.status_code in (400, 403)
    admin.post(f"/admin/projekte/{a.id}/news", {"title": "Nur A", "body_html": "<p>a</p>"})
    with session_factory() as s:
        nid = s.scalars(select(ProjectNews.id)).one()
    pub = public_app_factory()
    assert pub.get(f"{b.base}/news/{nid}").status_code == 404
    assert "Nur A" not in pub.get(f"{b.base}/news").text
    # Admin-Bearbeiten ueber fremdes Projekt
    r = admin.post(f"/admin/projekte/{b.id}/news/{nid}", {"title": "x", "body_html": "<p>x</p>"})
    assert r.status_code == 303
    assert "fehler=news_nicht_gefunden" in r.headers["location"]
    assert pub.get("/p/" + "x" * 43 + "/news").status_code == 404


def test_news_pagination(session_factory, public_app_factory) -> None:
    proj = make_project(session_factory)
    with session_factory() as s:
        for i in range(limits.NEWS_PAGE_SIZE + 3):
            data = content.validate_news(f"Beitrag {i:02d}", f"<p>{i}</p>")
            content.create_news(s, proj.id, data, "t")
        s.commit()
    pub = public_app_factory()
    first = pub.get(f"{proj.base}/news")
    assert first.text.count('class="news-item"') == limits.NEWS_PAGE_SIZE
    assert "Seite 1 von 2" in first.text
    second = pub.get(f"{proj.base}/news?seite=2")
    assert second.text.count('class="news-item"') == 3
    assert pub.get(f"{proj.base}/news?seite=999").status_code == 200
    assert pub.get(f"{proj.base}/news?seite=abc").status_code == 200


def test_news_limit_per_project(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(limits, "NEWS_MAX_PER_PROJECT", 2)
    proj = make_project(session_factory)
    with session_factory() as s:
        data = content.validate_news("T", "<p>x</p>")
        content.create_news(s, proj.id, data, "t")
        content.create_news(s, proj.id, data, "t")
        with pytest.raises(content.ContentError, match="news_zu_viele"):
            content.create_news(s, proj.id, data, "t")


# ---------------------------------------------------------------- FAQ
def test_faq_crud_and_search(admin: AdminClient, session_factory, public_app_factory) -> None:
    proj = make_project(session_factory)
    base = f"/admin/projekte/{proj.id}/faq"
    neu = admin.get(f"{base}/neu")
    assert neu.status_code == 200
    assert 'value="10"' in neu.text
    entries = [
        (
            "Wie trage ich die Version ein?",
            "<p>Spalte <strong>Installierte Version</strong>.</p>",
            "20",
        ),
        (
            "Was bedeutet 100%_sicher?",
            "<p>Gar nichts. Siehe <a href='https://x.test'>Doku</a></p>",
            "10",
        ),
        ("Wer ist zuständig?", XSS, "30"),
    ]
    for q, a, o in entries:
        r = admin.post(base, {"question": q, "answer_html": a, "sort_order": o})
        assert r.status_code == 303, r.text
    pub = public_app_factory()
    page = pub.get(f"{proj.base}/faq")
    assert page.status_code == 200
    order = re.findall(r'data-testid="faq-question-\d+">([^<]+)<', page.text)
    assert order[0].startswith("Was bedeutet")
    assert order[1].startswith("Wie trage")
    assert "faq.js" in page.text
    assert "3 Einträge" in page.text
    assert_clean(page.text.split('data-testid="faq-list"')[1])
    # Suche in Frage und Antwort-Plaintext, alle Woerter, Gross-/Kleinschreibung egal
    hit = pub.get(f"{proj.base}/faq", params={"q": "INSTALLIERTE version"})
    assert re.findall(r'data-testid="faq-question-\d+">([^<]+)<', hit.text) == [
        "Wie trage ich die Version ein?"
    ]
    assert "1 von 3 Einträgen" in hit.text
    # LIKE-Sonderzeichen werden escaped
    pct = pub.get(f"{proj.base}/faq", params={"q": "100%_"})
    assert len(re.findall(r'data-testid="faq-question-', pct.text)) == 1
    assert (
        len(
            re.findall(
                r'data-testid="faq-question-', pub.get(f"{proj.base}/faq", params={"q": "%"}).text
            )
        )
        == 1
    )
    none = pub.get(f"{proj.base}/faq", params={"q": "gibtsnicht"})
    assert "Keine Treffer" in none.text
    long = pub.get(f"{proj.base}/faq", params={"q": "x" * 5000})
    assert long.status_code == 200
    # Admin-Liste, Bearbeiten, Loeschen
    with session_factory() as s:
        ids = s.scalars(select(ProjectFaq.id).order_by(ProjectFaq.id)).all()
    r = admin.post(
        f"{base}/{ids[0]}", {"question": "Neu?", "answer_html": "<p>Ja</p>", "sort_order": "1"}
    )
    assert r.status_code == 303
    r = admin.post(f"{base}/{ids[0]}", {"question": "Neu?", "answer_html": "", "sort_order": "1"})
    assert r.status_code == 422
    assert "Bitte eine Antwort eingeben" in r.text
    assert admin.post(f"{base}/{ids[1]}/loeschen").status_code == 303
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(ProjectFaq)) == 2
        actions = set(s.scalars(select(AdminAuditLog.action)).all())
    assert {"faq_angelegt", "faq_geaendert", "faq_geloescht"} <= actions


def test_content_allowed_when_project_closed_and_deleted_with_project(
    admin: AdminClient, session_factory
) -> None:
    proj = make_project(session_factory)
    from npt.db.models import Project

    with session_factory() as s:
        s.execute(update(Project).where(Project.id == proj.id).values(status="archiviert"))
        s.commit()
    r = admin.post(f"/admin/projekte/{proj.id}/news", {"title": "T", "body_html": "<p>x</p>"})
    assert r.status_code == 303
    assert "ok=" in r.headers["location"]
    with session_factory() as s:
        content.create_faq(s, proj.id, content.validate_faq("F", "<p>A</p>", 1), "t")
        s.commit()
    r = admin.post(f"/admin/projekte/{proj.id}/loeschen", {"confirm_name": proj.name})
    assert r.status_code == 303
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(ProjectNews)) == 0
        assert s.scalar(select(func.count()).select_from(ProjectFaq)) == 0


def test_project_page_links_news_faq_mcp(admin: AdminClient, session_factory) -> None:
    proj = make_project(session_factory)
    page = admin.get(f"/admin/projekte/{proj.id}")
    assert f"/admin/projekte/{proj.id}/news" in page.text
    assert f"/admin/projekte/{proj.id}/faq" in page.text
    assert f"http://npt.test/p/{proj.token}/mcp" in page.text


def test_public_nav_has_news_and_faq(session_factory, public_app_factory) -> None:
    proj = make_project(session_factory)
    page = public_app_factory().get(proj.base)
    assert f"{proj.base}/news" in page.text
    assert f"{proj.base}/faq" in page.text


@pytest.mark.parametrize("raw", ["99999999999999999999", "abc", "3.5", "0", "-1", "١٢"])
def test_invalid_ids_are_not_found(
    raw: str, admin: AdminClient, session_factory, public_app_factory
) -> None:
    # QA 2aa2a2d5: ausserhalb bigint -> 500, nicht-numerisch -> 422-JSON; jetzt 404 bzw. Redirect
    proj = make_project(session_factory)
    pub = public_app_factory()
    r = pub.get(f"{proj.base}/news/{raw}")
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    for kind in ("news", "faq"):
        base = f"/admin/projekte/{proj.id}/{kind}"
        for resp in (
            admin.get(f"{base}/{raw}"),
            admin.post(f"{base}/{raw}", {"title": "T", "body_html": "<p>x</p>"}),
            admin.post(f"{base}/{raw}/loeschen"),
        ):
            assert resp.status_code == 303
            assert f"fehler={kind}_nicht_gefunden" in resp.headers["location"]
