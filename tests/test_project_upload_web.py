"""Admin-Upload und Aenderungsprotokoll ueber die Admin-App (Auftrag 6.1/6.4)."""

from __future__ import annotations

import html
import io
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from sqlalchemy import select, update

from npt.db.models import AdminAuditLog, Backup, ChangeLog, ProjectSystem
from npt.domain import fields
from npt.masterdata.excel import XLSX_MIME
from tests.admin_helpers import AdminClient, bootstrap
from tests.certs import CertFactory
from tests.project_helpers import make_project, seed_master
from tests.test_project_upload import set_field, upload_xlsx


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    return tmp_path / "backups"


@pytest.fixture
def admin(admin_app_factory, session_factory, certs: CertFactory, backup_dir: Path) -> AdminClient:
    bootstrap(session_factory)
    return AdminClient(admin_app_factory(BACKUP_DIR=str(backup_dir)), certs.issue())


@pytest.fixture
def proj(session_factory) -> Any:
    seed_master(session_factory, 4)
    return make_project(session_factory)


def post_file(admin: AdminClient, pid: int, data: bytes, name: str = "u.xlsx") -> Any:
    return admin.post(f"/admin/projekte/{pid}/upload", files={"datei": (name, data, XLSX_MIME)})


def form_values(page: str, name: str) -> list[str]:
    return [html.unescape(v) for v in re.findall(rf'name="{name}" value="([^"]+)"', page)]


def test_upload_page_mentions_empty_cells(admin: AdminClient, proj: Any) -> None:
    page = admin.get(f"/admin/projekte/{proj.id}/upload")
    assert page.status_code == 200
    assert "Leere Zellen löschen keine vorhandenen Werte" in page.text
    assert 'data-testid="upload-empty-cells-hint"' in page.text
    detail = admin.get(f"/admin/projekte/{proj.id}")
    assert 'data-testid="project-upload-link"' in detail.text
    assert 'title="Leere Zellen im Upload löschen keine vorhandenen Werte."' in detail.text


def test_upload_rejects_bad_files(admin: AdminClient, proj: Any) -> None:
    r = post_file(admin, proj.id, b"kein excel", "x.csv")
    assert r.status_code == 422
    assert "Format .xlsx" in r.text
    r = post_file(admin, proj.id, b"PK\x03\x04kaputt")
    assert r.status_code == 422
    assert "keine gültige Excel-Datei" in r.text
    r = post_file(admin, proj.id, upload_xlsx(["system_name"], ["x"]))
    assert r.status_code == 422
    assert "System-ID" in r.text


def test_preview_with_errors_blocks_apply(admin: AdminClient, proj: Any) -> None:
    r = post_file(admin, proj.id, upload_xlsx(["system_id", "affected"], ["APP-00001", "kaputt"]))
    assert r.status_code == 303
    page = admin.get(r.headers["location"])
    assert 'data-testid="report-errors"' in page.text
    assert "<td>2</td><td>APP-00001</td>" in page.text  # Zeilenangabe
    assert re.search(r'data-testid="upload-apply" disabled', page.text)


def test_full_flow_with_conflict_confirmation(
    admin: AdminClient, proj: Any, session_factory, backup_dir: Path
) -> None:
    sf = session_factory
    set_field(sf, proj.id, "APP-00001", "affected", "nein")
    set_field(sf, proj.id, "APP-00002", "affected", "nein")
    data = upload_xlsx(
        ["system_id", "affected", "remark"],
        ["APP-00001", "Ja", "@SUM(1+1)"],
        ["APP-00002", "Ja", None],
        ["APP-00003", None, None],
        ["UNBEKANNT-1", "Ja", None],
    )
    r = post_file(admin, proj.id, data)
    assert r.status_code == 303
    loc = r.headers["location"]
    page = admin.get(loc).text
    assert 'data-testid="count-conflicts">2<' in page
    assert 'data-testid="count-fills">1<' in page
    assert 'data-testid="count-unknown">1<' in page
    assert 'data-testid="count-unchanged">1<' in page
    assert "Leere Zellen im Upload löschen keine vorhandenen Werte" in page
    keys = form_values(page, "c")
    assert len(keys) == 2
    key1 = next(k for k in keys if k.endswith(":APP-00001"))
    fingerprint = form_values(page, "fingerprint")[0]
    r = admin.post(f"{loc}/uebernehmen", {"fingerprint": fingerprint, "c": [key1]})
    assert r.status_code == 303, r.text
    assert r.headers["location"] == f"/admin/projekte/{proj.id}?ok=upload_uebernommen"
    with sf() as s:
        rows = {
            r.system_id: r
            for r in s.scalars(select(ProjectSystem).where(ProjectSystem.project_id == proj.id))
        }
        assert rows["APP-00001"].affected == "ja"
        assert rows["APP-00001"].remark == "@SUM(1+1)"
        assert rows["APP-00002"].affected == "nein"
        assert "UNBEKANNT-1" not in rows
        assert s.scalars(select(Backup.trigger)).all() == ["vor_import"]
        audit = s.scalars(select(AdminAuditLog.action)).all()
        assert "projekt_upload_hochgeladen" in audit
        assert "projekt_upload_uebernommen" in audit
    assert len(list(backup_dir.rglob("*.json"))) == 1

    # Aenderungsprotokoll: Admin-Upload mit Identifier, Export mit Formel-Schutz
    log_page = admin.get(f"/admin/projekte/{proj.id}/aenderungen?quelle=admin_upload")
    assert 'data-testid="log-total">2<' in log_page.text
    assert "Admin-Upload" in log_page.text
    xlsx = admin.get(f"/admin/projekte/{proj.id}/aenderungen.xlsx?quelle=admin_upload")
    assert xlsx.headers["content-type"] == XLSX_MIME
    ws = load_workbook(io.BytesIO(xlsx.content)).active
    assert ws is not None
    cells = [c for row in ws.iter_rows(min_row=2) for c in row if c.value == "@SUM(1+1)"]
    assert cells
    assert cells[0].data_type == "s"
    assert cells[0].quotePrefix
    assert ws.freeze_panes == "A2"


def test_stale_confirmation_shows_updated_preview(
    admin: AdminClient, proj: Any, session_factory
) -> None:
    sf = session_factory
    set_field(sf, proj.id, "APP-00001", "affected", "nein")
    set_field(sf, proj.id, "APP-00002", "affected", "nein")
    data = upload_xlsx(["system_id", "affected"], ["APP-00001", "Ja"], ["APP-00002", "Ja"])
    loc = post_file(admin, proj.id, data).headers["location"]
    page = admin.get(loc).text
    keys = form_values(page, "c")
    fp = form_values(page, "fingerprint")[0]
    # zwischenzeitliche Bearbeitung eines betroffenen Felds
    set_field(sf, proj.id, "APP-00002", "affected", "in_pruefung")
    r = admin.post(f"{loc}/uebernehmen", {"fingerprint": fp, "c": keys})
    assert r.status_code == 409
    assert 'data-testid="upload-stale"' in r.text
    assert "In Prüfung" in r.text
    new_fp = form_values(r.text, "fingerprint")[0]
    assert new_fp != fp
    # unveraenderter Konflikt bleibt ausgewaehlt, der geaenderte nicht
    checked = re.findall(r'name="c" value="([^"]+)"[^>]*checked', r.text)
    assert [html.unescape(k) for k in checked] == [k for k in keys if k.endswith(":APP-00001")]
    with sf() as s:
        assert s.scalars(select(Backup)).first() is None
        rows = dict(
            s.execute(
                select(ProjectSystem.system_id, ProjectSystem.affected).where(
                    ProjectSystem.project_id == proj.id
                )
            ).all()
        )
    assert rows["APP-00001"] == "nein"
    assert rows["APP-00002"] == "in_pruefung"
    r = admin.post(f"{loc}/uebernehmen", {"fingerprint": new_fp, "alle": "1"})
    assert r.status_code == 303
    with sf() as s:
        values = s.scalars(
            select(ProjectSystem.affected).where(
                ProjectSystem.project_id == proj.id,
                ProjectSystem.system_id.in_(["APP-00001", "APP-00002"]),
            )
        ).all()
    assert values == ["ja", "ja"]


def test_unknown_create_via_form_and_discard(
    admin: AdminClient, proj: Any, session_factory
) -> None:
    data = upload_xlsx(["system_id", "system_name"], ["X-1", "Neu 1"], ["X-2", "Neu 2"])
    loc = post_file(admin, proj.id, data).headers["location"]
    page = admin.get(loc).text
    assert form_values(page, "anlegen") == ["X-1", "X-2"]
    r = admin.post(
        f"{loc}/uebernehmen",
        {"fingerprint": form_values(page, "fingerprint")[0], "anlegen": ["X-2"]},
    )
    assert r.status_code == 303
    with session_factory() as s:
        origin = dict(
            s.execute(
                select(ProjectSystem.system_id, ProjectSystem.origin).where(
                    ProjectSystem.project_id == proj.id, ProjectSystem.system_id.like("X-%")
                )
            ).all()
        )
    assert origin == {"X-2": "admin_upload"}
    # Verwerfen
    loc = post_file(admin, proj.id, data).headers["location"]
    r = admin.post(f"{loc}/verwerfen", {})
    assert r.status_code == 303
    r = admin.get(loc)
    assert r.status_code == 303
    assert "upload_nicht_gefunden" in r.headers["location"]


def test_stage_of_other_project_not_reachable(
    admin: AdminClient, proj: Any, session_factory
) -> None:
    other = make_project(session_factory, "anderes")
    loc = post_file(admin, proj.id, upload_xlsx(["system_id"], ["APP-00001"])).headers["location"]
    stage_id = loc.rsplit("/", 1)[1]
    r = admin.get(f"/admin/projekte/{other.id}/upload/{stage_id}")
    assert r.status_code == 303
    assert "upload_nicht_gefunden" in r.headers["location"]


def test_changelog_filters_and_pagination(admin: AdminClient, proj: Any, session_factory) -> None:
    sf = session_factory
    for i in range(1, 4):
        set_field(sf, proj.id, f"APP-0000{i}", "affected", "ja")
    set_field(sf, proj.id, "APP-00001", "remark", "Text <b>")
    with sf() as s:
        # Ein Eintrag am 2026-01-01 23:30 Berlin (= 22:30 UTC), einer am 02.01. 00:30 Berlin
        s.execute(
            update(ChangeLog)
            .where(ChangeLog.system_id == "APP-00002")
            .values(changed_at=datetime(2026, 1, 1, 22, 30, tzinfo=UTC))
        )
        s.execute(
            update(ChangeLog)
            .where(ChangeLog.system_id == "APP-00003")
            .values(changed_at=datetime(2026, 1, 1, 23, 30, tzinfo=UTC))
        )
        s.commit()
    base = f"/admin/projekte/{proj.id}/aenderungen"
    page = admin.get(base).text
    assert 'data-testid="log-total">4<' in page
    assert "Text &lt;b&gt;" in page  # escaped
    assert "Bea" in page
    page = admin.get(f"{base}?sid=app-00001").text
    assert 'data-testid="log-total">2<' in page
    page = admin.get(f"{base}?feld=remark").text
    assert 'data-testid="log-total">1<' in page
    page = admin.get(f"{base}?von=2026-01-01&bis=2026-01-01").text
    assert 'data-testid="log-total">1<' in page
    assert "APP-00002" in page
    page = admin.get(f"{base}?von=2026-01-02&bis=2026-01-02").text
    assert 'data-testid="log-total">1<' in page
    assert "APP-00003" in page
    page = admin.get(f"{base}?feld=kaputt&von=nix").text  # ungueltig -> ignoriert
    assert 'data-testid="log-total">4<' in page
    export = admin.get(f"/admin/projekte/{proj.id}/aenderungen.xlsx?feld=affected")
    ws = load_workbook(io.BytesIO(export.content)).active
    assert ws is not None
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][:4] == ("Zeitpunkt", "System-ID", "Aktion", "Feld")
    assert len(rows) == 4
    assert {r[3] for r in rows[1:]} == {fields.DISPLAY_NAMES["affected"]}
    assert {r[5] for r in rows[1:]} == {"Ja"}
    assert "2026-01-02 00:30:00" in {r[0] for r in rows[1:]}  # Anzeige Europe/Berlin


def test_changelog_paginates(admin: AdminClient, proj: Any, session_factory) -> None:
    with session_factory() as s:
        s.add_all(
            ChangeLog(
                project_id=proj.id,
                seq=i,
                system_id=f"APP-{i:05d}",
                field="remark",
                new_value=str(i),
                source="ui",
            )
            for i in range(1, 251)
        )
        s.commit()
    base = f"/admin/projekte/{proj.id}/aenderungen"
    page = admin.get(base).text
    assert "Seite 1 von 3" in page
    assert 'data-testid="log-next"' in page
    page = admin.get(f"{base}?seite=3").text
    assert "Seite 3 von 3" in page
    assert "APP-00001" in page
    assert "APP-00250" not in page
    assert admin.get(f"{base}?seite=99").status_code == 200
