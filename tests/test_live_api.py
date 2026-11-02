"""Delta-Endpunkt ``?updated_since=<seq>`` (Polling-Fallback, NPT-03) und SSE-Zulassung."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import update

from npt import live, projects
from npt.db.models import Project
from npt.editing import PUBLIC_COLUMNS
from tests.project_helpers import ACTOR, Editor, make_project, seed_master


@pytest.fixture
def setup(session_factory, public_app_factory):
    seed_master(session_factory, 6)
    proj = make_project(session_factory)
    return proj, Editor(public_app_factory(), proj)


def get(ed: Editor, since: object = None) -> dict[str, Any]:
    url = f"{ed.p.base}/api/systems"
    if since is not None:
        url += f"?updated_since={since}"
    r = ed.c.get(url)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    data: dict[str, Any] = r.json()
    return data


def test_full_list_carries_cursor(setup) -> None:
    _, ed = setup
    data = get(ed)
    assert data["seq"] == 0
    assert data["full_reload"] is True
    assert len(data["rows"]) == 6
    assert data["project"] == {"name": "CVE-2026-0001", "status": "aktiv", "read_only": False}


def test_delta_only_changed_rows(setup) -> None:
    _, ed = setup
    assert ed.patch("APP-00002", "affected", "ja").status_code == 200
    assert ed.patch("APP-00004", "remark", "x").status_code == 200
    assert ed.patch("APP-00002", "installed_version", "1.0").status_code == 200
    data = get(ed, 0)
    assert data["full_reload"] is False
    assert data["seq"] == 3
    # Reihenfolge nach row_seq; jede Zeile einmal mit ihrem neuesten Stand
    assert [r["system_id"] for r in data["rows"]] == ["APP-00004", "APP-00002"]
    app2 = data["rows"][1]
    assert (app2["affected"], app2["installed_version"], app2["row_seq"]) == ("ja", "1.0", 3)
    assert [r["system_id"] for r in get(ed, 2)["rows"]] == ["APP-00002"]
    up_to_date = get(ed, 3)
    assert (up_to_date["rows"], up_to_date["seq"], up_to_date["full_reload"]) == ([], 3, False)
    # nur Allowlist-Spalten
    assert set(app2) == {*PUBLIC_COLUMNS}


def test_new_system_appears_in_delta(setup) -> None:
    from tests.project_helpers import new_system_body

    _, ed = setup
    r = ed.create(**new_system_body())
    assert r.status_code == 201
    rows = get(ed, 0)["rows"]
    assert [x["system_id"] for x in rows] == [r.json()["row"]["system_id"]]
    assert rows[0]["origin"] == "manuell_angelegt"


def test_cursor_below_reset_seq_requests_full_reload(setup, session_factory) -> None:
    proj, ed = setup
    ed.patch("APP-00001", "affected", "ja")
    ed.patch("APP-00003", "affected", "nein")
    with session_factory() as s:  # wie ein Restore (Phase 7): reset_seq = change_seq
        s.execute(update(Project).where(Project.id == proj.id).values(reset_seq=Project.change_seq))
        s.commit()
    data = get(ed, 1)
    assert data["full_reload"] is True
    assert data["seq"] == 2
    assert len(data["rows"]) == 6
    assert get(ed, 2)["full_reload"] is False  # Cursor == reset_seq ist gueltig


def test_cursor_ahead_of_project_requests_full_reload(setup) -> None:
    _, ed = setup
    data = get(ed, 99)
    assert data["full_reload"] is True
    assert len(data["rows"]) == 6


@pytest.mark.parametrize("bad", ["-1", "abc", "1.5", "", "9" * 30])
def test_invalid_cursor_422(setup, bad: str) -> None:
    _, ed = setup
    r = ed.c.get(f"{ed.p.base}/api/systems?updated_since={bad}")
    assert r.status_code == 422
    assert r.json()["errors"] == {"updated_since": "Ganzzahl >= 0 erwartet"}


def test_status_in_delta_and_token_change(setup, session_factory) -> None:
    proj, ed = setup
    with session_factory() as s:
        projects.set_status(s, ACTOR, proj.id, "abgeschlossen")
        s.commit()
    data = get(ed, 0)
    assert data["seq"] == 1
    assert data["rows"] == []
    assert data["project"]["read_only"] is True
    with session_factory() as s:
        projects.regenerate_token(s, ACTOR, proj.id)
        s.commit()
    r = ed.c.get(f"{proj.base}/api/systems?updated_since=1")
    assert r.status_code == 404
    assert r.json()["reason"] == "nicht_gefunden"


def test_noop_and_conflict_do_not_move_cursor(setup) -> None:
    _, ed = setup
    assert ed.patch("APP-00001", "affected", "ja").status_code == 200
    assert ed.patch("APP-00001", "affected", "ja", "ja").json()["changed"] is False
    assert ed.patch("APP-00001", "affected", "nein", None).status_code == 409
    assert get(ed)["seq"] == 1


# ---------------------------------------------------------------- SSE-Zulassung
def test_events_without_broker_503(setup) -> None:
    _, ed = setup
    r = ed.c.get(f"{ed.p.base}/api/events")
    assert r.status_code == 503
    assert r.json()["reason"] == "ueberlastet"
    assert r.headers["retry-after"] == "5"


def test_events_over_capacity_503(session_factory) -> None:
    from fastapi.testclient import TestClient

    from npt.web.public import create_public_app
    from tests.conftest import PUBLIC_ORIGIN, make_settings
    from tests.test_live_broker import FakeSource

    app = create_public_app(
        make_settings(PUBLIC_BASE_URL=PUBLIC_ORIGIN),
        session_factory=session_factory,
        enable_monitor=False,
        enable_live=False,
    )

    full = live.LiveBroker(FakeSource(), max_subscribers=0)
    full._running = True  # wie gestartet, aber ohne freie Plaetze
    app.state.live = full
    c = TestClient(app, base_url=PUBLIC_ORIGIN)
    r = c.get("/p/" + "A" * 43 + "/api/events")
    assert r.status_code == 503
    assert r.headers["retry-after"] == "5"
    app.state.live = live.LiveBroker(FakeSource())  # nicht gestartet -> keine Kapazitaet
    assert c.get("/p/" + "A" * 43 + "/api/events").status_code == 503


def test_events_route_not_behind_admission() -> None:
    """Der Stream darf keinen Semaphor-Platz belegen (eigener Router ohne Dependency)."""
    from npt.web.public_live import build_live_router

    router = build_live_router()
    assert not router.dependencies
    paths = {getattr(r, "path", "") for r in router.routes}
    assert paths == {"/p/{token}/api/events"}
