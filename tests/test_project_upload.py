"""Admin-Upload ins Projekt (Auftrag 6.1): Parsing, Diff-Gruppierung, Uebernahme, Revalidierung."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from npt import editing, export
from npt.admins import Actor
from npt.db.models import Backup, ChangeLog, Project, ProjectSystem, StagedUpload
from npt.domain import fields
from npt.masterdata.source import MasterDataError
from npt.project_upload import service
from npt.project_upload.diff import compute_diff, conflict_key
from npt.project_upload.parse import UploadRecord, parse_upload
from tests.conftest import DB_URL
from tests.project_helpers import Editor, make_project, seed_master
from tests.xlsx import xlsx_bytes

ACTOR = Actor("issuer", "cn=upload admin", "192.0.2.20")
H = fields.DISPLAY_NAMES


def upload_xlsx(columns: list[str], *rows: list[Any]) -> bytes:
    return xlsx_bytes([[H[c] for c in columns], *rows])


# ---------------------------------------------------------------- Parsing (ohne DB)
def test_parse_enum_labels_keys_and_empty() -> None:
    data = upload_xlsx(
        ["system_id", "affected", "action_taken", "remark"],
        ["A", "ja", "Mitigiert (siehe Bemerkung)", None],
        ["B", "In Prüfung", "leer", "  "],
        ["C", "(leer)", "update_installiert", "Text"],
    )
    parsed = parse_upload(data)
    assert parsed.report.ok, parsed.report.errors
    recs = {r.system_id: r.values for r in parsed.records}
    assert recs["A"] == {"affected": "ja", "action_taken": "mitigiert"}
    assert recs["B"] == {"affected": "in_pruefung"}  # „leer" und Leerzeichen = kein Wert
    assert recs["C"] == {"action_taken": "update_installiert", "remark": "Text"}
    assert parsed.columns == ["affected", "action_taken", "remark"]


def test_parse_invalid_enum_is_error_with_row() -> None:
    data = xlsx_bytes(
        [
            ["Titelzeile"],
            [H["system_id"], H["affected"], H["action_taken"]],
            ["A", "Ja", None],
            ["B", "vielleicht", None],
            ["C", None, "Repariert"],
        ]
    )
    parsed = parse_upload(data)
    assert not parsed.report.ok
    errors = [(e.row, e.system_id, e.message) for e in parsed.report.errors]
    assert errors[0][:2] == (4, "B")
    assert "System betroffen" in errors[0][2]
    assert "vielleicht" in errors[0][2]
    assert "Ja" in errors[0][2]  # erlaubte Werte werden genannt
    assert errors[1][:2] == (5, "C")
    assert "Ausgeführte Aktion" in errors[1][2]
    assert [r.system_id for r in parsed.records] == ["A"]


def test_parse_errors_duplicates_lengths_and_ignored_columns() -> None:
    data = upload_xlsx(
        ["system_id", "installed_version", "origin", "system_name"],
        ["A", "1.0", "Stammdaten", "x"],
        ["A", "2.0", None, None],
        [None, "3.0", None, None],
        ["B", "v" * 300, None, None],
    )
    parsed = parse_upload(data)
    msgs = [(e.row, e.message) for e in parsed.report.errors]
    assert (3, "System-ID ist doppelt (erstmals in Zeile 2).") in msgs
    assert (4, "System-ID ist leer.") in msgs
    assert any(r == 5 and "Installierte Version" in m for r, m in msgs)
    assert any("Herkunft" in w.message for w in parsed.report.warnings)
    assert "origin" not in parsed.columns


def test_parse_requires_system_id_column() -> None:
    with pytest.raises(MasterDataError, match="System-ID"):
        parse_upload(upload_xlsx(["system_name"], ["x"]))


def test_reimport_of_export_is_lossless() -> None:
    row = dict.fromkeys(fields.LIST_FIELDS) | {
        "system_id": "A",
        "system_name": "=HYPERLINK(1)",
        "affected": "in_pruefung",
        "action_taken": "mitigiert",
        "remark": "Zeile 1\nZeile 2",
        "origin": "stammdaten",
    }
    parsed = parse_upload(export.systems_workbook([row]))
    assert parsed.report.ok
    assert parsed.records[0].values == {
        "system_name": "=HYPERLINK(1)",
        "affected": "in_pruefung",
        "action_taken": "mitigiert",
        "remark": "Zeile 1\nZeile 2",
    }


# ---------------------------------------------------------------- Diff (rein)
def test_diff_groups_conflicts_fills_unknown_unchanged() -> None:
    current: dict[str, dict[str, str | None]] = {
        "A": {"system_name": "Alt", "affected": "ja", "remark": None},
        "B": {"system_name": "B", "affected": None, "remark": "r"},
        "C": {"system_name": "C", "affected": "nein", "remark": None},
        "NEW-0001": {"system_name": "N", "affected": None, "remark": None},
    }
    records = [
        UploadRecord("A", {"system_name": "Neu", "affected": "ja", "remark": "neu"}, 2),
        UploadRecord("B", {"affected": "nein"}, 3),  # remark leer -> bleibt
        UploadRecord("C", {"affected": "nein"}, 4),  # gleich
        UploadRecord("X", {"system_name": "unbekannt"}, 5),
        UploadRecord("new-0099", {"system_name": "reserviert"}, 6),
        UploadRecord("NEW-0001", {}, 7),  # nur ID -> unveraendert
    ]
    diff = compute_diff(current, records, ["system_name", "affected", "remark"])
    by_id = {s.system_id: s for s in diff.systems}
    assert [(c.field, c.old, c.new) for c in by_id["A"].conflicts] == [
        ("system_name", "Alt", "Neu")
    ]
    assert [(c.field, c.old, c.new) for c in by_id["A"].fills] == [("remark", None, "neu")]
    assert [(c.field, c.new) for c in by_id["B"].fills] == [("affected", "nein")]
    assert by_id["B"].conflicts == ()
    assert "C" not in by_id
    assert diff.unchanged == 2
    assert [(u.system_id, u.creatable) for u in diff.unknown] == [
        ("X", True),
        ("new-0099", False),
    ]
    assert diff.conflict_count == 1
    assert diff.fill_count == 2
    assert [s.system_id for s in diff.conflict_systems] == ["A"]
    assert {s.system_id for s in diff.fill_systems} == {"A", "B"}


def test_fingerprint_depends_only_on_affected_fields() -> None:
    current: dict[str, dict[str, str | None]] = {"A": {"affected": "ja", "remark": "x"}}
    records = [UploadRecord("A", {"affected": "nein"}, 2)]
    fp = compute_diff(current, records, ["affected"]).fingerprint()
    other: dict[str, dict[str, str | None]] = {"A": {"affected": "ja", "remark": "anders"}}
    assert compute_diff(other, records, ["affected"]).fingerprint() == fp
    changed: dict[str, dict[str, str | None]] = {"A": {"affected": "in_pruefung", "remark": "x"}}
    assert compute_diff(changed, records, ["affected"]).fingerprint() != fp


# ---------------------------------------------------------------- Service (DB)
@pytest.fixture
def proj(session_factory: sessionmaker[Session]) -> Any:
    seed_master(session_factory, 6)
    return make_project(session_factory)


def set_field(sf: sessionmaker[Session], pid: int, sid: str, field: str, value: str) -> None:
    with sf() as s:
        cur = s.scalar(
            select(getattr(ProjectSystem, field)).where(
                ProjectSystem.project_id == pid, ProjectSystem.system_id == sid
            )
        )
        editing.update_field(
            s, pid, sid, field, value, cur, client_ip="10.0.0.1", client_name="Bea"
        )
        s.commit()


def stage_upload(sf: sessionmaker[Session], pid: int, data: bytes) -> str:
    with sf() as s:
        stage_id = service.stage(
            s, pid, parse_upload(data), created_by="t", file_name="u.xlsx", file_hash="0" * 64
        )
        s.commit()
        return stage_id


def systems(sf: sessionmaker[Session], pid: int) -> dict[str, ProjectSystem]:
    with sf() as s:
        rows = s.scalars(select(ProjectSystem).where(ProjectSystem.project_id == pid))
        return {r.system_id: r for r in rows}


def apply_upload(
    sf: sessionmaker[Session],
    pid: int,
    stage_id: str,
    backup_dir: Path,
    selection: service.Selection | None = None,
    fingerprint: str | None = None,
) -> service.ApplyResult:
    with sf() as s:
        fp = fingerprint or service.preview(s, pid, stage_id).fingerprint
        result = service.apply(
            s, ACTOR, pid, stage_id, fp, selection or service.Selection(), backup_dir=backup_dir
        )
        s.commit()
        return result


def test_apply_fills_keeps_conflicts_and_never_deletes(
    session_factory: sessionmaker[Session], proj: Any, tmp_path: Path
) -> None:
    sf = session_factory
    set_field(sf, proj.id, "APP-00001", "affected", "nein")
    set_field(sf, proj.id, "APP-00002", "remark", "bleibt")
    before = systems(sf, proj.id)
    data = upload_xlsx(
        ["system_id", "system_name", "affected", "remark", "fqdn"],
        ["APP-00001", "System 1", "ja", "neu 1", None],  # affected = Konflikt, remark = Neu
        ["APP-00002", None, "in_pruefung", None, ""],  # leere Zellen loeschen nichts
        ["APP-00003", "System 3", None, None, None],  # unveraendert
    )
    stage_id = stage_upload(sf, proj.id, data)
    with sf() as s:
        pv = service.preview(s, proj.id, stage_id)
    assert pv.diff.summary() == {
        "konflikte": 1,
        "konflikt_systeme": 1,
        "neubefuellungen": 2,
        "unbekannt": 0,
        "unveraendert": 1,
    }
    res = apply_upload(sf, proj.id, stage_id, tmp_path)
    assert (res.systems_changed, res.fields_filled, res.conflicts_taken) == (2, 2, 0)
    assert res.conflicts_skipped == 1
    after = systems(sf, proj.id)
    assert after["APP-00001"].affected == "nein"  # Konflikt nicht bestaetigt
    assert after["APP-00001"].remark == "neu 1"
    assert after["APP-00002"].remark == "bleibt"
    assert after["APP-00002"].affected == "in_pruefung"
    assert after["APP-00002"].fqdn == before["APP-00002"].fqdn
    assert after["APP-00002"].system_name == before["APP-00002"].system_name
    assert after["APP-00003"].version == before["APP-00003"].version
    for sid in ("APP-00001", "APP-00002"):
        assert after[sid].version == before[sid].version + 1
        assert after[sid].row_seq == res.seq
    with sf() as s:
        logs = s.scalars(select(ChangeLog).where(ChangeLog.source == "admin_upload")).all()
        assert {(e.system_id, e.field, e.old_value, e.new_value) for e in logs} == {
            ("APP-00001", "remark", None, "neu 1"),
            ("APP-00002", "affected", None, "in_pruefung"),
        }
        assert {e.admin_identifier for e in logs} == {ACTOR.identifier}
        assert {e.seq for e in logs} == {res.seq}
        backup = s.scalars(select(Backup)).one()
        assert backup.trigger == "vor_import"
        assert backup.id == res.backup_id
        assert res.seq is not None
        assert backup.change_seq == res.seq - 1  # Stand vor dem Import
        assert s.scalar(select(func.count()).select_from(StagedUpload)) == 0
    doc = json.loads((tmp_path / backup.json_path).read_text())
    snap = {r["system_id"]: r for r in doc["systems"]}
    assert snap["APP-00001"]["remark"] is None  # Backup zeigt den Stand davor
    assert "access_token" not in json.dumps(doc)


def test_conflict_selection_single_and_all(
    session_factory: sessionmaker[Session], proj: Any, tmp_path: Path
) -> None:
    sf = session_factory
    for sid in ("APP-00001", "APP-00002"):
        set_field(sf, proj.id, sid, "affected", "nein")
        set_field(sf, proj.id, sid, "installed_version", "1.0")
    data = upload_xlsx(
        ["system_id", "affected", "installed_version"],
        ["APP-00001", "Ja", "2.0"],
        ["APP-00002", "Ja", "2.0"],
    )
    stage_id = stage_upload(sf, proj.id, data)
    with sf() as s:
        pv = service.preview(s, proj.id, stage_id)
    assert pv.diff.conflict_count == 4
    one = next(s for s in pv.diff.systems if s.system_id == "APP-00001")
    chosen = conflict_key("APP-00001", next(c for c in one.conflicts if c.field == "affected"))
    res = apply_upload(
        sf, proj.id, stage_id, tmp_path, service.Selection(conflicts=frozenset({chosen}))
    )
    assert (res.conflicts_taken, res.conflicts_skipped) == (1, 3)
    rows = systems(sf, proj.id)
    assert (rows["APP-00001"].affected, rows["APP-00001"].installed_version) == ("ja", "1.0")
    assert (rows["APP-00002"].affected, rows["APP-00002"].installed_version) == ("nein", "1.0")

    stage_id = stage_upload(sf, proj.id, data)
    res = apply_upload(sf, proj.id, stage_id, tmp_path, service.Selection(accept_all=True))
    assert (res.conflicts_taken, res.conflicts_skipped) == (3, 0)
    rows = systems(sf, proj.id)
    for sid in ("APP-00001", "APP-00002"):
        assert (rows[sid].affected, rows[sid].installed_version) == ("ja", "2.0")


def test_revalidation_on_interim_change(
    session_factory: sessionmaker[Session], proj: Any, tmp_path: Path
) -> None:
    sf = session_factory
    set_field(sf, proj.id, "APP-00001", "affected", "nein")
    data = upload_xlsx(["system_id", "affected"], ["APP-00001", "Ja"], ["APP-00002", "Nein"])
    stage_id = stage_upload(sf, proj.id, data)
    with sf() as s:
        pv = service.preview(s, proj.id, stage_id)
        seq_before = s.get(Project, proj.id).change_seq  # type: ignore[union-attr]
    old_fp = pv.fingerprint
    # Aenderung an einem nicht betroffenen Feld: Vorschau bleibt gueltig ...
    set_field(sf, proj.id, "APP-00001", "remark", "egal")
    with sf() as s:
        assert service.preview(s, proj.id, stage_id).fingerprint == old_fp
    # ... an einem betroffenen Feld nicht.
    set_field(sf, proj.id, "APP-00002", "affected", "in_pruefung")
    with sf() as s, pytest.raises(service.StaleUploadError):
        service.apply(
            s,
            ACTOR,
            proj.id,
            stage_id,
            old_fp,
            service.Selection(accept_all=True),
            backup_dir=tmp_path,
        )
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Backup)) == 0
        assert s.get(Project, proj.id).change_seq == seq_before + 2  # type: ignore[union-attr]
        new_pv = service.preview(s, proj.id, stage_id)
    assert new_pv.fingerprint != old_fp
    assert new_pv.diff.conflict_count == 2  # APP-00002 ist jetzt ebenfalls ein Konflikt
    assert systems(sf, proj.id)["APP-00001"].affected == "nein"
    res = apply_upload(
        sf, proj.id, stage_id, tmp_path, service.Selection(accept_all=True), new_pv.fingerprint
    )
    assert res.conflicts_taken == 2
    assert systems(sf, proj.id)["APP-00002"].affected == "nein"


def test_unknown_ids_ignored_by_default_or_created(
    session_factory: sessionmaker[Session], proj: Any, tmp_path: Path
) -> None:
    sf = session_factory
    data = upload_xlsx(
        ["system_id", "system_name", "digital_access_zone", "affected"],
        ["NEU-1", "Neues System", "DAZ-X", "Ja"],
        ["NEU-2", "Zweites", None, None],
        ["NEW-7777", "reserviert", None, None],
    )
    stage_id = stage_upload(sf, proj.id, data)
    res = apply_upload(sf, proj.id, stage_id, tmp_path)
    assert res.seq is None
    assert res.created == 0
    assert "NEU-1" not in systems(sf, proj.id)
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Backup)) == 0

    stage_id = stage_upload(sf, proj.id, data)
    with sf() as s:
        pv = service.preview(s, proj.id, stage_id)
        assert pv.diff.creatable_ids == {"NEU-1", "NEU-2"}
        with pytest.raises(service.StaleUploadError):  # reservierte ID nicht anlegbar
            service.apply(
                s,
                ACTOR,
                proj.id,
                stage_id,
                pv.fingerprint,
                service.Selection(create=frozenset({"NEW-7777"})),
                backup_dir=tmp_path,
            )
        s.rollback()
    res = apply_upload(
        sf, proj.id, stage_id, tmp_path, service.Selection(create=frozenset({"NEU-1"}))
    )
    assert res.created == 1
    rows = systems(sf, proj.id)
    assert "NEU-2" not in rows
    new = rows["NEU-1"]
    assert (new.origin, new.system_name, new.digital_access_zone, new.affected) == (
        "admin_upload",
        "Neues System",
        "DAZ-X",
        "ja",
    )
    assert new.row_seq == res.seq
    with sf() as s:
        actions = s.execute(
            select(ChangeLog.action, ChangeLog.field).where(ChangeLog.system_id == "NEU-1")
        ).all()
    assert ("angelegt", None) in actions
    assert ("geaendert", "affected") in actions


def test_apply_is_one_transaction(
    session_factory: sessionmaker[Session],
    proj: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sf = session_factory
    data = upload_xlsx(
        ["system_id", "affected", "remark"], ["APP-00001", "Ja", "a"], ["APP-00002", "Nein", "b"]
    )
    stage_id = stage_upload(sf, proj.id, data)
    before = systems(sf, proj.id)

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("Abbruch nach dem Schreiben")

    monkeypatch.setattr(service, "notify", boom)
    with sf() as s:
        pv = service.preview(s, proj.id, stage_id)
    with sf() as s, pytest.raises(RuntimeError):
        service.apply(
            s, ACTOR, proj.id, stage_id, pv.fingerprint, service.Selection(), backup_dir=tmp_path
        )
    after = systems(sf, proj.id)
    assert {k: (v.affected, v.remark, v.version) for k, v in after.items()} == {
        k: (v.affected, v.remark, v.version) for k, v in before.items()
    }
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Backup)) == 0
        assert s.scalar(select(func.count()).select_from(ChangeLog)) == 0
        assert s.get(StagedUpload, stage_id) is not None
    assert not list(tmp_path.rglob("*.json")), "Backup-Dateien des Fehlversuchs entfernt"


def test_closed_project_rejects_apply(
    session_factory: sessionmaker[Session], proj: Any, tmp_path: Path
) -> None:
    from npt import projects
    from tests.project_helpers import ACTOR as PACTOR

    stage_id = stage_upload(
        session_factory, proj.id, upload_xlsx(["system_id", "affected"], ["APP-00001", "Ja"])
    )
    with session_factory() as s:
        projects.set_status(s, PACTOR, proj.id, "abgeschlossen")
        s.commit()
    with pytest.raises(editing.ReadOnlyError):
        apply_upload(session_factory, proj.id, stage_id, tmp_path)


def test_upload_reaches_clients_via_delta_and_notify(
    session_factory: sessionmaker[Session], proj: Any, tmp_path: Path, public_app_factory: Any
) -> None:
    editor = Editor(public_app_factory(), proj)
    cursor = editor.c.get(f"{proj.base}/api/systems").json()["seq"]
    stage_id = stage_upload(
        session_factory, proj.id, upload_xlsx(["system_id", "affected"], ["APP-00003", "Ja"])
    )
    dsn = DB_URL.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as listener:
        listener.execute("LISTEN npt_changes")
        res = apply_upload(session_factory, proj.id, stage_id, tmp_path)
        payloads = [json.loads(n.payload) for n in listener.notifies(timeout=2, stop_after=1)]
    assert payloads == [{"p": proj.id, "s": res.seq, "k": "upload"}]
    delta = editor.c.get(f"{proj.base}/api/systems", params={"updated_since": cursor}).json()
    assert [r["system_id"] for r in delta["rows"]] == ["APP-00003"]
    assert delta["rows"][0]["affected"] == "ja"
