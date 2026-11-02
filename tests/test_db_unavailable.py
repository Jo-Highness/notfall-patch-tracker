"""Datenbank nicht erreichbar (QA 3aeca611): 503 mit Retry-After statt 500, beide Apps.

Nachgestellt wird ``docker compose stop db``: Die Seite wurde mit laufender Datenbank
geladen (CSRF-Token vorhanden), danach zeigt die Session-Fabrik auf einen Port, an dem
niemand lauscht (Verbindungsaufbau scheitert wie bei gestoppter Datenbank). Der
TestClient wirft unbehandelte Ausnahmen weiter – ein Durchreichen bis zur
``ServerErrorMiddleware`` (500, „Exception in ASGI application") liesse den Test scheitern.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import exc as sa_exc
from sqlalchemy.orm import Session, sessionmaker

from npt import limits, live
from npt.db.session import make_engine
from tests.conftest import (
    ADMIN_ORIGIN,
    GATEWAY_IP,
    OTHER_IP,
    PUBLIC_ORIGIN,
    gateway_headers,
    make_settings,
    with_peer,
)
from tests.project_helpers import Editor, make_project, new_system_body, seed_master
from tests.test_security_hardening import assert_security_headers

DEAD_URL = "postgresql+psycopg://npt:x@127.0.0.1:1/npt"
RETRY = str(limits.DB_UNAVAILABLE_RETRY_AFTER_S)


class Switch:
    """Session-Fabrik, die sich zur Laufzeit auf eine tote Datenbank umstellen laesst."""

    def __init__(self, live_sf: sessionmaker[Session]) -> None:
        self._live = live_sf
        self._dead = sessionmaker(bind=make_engine(DEAD_URL, pool_size=1))
        self.down = False

    def __call__(self) -> Session:
        return (self._dead if self.down else self._live)()

    def dispose(self) -> None:
        bind = self._dead.kw["bind"]
        bind.dispose()


@pytest.fixture
def switch(session_factory: sessionmaker[Session]) -> Iterator[Switch]:
    sw = Switch(session_factory)
    yield sw
    sw.dispose()


def _assert_db_unavailable_logged(caplog: pytest.LogCaptureFixture) -> None:
    db_records = [r for r in caplog.records if r.name == "npt.db"]
    assert db_records, "Warnung npt.db fehlt"
    for rec in caplog.records:
        assert rec.exc_info is None, (rec.name, rec.getMessage())  # keine Tracebacks
        assert "Exception in ASGI application" not in rec.getMessage()


def test_public_db_down_returns_503_with_retry_after(
    session_factory: sessionmaker[Session], switch: Switch, caplog: pytest.LogCaptureFixture
) -> None:
    from npt.web.public import create_public_app

    seed_master(session_factory, 2)
    proj = make_project(session_factory)
    app = create_public_app(
        make_settings(PUBLIC_BASE_URL=PUBLIC_ORIGIN),
        session_factory=switch,
        enable_monitor=False,
        enable_live=False,
    )
    c = TestClient(with_peer(app, OTHER_IP), base_url=PUBLIC_ORIGIN, follow_redirects=False)
    ed = Editor(c, proj)  # Seite mit laufender Datenbank geladen
    switch.down = True
    caplog.set_level(logging.WARNING)

    api_calls: list[Callable[[], Any]] = [
        lambda: c.get(f"{proj.base}/api/systems"),
        lambda: c.get(f"{proj.base}/api/systems?updated_since=0"),
        lambda: c.get(f"{proj.base}/api/pivot"),
        lambda: ed.patch("APP-00001", "affected", "ja"),
        lambda: c.post(f"{proj.base}/api/systems", json=new_system_body(), headers=ed.headers()),
    ]
    for call in api_calls:
        r = call()
        assert r.status_code == 503, (r.request.url, r.text)
        assert r.headers["retry-after"] == RETRY
        assert r.headers["content-type"].startswith("application/json")
        assert r.json() == {"reason": "datenbank"}
        assert r.headers["cache-control"] == "no-store"
        assert_security_headers(r)

    for path in (proj.base, f"{proj.base}/uebersicht", f"{proj.base}/hilfe"):
        r = c.get(path)
        assert r.status_code == 503, path
        assert r.headers["retry-after"] == RETRY
        assert r.headers["content-type"].startswith("text/html")
        assert 'data-testid="unavailable"' in r.text
        assert proj.token not in r.text
        assert_security_headers(r)

    # Ohne Datenbank-Bezug bleibt alles erreichbar.
    assert c.get("/robots.txt").status_code == 200
    assert c.get("/static/js/hauptliste.js").status_code == 200

    _assert_db_unavailable_logged(caplog)
    for rec in caplog.records:
        assert proj.token not in rec.getMessage()
        assert proj.token not in str(getattr(rec, "path", ""))

    # Datenbank wieder da -> normale Antworten ohne Neustart.
    switch.down = False
    assert c.get(f"{proj.base}/api/systems").status_code == 200
    assert ed.patch("APP-00001", "affected", "ja").status_code == 200


class _DeadSource:
    """Live-Quelle, deren Datenbank nicht erreichbar ist."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def listen(self) -> Any:  # pragma: no cover - Broker wird nicht gestartet
        raise self.error

    async def fetch(self, project_id: int, since: int | None) -> live.Delta:
        raise self.error

    async def resolve(self, token: str) -> tuple[int, int] | None:
        raise self.error

    async def close(self) -> None:
        return None


@pytest.mark.parametrize(
    "error",
    [
        sa_exc.OperationalError("SELECT 1", {}, Exception("connection refused")),
        psycopg.OperationalError("connection refused"),
    ],
)
def test_sse_db_down_returns_503(
    session_factory: sessionmaker[Session], error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    from npt.web.public import create_public_app

    app = create_public_app(
        make_settings(PUBLIC_BASE_URL=PUBLIC_ORIGIN),
        session_factory=session_factory,
        enable_monitor=False,
        enable_live=False,
    )
    broker = live.LiveBroker(_DeadSource(error))  # type: ignore[arg-type]
    broker._running = True  # wie gestartet
    app.state.live = broker
    caplog.set_level(logging.WARNING)
    c = TestClient(with_peer(app, OTHER_IP), base_url=PUBLIC_ORIGIN)
    r = c.get("/p/" + "A" * 43 + "/api/events")
    assert r.status_code == 503
    assert r.headers["retry-after"] == RETRY
    assert r.json() == {"reason": "datenbank"}
    assert_security_headers(r)
    _assert_db_unavailable_logged(caplog)


def test_admin_db_down_returns_503_with_retry_after(
    session_factory: sessionmaker[Session],
    switch: Switch,
    certs: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from npt.web.admin import create_admin_app
    from tests.admin_helpers import ADMIN_DN, bootstrap

    bootstrap(session_factory, ADMIN_DN)
    seed_master(session_factory, 1)
    proj = make_project(session_factory)
    app = create_admin_app(make_settings(), session_factory=switch, enable_monitor=False)
    c = TestClient(with_peer(app, GATEWAY_IP), base_url=ADMIN_ORIGIN, follow_redirects=False)
    hdrs = gateway_headers(certs.issue().pem)
    assert c.get("/admin/", headers=hdrs).status_code == 200
    switch.down = True
    caplog.set_level(logging.WARNING)

    for path in ("/admin/", "/admin/hilfe", "/admin/benutzer", f"/admin/projekte/{proj.id}"):
        r = c.get(path, headers=hdrs)
        assert r.status_code == 503, (path, r.text)
        assert r.headers["retry-after"] == RETRY
        assert "Datenbank" in r.text
        assert_security_headers(r, referrer="same-origin")
        assert r.headers["cache-control"] == "no-store"
    # Nicht-Admin (Admin-Pruefung braucht die Datenbank) ebenfalls 503.
    other = gateway_headers(certs.issue(cn="Niemand").pem)
    r = c.get("/admin/", headers=other)
    assert r.status_code == 503
    assert r.headers["retry-after"] == RETRY
    _assert_db_unavailable_logged(caplog)

    switch.down = False
    assert c.get("/admin/", headers=hdrs).status_code == 200


def test_jobs_log_db_outage_without_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import threading

    from npt import jobs

    stop = threading.Event()
    calls: list[str] = []

    def db_down() -> None:
        calls.append("db")
        raise sa_exc.OperationalError("SELECT 1", {}, Exception("connection refused"))

    def broken() -> None:
        calls.append("bug")
        stop.set()
        raise RuntimeError("echter Fehler")

    monkeypatch.setattr(jobs, "JOBS", [("backups", db_down), ("kaputt", broken)])
    caplog.set_level(logging.WARNING, logger="npt.jobs")
    assert jobs.run_forever(stop) == 0
    assert calls == ["db", "bug"]
    by_msg = {r.getMessage(): r for r in caplog.records if r.name == "npt.jobs"}
    outage = by_msg["Job: Datenbank nicht erreichbar"]
    assert outage.levelno == logging.WARNING
    assert outage.exc_info is None
    assert by_msg["Job fehlgeschlagen"].exc_info is not None  # echte Fehler mit Traceback
