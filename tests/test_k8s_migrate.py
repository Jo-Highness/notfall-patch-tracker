"""Migration fuer mehrere gleichzeitig startende Pods (NPT-12 K2, C3) und Aufraeumen (C6)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command as alembic_command
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session, sessionmaker

from npt import admins, migrate
from npt.jobs import housekeeping

BOOTSTRAP = ["CN=Kube Admin,O=Beispiel", "CN=Zweiter Admin,O=Beispiel"]


def _head() -> str:
    head = ScriptDirectory.from_config(migrate.alembic_config()).get_current_head()
    assert head is not None
    return head


@pytest.fixture
def empty_db(db_engine: Engine) -> Iterator[Engine]:
    """Leere Datenbank (base); danach wieder head fuer die uebrigen Tests."""
    migrate.downgrade_base(db_engine)
    yield db_engine
    migrate.upgrade_head(db_engine)
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE admin_users, admin_audit_log RESTART IDENTITY CASCADE"))


def _bootstrap(created: list[int]) -> migrate.Bootstrap:
    def run(conn: Connection) -> None:
        with Session(bind=conn) as session:
            created.append(admins.bootstrap_admins(session, "subject_dn", BOOTSTRAP))
            session.commit()

    return run


def test_concurrent_first_migration_with_bootstrap(
    empty_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zwei gleichzeitige Starts auf leerer DB: genau eine Migration, Bootstrap im Lock."""
    upgrades: list[tuple[set[str], float, float]] = []
    real_upgrade = alembic_command.upgrade

    def slow_upgrade(cfg: Any, rev: str) -> None:
        from alembic.migration import MigrationContext

        conn = cfg.attributes["connection"]
        before = set(MigrationContext.configure(conn).get_current_heads())
        started = time.monotonic()
        real_upgrade(cfg, rev)
        # Laenger als lock_timeout (2 s): der alte pg_advisory_lock-Pfad brach hier ab.
        time.sleep(2.5)
        upgrades.append((before, started, time.monotonic()))

    monkeypatch.setattr(alembic_command, "upgrade", slow_upgrade)
    created: list[int] = []
    errors: list[BaseException] = []

    def start() -> None:
        try:
            assert migrate.upgrade_head(empty_db, bootstrap=_bootstrap(created), lock_wait_s=30)
        except BaseException as exc:  # pragma: no cover - Fehlerfall wird gemeldet
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(upgrades) == 2
    # Genau ein Lauf fand die leere DB vor, der zweite bereits head.
    assert sorted(len(before) for before, _, _ in upgrades) == [0, 1]
    assert [before for before, _, _ in upgrades if before] == [{_head()}]
    # Serialisiert: die Zeitfenster ueberlappen nicht.
    (_, _s1, e1), (_, s2, _e2) = sorted(upgrades, key=lambda u: u[1])
    assert s2 >= e1
    # Bootstrap: beide Laeufe im Lock, zusammen genau zwei neue Admins, keine Dubletten.
    assert sorted(created) == [0, 2]
    with empty_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM admin_users")).scalar() == 2


def test_lock_wait_budget(db_engine: Engine) -> None:
    holder = db_engine.connect()
    try:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": migrate.MIGRATION_LOCK_KEY})
        holder.commit()
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            migrate.upgrade_head(db_engine, lock_wait_s=1.5)
        assert time.monotonic() - started >= 1.5

        def release() -> None:
            time.sleep(3.0)
            holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": migrate.MIGRATION_LOCK_KEY})
            holder.commit()

        t = threading.Thread(target=release)
        t.start()
        # Wartet laenger als lock_timeout (2 s) und bekommt den Lock danach.
        assert migrate.upgrade_head(db_engine, lock_wait_s=20) is True
        t.join()
    finally:
        holder.close()


@pytest.fixture
def future_revision(db_engine: Engine) -> Iterator[Engine]:
    with db_engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'ffff_aus_der_zukunft'"))
    yield db_engine
    with db_engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = :h"), {"h": _head()})


def test_schema_newer_fail(future_revision: Engine) -> None:
    called: list[int] = []
    with pytest.raises(migrate.SchemaNewerError, match="ffff_aus_der_zukunft"):
        migrate.upgrade_head(future_revision, bootstrap=_bootstrap(called))
    assert called == []


def test_schema_newer_warn_starts_without_migration(future_revision: Engine) -> None:
    called: list[int] = []
    started = migrate.upgrade_head(
        future_revision, bootstrap=_bootstrap(called), schema_newer="warn"
    )
    assert started is False
    assert called == []
    with future_revision.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
            "ffff_aus_der_zukunft"
        )
        # Lock wieder frei
        assert conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": migrate.MIGRATION_LOCK_KEY}
        ).scalar()
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": migrate.MIGRATION_LOCK_KEY})


# ------------------------------------------------------------- C6 Aufraeumen
def test_stale_health_probes_deleted(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as s:
        s.execute(
            text(
                "INSERT INTO health_probe (worker_id, probed_at, latency_ms) VALUES "
                "('alt:web:1', now() - interval '25 hours', 1), "
                "('frisch:web:2', now() - interval '23 hours', 1), "
                "('jetzt:web:3', now(), 1)"
            )
        )
        s.commit()
    job = housekeeping.make_job(session_factory, interval_s=3600)
    job()
    with session_factory() as s:
        rows = s.execute(text("SELECT worker_id FROM health_probe ORDER BY 1")).scalars().all()
        assert rows == ["frisch:web:2", "jetzt:web:3"]
        s.execute(
            text(
                "INSERT INTO health_probe (worker_id, probed_at) "
                "VALUES ('alt2', now() - interval '2 days')"
            )
        )
        s.commit()
    # Intervall noch nicht abgelaufen: kein zweiter Lauf.
    job()
    with session_factory() as s:
        assert s.execute(text("SELECT count(*) FROM health_probe")).scalar() == 3
        assert housekeeping.delete_stale_probes(s) == 1
        s.commit()
