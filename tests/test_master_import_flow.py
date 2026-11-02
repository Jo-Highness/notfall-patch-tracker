"""Stammdatenimport end-to-end (Service + Admin-App, Wegwerf-Postgres)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from npt.admins import Actor
from npt.db.models import AdminAuditLog, MasterImport, MasterSystem, StagedUpload
from npt.masterdata import service
from npt.masterdata.excel import XLSX_MIME, ExcelMasterDataSource
from tests.admin_helpers import AdminClient, bootstrap
from tests.certs import CertFactory
from tests.xlsx import master_row, master_xlsx, read_xlsx

ACTOR = Actor("issuer", "cn=test admin")
ROOT = Path(__file__).resolve().parent.parent


def stage_bytes(s: Session, data: bytes, name: str = "s.xlsx") -> str:
    result = ExcelMasterDataSource(data, name).load()
    return service.stage(
        s, result, created_by="t", file_name=name, file_hash=service.file_sha256(data)
    )


def run_import(sf: sessionmaker[Session], data: bytes, remove: set[str] | None = None) -> None:
    with sf() as s:
        sid = stage_bytes(s, data)
        pv = service.preview(s, sid)
        service.apply(s, ACTOR, sid, pv.fingerprint, remove or set())
        s.commit()


def master_ids(sf: sessionmaker[Session]) -> dict[str, MasterSystem]:
    with sf() as s:
        return {m.system_id: m for m in s.scalars(select(MasterSystem))}


def test_import_new_changed_missing_kept(session_factory) -> None:
    run_import(session_factory, master_xlsx(master_row("A"), master_row("B"), master_row("C")))
    rows = master_ids(session_factory)
    assert set(rows) == {"A", "B", "C"}
    assert rows["A"].source == "excel"

    data = master_xlsx(master_row("A", name="neu"), master_row("B"), master_row("D"))
    with session_factory() as s:
        sid = stage_bytes(s, data)
        pv = service.preview(s, sid)
        assert pv.diff.summary() == {"new": 1, "changed": 1, "missing": 1, "unchanged": 1}
        res = service.apply(s, ACTOR, sid, pv.fingerprint, set())
        s.commit()
    assert (res.count_new, res.count_changed, res.count_removed, res.count_missing_kept) == (
        1,
        1,
        0,
        1,
    )
    rows = master_ids(session_factory)
    assert rows["A"].system_name == "neu"
    assert rows["C"].missing_since is not None  # behalten und markiert
    assert rows["B"].missing_since is None
    with session_factory() as s:
        imp = s.scalars(select(MasterImport).order_by(MasterImport.id.desc())).first()
        assert imp is not None
        assert imp.file_hash == service.file_sha256(data)
        assert imp.admin_identifier == ACTOR.identifier
        assert imp.details is not None
        assert imp.details["missing_kept_ids"] == ["C"]
        assert s.scalar(select(func.count()).select_from(StagedUpload)) == 0
        assert "stammdaten_importiert" in set(s.scalars(select(AdminAuditLog.action)))

    # C taucht wieder auf -> Markierung weg; explizites Entfernen von D
    run_import(
        session_factory,
        master_xlsx(master_row("A", name="neu"), master_row("B"), master_row("C")),
        remove={"D"},
    )
    rows = master_ids(session_factory)
    assert set(rows) == {"A", "B", "C"}
    assert rows["C"].missing_since is None


def test_stale_preview_is_rejected(session_factory) -> None:
    run_import(session_factory, master_xlsx(master_row("A")))
    with session_factory() as s:
        sid = stage_bytes(s, master_xlsx(master_row("A", name="x")))
        s.commit()
        fp = service.preview(s, sid).fingerprint
    # parallele Aenderung des Bestands nach der Vorschau
    with session_factory() as s:
        s.execute(update(MasterSystem).values(system_name="parallel"))
        s.commit()
    with session_factory() as s, pytest.raises(service.StalePreviewError):
        service.apply(s, ACTOR, sid, fp, set())
    with session_factory() as s:
        pv = service.preview(s, sid)
        assert pv.fingerprint != fp
        assert pv.diff.changed[0].changes[0].old == "parallel"
        service.apply(s, ACTOR, sid, pv.fingerprint, set())
        s.commit()
    assert master_ids(session_factory)["A"].system_name == "x"


def test_remove_only_listed_missing(session_factory) -> None:
    run_import(session_factory, master_xlsx(master_row("A"), master_row("B")))
    with session_factory() as s:
        sid = stage_bytes(s, master_xlsx(master_row("B")))
        pv = service.preview(s, sid)
        with pytest.raises(service.StalePreviewError):
            service.apply(s, ACTOR, sid, pv.fingerprint, {"B"})  # B ist nicht fehlend


def test_errors_block_apply(session_factory) -> None:
    with session_factory() as s:
        sid = stage_bytes(s, master_xlsx(master_row("A"), master_row("A")))
        pv = service.preview(s, sid)
        assert not pv.can_apply
        with pytest.raises(service.MasterImportError, match="vorschau_hat_fehler"):
            service.apply(s, ACTOR, sid, pv.fingerprint, set())


def test_missing_column_keeps_existing_values(session_factory) -> None:
    run_import(session_factory, master_xlsx(master_row("A", fqdn="alt.test")))
    data = master_xlsx(["A", "Name neu"], header=["System-ID", "System-Name"])
    run_import(session_factory, data)
    row = master_ids(session_factory)["A"]
    assert row.system_name == "Name neu"
    assert row.fqdn == "alt.test"


# ---------------------------------------------------------------- ueber die Admin-App
@pytest.fixture
def admin(admin_app_factory, session_factory, certs: CertFactory) -> AdminClient:
    bootstrap(session_factory)
    return AdminClient(admin_app_factory(), certs.issue())


def upload(admin: AdminClient, data: bytes, name: str = "stamm.xlsx"):
    admin.get("/admin/stammdaten")
    return admin.post("/admin/stammdaten/upload", files={"datei": (name, data, XLSX_MIME)})


def test_admin_upload_preview_apply(admin: AdminClient, session_factory) -> None:
    page = admin.get("/admin/stammdaten")
    assert page.status_code == 200
    assert "Excel-Vorlage herunterladen" in page.text
    data = master_xlsx(
        master_row("A", owner="kaputt"), master_row("B"), master_row("NEW-0005"), master_row("C")
    )
    r = upload(admin, data)
    assert r.status_code == 303, r.text
    loc = r.headers["location"]
    assert loc.startswith("/admin/stammdaten/vorschau/")
    pv = admin.get(loc)
    assert pv.status_code == 200
    assert 'data-testid="count-new">3<' in pv.text
    assert "reserviertem Präfix" in pv.text
    assert "Anwendungsverantwortlicher: ungültige Adresse(n): kaputt." in pv.text
    fp = re.search(r'name="fingerprint" value="([0-9a-f]{64})"', pv.text)
    assert fp
    r = admin.post(loc + "/uebernehmen", {"fingerprint": fp.group(1)})
    assert r.status_code == 303
    assert "ok=import_uebernommen" in r.headers["location"]
    assert set(master_ids(session_factory)) == {"A", "B", "C"}
    hist = admin.get("/admin/stammdaten")
    assert 'data-testid="import-history"' in hist.text
    assert "stamm.xlsx" in hist.text

    # Zweiter Import: B fehlt, explizit zum Entfernen ausgewaehlt; C fehlt -> markiert
    r = upload(admin, master_xlsx(master_row("A")))
    loc = r.headers["location"]
    pv = admin.get(loc)
    assert 'data-testid="count-missing">2<' in pv.text
    fp = re.search(r'name="fingerprint" value="([0-9a-f]{64})"', pv.text)
    assert fp
    r = admin.post(loc + "/uebernehmen", {"fingerprint": fp.group(1), "remove": ["B"]})
    assert "ok=import_uebernommen" in r.headers["location"]
    rows = master_ids(session_factory)
    assert set(rows) == {"A", "C"}
    assert rows["C"].missing_since is not None


def test_admin_upload_stale_fingerprint_redirects_to_new_preview(admin: AdminClient) -> None:
    r = upload(admin, master_xlsx(master_row("A")))
    loc = r.headers["location"]
    r = admin.post(loc + "/uebernehmen", {"fingerprint": "0" * 64})
    assert r.status_code == 303
    assert r.headers["location"] == loc + "?fehler=vorschau_veraltet"
    assert "hat sich seit der Vorschau geändert" in admin.get(r.headers["location"]).text


def test_admin_upload_rejects_bad_files(admin: AdminClient) -> None:
    r = upload(admin, b"kein excel", "x.xlsx")
    assert r.status_code == 422
    assert "keine gültige Excel-Datei" in r.text
    r = upload(admin, master_xlsx(master_row("A")), "x.xls")
    assert r.status_code == 422
    assert "nur" in r.text or ".xlsx" in r.text
    r = upload(admin, master_xlsx(["x"], header=["System-Name"]), "x.xlsx")
    assert r.status_code == 422
    assert "Pflichtspalte „System-ID“ nicht gefunden" in r.text


def test_admin_upload_too_large(admin_app_factory, session_factory, certs: CertFactory) -> None:
    bootstrap(session_factory)
    a = AdminClient(admin_app_factory(UPLOAD_MAX_MB=1), certs.issue())
    big = b"PK\x03\x04" + b"0" * (3 * 1024 * 1024)
    r = upload(a, big, "gross.xlsx")
    assert r.status_code == 413


def test_admin_discard_and_template(admin: AdminClient, session_factory) -> None:
    loc = upload(admin, master_xlsx(master_row("A"))).headers["location"]
    r = admin.post(loc + "/verwerfen")
    assert "ok=vorschau_verworfen" in r.headers["location"]
    assert admin.get(loc).headers["location"].endswith("fehler=vorschau_nicht_gefunden")
    t = admin.get("/admin/stammdaten/vorlage.xlsx")
    assert t.status_code == 200
    assert t.headers["content-type"] == XLSX_MIME
    assert read_xlsx(t.content)[0][0] == "System-ID"


def test_upload_requires_csrf(admin: AdminClient) -> None:
    r = admin.c.post(
        "/admin/stammdaten/upload",
        headers=admin.headers,
        files={"datei": ("a.xlsx", master_xlsx(master_row("A")), XLSX_MIME)},
    )
    assert r.status_code == 403


def test_generated_testdata_imports(session_factory, tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts" / "gen-testdata.py")
    assert spec
    assert spec.loader
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    out = tmp_path / "t.xlsx"
    assert gen.main(["--out", str(out)]) == 0
    rows = read_xlsx(out.read_bytes())[1:]
    assert len(rows) == 2000
    zones = {r[3] for r in rows if r[3]}
    owners = [r[5] for r in rows]
    assert 13 <= len(zones) <= 15
    assert 300 <= len(set(owners)) <= 400
    assert any(owners.count(o) > 1 for o in owners)
    assert any(";" in str(o) or "," in str(o) for o in owners)
    result = ExcelMasterDataSource(out.read_bytes()).load()
    assert result.report.ok
    assert result.report.invalid_emails > 0
    with session_factory() as s:
        sid = service.stage(s, result, created_by="t", file_name="t.xlsx", file_hash="0" * 64)
        pv = service.preview(s, sid)
        assert len(pv.diff.new) == 2000
        service.apply(s, ACTOR, sid, pv.fingerprint, set())
        s.commit()
    assert len(master_ids(session_factory)) == 2000
