"""Alembic-Initialmigration auf leerer DB (Auftrag 9, NPT-10)."""

from __future__ import annotations

import threading

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text

from npt.db.models import Base
from npt.migrate import downgrade_base, upgrade_head

EXPECTED_TABLES = {
    "master_systems",
    "master_imports",
    "projects",
    "project_systems",
    "change_log",
    "backups",
    "mail_campaigns",
    "mail_runs",
    "mail_deliveries",
    "admin_users",
    "admin_access_requests",
    "admin_audit_log",
    "staged_uploads",
    "rate_limit_buckets",
    "health_probe",
    "alembic_version",
}


def test_migration_runs_on_empty_db_and_matches_models(db_engine: Engine) -> None:
    downgrade_base(db_engine)
    with db_engine.connect() as conn:
        assert set(inspect(conn).get_table_names()) <= {"alembic_version"}
        assert conn.execute(text("SELECT to_regclass('new_system_seq')")).scalar() is None
    upgrade_head(db_engine)
    with db_engine.connect() as conn:
        assert set(inspect(conn).get_table_names()) == EXPECTED_TABLES
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == [], diff


def test_sequence_and_new_id_format(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        value = conn.execute(
            text("SELECT 'NEW-' || lpad(nextval('new_system_seq')::text, 4, '0')")
        ).scalar()
    assert value is not None
    assert value.startswith("NEW-")
    assert len(value) == 8


def test_rate_limit_table_is_unlogged(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        persistence = conn.execute(
            text("SELECT relpersistence FROM pg_class WHERE relname = 'rate_limit_buckets'")
        ).scalar()
    assert persistence == "u"


def test_check_constraints_enforced(db_engine: Engine) -> None:
    import pytest
    from sqlalchemy.exc import IntegrityError

    with db_engine.connect() as conn, pytest.raises(IntegrityError):
        conn.execute(text("INSERT INTO master_systems (system_id) VALUES ('NEW-0001')"))
    with db_engine.connect() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO projects (name, access_token, status) "
                "VALUES ('x', 'abcdefghijklmnopqrstuvwxyz', 'kaputt')"
            )
        )


def test_concurrent_upgrades_serialize(db_engine: Engine) -> None:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            upgrade_head(db_engine)
        except BaseException as exc:  # pragma: no cover - Fehlerfall wird gemeldet
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_reserved_prefix_check_is_exact(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        definition = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_master_systems_system_id_not_reserved'"
            )
        ).scalar()
    assert definition is not None
    assert "'NEW-%'" in definition
    assert "%%" not in definition
