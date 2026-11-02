"""ID-Vergabe NEW-xxxx unter paralleler Last (Auftrag 5.6/10)."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from npt import editing
from npt.db.models import ChangeLog, ProjectSystem
from npt.db.session import make_engine
from npt.domain.fields import format_new_id
from tests.conftest import DB_URL
from tests.project_helpers import make_project, new_system_body, seed_master

PARALLEL = 200
WORKERS = 20


def test_format_new_id() -> None:
    assert format_new_id(1) == "NEW-0001"
    assert format_new_id(42) == "NEW-0042"
    assert format_new_id(9999) == "NEW-9999"
    assert format_new_id(10000) == "NEW-10000"  # ohne Kuerzung weiter
    assert format_new_id(123456) == "NEW-123456"


def test_parallel_creation_has_no_duplicates(session_factory: sessionmaker[Session]) -> None:
    seed_master(session_factory, 1)
    projects = [make_project(session_factory, f"P{i}") for i in range(2)]
    # eigene Engine mit genug Verbindungen: jede Anlage auf einer eigenen Verbindung/Transaktion
    engine = make_engine(DB_URL, pool_size=WORKERS)
    sf = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    def create(i: int) -> str:
        proj = projects[i % len(projects)]
        for _attempt in range(5):
            with sf() as s:
                try:
                    row = editing.create_system(
                        s,
                        proj.id,
                        new_system_body(system_name=f"Parallel {i}"),
                        client_ip="10.0.0.1",
                        client_name=None,
                    )
                    s.commit()
                    return str(row["system_id"])
                except editing.BusyError:
                    s.rollback()
        raise AssertionError("dauerhaft gesperrt")

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            ids = list(pool.map(create, range(PARALLEL)))
    finally:
        engine.dispose()
    assert len(ids) == PARALLEL
    assert len(set(ids)) == PARALLEL
    assert all(re.fullmatch(r"NEW-\d{4,}", i) for i in ids)
    with session_factory() as s:
        manual = s.scalar(
            select(func.count())
            .select_from(ProjectSystem)
            .where(ProjectSystem.origin == "manuell_angelegt")
        )
        distinct = s.scalar(
            select(func.count(func.distinct(ProjectSystem.system_id))).where(
                ProjectSystem.origin == "manuell_angelegt"
            )
        )
        created_logs = s.scalar(
            select(func.count()).select_from(ChangeLog).where(ChangeLog.action == "angelegt")
        )
    assert manual == distinct == created_logs == PARALLEL


def test_ids_are_global_across_projects(session_factory: sessionmaker[Session]) -> None:
    seed_master(session_factory, 1)
    a, b = make_project(session_factory, "A"), make_project(session_factory, "B")
    got = []
    for proj in (a, b, a):
        with session_factory() as s:
            got.append(
                editing.create_system(
                    s, proj.id, new_system_body(), client_ip=None, client_name=None
                )["system_id"]
            )
            s.commit()
    nums = [int(x.split("-")[1]) for x in got]
    assert nums == sorted(nums)
    assert len(set(nums)) == 3
