"""SSE-Integration gegen echte Postgres mit **zwei** Uvicorn-Prozessen (zwei Worker,
je eigene LISTEN-Verbindung): Aenderungen erscheinen bei allen Clients <= 2 s,
Nachholen per Last-Event-ID, Status-/Token-Wechsel, Loeschung, Header (NPT-03)."""

from __future__ import annotations

import os
import re
import socket
import statistics
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from npt import projects
from tests.conftest import CSRF_SECRET, DB_URL
from tests.project_helpers import ACTOR, Proj, make_project, new_system_body, seed_master
from tests.sse_client import SseClient

LIVE_MAX_S = 2.0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture
def servers(session_factory) -> Iterator[list[str]]:
    """Zwei unabhaengige App-Prozesse gegen dieselbe Datenbank."""
    ports = [free_port(), free_port()]
    env = {
        **os.environ,
        "DATABASE_URL": DB_URL,
        "CSRF_SECRET": CSRF_SECRET,
        "PUBLIC_BASE_URL": "http://npt.test",
        "LOG_LEVEL": "WARNING",
    }
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "npt.web.public:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
                "--no-access-log",
                "--timeout-graceful-shutdown",
                "2",
            ],
            env=env,
        )
        for port in ports
    ]
    urls = [f"http://127.0.0.1:{p}" for p in ports]
    try:
        deadline = time.monotonic() + 20
        for url in urls:
            while True:
                try:
                    if httpx.get(url + "/healthz", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("Server startet nicht")
                time.sleep(0.1)
        yield urls
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


class Writer:
    def __init__(self, url: str, proj: Proj) -> None:
        self.url = url
        self.proj = proj
        self.c = httpx.Client(base_url=url, timeout=10)
        page = self.c.get(proj.base)
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text)
        assert m
        self.h = {"X-CSRF-Token": m.group(1)}

    def patch(
        self, sid: str, field: str, value: str | None, expected: str | None = None
    ) -> dict[str, Any]:
        r = self.c.patch(
            f"{self.proj.base}/api/systems/{sid}",
            json={"field": field, "value": value, "expected": expected},
            headers=self.h,
        )
        assert r.status_code == 200, r.text
        data: dict[str, Any] = r.json()
        return data


def connect(url: str, proj: Proj, since: int | None = 0, **headers: str) -> SseClient:
    q = f"?since={since}" if since is not None else ""
    client = SseClient(f"{url}{proj.base}/api/events{q}", headers=headers)
    assert client.wait_open() == 200
    hello = client.next(timeout=5)
    assert hello is not None
    assert hello.retry == 3000
    return client


@pytest.fixture
def proj(session_factory) -> Proj:
    seed_master(session_factory, 20)
    return make_project(session_factory)


def test_change_reaches_clients_on_both_workers(servers, proj) -> None:
    a, b = servers
    clients = [connect(a, proj), connect(a, proj), connect(b, proj), connect(b, proj)]
    try:
        writers = [Writer(a, proj), Writer(b, proj)]
        latencies: list[float] = []
        for i in range(10):
            sid = f"APP-{i + 1:05d}"
            res = writers[i % 2].patch(sid, "affected", "ja")
            done = time.monotonic()
            for cl in clients:
                ev = cl.next(timeout=LIVE_MAX_S + 1, kind="delta")
                assert ev is not None
                assert ev.id == res["seq"]
                assert ev.data["seq"] == res["seq"]
                assert [r["system_id"] for r in ev.data["rows"]] == [sid]
                assert ev.data["rows"][0]["affected"] == "ja"
                latencies.append(max(0.0, ev.received - done))
        worst = max(latencies)
        print(
            f"\nSSE-Latenz (2 Prozesse, 4 Clients, 10 Schreibvorgaenge): "
            f"median {statistics.median(latencies) * 1000:.0f} ms, max {worst * 1000:.0f} ms"
        )
        assert worst <= LIVE_MAX_S
    finally:
        for cl in clients:
            cl.close()


def test_burst_is_bundled_and_complete(servers, proj) -> None:
    a, b = servers
    client = connect(b, proj)
    try:
        w = Writer(a, proj)
        last = 0
        for i in range(8):
            last = w.patch(f"APP-{i + 1:05d}", "remark", f"r{i}")["seq"]
        seen: dict[str, str] = {}
        deadline = time.monotonic() + 5
        frames = 0
        while len(seen) < 8 and time.monotonic() < deadline:
            ev = client.next(timeout=5, kind="delta")
            assert ev is not None
            frames += 1
            seen.update({r["system_id"]: r["remark"] for r in ev.data["rows"]})
        assert seen == {f"APP-{i + 1:05d}": f"r{i}" for i in range(8)}
        assert ev is not None
        assert ev.id == last
        assert frames <= 8
    finally:
        client.close()


def test_reconnect_with_last_event_id_catches_up(servers, proj) -> None:
    a, b = servers
    w = Writer(a, proj)
    first = connect(a, proj)
    seq1 = w.patch("APP-00001", "affected", "nein")["seq"]
    ev = first.next(timeout=3, kind="delta")
    assert ev is not None
    assert ev.id == seq1
    first.close()
    # waehrend der Trennung
    w.patch("APP-00002", "affected", "ja")
    seq3 = w.patch("APP-00003", "installed_version", "2.0")["seq"]
    again = connect(b, proj, since=None, **{"Last-Event-ID": str(seq1)})
    try:
        ev = again.next(timeout=3, kind="delta")
        assert ev is not None
        assert ev.id == seq3
        assert [r["system_id"] for r in ev.data["rows"]] == ["APP-00002", "APP-00003"]
    finally:
        again.close()
    # ?since= ohne Last-Event-ID (manueller Neuaufbau)
    manual = connect(b, proj, since=seq1)
    try:
        ev = manual.next(timeout=3, kind="delta")
        assert ev is not None
        assert {r["system_id"] for r in ev.data["rows"]} == {"APP-00002", "APP-00003"}
    finally:
        manual.close()


def test_new_system_appears_on_other_worker(servers, proj) -> None:
    a, b = servers
    client = connect(a, proj)
    try:
        w = Writer(b, proj)
        r = w.c.post(f"{proj.base}/api/systems", json=new_system_body(), headers=w.h)
        assert r.status_code == 201
        ev = client.next(timeout=3, kind="delta")
        assert ev is not None
        row = ev.data["rows"][0]
        assert row["system_id"] == r.json()["row"]["system_id"]
        assert row["system_id"].startswith("NEW-")
        assert row["origin"] == "manuell_angelegt"
    finally:
        client.close()


def test_status_token_and_delete(servers, proj, session_factory) -> None:
    a, b = servers
    client = connect(a, proj)
    try:
        with session_factory() as s:
            projects.set_status(s, ACTOR, proj.id, "abgeschlossen")
            s.commit()
        ev = client.next(timeout=3, kind="delta")
        assert ev is not None
        assert ev.data["project"] == {"status": "abgeschlossen", "read_only": True}
        with session_factory() as s:
            p = projects.regenerate_token(s, ACTOR, proj.id)
            s.commit()
            new = Proj(p.id, p.access_token, p.name)
        ev = client.next(timeout=3)
        assert ev is not None
        assert (ev.event, ev.data) == ("gone", {"reason": "token"})
        assert client.next(timeout=3) is None  # Server schliesst den Stream
    finally:
        client.close()
    old = SseClient(f"{b}{proj.base}/api/events")
    assert old.wait_open() == 404
    old.close()
    fresh = connect(b, new)
    try:
        with session_factory() as s:
            projects.delete_project(s, ACTOR, new.id, new.name)
            s.commit()
        ev = fresh.next(timeout=3)
        assert ev is not None
        assert (ev.event, ev.data) == ("gone", {"reason": "geloescht"})
    finally:
        fresh.close()


def test_sse_response_headers(servers, proj) -> None:
    client = connect(servers[0], proj, since=None)
    try:
        h = client.response_headers
        assert h is not None
        assert h["content-type"].startswith("text/event-stream")
        assert h["x-accel-buffering"] == "no"
        assert h["cache-control"] == "no-store"
        assert "content-encoding" not in h
        assert h["referrer-policy"] == "no-referrer"
    finally:
        client.close()
