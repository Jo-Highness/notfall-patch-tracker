"""Backups (Auftrag 6.2, NPT-05): Scheduler, Advisory Lock, Aufbewahrung, Restore."""

from __future__ import annotations

import io
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from npt import backups, editing, projects, restore
from npt.admins import Actor
from npt.db.models import Backup, ChangeLog, Project, ProjectSystem
from npt.project_upload import service as upload_service
from npt.project_upload.parse import parse_upload
from tests.conftest import DB_URL
from tests.project_helpers import ACTOR as PACTOR
from tests.project_helpers import Editor, make_project, new_system_body, seed_master
from tests.test_project_upload import set_field, upload_xlsx

ACTOR = Actor("issuer", "cn=backup admin", "192.0.2.30")
LATER = datetime(2100, 1, 1, tzinfo=UTC)


@pytest.fixture
def proj(session_factory: sessionmaker[Session]) -> Any:
    seed_master(session_factory, 5)
    return make_project(session_factory)


def count_backups(sf: sessionmaker[Session], trigger: str | None = None) -> int:
    with sf() as s:
        stmt = select(func.count()).select_from(Backup)
        if trigger:
            stmt = stmt.where(Backup.trigger == trigger)
        return int(s.scalar(stmt) or 0)


def project_row(sf: sessionmaker[Session], pid: int) -> Project:
    with sf() as s:
        p = s.get(Project, pid)
        assert p is not None
        return p


def state(sf: sessionmaker[Session], pid: int) -> dict[str, tuple[Any, ...]]:
    cols = [getattr(ProjectSystem, c) for c in restore.RESTORE_FIELDS]
    with sf() as s:
        rows = s.execute(
            select(ProjectSystem.system_id, *cols).where(ProjectSystem.project_id == pid)
        ).all()
    return {r[0]: tuple(r[1:]) for r in rows}


# ---------------------------------------------------------------- Erzeugung
def test_backup_files_atomic_complete_without_token(
    session_factory: sessionmaker[Session], proj: Any, tmp_path: Path
) -> None:
    set_field(session_factory, proj.id, "APP-00001", "remark", "=1+1")
    with session_factory() as s:
        created, obsolete = backups.manual_backup(s, ACTOR, proj.id, backup_dir=tmp_path)
        s.commit()
    assert obsolete == []
    b = created.backup
    assert b.trigger == "manuell"
    assert b.created_by == ACTOR.identifier
    files = sorted(p.name for p in (tmp_path / str(proj.id)).iterdir())
    assert len(files) == 2
    assert not [f for f in files if f.startswith(".")], "keine tmp-Dateien uebrig"
    raw = (tmp_path / b.json_path).read_text()
    assert proj.token not in raw
    assert "access_token" not in raw
    assert "token_generation" not in raw
    doc = json.loads(raw)
    assert doc["format"] == backups.FORMAT
    assert doc["project"]["change_seq"] == b.change_seq
    assert len(doc["systems"]) == 5
    assert set(doc["systems"][0]) == set(backups.SYSTEM_COLUMNS)
    ws = load_workbook(io.BytesIO((tmp_path / str(b.xlsx_path)).read_bytes())).active
    assert ws is not None
    remark = [c for row in ws.iter_rows(min_row=2) for c in row if c.value == "=1+1"]
    assert remark
    assert remark[0].data_type == "s"
    assert b.size_bytes == sum(
        (tmp_path / str(p)).stat().st_size for p in (b.json_path, b.xlsx_path)
    )
    p = project_row(session_factory, proj.id)
    assert p.last_backup_seq == b.change_seq
    assert p.last_backup_at is not None


def test_resolve_path_rejects_traversal(tmp_path: Path) -> None:
    (tmp_path / "1").mkdir()
    assert backups.resolve_path(tmp_path, 1, "1/x.json") == (tmp_path / "1" / "x.json").resolve()
    for bad in ("../etc/passwd", "1/../2/x.json", "2/x.json", "/etc/passwd", "1", "1/../../x"):
        with pytest.raises(backups.BackupError):
            backups.resolve_path(tmp_path, 1, bad)


def test_atomic_write_leaves_no_tmp_on_error(tmp_path: Path, monkeypatch) -> None:
    def fail(*_a: Any) -> None:
        raise OSError("Platte voll")

    monkeypatch.setattr("npt.backups.os.replace", fail)
    with pytest.raises(OSError, match="Platte"):
        backups.atomic_write(tmp_path / "a.json", b"{}")
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- Scheduler
def test_scheduler_only_on_change_and_after_interval(
    session_factory: sessionmaker[Session], proj: Any, tmp_path: Path
) -> None:
    sf = session_factory
    # Neues Projekt ohne Bearbeitung: Snapshot ohne Aenderung -> kein Backup
    assert backups.run_due(sf, tmp_path, LATER) == []
    set_field(sf, proj.id, "APP-00001", "affected", "ja")
    created = backups.run_due(sf, tmp_path, LATER)
    assert len(created) == 1
    assert count_backups(sf, "zeitgesteuert") == 1
    # keine weitere Aenderung -> kein weiteres Backup, auch nach Ablauf des Intervalls
    assert backups.run_due(sf, tmp_path, LATER + timedelta(days=1)) == []
    # Aenderung, aber Intervall (60 min) noch nicht um
    set_field(sf, proj.id, "APP-00002", "affected", "nein")
    last = project_row(sf, proj.id).last_backup_at
    assert last is not None
    assert backups.run_due(sf, tmp_path, last + timedelta(minutes=59)) == []
    assert len(backups.run_due(sf, tmp_path, last + timedelta(minutes=60))) == 1
    # Status-/Token-Wechsel sind keine Datenaenderung
    with sf() as s:
        projects.regenerate_token(s, PACTOR, proj.id)
        projects.set_status(s, PACTOR, proj.id, "abgeschlossen")
        s.commit()
    assert backups.run_due(sf, tmp_path, LATER + timedelta(days=2)) == []
    # Intervall „aus"
    with sf() as s:
        projects.set_status(s, PACTOR, proj.id, "aktiv")
        s.execute(update(Project).values(backup_interval_min=0))
        s.commit()
    set_field(sf, proj.id, "APP-00003", "affected", "ja")
    assert backups.run_due(sf, tmp_path, LATER + timedelta(days=3)) == []
    assert count_backups(sf) == 2


def test_advisory_lock_held_elsewhere_skips(
    session_factory: sessionmaker[Session], proj: Any, tmp_path: Path
) -> None:
    set_field(session_factory, proj.id, "APP-00001", "affected", "ja")
    dsn = DB_URL.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as other:  # andere Instanz haelt die Sperre (offene Transaktion)
        other.execute("SELECT pg_advisory_xact_lock(%s)", (backups.lock_key(proj.id),))
        assert backups.run_due(session_factory, tmp_path, LATER) == []
    assert len(backups.run_due(session_factory, tmp_path, LATER)) == 1


def test_run_due_reports_progress_per_project(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    """progress (Heartbeat des jobs-Prozesses) kommt vor jedem Projekt, nicht nur je Lauf."""
    sf = session_factory
    seed_master(sf, 300)
    projs = [make_project(sf, f"P{i}") for i in range(3)]
    for p in projs:
        set_field(sf, p.id, "APP-00001", "affected", "ja")
    calls: list[int] = []

    def progress() -> None:
        with sf() as s:
            calls.append(int(s.scalar(select(func.count()).select_from(Backup)) or 0))

    assert len(backups.run_due(sf, tmp_path, LATER, progress=progress)) == 3
    # Je Projekt ein Aufruf, jeweils nach dem vorigen Backup.
    assert calls == [0, 1, 2]
    # Nichts faellig -> kein Aufruf (Schleife liefert sofort zurueck).
    calls.clear()
    assert backups.run_due(sf, tmp_path, LATER, progress=progress) == []
    assert calls == []


def test_parallel_ticks_back_up_exactly_once(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    sf = session_factory
    seed_master(sf, 300)
    projs = [make_project(sf, f"P{i}") for i in range(3)]
    for p in projs:
        set_field(sf, p.id, "APP-00001", "affected", "ja")
    barrier = threading.Barrier(6)
    results: list[list[int]] = []
    errors: list[BaseException] = []

    def tick() -> None:
        try:
            barrier.wait(5)
            results.append(backups.run_due(sf, tmp_path, LATER))
        except BaseException as exc:  # pragma: no cover - Fehler werden gemeldet
            errors.append(exc)

    threads = [threading.Thread(target=tick) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sum(len(r) for r in results) == 3
    with sf() as s:
        per_project = dict(
            s.execute(select(Backup.project_id, func.count()).group_by(Backup.project_id)).all()
        )
    assert per_project == {p.id: 1 for p in projs}
    assert len(list(tmp_path.rglob("*.json"))) == 3


def test_retention_keeps_newest_n(
    session_factory: sessionmaker[Session], proj: Any, tmp_path: Path
) -> None:
    sf = session_factory
    with sf() as s:
        obsolete = backups.update_settings(s, ACTOR, proj.id, 15, 3, backup_dir=tmp_path)
        s.commit()
    assert obsolete == []
    ids = []
    for _ in range(5):
        with sf() as s:
            created, obsolete = backups.manual_backup(s, ACTOR, proj.id, backup_dir=tmp_path)
            s.commit()
        backups.remove_files(obsolete)
        ids.append(created.backup.id)
    with sf() as s:
        kept = [b.id for b in backups.list_backups(s, proj.id)]
    assert kept == ids[:-4:-1]
    assert len(list((tmp_path / str(proj.id)).iterdir())) == 6
    # Aufbewahrung verkleinern wirkt sofort
    with sf() as s:
        obsolete = backups.update_settings(s, ACTOR, proj.id, 15, 1, backup_dir=tmp_path)
        s.commit()
    backups.remove_files(obsolete)
    assert count_backups(sf) == 1
    assert len(list((tmp_path / str(proj.id)).iterdir())) == 2
    with sf() as s, pytest.raises(backups.BackupError):
        backups.update_settings(s, ACTOR, proj.id, 7, 3, backup_dir=tmp_path)
    with sf() as s, pytest.raises(backups.BackupError):
        backups.update_settings(s, ACTOR, proj.id, 15, 0, backup_dir=tmp_path)


# ---------------------------------------------------------------- Restore
def test_roundtrip_backup_restore_with_manual_systems(
    session_factory: sessionmaker[Session],
    proj: Any,
    tmp_path: Path,
    public_app_factory: Any,
) -> None:
    sf = session_factory
    editor = Editor(public_app_factory(), proj)
    set_field(sf, proj.id, "APP-00001", "affected", "ja")
    set_field(sf, proj.id, "APP-00002", "remark", "vorher")
    r = editor.create(**new_system_body(system_name="Manuell 1", affected="ja"))
    assert r.status_code == 201, r.text
    manual_id = r.json()["row"]["system_id"]
    with sf() as s:
        created, _ = backups.manual_backup(s, ACTOR, proj.id, backup_dir=tmp_path)
        s.commit()
    snapshot = state(sf, proj.id)
    assert manual_id in snapshot

    # Danach: Aenderungen, ein weiteres manuelles System, ein Upload mit Neuanlage,
    # das manuelle System aus dem Backup wird (per SQL) entfernt
    set_field(sf, proj.id, "APP-00001", "affected", "nein")
    set_field(sf, proj.id, "APP-00003", "installed_version", "9.9")
    r = editor.create(**new_system_body(system_name="Manuell 2"))
    later_manual = r.json()["row"]["system_id"]
    with sf() as s:
        stage_id = upload_service.stage(
            s,
            proj.id,
            parse_upload(upload_xlsx(["system_id", "system_name"], ["UP-1", "Upload neu"])),
            created_by="t",
            file_name="u.xlsx",
            file_hash="0" * 64,
        )
        pv = upload_service.preview(s, proj.id, stage_id)
        upload_service.apply(
            s,
            ACTOR,
            proj.id,
            stage_id,
            pv.fingerprint,
            upload_service.Selection(create=frozenset({"UP-1"})),
            backup_dir=tmp_path,
        )
        s.execute(text("DELETE FROM project_systems WHERE system_id = :s"), {"s": manual_id})
        s.commit()
    before_restore = state(sf, proj.id)
    assert before_restore != snapshot
    cursor = editor.c.get(f"{proj.base}/api/systems").json()["seq"]

    dsn = DB_URL.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as listener:
        listener.execute("LISTEN npt_changes")
        with sf() as s:
            result = restore.restore(s, ACTOR, proj.id, created.backup.id, backup_dir=tmp_path)
            s.commit()
        notes = [json.loads(n.payload) for n in listener.notifies(timeout=2, stop_after=1)]
    assert notes == [{"p": proj.id, "s": result.seq, "k": "reload"}]
    assert state(sf, proj.id) == snapshot
    assert (result.created, result.deleted) == (1, 2)  # manual_id zurueck; UP-1, Manuell 2 weg
    p = project_row(sf, proj.id)
    assert p.reset_seq == p.change_seq == result.seq
    with sf() as s:
        before = s.get(Backup, result.before_backup_id)
        assert before is not None
        assert before.trigger == "vor_restore"
        assert before.change_seq == result.seq - 1
        doc = json.loads((tmp_path / before.json_path).read_text())
        assert {x["system_id"] for x in doc["systems"]} == set(before_restore)
        logs = s.scalars(select(ChangeLog).where(ChangeLog.source == "restore")).all()
    actions = {(e.system_id, e.action) for e in logs}
    assert (manual_id, "angelegt") in actions
    assert (later_manual, "geloescht") in actions
    assert ("UP-1", "geloescht") in actions
    assert ("APP-00001", "geaendert") in actions
    assert {e.admin_identifier for e in logs} == {ACTOR.identifier}
    assert {e.seq for e in logs} == {result.seq}
    # Clients mit altem Cursor bekommen full_reload
    delta = editor.c.get(f"{proj.base}/api/systems", params={"updated_since": cursor}).json()
    assert delta["full_reload"] is True
    assert {r["system_id"] for r in delta["rows"]} == set(snapshot)
    # Restore des vor_restore-Backups stellt den vorherigen Stand wieder her
    with sf() as s:
        restore.restore(s, ACTOR, proj.id, result.before_backup_id, backup_dir=tmp_path)
        s.commit()
    assert state(sf, proj.id) == before_restore


def test_restore_rejects_invalid_file_and_closed_project(
    session_factory: sessionmaker[Session], proj: Any, tmp_path: Path
) -> None:
    sf = session_factory
    with sf() as s:
        created, _ = backups.manual_backup(s, ACTOR, proj.id, backup_dir=tmp_path)
        s.commit()
    path = tmp_path / created.backup.json_path
    doc = json.loads(path.read_text())
    doc["systems"][0]["affected"] = "kaputt"
    path.write_text(json.dumps(doc))
    seq_before = project_row(sf, proj.id).change_seq
    with sf() as s, pytest.raises(restore.RestoreError):
        restore.restore(s, ACTOR, proj.id, created.backup.id, backup_dir=tmp_path)
    assert project_row(sf, proj.id).change_seq == seq_before
    assert count_backups(sf) == 1  # kein vor_restore
    path.write_text("{kein json")
    with sf() as s, pytest.raises(restore.RestoreError):
        restore.restore(s, ACTOR, proj.id, created.backup.id, backup_dir=tmp_path)
    other = make_project(sf, "anderes")
    with sf() as s, pytest.raises(backups.BackupError):
        restore.restore(s, ACTOR, other.id, created.backup.id, backup_dir=tmp_path)
    with sf() as s:
        projects.set_status(s, PACTOR, proj.id, "abgeschlossen")
        s.commit()
    with sf() as s, pytest.raises(editing.ReadOnlyError):
        restore.restore(s, ACTOR, proj.id, created.backup.id, backup_dir=tmp_path)


def test_jobs_registry_contains_backup_job(tmp_path: Path) -> None:
    from npt.jobs import backups as backup_job
    from tests.conftest import make_settings

    job = backup_job.make_job(make_settings(BACKUP_DIR=str(tmp_path)), lambda: None)  # type: ignore[arg-type,return-value]
    assert callable(job)
