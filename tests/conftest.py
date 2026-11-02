"""Gemeinsame Test-Fixtures.

DB-Tests laufen nur mit ``NPT_TEST_DATABASE_URL`` (Wegwerf-Postgres, siehe
docker-compose.test.yml); ohne die Variable werden sie uebersprungen.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from npt.config import Settings
from npt.identity import sha1_fingerprint
from tests.certs import CertFactory

GATEWAY_IP = "10.99.0.2"
OTHER_IP = "10.99.0.77"
ADMIN_ORIGIN = "https://admin.npt.test"
CSRF_SECRET = "x" * 48
DB_URL = os.environ.get("NPT_TEST_DATABASE_URL", "")


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "CSRF_SECRET": CSRF_SECRET,
        "ADMIN_GATEWAY_CIDR": f"{GATEWAY_IP}/32",
        "ADMIN_BASE_URL": ADMIN_ORIGIN,
        "DATABASE_URL": DB_URL or "postgresql+psycopg://u:p@127.0.0.1:1/none",
        "ADMIN_CERT_ID_ATTRIBUTE": "subject_dn",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(scope="session")
def certs() -> CertFactory:
    return CertFactory()


def gateway_headers(
    cert_pem: str, *, verify: str = "SUCCESS", fingerprint: str | None = None, cert_obj: Any = None
) -> dict[str, str]:
    from cryptography import x509

    cert = cert_obj or x509.load_pem_x509_certificate(cert_pem.encode())
    return {
        "X-SSL-Client-Verify": verify,
        "X-SSL-Client-Cert": quote(cert_pem, safe=""),
        "X-SSL-Client-Fingerprint": fingerprint or sha1_fingerprint(cert),
        "X-Forwarded-For": "192.0.2.10",
        "Origin": ADMIN_ORIGIN,
    }


def with_peer(app: Any, ip: str) -> Any:
    """ASGI-Wrapper: setzt die direkte Absender-IP (wie der Socket-Peer hinter Uvicorn)."""

    async def wrapped(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["client"] = (ip, 50000)
        await app(scope, receive, send)

    return wrapped


# ---------------------------------------------------------------- Datenbank
@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    if not DB_URL:
        pytest.skip("NPT_TEST_DATABASE_URL nicht gesetzt")
    from npt.db.session import make_engine
    from npt.migrate import upgrade_head

    engine = make_engine(DB_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    upgrade_head(engine)
    yield engine
    engine.dispose()


TRUNCATE_TABLES = (
    "admin_users, admin_access_requests, admin_audit_log, change_log, mail_deliveries, "
    "mail_runs, mail_campaigns, backups, staged_uploads, project_systems, projects, "
    "master_systems, master_imports, rate_limit_buckets, health_probe"
)


@pytest.fixture
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    with db_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TRUNCATE_TABLES} RESTART IDENTITY CASCADE"))
    return sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
def admin_app_factory(
    session_factory: sessionmaker[Session],
) -> Callable[..., TestClient]:
    from npt.web.admin import create_admin_app

    def factory(peer: str = GATEWAY_IP, **settings_overrides: Any) -> TestClient:
        app = create_admin_app(
            make_settings(**settings_overrides),
            session_factory=session_factory,
            enable_monitor=False,
        )
        return TestClient(with_peer(app, peer), base_url=ADMIN_ORIGIN, follow_redirects=False)

    return factory


PUBLIC_ORIGIN = "http://npt.test"


@pytest.fixture
def public_app_factory(
    session_factory: sessionmaker[Session],
) -> Callable[..., TestClient]:
    from npt.web.public import create_public_app

    def factory(peer: str = OTHER_IP, **settings_overrides: Any) -> TestClient:
        settings_overrides.setdefault("PUBLIC_BASE_URL", PUBLIC_ORIGIN)
        app = create_public_app(
            make_settings(**settings_overrides),
            session_factory=session_factory,
            enable_monitor=False,
        )
        return TestClient(with_peer(app, peer), base_url=PUBLIC_ORIGIN, follow_redirects=False)

    return factory
