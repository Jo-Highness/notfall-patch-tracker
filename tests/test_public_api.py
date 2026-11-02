"""Bearbeiter-API und Hauptliste (Auftrag 5.2/5.3/5.5/5.6/8.2, NPT-04/08/09)."""

from __future__ import annotations

import json
import re
import time

import pytest
from sqlalchemy import func, select, update

from npt import csrf, ratelimit
from npt.db.models import ChangeLog, Project, ProjectSystem
from npt.domain import fields
from npt.editing import PUBLIC_COLUMNS
from tests.conftest import CSRF_SECRET, OTHER_IP, PUBLIC_ORIGIN
from tests.project_helpers import Editor, make_project, new_system_body, seed_master


@pytest.fixture
def setup(session_factory, public_app_factory):
    seed_master(session_factory, 5)
    proj = make_project(session_factory)
    return proj, Editor(public_app_factory(), proj)


def project_row(sf, proj, sid: str) -> ProjectSystem:
    with sf() as s:
        row: ProjectSystem = s.scalars(
            select(ProjectSystem).where(
                ProjectSystem.project_id == proj.id, ProjectSystem.system_id == sid
            )
        ).one()
        return row


def change_seq(sf, proj) -> int:
    with sf() as s:
        return int(s.scalar(select(Project.change_seq).where(Project.id == proj.id)) or 0)


# ---------------------------------------------------------------- Seite und Lesen
def test_main_list_page(setup) -> None:
    proj, ed = setup
    r = ed.c.get(proj.base)
    assert r.status_code == 200
    html = r.text
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert "noindex" in r.headers["x-robots-tag"]
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert "unsafe-inline" not in r.headers["content-security-policy"]
    assert "<script>" not in html  # kein Inline-Script (nur JSON-Datenblock)
    assert f'href="{proj.base}/hilfe"' in html
    assert f'href="{proj.base}/uebersicht"' in html
    assert 'data-testid="new-system-open"' in html
    m = re.search(r'<script type="application/json" id="npt-config">(.*?)</script>', html, re.S)
    assert m
    cfg = json.loads(m.group(1))
    assert [c["field"] for c in cfg["columns"]] == list(fields.LIST_FIELDS)
    assert [c["title"] for c in cfg["columns"]][:5] == [
        "System-ID",
        "System-Name",
        "SBF",
        "Digital Access Zone",
        "System betroffen*",
    ]
    assert {c["field"] for c in cfg["columns"] if c["editable"]} == set(fields.EDITABLE_FIELDS)
    for path in ("/hilfe", "/uebersicht", "/"):
        assert ed.c.get(proj.base + path).status_code == 200


def test_static_assets_served(setup) -> None:
    _, ed = setup
    for path in (
        "/static/vendor/tabulator/tabulator.min.js",
        "/static/vendor/tabulator/tabulator.min.css",
        "/static/js/hauptliste.js",
        "/static/js/npt-filter.js",
        "/static/js/npt-live.js",
    ):
        assert ed.c.get(path).status_code == 200, path


@pytest.mark.parametrize("token", ["x" * 43, "kurz", "a" * 21, "a/b", "a.b-c_d" * 4])
def test_unknown_token_404(setup, token: str) -> None:
    _, ed = setup
    r = ed.c.get(f"/p/{token}")
    assert r.status_code == 404
    r = ed.c.get(f"/p/{'y' * 43}/api/systems")
    assert r.status_code == 404
    assert r.json()["reason"] == "nicht_gefunden"


def test_list_uses_allowlist(setup) -> None:
    _, ed = setup
    rows = ed.rows()
    assert len(rows) == 5
    for row in rows.values():
        assert set(row) == set(PUBLIC_COLUMNS)
        assert row["origin"] == "stammdaten"
        assert row["affected"] is None


# ---------------------------------------------------------------- Schreiben
def test_patch_writes_field_and_logs(setup, session_factory) -> None:
    proj, ed = setup
    seq0 = change_seq(session_factory, proj)
    r = ed.patch("APP-00001", "affected", "ja", None, client_name="  Max  ")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True
    assert body["row"]["affected"] == "ja"
    assert body["row"]["version"] == 2
    assert body["seq"] == seq0 + 1
    assert body["row"]["row_seq"] == seq0 + 1
    with session_factory() as s:
        log = s.scalars(select(ChangeLog)).one()
    assert (log.field, log.old_value, log.new_value, log.source) == ("affected", None, "ja", "ui")
    assert log.client_ip == OTHER_IP
    assert log.client_name == "Max"
    assert log.seq == seq0 + 1
    # Text-Felder, Leeren mit "" bzw. null
    assert ed.patch("APP-00001", "remark", "Zeile 1\r\nZeile 2", None).json()["row"]["remark"] == (
        "Zeile 1\nZeile 2"
    )
    r = ed.patch("APP-00001", "remark", "", "Zeile 1\nZeile 2")
    assert r.json()["row"]["remark"] is None


def test_noop_write_does_not_consume_sequence(setup, session_factory) -> None:
    proj, ed = setup
    ed.patch("APP-00001", "installed_version", "1.0", None)
    seq = change_seq(session_factory, proj)
    r = ed.patch("APP-00001", "installed_version", " 1.0 ", "1.0")
    assert r.status_code == 200
    assert r.json()["changed"] is False
    assert change_seq(session_factory, proj) == seq


@pytest.mark.parametrize(
    "body",
    [
        {"field": "system_name", "value": "x", "expected": "System 1"},
        {"field": "origin", "value": "manuell_angelegt", "expected": "stammdaten"},
        {"field": "system_id", "value": "X", "expected": "APP-00001"},
        {"field": "version", "value": "9", "expected": "2"},
        {"field": "affected", "value": "ja", "expected": None, "system_name": "x"},
        {"field": "affected", "value": "ja"},
        {"field": "affected", "value": "Ja", "expected": None},
        {"field": "action_taken", "value": "gepatcht", "expected": None},
        {"field": "installed_version", "value": "v" * 256, "expected": None},
        {"field": "remark", "value": "r" * 4001, "expected": None},
        {"field": "remark", "value": "a\x00b", "expected": None},
        {"field": "affected", "value": 1, "expected": None},
    ],
)
def test_only_star_fields_writable_and_validated(setup, session_factory, body) -> None:
    proj, ed = setup
    before = project_row(session_factory, proj, "APP-00001")
    r = ed.c.patch(f"{proj.base}/api/systems/APP-00001", json=body, headers=ed.headers())
    assert r.status_code == 422, r.text
    assert r.json()["reason"] == "validierung"
    after = project_row(session_factory, proj, "APP-00001")
    for name in (*fields.LIST_FIELDS, "version"):
        assert getattr(after, name) == getattr(before, name)
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(ChangeLog)) == 0


def test_conflict_same_field_and_merge_different_fields(setup, session_factory) -> None:
    proj, ed = setup
    # zwei Bearbeiter kennen denselben Stand (alles leer)
    assert ed.patch("APP-00002", "affected", "ja", None).status_code == 200
    r = ed.patch("APP-00002", "affected", "nein", None)  # veraltetes expected
    assert r.status_code == 409
    body = r.json()
    assert body["reason"] == "konflikt"
    assert body["field"] == "affected"
    assert body["current"] == "ja"
    assert body["row"]["affected"] == "ja"
    assert (
        project_row(session_factory, proj, "APP-00002").affected == "ja"
    )  # nicht still ueberschrieben
    # anderes Feld derselben Zeile mit „altem" Kenntnisstand: kein Konflikt (Merge)
    r = ed.patch("APP-00002", "remark", "geprueft", None)
    assert r.status_code == 200
    row = project_row(session_factory, proj, "APP-00002")
    assert (row.affected, row.remark, row.version) == ("ja", "geprueft", 3)
    # bewusst ueberschreiben = erneut senden mit dem gezeigten aktuellen Wert
    r = ed.patch("APP-00002", "affected", "nein", body["current"])
    assert r.status_code == 200
    assert project_row(session_factory, proj, "APP-00002").affected == "nein"


def test_patch_unknown_system_404(setup) -> None:
    _, ed = setup
    r = ed.patch("GIBTS-NICHT", "affected", "ja", None)
    assert r.status_code == 404


def test_system_id_with_slash_and_encoding(session_factory, public_app_factory) -> None:
    from npt.db.models import MasterSystem

    with session_factory() as s:
        s.add(MasterSystem(system_id="SAP/PRD 01", system_name="SAP"))
        s.commit()
    proj = make_project(session_factory)
    ed = Editor(public_app_factory(), proj)
    r = ed.c.patch(
        f"{proj.base}/api/systems/SAP%2FPRD%2001",
        json={"field": "affected", "value": "ja", "expected": None},
        headers=ed.headers(),
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- CSRF / Origin (B1)
def test_csrf_and_origin_rules(setup, session_factory) -> None:
    proj, ed = setup
    ok = ed.headers()

    def attempt(headers: dict[str, str]) -> int:
        r = ed.patch("APP-00003", "remark", f"x{time.monotonic_ns()}", None, headers=headers)
        return int(r.status_code)

    assert attempt({k: v for k, v in ok.items() if k != "X-CSRF-Token"}) == 403
    assert attempt({**ok, "X-CSRF-Token": "123.abc"}) == 403
    assert attempt({**ok, "Origin": "https://evil.example"}) == 403
    assert attempt({**ok, "Origin": "http://npt.test.evil.example"}) == 403
    assert attempt({**ok, "Sec-Fetch-Site": "cross-site"}) == 403
    assert attempt({**ok, "Content-Type": "text/plain"}) == 415
    assert attempt({**ok, "Content-Type": "application/x-www-form-urlencoded"}) == 415
    # Token eines anderen Projekts bzw. einer alten token_generation
    other = csrf.issue_token(CSRF_SECRET.encode(), csrf.project_scope(proj.id + 999, 1))
    assert attempt({**ok, "X-CSRF-Token": other}) == 403
    old_gen = csrf.issue_token(CSRF_SECRET.encode(), csrf.project_scope(proj.id, 0))
    assert attempt({**ok, "X-CSRF-Token": old_gen}) == 403
    # akzeptiert: Origin fehlt, Origin "null" (no-referrer), Origin == Basis-URL
    assert attempt(ok) == 200
    for headers in (
        {**ok, "Origin": "null"},
        {**ok, "Origin": PUBLIC_ORIGIN},
        {**ok, "Sec-Fetch-Site": "same-origin", "Origin": "null"},
    ):
        cur = project_row(session_factory, proj, "APP-00003").remark
        r = ed.patch("APP-00003", "remark", f"y{time.monotonic_ns()}", cur, headers=headers)
        assert r.status_code == 200, headers
    # keine CORS-Header
    r = ed.c.options(
        f"{proj.base}/api/systems/APP-00003",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "PATCH"},
    )
    assert "access-control-allow-origin" not in r.headers


def test_body_too_large_rejected(setup) -> None:
    proj, ed = setup
    big = json.dumps({"field": "remark", "value": "x" * 70_000, "expected": None})
    r = ed.c.patch(f"{proj.base}/api/systems/APP-00001", content=big, headers=ed.headers())
    assert r.status_code == 413


# ---------------------------------------------------------------- Schreibschutz
@pytest.mark.parametrize("status", ["abgeschlossen", "archiviert"])
def test_closed_and_archived_projects_are_read_only(setup, session_factory, status) -> None:
    proj, ed = setup
    with session_factory() as s:
        s.execute(update(Project).where(Project.id == proj.id).values(status=status))
        s.commit()
    r = ed.patch("APP-00001", "affected", "ja", None)
    assert r.status_code == 403
    assert r.json()["reason"] == "schreibgeschuetzt"
    r = ed.create(**new_system_body())
    assert r.status_code == 403
    page = ed.c.get(proj.base)
    assert page.status_code == 200
    assert 'data-testid="readonly-banner"' in page.text
    assert 'data-testid="new-system-open"' not in page.text
    assert ed.c.get(f"{proj.base}/api/systems").status_code == 200  # Lesen bleibt erlaubt
    assert project_row(session_factory, proj, "APP-00001").affected is None


# ---------------------------------------------------------------- Rate-Limits (NPT-09)
def test_write_rate_limit(session_factory, public_app_factory, monkeypatch) -> None:
    # Feste Uhr mitten im 10-s-Fenster: alle Zugriffe fallen sicher in dasselbe Fenster
    # (vorher sporadisch rot, wenn der Test eine Fenstergrenze ueberschritt).
    monkeypatch.setattr(ratelimit, "clock", lambda: 1_000_000_003.0)
    seed_master(session_factory, 2)
    proj = make_project(session_factory)
    ed = Editor(public_app_factory(RATE_LIMIT_WRITES=3), proj)
    cur = None
    for i in range(3):
        r = ed.patch("APP-00001", "remark", f"r{i}", cur)
        assert r.status_code == 200
        cur = f"r{i}"
    r = ed.patch("APP-00001", "remark", "zu viel", cur)
    assert r.status_code == 429
    assert r.json()["reason"] == "rate_limit"
    assert int(r.headers["retry-after"]) == 7
    # naechstes Fenster: wieder frei
    monkeypatch.setattr(ratelimit, "clock", lambda: 1_000_000_010.5)
    assert ed.patch("APP-00001", "remark", "neues fenster", cur).status_code == 200
    cur = "neues fenster"
    # andere IP ist nicht betroffen
    other = Editor(public_app_factory(peer="10.99.0.88", RATE_LIMIT_WRITES=3), proj)
    assert other.patch("APP-00001", "remark", "andere ip", cur).status_code == 200


def test_write_rate_limit_counts_domain_but_not_schema_errors(
    session_factory, public_app_factory, monkeypatch
) -> None:
    """DECISIONS D8: Schema-422 (Pydantic, vor dem Endpunkt) zaehlt nicht, fachliche
    Ablehnungen (Domain-422, 409) zaehlen."""
    monkeypatch.setattr(ratelimit, "clock", lambda: 1_000_000_033.0)
    seed_master(session_factory, 2)
    proj = make_project(session_factory)
    ed = Editor(public_app_factory(RATE_LIMIT_WRITES=2), proj)
    for _ in range(5):  # Schema-Fehler: nicht schreibbares Feld, unbekanntes Feld
        assert ed.patch("APP-00001", "system_name", "x").status_code == 422
        assert ed.patch("APP-00001", "remark", "x", None, extra_feld=1).status_code == 422
    r = ed.patch("APP-00001", "affected", "gibt-es-nicht")  # Domain-Fehler
    assert r.status_code == 422
    assert ed.patch("APP-00001", "remark", "a", "falsch").status_code == 409  # Konflikt
    r = ed.patch("APP-00001", "remark", "b", None)
    assert r.status_code == 429


def test_create_rate_limit(session_factory, public_app_factory, monkeypatch) -> None:
    monkeypatch.setattr(ratelimit, "clock", lambda: 1_000_000_020.0)
    seed_master(session_factory, 1)
    proj = make_project(session_factory)
    ed = Editor(public_app_factory(RATE_LIMIT_CREATE=2), proj)
    assert ed.create(**new_system_body()).status_code == 201
    assert ed.create(**new_system_body()).status_code == 201
    r = ed.create(**new_system_body())
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1


def test_trusted_proxy_client_ip(session_factory, public_app_factory) -> None:
    seed_master(session_factory, 1)
    proj = make_project(session_factory)
    c = public_app_factory(peer="10.0.0.5", TRUSTED_PROXY_IPS="10.0.0.5")
    ed = Editor(c, proj)
    hdrs = ed.headers(**{"X-Forwarded-For": "198.51.100.7"})
    assert ed.patch("APP-00001", "affected", "ja", None, headers=hdrs).status_code == 200
    with session_factory() as s:
        assert s.scalars(select(ChangeLog.client_ip)).one() == "198.51.100.7"


# ---------------------------------------------------------------- Neue Systeme (5.6)
def test_create_system(setup, session_factory) -> None:
    _, ed = setup
    r = ed.create(**new_system_body(affected="ja", remark="neu"), client_name="Eva")
    assert r.status_code == 201, r.text
    row = r.json()["row"]
    assert re.fullmatch(r"NEW-\d{4,}", row["system_id"])
    assert row["origin"] == "manuell_angelegt"
    assert row["affected"] == "ja"
    assert set(row) == set(PUBLIC_COLUMNS)
    assert row["system_id"] in ed.rows()
    with session_factory() as s:
        actions = [
            (c.action, c.field, c.client_name)
            for c in s.scalars(select(ChangeLog).order_by(ChangeLog.id))
        ]
    assert actions[0] == ("angelegt", None, "Eva")
    assert ("geaendert", "affected", "Eva") in actions
    # Stammdatenfelder sind danach fuer Bearbeiter schreibgeschuetzt
    r = ed.patch(row["system_id"], "system_name", "x", row["system_name"])
    assert r.status_code == 422


@pytest.mark.parametrize("missing", list(fields.MASTER_DATA_FIELDS))
def test_create_requires_all_master_fields(setup, missing: str) -> None:
    _, ed = setup
    for empty in (None, "", "   "):
        r = ed.create(**new_system_body(**{missing: empty}))
        assert r.status_code == 422
        assert r.json()["errors"][missing] == "Pflichtfeld"


@pytest.mark.parametrize(
    ("owner", "ok"),
    [
        ("a@firma.test", True),
        ("Max <max@firma.test>", True),
        ("a@firma.test; b@firma.test", True),
        ("max at firma", False),
        ("a@firma.test; kaputt", False),
        ("a@", False),
    ],
)
def test_create_validates_email(setup, owner: str, ok: bool) -> None:
    _, ed = setup
    r = ed.create(**new_system_body(app_owner=owner))
    assert (r.status_code == 201) is ok, r.text
    if not ok:
        assert "E-Mail" in r.json()["errors"]["app_owner"]


def test_create_rejects_extra_and_bad_star_fields(setup) -> None:
    _, ed = setup
    assert ed.create(**new_system_body(system_id="NEW-9999")).status_code == 422
    assert ed.create(**new_system_body(origin="stammdaten")).status_code == 422
    assert ed.create(**new_system_body(affected="vielleicht")).status_code == 422
    assert ed.create(**new_system_body(installed_version="v" * 300)).status_code == 422


# ---------------------------------------------------------------- Ladezeit
def test_list_load_time_2000_rows(session_factory, public_app_factory) -> None:
    seed_master(session_factory, 2000)
    proj = make_project(session_factory)
    c = public_app_factory()
    c.get(f"{proj.base}/api/systems")  # Aufwaermen (Verbindungsaufbau)
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        r = c.get(f"{proj.base}/api/systems")
        times.append(time.perf_counter() - t0)
        assert r.status_code == 200
        assert len(r.json()["rows"]) == 2000
    best, worst = min(times), max(times)
    print(f"\nJSON-Liste 2000 Zeilen: min {best * 1000:.0f} ms, max {worst * 1000:.0f} ms")
    assert worst < 2.0
