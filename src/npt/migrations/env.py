"""Alembic-Umgebung.

Wird ueber ``npt.migrate`` mit einer bereits geoeffneten Verbindung (unter
Advisory Lock) aufgerufen; ohne Verbindung (CLI) wird eine eigene geoeffnet.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from npt.db.models import Base

config = context.config
target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    from npt.db.session import make_engine

    engine = make_engine(pool_size=1)
    try:
        with engine.connect() as conn:
            _run(conn)
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline-Migrationen werden nicht unterstuetzt")
run_migrations_online()
