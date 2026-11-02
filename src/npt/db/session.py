"""Engine und Sessions (synchron, psycopg 3).

Jeder Worker-Prozess erzeugt seine Engine lazily (nach dem Fork von Gunicorn).
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

import psycopg
from sqlalchemy import Engine, create_engine
from sqlalchemy import exc as sa_exc
from sqlalchemy.orm import Session, sessionmaker

from npt import limits
from npt.config import get_settings

# Datenbank nicht erreichbar bzw. Verbindung abgebrochen (Verbindungsaufbau, Abbruch
# mitten in der Abfrage, Statement-Timeout ausserhalb des Schreibpfads). Web: 503 mit
# Retry-After (``npt.web.common.register_db_unavailable``), Jobs: Warnung ohne Traceback.
# Andere DB-Fehler (Integritaet, Programmierfehler) zaehlen nicht dazu.
DB_UNAVAILABLE_ERRORS: tuple[type[Exception], ...] = (
    sa_exc.OperationalError,
    sa_exc.InterfaceError,
    psycopg.OperationalError,
    psycopg.InterfaceError,
)


def _connect_args() -> dict[str, object]:
    opts = (
        f"-c lock_timeout={limits.DB_LOCK_TIMEOUT_MS} "
        f"-c statement_timeout={limits.DB_STATEMENT_TIMEOUT_MS} "
        "-c timezone=UTC"
    )
    return {"options": opts, "connect_timeout": limits.DB_CONNECT_TIMEOUT_S}


def make_engine(url: str | None = None, *, pool_size: int | None = None) -> Engine:
    return create_engine(
        url or get_settings().sqlalchemy_url,
        pool_size=pool_size if pool_size is not None else limits.DB_POOL_SIZE,
        max_overflow=limits.DB_MAX_OVERFLOW if pool_size is None else 0,
        pool_timeout=limits.DB_POOL_TIMEOUT_S,
        pool_pre_ping=True,
        pool_recycle=1800,
        # Gebundene Parameter (z. B. Projekt-Token) nie in Ausnahmetexte/Logs (8.2).
        hide_parameters=True,
        connect_args=_connect_args(),
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return make_engine()


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


def new_session() -> Session:
    return _session_factory()()


def get_session() -> Iterator[Session]:
    """FastAPI-Dependency: Session je Request; Abschluss immer im finally."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()
