"""Backups ueber die Admin-App (Auftrag 6.2): Einstellungen, Button, Download, Restore."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update

from npt.db.models import Backup, Project
from npt.masterdata.excel import XLSX_MIME
from tests.admin_helpers import AdminClient, bootstrap
from tests.certs import CertFactory
from tests.project_helpers import make_project, seed_master
from tests.test_project_upload import set_field


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    return tmp_path / "backups"


@pytest.fixture
def admin(admin_app_factory, session_factory, certs: CertFactory, backup_dir: Path) -> AdminClient:
    bootstrap(session_factory)
    return AdminClient(admin_app_factory(BACKUP_DIR=str(backup_dir)), certs.issue())


@pytest.fixture
def proj(session_factory) -> Any:
    seed_master(session_factory, 3)
    return make_project(session_factory)


def latest_backup(sf: Any) -> Backup:
    with sf() as s:
        b: Backup | None = s.scalars(select(Backup).order_by(Backup.id.desc())).first()
        assert b is not None
        return b


def test_settings_manual_and_download(
    admin: AdminClient, proj: Any, session_factory, backup_dir: Path
) -> None:
    page = admin.get(f"/admin/projekte/{proj.id}").text
    assert 'data-testid="backup-settings"' in page
    assert "Noch keine Backups." in page
    base = f"/admin/projekte/{proj.id}/backups"
    r = admin.post(f"{base}/einstellungen", {"interval": "240", "retention": "5"})
    assert r.headers["location"].endswith("ok=backup_einstellungen")
    with session_factory() as s:
        p = s.get(Project, proj.id)
        assert p is not None
        assert (p.backup_interval_min, p.backup_retention) == (240, 5)
    r = admin.post(f"{base}/einstellungen", {"interval": "17", "retention": "5"})
    assert "fehler=backup_intervall_ungueltig" in r.headers["location"]
    r = admin.post(f"{base}/einstellungen", {"interval": "15", "retention": "0"})
    assert "fehler=backup_aufbewahrung_ungueltig" in r.headers["location"]

    r = admin.post(f"{base}/erstellen", {})
    assert r.headers["location"].endswith("ok=backup_erstellt")
    b = latest_backup(session_factory)
    page = admin.get(f"/admin/projekte/{proj.id}").text
    assert 'data-testid="backup-list"' in page
    assert f'data-testid="backup-{b.id}"' in page
    assert "manuell" in page

    r = admin.get(f"{base}/{b.id}/datei.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.json"')
    assert json.loads(r.content)["project"]["id"] == proj.id
    assert proj.token not in r.text
    r = admin.get(f"{base}/{b.id}/datei.xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"] == XLSX_MIME
    assert admin.get(f"{base}/{b.id}/datei.txt").status_code == 422
    r = admin.get(f"{base}/999999/datei.json")
    assert "fehler=backup_nicht_gefunden" in r.headers["location"]
    other = make_project(session_factory, "anderes")
    r = admin.get(f"/admin/projekte/{other.id}/backups/{b.id}/datei.json")
    assert "fehler=backup_nicht_gefunden" in r.headers["location"]


def test_download_rejects_manipulated_path(
    admin: AdminClient, proj: Any, session_factory, backup_dir: Path
) -> None:
    secret = backup_dir.parent / "geheim.json"
    secret.write_text("{}")
    admin.post(f"/admin/projekte/{proj.id}/backups/erstellen", {})
    b = latest_backup(session_factory)
    with session_factory() as s:
        s.execute(update(Backup).where(Backup.id == b.id).values(json_path="../geheim.json"))
        s.commit()
    r = admin.get(f"/admin/projekte/{proj.id}/backups/{b.id}/datei.json")
    assert r.status_code == 303
    assert "fehler=backup_nicht_gefunden" in r.headers["location"]


def test_restore_dialog_and_apply(
    admin: AdminClient, proj: Any, session_factory, backup_dir: Path
) -> None:
    sf = session_factory
    admin.post(f"/admin/projekte/{proj.id}/backups/erstellen", {})
    b = latest_backup(sf)
    set_field(sf, proj.id, "APP-00001", "affected", "ja")
    url = f"/admin/projekte/{proj.id}/backups/{b.id}/wiederherstellen"
    page = admin.get(url)
    assert page.status_code == 200
    assert 'data-testid="restore-dialog"' in page.text
    assert "data-confirm=" in page.text
    assert "vor Wiederherstellung" in page.text
    r = admin.post(url, {})
    assert "fehler=restore_unbestaetigt" in r.headers["location"]
    r = admin.post(url, {"confirm": "ja"})
    assert r.headers["location"].endswith("ok=restore_ok")
    with sf() as s:
        triggers = s.scalars(select(Backup.trigger).order_by(Backup.id)).all()
        value = s.execute(
            select(Project.reset_seq, Project.change_seq).where(Project.id == proj.id)
        ).one()
    assert triggers == ["manuell", "vor_restore"]
    assert value[0] == value[1]
    log_page = admin.get(f"/admin/projekte/{proj.id}/aenderungen?quelle=restore").text
    assert 'data-testid="log-total">1<' in log_page
    assert "Wiederherstellung" in log_page


def test_delete_project_removes_backup_files(
    admin: AdminClient, proj: Any, session_factory, backup_dir: Path
) -> None:
    admin.post(f"/admin/projekte/{proj.id}/backups/erstellen", {})
    assert (backup_dir / str(proj.id)).is_dir()
    r = admin.post(f"/admin/projekte/{proj.id}/loeschen", {"confirm_name": proj.name})
    assert r.headers["location"].endswith("ok=projekt_geloescht")
    assert not (backup_dir / str(proj.id)).exists()
