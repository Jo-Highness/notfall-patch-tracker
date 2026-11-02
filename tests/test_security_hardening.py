"""Security-Haertung (Phase 9, Auftrag 8.2): Header auch auf Fehler-/500-Antworten,
Body-Grenze, Token nie in Logs, Formel-Schutz in allen Exporten, Handbuch-Rendering.
"""

from __future__ import annotations

import io
import json
import logging
import secrets
from collections.abc import Iterator
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from npt import changelog, export, handbook, projects
from npt.logging_setup import JsonFormatter, mask_secrets
from tests.conftest import gateway_headers, with_peer
from tests.project_helpers import Editor, make_project, new_system_body, seed_master

REQUIRED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "x-robots-tag": "noindex, nofollow",
}


def assert_security_headers(r, referrer: str = "no-referrer") -> None:
    for key, value in REQUIRED_HEADERS.items():
        assert r.headers.get(key) == value, (key, r.status_code, r.request.url)
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp
    assert r.headers["referrer-policy"] == referrer


# ---------------------------------------------------------------- Header ueberall
def test_public_headers_on_all_kinds_of_responses(session_factory, public_app_factory) -> None:
    seed_master(session_factory, 2)
    proj = make_project(session_factory)
    c = public_app_factory()
    ed = Editor(c, proj)
    responses = [
        c.get(proj.base),  # Hauptliste
        c.get(f"{proj.base}/uebersicht"),
        c.get(f"{proj.base}/hilfe"),
        c.get(f"{proj.base}/api/systems"),
        c.get("/static/js/hauptliste.js"),  # Static
        c.get("/static/gibt-es-nicht.js"),  # 404 Static
        c.get("/robots.txt"),
        c.get("/p/" + "x" * 43),  # unbekannter Token -> 404-Seite
        c.get("/admin/"),  # 404 (keine Admin-Routen)
        c.post(f"{proj.base}/api/systems", content=b"x", headers=ed.headers()),  # 422
        c.patch(f"{proj.base}/api/systems/APP-00001", content=b"{}"),  # 415
        c.delete(proj.base),  # 405
    ]
    for r in responses:
        assert_security_headers(r)


def test_public_500_has_security_headers(session_factory) -> None:
    from npt.web.public import create_public_app
    from tests.conftest import make_settings

    app = create_public_app(make_settings(), session_factory=session_factory, enable_monitor=False)

    @app.get("/kaputt")
    async def boom() -> None:
        raise RuntimeError("absichtlich")

    c = TestClient(with_peer(app, "10.99.0.77"), raise_server_exceptions=False)
    r = c.get("/kaputt")
    assert r.status_code == 500
    assert_security_headers(r)


def test_admin_404_and_500_have_security_headers(session_factory, certs) -> None:
    from npt.web.admin import create_admin_app
    from tests.conftest import ADMIN_ORIGIN, GATEWAY_IP, make_settings

    app = create_admin_app(make_settings(), session_factory=session_factory, enable_monitor=False)

    @app.get("/admin/kaputt")
    async def boom() -> None:
        raise RuntimeError("absichtlich")

    c = TestClient(with_peer(app, GATEWAY_IP), base_url=ADMIN_ORIGIN, raise_server_exceptions=False)
    hdrs = gateway_headers(certs.issue().pem)
    for r in (
        c.get("/admin/kaputt", headers=hdrs),
        c.get("/admin/gibt-es-nicht", headers=hdrs),
        c.get("/anderswo", headers=hdrs),
        c.get("/admin/"),  # ohne Zertifikats-Header -> abgelehnt
        c.get("/admin/static/css/app.css", headers=hdrs),
    ):
        assert_security_headers(r, referrer="same-origin")
        assert r.headers["cache-control"] == "no-store"


def test_robots_and_noindex(session_factory, public_app_factory) -> None:
    seed_master(session_factory, 1)
    proj = make_project(session_factory)
    c = public_app_factory()
    assert c.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"
    for path in (proj.base, f"{proj.base}/uebersicht", f"{proj.base}/hilfe"):
        assert '<meta name="robots" content="noindex, nofollow">' in c.get(path).text


# ---------------------------------------------------------------- Body-Grenze
def test_body_limit_also_for_chunked_requests(session_factory, public_app_factory) -> None:
    seed_master(session_factory, 1)
    proj = make_project(session_factory)
    c = public_app_factory()
    ed = Editor(c, proj)

    def chunks() -> Iterator[bytes]:
        for _ in range(40):
            yield b"x" * 8192  # 320 KB ohne Content-Length (chunked)

    r = c.post(f"{proj.base}/api/systems", content=chunks(), headers=ed.headers())
    assert r.status_code == 413
    assert_security_headers(r)
    big = b"{" + b" " * (300 * 1024) + b"}"
    r = c.post(f"{proj.base}/api/systems", content=big, headers=ed.headers())
    assert r.status_code == 413


# ---------------------------------------------------------------- Token nie in Logs
def test_project_token_never_in_access_or_error_logs(
    session_factory, public_app_factory, caplog
) -> None:
    seed_master(session_factory, 2)
    proj = make_project(session_factory)
    c = public_app_factory()
    caplog.set_level(logging.DEBUG)
    ed = Editor(c, proj)
    ed.patch("APP-00001", "remark", "x")
    ed.patch("APP-00001", "remark", "y", "falsch")  # 409
    c.get(f"{proj.base}/api/systems?updated_since=kaputt")  # 422
    c.get(f"{proj.base}/export.xlsx?umfang=alle&q={proj.token}")
    c.get(f"{proj.base}/hilfe")
    c.post(f"{proj.base}/api/systems", json=new_system_body(), headers={"X-CSRF-Token": "x"})
    # Fremde Logzeile mit freistehendem Token (z. B. Ausnahmetext ohne /p/-Praefix)
    logging.getLogger("uvicorn.error").error("Fehler fuer %s", proj.token)
    fmt = JsonFormatter()
    out = "\n".join(fmt.format(rec) for rec in caplog.records)
    assert caplog.records, "keine Logzeilen erfasst"
    assert '"path": "/p/***' in out
    assert proj.token not in out
    # Das Access-Log maskiert bereits beim Schreiben (nicht erst im Formatter).
    paths = [getattr(r, "path", "") for r in caplog.records if r.name == "npt.access"]
    assert paths
    assert all(proj.token not in p for p in paths)


def test_mask_secrets_variants() -> None:
    token = secrets.token_urlsafe(32)
    for text_ in (
        f"/p/{token}/api/events?since=3",
        f"Referer https://npt.example/p/{token}/",
        f"ProjectNotFound({token!r})",
        f"token={token}",
        f"/p/{token.replace('-', '%2D')}",
    ):
        assert token not in mask_secrets(text_), text_
    assert mask_secrets("sha256 " + "a" * 64) == "sha256 " + "a" * 64  # Hashes bleiben


def test_sql_errors_hide_bound_parameters(db_engine) -> None:
    token = secrets.token_urlsafe(32)
    with pytest.raises(DBAPIError) as info, db_engine.connect() as conn:
        conn.execute(text("SELECT CAST(:t AS text), 1/0"), {"t": token})
    assert token not in str(info.value)
    assert "hidden due to hide_parameters" in str(info.value)
    # Echo des Werts durch Postgres selbst (z. B. ungueltige Eingabe) faengt der Formatter
    with pytest.raises(DBAPIError) as info2, db_engine.connect() as conn:
        conn.execute(text("SELECT CAST(:t AS integer)"), {"t": token})
    assert token in str(info2.value)
    assert token not in mask_secrets(str(info2.value))


# ---------------------------------------------------------------- Formel-Injection
FORMULAS = ["=1+1", "+SUM(A1)", "-2+3", "@cmd", '=HYPERLINK("http://x","y")']


def _formula_cells(data: bytes) -> list[tuple[object, bool, str]]:
    ws = load_workbook(io.BytesIO(data)).active
    assert ws is not None
    return [
        (c.value, bool(c.quotePrefix), c.data_type)
        for row in ws.iter_rows()
        for c in row
        if isinstance(c.value, str) and c.value[:1] in "=+-@"
    ]


def _assert_all_protected(data: bytes, expected: int) -> None:
    cells = _formula_cells(data)
    assert len(cells) >= expected, cells
    for value, quoted, dtype in cells:
        assert dtype == "s", value  # Text, keine Formel
        assert quoted, value


def test_formula_injection_all_exports(session_factory, public_app_factory) -> None:
    seed_master(session_factory, 2)
    proj = make_project(session_factory)
    c = public_app_factory()
    ed = Editor(c, proj)
    cur = None
    for v in FORMULAS:  # Bemerkung mehrfach -> mehrere Protokolleintraege
        assert ed.patch("APP-00001", "remark", v, cur).status_code == 200
        cur = v
    assert ed.patch("APP-00002", "installed_version", "=2+2").status_code == 200
    assert ed.create(**new_system_body(system_name="=EVIL()", remark="@x")).status_code == 201

    # 1) Export der Hauptliste (Sicht und alle)
    for umfang in ("alle", "sicht"):
        r = c.get(f"{proj.base}/export.xlsx?umfang={umfang}")
        assert r.status_code == 200
        _assert_all_protected(r.content, 4)
    with session_factory() as s:
        # 2) manuell angelegte Systeme
        _assert_all_protected(export.systems_workbook(projects.manual_systems(s, proj.id)), 2)
        # 3) Aenderungsprotokoll
        tz = ZoneInfo("Europe/Berlin")
        entries = changelog.export_entries(s, proj.id, changelog.LogFilter(), tz)
        _assert_all_protected(changelog.workbook(entries, tz), len(FORMULAS))
    # 4) Backup-xlsx: tests/test_backups.py::test_backup_files_* (gleicher Schreibpfad)


# ---------------------------------------------------------------- Handbuch
def test_handbook_rendering_is_sanitized(tmp_path, monkeypatch) -> None:
    (tmp_path / "bearbeiter.md").write_text(
        "# Hilfe {{projekt}}\n\n"
        "<script>alert(1)</script>\n\n"
        "[boese](javascript:alert(1)) [gut](uebersicht) [extern](https://example.org)\n\n"
        '<img src=x onerror="alert(1)">\n\n'
        "## Abschnitt\n\n| a | b |\n|---|--:|\n| 1 | 2 |\n\n{{unbekannt}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(handbook, "_CANDIDATES", (tmp_path,))
    handbook._rendered.cache_clear()
    try:
        title, body = handbook.render("bearbeiter", {"projekt": '<b onclick="x">CVE</b>'})
    finally:
        handbook._rendered.cache_clear()
    # Titel ist Klartext; escaped wird er vom Template (Autoescape), siehe Hilfe-Seiten-Test.
    assert title == 'Hilfe <b onclick="x">CVE</b>'
    assert "<script" not in body
    assert "&lt;script&gt;" in body  # rohes HTML erscheint als Text
    assert "<img" not in body
    assert 'href="javascript' not in body  # markdown-it rendert solche Links nur als Text
    assert 'href="uebersicht"' in body
    assert 'href="https://example.org" rel="noopener noreferrer"' in body
    assert '<h2 id="abschnitt">' in body
    assert "<table>" in body
    assert "style=" not in body
    assert "{{unbekannt}}" in body


def test_help_pages_render_real_handbooks(
    session_factory, public_app_factory, admin_app_factory, certs
) -> None:
    seed_master(session_factory, 1)
    proj = make_project(session_factory, name="CVE <x>")
    c = public_app_factory()
    r = c.get(f"{proj.base}/hilfe")
    assert r.status_code == 200
    assert 'data-testid="handbook"' in r.text
    assert "{{" not in r.text  # alle Platzhalter ersetzt
    assert "CVE &lt;x&gt;" in r.text
    # Projektname genau einmal im Titel (QA ea7c23a1)
    title = r.text.split("<title>", 1)[1].split("</title>", 1)[0]
    assert title == "Hilfe für Bearbeiter – CVE &lt;x&gt; – Notfall-Patch-Tracker", title
    assert 'data-testid="nav-hilfe"' in r.text
    for heading in ("Neues System", "Konflikt", "Übersicht", "Export", "FAQ"):
        assert heading in r.text, heading
    # Admin-Handbuch existiert auf dem Bearbeiter-Port nicht
    assert c.get("/admin/hilfe").status_code == 404

    from tests.admin_helpers import ADMIN_DN, bootstrap

    bootstrap(session_factory, ADMIN_DN)
    a = admin_app_factory()
    hdrs = gateway_headers(certs.issue().pem)
    r = a.get("/admin/hilfe", headers=hdrs)
    assert r.status_code == 200, r.text
    assert "{{" not in r.text
    for heading in ("Notfall tritt ein", "Stammdaten", "Backup", "[LINK]", "Admins", "abschließen"):
        assert heading in r.text, heading
    # Nicht-Admin (gueltiges Zertifikat, kein Admin-Recht) -> Anfrage-Seite, kein Handbuch
    other = gateway_headers(certs.issue(cn="Niemand").pem)
    r = a.get("/admin/hilfe", headers=other)
    assert r.status_code == 403
    assert 'data-testid="handbook"' not in r.text


def test_main_list_escapes_project_name_and_config(session_factory, public_app_factory) -> None:
    seed_master(session_factory, 1)
    payload = "</script><script>alert(1)</script><img src=x onerror=alert(2)>"
    proj = make_project(session_factory, name=payload)
    r = public_app_factory().get(proj.base)
    assert r.status_code == 200
    assert payload not in r.text
    assert "<img src=x" not in r.text
    assert r.text.count("<script") == r.text.count("</script>")
    cfg = r.text.split('id="npt-config">', 1)[1].split("</script>", 1)[0]
    data = json.loads(cfg)
    assert data["columns"][4]["hint"]  # Tooltip am *-Feld
