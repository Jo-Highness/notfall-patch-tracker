"""Datenbank-Migration beim Start unter PostgreSQL Advisory Lock (NPT-02, NPT-12 K2).

Mehrere gleichzeitig startende Container/Pods migrieren so genau einmal; die anderen
warten auf den Lock (``pg_try_advisory_lock``-Schleife mit Zeitbudget, unabhaengig vom
``lock_timeout`` der Verbindung) und finden danach ``head`` vor. Der Admin-Bootstrap
laeuft im selben Lock.

Kennt das Abbild die Revision der Datenbank nicht (Datenbank neuer, z. B. nach
``helm rollback``), entscheidet ``schema_newer``: ``fail`` bricht ab (Default, Compose),
``warn`` startet ohne Migration und ohne Bootstrap (Migrationen expand/contract,
N-1-kompatibel).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import OperationalError

from npt import limits

log = logging.getLogger("npt.migrate")

# Fester Schluessel fuer den Migrations-Lock (beliebig, aber projektweit eindeutig).
MIGRATION_LOCK_KEY = 0x4E50_5401

SchemaNewer = Literal["fail", "warn"]
Bootstrap = Callable[[Connection], None]


class SchemaNewerError(RuntimeError):
    """Datenbank steht auf einer Revision, die dieses Abbild nicht kennt."""


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "npt:migrations")
    return cfg


def wait_for_db(engine: Engine, timeout_s: float = limits.MIGRATION_DB_WAIT_S) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            if time.monotonic() >= deadline:
                raise
            log.info("warte auf Datenbank")
            time.sleep(1.0)


def acquire_lock(
    conn: Connection,
    wait_s: float = limits.MIGRATION_LOCK_WAIT_S,
    poll_s: float = limits.MIGRATION_LOCK_POLL_S,
) -> None:
    """Session-Advisory-Lock per ``pg_try_advisory_lock``-Schleife (kein lock_timeout)."""
    deadline = time.monotonic() + wait_s
    logged = False
    while True:
        got = conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY}
        ).scalar()
        conn.commit()
        if got:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Migrations-Lock nach {wait_s:.0f} s nicht erhalten")
        if not logged:
            log.info("Migration laeuft in anderem Prozess, warte auf Lock")
            logged = True
        time.sleep(poll_s)


def unknown_revisions(conn: Connection, cfg: Config) -> set[str]:
    """Revisionen der Datenbank, die das Abbild nicht kennt (leer = alles bekannt)."""
    current = set(MigrationContext.configure(conn).get_current_heads())
    known = {rev.revision for rev in ScriptDirectory.from_config(cfg).walk_revisions()}
    return current - known


def upgrade_head(
    engine: Engine,
    *,
    bootstrap: Bootstrap | None = None,
    schema_newer: SchemaNewer = "fail",
    lock_wait_s: float = limits.MIGRATION_LOCK_WAIT_S,
) -> bool:
    """``alembic upgrade head`` und optional Bootstrap unter Session-Advisory-Lock.

    Liefert False, wenn wegen unbekannter (neuerer) Revision und ``schema_newer=warn``
    weder migriert noch gebootstrappt wurde.
    """
    with engine.connect() as conn:
        acquire_lock(conn, lock_wait_s)
        try:
            cfg = alembic_config()
            cfg.attributes["connection"] = conn
            unknown = unknown_revisions(conn, cfg)
            conn.commit()
            if unknown:
                if schema_newer != "warn":
                    raise SchemaNewerError(
                        f"Datenbank-Revision {sorted(unknown)} ist diesem Abbild unbekannt "
                        "(Datenbank neuer als die App); NPT_SCHEMA_NEWER=warn erlaubt den Start"
                    )
                log.warning(
                    "Datenbank-Revision unbekannt (neuer als die App), starte ohne Migration "
                    "und Bootstrap",
                    extra={"revision": sorted(unknown)},
                )
                return False
            command.upgrade(cfg, "head")
            conn.commit()
            log.info("Migration abgeschlossen")
            if bootstrap is not None:
                bootstrap(conn)
                conn.commit()
            return True
        finally:
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
            conn.commit()


def downgrade_base(engine: Engine) -> None:
    """Nur fuer Tests."""
    with engine.connect() as conn:
        cfg = alembic_config()
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
        conn.commit()
