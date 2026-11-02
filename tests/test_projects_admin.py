"""Projektverwaltung im Admin-Bereich (Auftrag 5.1/5.6/6.4/7)."""

from __future__ import annotations

import io
import re

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select, update

from npt import editing, projects
from npt.db.models import AdminAuditLog, ChangeLog, MasterSystem, Project, ProjectSystem
from tests.admin_helpers import AdminClient, bootstrap
from tests.certs import CertFactory
from tests.project_helpers import ACTOR, Editor, make_project, new_system_body, seed_master


@pytest.fixture
def admin(admin_app_factory, session_factory, certs: CertFactory) -> AdminClient:
    bootstrap(session_factory)
    return AdminClient(admin_app_factory(PUBLIC_BASE_URL="https://npt.example"), certs.issue())


def test_create_project_snapshot(session_factory) -> None:
    seed_master(session_factory, 4)
    proj = make_project(session_factory, "  CVE-2026-1  ")
    assert proj.name == "CVE-2026-1"
    assert len(proj.token) >= 43
    # spaetere Stammdatenaenderung wirkt nicht auf das Projekt
    with session_factory() as s:
        s.execute(update(MasterSystem).values(system_name="geaendert"))
        s.add(MasterSystem(system_id="APP-99999", system_name="neu"))
        s.commit()
        rows = list(s.scalars(select(ProjectSystem).where(ProjectSystem.project_id == proj.id)))
    assert len(rows) == 4
    assert {r.system_name for r in rows} == {"System 1", "System 2", "System 3", "System 4"}
    assert all(r.origin == "stammdaten" and r.affected is None for r in rows)
    assert all(r.version == 1 for r in rows)
    other = make_project(session_factory, "zweites")
    assert other.token != proj.token
    with session_factory() as s:
        n = s.scalar(
            select(func.count())
            .select_from(ProjectSystem)
            .where(ProjectSystem.project_id == other.id)
        )
    assert n == 5


def test_admin_dashboard_create_and_detail(admin: AdminClient, session_factory) -> None:
    seed_master(session_factory, 3)
    page = admin.get("/admin/")
    assert "Admin-Bereich" in page.text
    assert 'data-testid="project-create-form"' in page.text
    r = admin.post("/admin/projekte", {"name": "", "description": ""})
    assert "fehler=projektname_fehlt" in r.headers["location"]
    r = admin.post("/admin/projekte", {"name": "Log4Shell", "description": "CVE-2021-44228"})
    assert r.status_code == 303
    loc = r.headers["location"]
    assert re.fullmatch(r"/admin/projekte/\d+\?ok=projekt_angelegt", loc)
    detail = admin.get(loc)
    assert detail.status_code == 200
    m = re.search(
        r'id="project-url" value="(https://npt\.example/p/[A-Za-z0-9_-]{43})"', detail.text
    )
    assert m
    assert 'data-copy-target="project-url"' in detail.text
    dash = admin.get("/admin/")
    assert "Log4Shell" in dash.text
    assert "0 %" in dash.text
    with session_factory() as s:
        assert "projekt_angelegt" in set(s.scalars(select(AdminAuditLog.action)))


def test_progress_counts_affected(session_factory) -> None:
    seed_master(session_factory, 4)
    proj = make_project(session_factory)
    with session_factory() as s:
        s.execute(
            update(ProjectSystem)
            .where(ProjectSystem.project_id == proj.id, ProjectSystem.system_id == "APP-00001")
            .values(affected="nein")
        )
        s.commit()
        summary = projects.get_summary(s, proj.id)
    assert (summary.total, summary.processed, summary.progress_pct) == (4, 1, 25)


def test_regenerate_token(admin: AdminClient, session_factory, public_app_factory) -> None:
    seed_master(session_factory, 2)
    proj = make_project(session_factory)
    pub = public_app_factory()
    ed = Editor(pub, proj)
    r = admin.post(f"/admin/projekte/{proj.id}/token")
    assert "ok=token_neu" in r.headers["location"]
    assert pub.get(proj.base).status_code == 404  # alte URL ungueltig
    assert ed.patch("APP-00001", "affected", "ja", None).status_code == 404
    with session_factory() as s:
        p = s.get(Project, proj.id)
        assert p is not None
        assert p.token_generation == 2
        new_token = p.access_token
    assert new_token != proj.token
    proj.token = new_token
    # altes CSRF-Token (alte token_generation) gilt auch unter der neuen URL nicht
    r = ed.c.patch(
        f"{proj.base}/api/systems/APP-00001",
        json={"field": "affected", "value": "ja", "expected": None},
        headers=ed.headers(),
    )
    assert r.status_code == 403
    assert Editor(pub, proj).patch("APP-00001", "affected", "ja", None).status_code == 200


def test_status_transitions(admin: AdminClient, session_factory, public_app_factory) -> None:
    seed_master(session_factory, 1)
    proj = make_project(session_factory)
    ed = Editor(public_app_factory(), proj)
    r = admin.post(f"/admin/projekte/{proj.id}/status", {"status": "abgeschlossen"})
    assert "ok=status_geaendert" in r.headers["location"]
    assert ed.patch("APP-00001", "affected", "ja", None).status_code == 403
    r = admin.post(f"/admin/projekte/{proj.id}/status", {"status": "aktiv"})
    assert "ok=status_geaendert" in r.headers["location"]
    assert ed.patch("APP-00001", "affected", "ja", None).status_code == 200
    admin.post(f"/admin/projekte/{proj.id}/status", {"status": "archiviert"})
    r = admin.post(f"/admin/projekte/{proj.id}/status", {"status": "aktiv"})
    assert "fehler=status_ungueltig" in r.headers["location"]
    r = admin.post(f"/admin/projekte/{proj.id}/status", {"status": "quatsch"})
    assert "fehler=status_ungueltig" in r.headers["location"]
    with session_factory() as s:
        p = s.get(Project, proj.id)
        assert p is not None
        assert p.status == "archiviert"
        assert p.archived_at is not None


def test_delete_requires_exact_name(
    admin: AdminClient, session_factory, public_app_factory
) -> None:
    seed_master(session_factory, 2)
    proj = make_project(session_factory, "Zu löschen")
    ed = Editor(public_app_factory(), proj)
    ed.patch("APP-00001", "affected", "ja", None)
    r = admin.post(f"/admin/projekte/{proj.id}/loeschen", {"confirm_name": "zu löschen"})
    assert "fehler=name_bestaetigung_falsch" in r.headers["location"]
    assert ed.c.get(proj.base).status_code == 200
    r = admin.post(f"/admin/projekte/{proj.id}/loeschen", {"confirm_name": "Zu löschen"})
    assert r.headers["location"] == "/admin/?ok=projekt_geloescht"
    assert ed.c.get(proj.base).status_code == 404
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(ProjectSystem)) == 0
        assert s.scalar(select(func.count()).select_from(ChangeLog)) == 1  # bleibt erhalten


def test_admin_edit_system_master_fields(admin: AdminClient, session_factory) -> None:
    seed_master(session_factory, 2)
    proj = make_project(session_factory)
    page = admin.get(f"/admin/projekte/{proj.id}/system?sid=APP-00001")
    assert page.status_code == 200
    assert 'value="System 1"' in page.text
    form = {
        "sid": "APP-00001",
        "version": "1",
        "system_name": "Korrigiert",
        "sbf": "SBF-1",
        "digital_access_zone": "DAZ-1",
        "system_description": "Beschreibung 1",
        "app_owner": "owner1@firma.test",
        "app_type": "SaaS",
        "lifecycle": "Betrieb",
        "fqdn": "h1.firma.test",
        "cio_unit": "CIO-1",
        "foreign_key": "FK-1",
    }
    r = admin.post(f"/admin/projekte/{proj.id}/system", form)
    assert r.status_code == 303
    assert "ok=system_gespeichert" in r.headers["location"]
    with session_factory() as s:
        row = s.scalars(select(ProjectSystem).where(ProjectSystem.system_id == "APP-00001")).one()
        logs = list(s.scalars(select(ChangeLog).where(ChangeLog.source == "admin")))
        master = s.get(MasterSystem, "APP-00001")
    assert row.system_name == "Korrigiert"
    assert row.version == 2
    assert {lg.field for lg in logs} == {"system_name"}
    assert all(lg.admin_identifier == "C=de,CN=test admin,O=npt test,OU=admins" for lg in logs)
    assert master is not None
    assert master.system_name == "System 1"  # Stammdaten unberuehrt
    # veraltete Version -> Konflikt
    r = admin.post(f"/admin/projekte/{proj.id}/system", form | {"system_name": "X"})
    assert "fehler=system_konflikt" in r.headers["location"]
    # unveraendert
    r = admin.post(f"/admin/projekte/{proj.id}/system", form | {"version": "2"})
    assert "ok=system_unveraendert" in r.headers["location"]


def test_admin_edit_blocked_when_closed(session_factory) -> None:
    seed_master(session_factory, 1)
    proj = make_project(session_factory)
    with session_factory() as s:
        projects.set_status(s, ACTOR, proj.id, "abgeschlossen")
        s.commit()
    with session_factory() as s, pytest.raises(editing.ReadOnlyError):
        editing.admin_update_system(
            s, proj.id, "APP-00001", {"system_name": "x"}, 1, admin_identifier="a", client_ip=None
        )


def test_export_manual_systems(admin: AdminClient, session_factory, public_app_factory) -> None:
    seed_master(session_factory, 2)
    proj = make_project(session_factory)
    ed = Editor(public_app_factory(), proj)
    sid = ed.create(**new_system_body(system_name="=Manuell")).json()["row"]["system_id"]
    r = admin.get(f"/admin/projekte/{proj.id}/manuell.xlsx")
    assert r.status_code == 200
    ws = load_workbook(io.BytesIO(r.content)).active
    assert ws is not None
    assert ws.max_row == 2
    assert ws.cell(row=2, column=1).value == sid
    assert ws.cell(row=2, column=2).value == "=Manuell"
    assert ws.cell(row=2, column=2).data_type == "s"
