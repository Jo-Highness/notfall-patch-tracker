"""Broadcast-Logik des Live-Brokers mit Fake-Quelle (NPT-03): Buendelung, Nachholen,
reset_seq, Token-Wechsel, Loeschung, Queue-Ueberlauf, Kapazitaet, Heartbeat, Wiederverbinden."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from npt import limits, live


@dataclass
class FakeProject:
    id: int
    status: str = "aktiv"
    generation: int = 1
    change_seq: int = 0
    reset_seq: int = 0
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    def meta(self) -> live.ProjectMeta:
        return live.ProjectMeta(
            self.id, self.status, self.generation, self.change_seq, self.reset_seq
        )


class FakeSource:
    """In-Memory-Projekte + Benachrichtigungen wie NOTIFY (Versand nach dem 'Commit')."""

    def __init__(self) -> None:
        self.projects: dict[int, FakeProject] = {}
        self.notes: asyncio.Queue[int | str] = asyncio.Queue()
        self.fetches: list[tuple[int, int | None]] = []
        self.connects = 0

    def add(self, pid: int, rows: int = 3) -> FakeProject:
        p = FakeProject(pid)
        for i in range(rows):
            sid = f"S{i}"
            p.rows[sid] = {"system_id": sid, "affected": None, "row_seq": 0}
        self.projects[pid] = p
        return p

    def write(self, pid: int, sid: str, *, notify: bool = True, **values: Any) -> int:
        p = self.projects[pid]
        p.change_seq += 1
        row = p.rows.setdefault(sid, {"system_id": sid, "affected": None})
        row.update(values, row_seq=p.change_seq)
        if notify:
            self.notes.put_nowait(pid)
        return p.change_seq

    async def listen(self) -> AsyncIterator[int | None]:
        self.connects += 1
        yield None
        while True:
            item = await self.notes.get()
            if item == "fail":
                raise ConnectionError("weg")
            assert isinstance(item, int)
            yield item

    async def fetch(self, project_id: int, since: int | None) -> live.Delta:
        self.fetches.append((project_id, since))
        p = self.projects.get(project_id)
        if p is None:
            return live.Delta(None, since)
        meta = p.meta()
        if since is None:
            return live.Delta(meta, since)
        if since < p.reset_seq or since > p.change_seq:
            return live.Delta(meta, since, full_reload=True)
        rows = sorted(
            (dict(r) for r in p.rows.values() if r["row_seq"] > since),
            key=lambda r: r["row_seq"],
        )
        return live.Delta(meta, since, rows)

    async def resolve(self, token: str) -> tuple[int, int] | None:
        return None

    async def close(self) -> None:
        return None


def parse_frame(frame: bytes) -> tuple[str | None, int | None, Any]:
    event = event_id = data = None
    for line in frame.decode().splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("id: "):
            event_id = int(line[4:])
        elif line.startswith("data: "):
            data = json.loads(line[6:])
    return event, event_id, data


async def next_frame(stream: AsyncIterator[bytes], timeout: float = 2.0) -> bytes:
    async with asyncio.timeout(timeout):
        while True:
            frame = await anext(stream)
            if frame != live.HEARTBEAT_FRAME and not frame.startswith(b"retry:"):
                return frame


def run(coro_fn: Callable[[], Awaitable[None]]) -> None:
    async def wrapper() -> None:
        await coro_fn()

    asyncio.run(wrapper())


async def started(source: FakeSource, **kw: Any) -> live.LiveBroker:
    broker = live.LiveBroker(source, **kw)
    await broker.start()
    async with asyncio.timeout(2):
        await broker.connected.wait()
    return broker


# ---------------------------------------------------------------- Hilfsfunktionen
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0", 0), ("42", 42), (" 7 ", 7), ("-1", None), ("x", None), ("", None), (None, None)],
)
def test_parse_cursor(raw: str | None, expected: int | None) -> None:
    assert live.parse_cursor(raw) == expected


def test_parse_cursor_bounds() -> None:
    assert live.parse_cursor(str(2**63 - 1)) == 2**63 - 1
    assert live.parse_cursor(str(2**63)) is None
    assert live.parse_cursor("1" * 25) is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [('{"p":3,"s":9,"k":"row"}', 3), ('{"p":"3"}', None), ("kaputt", None), ("[1]", None)],
)
def test_parse_notify(payload: str, expected: int | None) -> None:
    assert live.parse_notify(payload) == expected


def test_frames_format() -> None:
    meta = live.ProjectMeta(1, "aktiv", 1, 5, 0)
    frame = live.delta_frame(live.Delta(meta, 3, [{"system_id": "Ä", "row_seq": 5}]))
    assert frame.startswith(b"id: 5\nevent: delta\ndata: ")
    assert frame.endswith(b"\n\n")
    event, eid, data = parse_frame(frame)
    assert (event, eid) == ("delta", 5)
    assert data == {
        "seq": 5,
        "since": 3,
        "project": {"status": "aktiv", "read_only": False},
        "rows": [{"system_id": "Ä", "row_seq": 5}],
    }
    assert parse_frame(live.delta_frame(live.Delta(meta, 1, full_reload=True)))[:2] == (
        "reload",
        5,
    )
    assert live.hello_frame().startswith(f"retry: {limits.SSE_RETRY_MS}\n".encode())


# ---------------------------------------------------------------- Broadcast
def test_bundling_one_query_per_project_and_shared_frame() -> None:
    async def main() -> None:
        src = FakeSource()
        src.add(1)
        src.add(2)
        broker = await started(src)
        a = await broker.subscribe(1, 1, None)
        b = await broker.subscribe(1, 1, None)
        c = await broker.subscribe(2, 1, None)
        src.fetches.clear()
        for i in range(10):
            src.write(1, f"S{i % 3}", affected="ja")
        src.write(2, "S0", affected="nein")
        fa = await next_frame(broker.stream(a))
        fb = await next_frame(broker.stream(b))
        fc = await next_frame(broker.stream(c))
        # eine Delta-Abfrage je Projekt und Buendel
        assert sorted(src.fetches) == [(1, 0), (2, 0)]
        assert fa is fb  # einmal serialisiert, von allen Queues geteilt
        event, eid, data = parse_frame(fa)
        assert (event, eid, data["since"]) == ("delta", 10, 0)
        assert {r["system_id"] for r in data["rows"]} == {"S0", "S1", "S2"}
        assert parse_frame(fc)[1] == 1
        await broker.stop()

    run(main)


def test_subscribe_catch_up_from_cursor() -> None:
    async def main() -> None:
        src = FakeSource()
        src.add(1)
        broker = await started(src)
        first = await broker.subscribe(1, 1, None)  # Broker kennt nun Cursor 0
        s_first = broker.stream(first)
        src.write(1, "S1", affected="ja")
        src.write(1, "S2", affected="nein")
        _, eid, data = parse_frame(await next_frame(s_first))
        assert eid == 2
        assert [r["system_id"] for r in data["rows"]] == ["S1", "S2"]
        # Client mit Last-Event-ID 1 -> Nachholen nur von S2
        late = await broker.subscribe(1, 1, 1)
        s_late = broker.stream(late)
        event, eid, data = parse_frame(await next_frame(s_late))
        assert (event, eid) == ("delta", 2)
        assert [r["system_id"] for r in data["rows"]] == ["S2"]
        # spaeteres Broadcast (seq 3) erreicht beide
        src.write(1, "S0", affected="ja")
        for stream in (s_late, s_first):
            _, eid, data = parse_frame(await next_frame(stream))
            assert eid == 3
            assert [r["system_id"] for r in data["rows"]] == ["S0"]
        await broker.stop()

    run(main)


def test_client_ahead_of_broker_waits_for_broadcast() -> None:
    async def main() -> None:
        src = FakeSource()
        src.add(1)
        broker = await started(src)
        await broker.subscribe(1, 1, None)  # Cursor 0
        src.write(1, "S1", notify=False, affected="ja")  # committet, noch nicht verteilt
        src.fetches.clear()
        ahead = await broker.subscribe(1, 1, 1)  # Client hat seq 1 schon (Erstladen)
        assert src.fetches == []
        assert ahead.cursor == 1
        broker.notify(1)
        await broker.flush()
        # Frame (seq 1) ist nicht neuer als der Client-Stand -> nicht doppelt gesendet
        assert ahead.queue.empty()
        await broker.stop()

    run(main)


def test_subscriber_ahead_of_broker_gets_no_duplicate_catch_up() -> None:
    async def main() -> None:
        src = FakeSource()
        src.add(1)
        broker = await started(src)
        await broker.subscribe(1, 1, None)
        src.fetches.clear()
        sub = await broker.subscribe(1, 1, 0)  # since == Broker-Cursor
        assert src.fetches == []
        assert sub.queue.empty()
        await broker.stop()

    run(main)


def test_reset_seq_sends_reload() -> None:
    async def main() -> None:
        src = FakeSource()
        p = src.add(1)
        broker = await started(src)
        sub = await broker.subscribe(1, 1, None)
        p.change_seq = 10
        p.reset_seq = 10  # Restore
        src.notes.put_nowait(1)
        event, eid, _ = parse_frame(await next_frame(broker.stream(sub)))
        assert (event, eid) == ("reload", 10)
        # neuer Client mit Cursor unter reset_seq bekommt ebenfalls reload
        old = await broker.subscribe(1, 1, 3)
        assert parse_frame(await next_frame(broker.stream(old)))[0] == "reload"
        await broker.stop()

    run(main)


def test_status_change_without_rows_is_broadcast() -> None:
    async def main() -> None:
        src = FakeSource()
        p = src.add(1)
        broker = await started(src)
        sub = await broker.subscribe(1, 1, None)
        p.change_seq += 1
        p.status = "abgeschlossen"
        src.notes.put_nowait(1)
        event, eid, data = parse_frame(await next_frame(broker.stream(sub)))
        assert (event, eid, data["rows"]) == ("delta", 1, [])
        assert data["project"] == {"status": "abgeschlossen", "read_only": True}
        await broker.stop()

    run(main)


def test_token_change_and_deletion_end_streams() -> None:
    async def main() -> None:
        src = FakeSource()
        p = src.add(1)
        src.add(2)
        broker = await started(src)
        sub = await broker.subscribe(1, 1, None)
        p.generation = 2
        p.change_seq += 1
        src.notes.put_nowait(1)
        stream = broker.stream(sub)
        event, _, data = parse_frame(await next_frame(stream))
        assert (event, data) == ("gone", {"reason": "token"})
        with pytest.raises(StopAsyncIteration):
            await next_frame(stream)
        assert broker.subscriber_count == 0
        # Loeschen
        sub2 = await broker.subscribe(2, 1, None)
        del src.projects[2]
        src.notes.put_nowait(2)
        stream2 = broker.stream(sub2)
        assert parse_frame(await next_frame(stream2))[2] == {"reason": "geloescht"}
        with pytest.raises(StopAsyncIteration):
            await next_frame(stream2)
        # Anmeldung mit veralteter Generation (Race beim Token-Wechsel)
        stale = await broker.subscribe(1, 1, 0)
        assert parse_frame(await next_frame(broker.stream(stale)))[2] == {"reason": "token"}
        await broker.stop()

    run(main)


def test_queue_overflow_closes_connection() -> None:
    async def main() -> None:
        src = FakeSource()
        src.add(1)
        broker = await started(src)
        slow = await broker.subscribe(1, 1, None)
        fast = await broker.subscribe(1, 1, None)
        fast_stream = broker.stream(fast)
        await anext(fast_stream)  # hello
        for i in range(limits.SSE_QUEUE_MAX + 5):
            src.write(1, "S0", notify=False, affected="ja" if i % 2 else "nein")
            broker.notify(1)
            await broker.flush()
            await next_frame(fast_stream)  # der schnelle Client liest mit
        assert slow.closed
        assert slow.reason == "overflow"
        assert broker.stats.overflows == 1
        stream = broker.stream(slow)
        assert (await anext(stream)).startswith(b"retry:")
        with pytest.raises(StopAsyncIteration):
            await anext(stream)  # Verbindung zu -> Browser verbindet neu (Last-Event-ID)
        assert not fast.closed
        assert broker.subscriber_count == 1
        await broker.stop()

    run(main)


def test_capacity_limit() -> None:
    async def main() -> None:
        src = FakeSource()
        src.add(1)
        broker = await started(src, max_subscribers=2)
        a = await broker.subscribe(1, 1, None)
        await broker.subscribe(1, 1, None)
        assert not broker.has_capacity()
        with pytest.raises(live.CapacityError):
            await broker.subscribe(1, 1, None)
        broker.unsubscribe(a)
        broker.unsubscribe(a)  # idempotent
        assert broker.subscriber_count == 1
        assert broker.has_capacity()
        await broker.stop()
        with pytest.raises(live.UnavailableError):
            await broker.subscribe(1, 1, None)

    run(main)


def test_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "SSE_HEARTBEAT_S", 0.05)

    async def main() -> None:
        src = FakeSource()
        src.add(1)
        broker = await started(src)
        sub = await broker.subscribe(1, 1, None)
        stream = broker.stream(sub)
        assert (await anext(stream)).startswith(b"retry:")
        async with asyncio.timeout(1):
            assert await anext(stream) == live.HEARTBEAT_FRAME
        await stream.aclose()  # Client weg -> abgemeldet
        assert broker.subscriber_count == 0
        await broker.stop()

    run(main)


def test_listen_reconnect_rechecks_all_projects() -> None:
    async def main() -> None:
        src = FakeSource()
        src.add(1)
        broker = live.LiveBroker(src, reconnect_max_s=0.05)
        await broker.start()
        async with asyncio.timeout(2):
            await broker.connected.wait()
        sub = await broker.subscribe(1, 1, None)
        src.notes.put_nowait("fail")
        await asyncio.sleep(0.05)
        # Aenderung waehrend der Unterbrechung, deren NOTIFY verloren ging
        src.write(1, "S1", notify=False, affected="ja")
        _, eid, data = parse_frame(await next_frame(broker.stream(sub), timeout=3))
        assert eid == 1
        assert data["rows"][0]["system_id"] == "S1"
        assert src.connects >= 2
        assert broker.stats.listen_reconnects >= 1
        await broker.stop()

    run(main)


def test_failed_fetch_is_retried() -> None:
    async def main() -> None:
        src = FakeSource()
        src.add(1)
        broker = await started(src)
        sub = await broker.subscribe(1, 1, None)
        original = src.fetch
        calls = {"n": 0}

        async def flaky(pid: int, since: int | None) -> live.Delta:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("DB kurz weg")
            return await original(pid, since)

        src.fetch = flaky  # type: ignore[method-assign,assignment]
        src.write(1, "S2", affected="ja")
        _, eid, _ = parse_frame(await next_frame(broker.stream(sub), timeout=4))
        assert eid == 1
        assert calls["n"] >= 2
        await broker.stop()

    run(main)


def test_stop_closes_open_streams() -> None:
    async def main() -> None:
        src = FakeSource()
        src.add(1)
        broker = await started(src)
        sub = await broker.subscribe(1, 1, None)
        stream = broker.stream(sub)
        await anext(stream)
        await broker.stop()
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    run(main)
