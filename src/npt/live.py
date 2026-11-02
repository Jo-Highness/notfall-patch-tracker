"""Live-Updates je Projekt (NPT-03, Auftrag 5.5): Delta-Cursor, LISTEN/NOTIFY, Broadcast.

Cursor ist ``projects.change_seq`` (kein Zeitstempel, Abweichung A1). Jede
Schreibtransaktion erhoeht ihn unter Projektsperre; geaenderte Zeilen tragen
``row_seq``. Weil die Sperre bis zum Commit gehalten wird, sind alle Sequenzen
``<= change_seq`` eines Schnappschusses committet. Deshalb gilt fuer den Delta-Abruf:
**erst** ``change_seq`` lesen, **dann** die Zeilen mit ``row_seq > since`` – der
gelieferte Cursor verliert so keine Aenderung (Zeilen juengerer Commits koennen
doppelt kommen; der Client wendet eine Zeile nur an, wenn ihr ``row_seq`` neuer ist).

Fan-out je Worker-Prozess (:class:`LiveBroker`):

- genau eine LISTEN-Verbindung (psycopg ``AsyncConnection``) auf ``npt_changes``;
- Benachrichtigungen werden 150 ms gebuendelt, danach **eine** Delta-Abfrage je
  Projekt (eigene 1-Verbindungs-Engine im eigenen Thread, belastet weder den
  Request-Pool noch den Standard-Threadpool) und Verteilung an alle lokalen Clients;
- ein SSE-Frame wird einmal serialisiert und von allen Client-Queues geteilt;
- je Client eine begrenzte Queue (64); laeuft sie ueber, wird die Verbindung
  geschlossen, der Browser verbindet neu und holt per ``Last-Event-ID`` nach.

Ereignisse (SSE ``event``): ``delta`` (Zeilen + Projektstatus), ``reload`` (Cursor
unter ``reset_seq``: Liste komplett neu laden), ``gone`` (Projekt geloescht oder
Token neu generiert). ``id`` ist immer die ``change_seq`` nach dem Ereignis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, cast

import anyio
from sqlalchemy import Connection, Engine, Table, select
from sqlalchemy.engine import make_url

from npt import limits
from npt.db.models import Project, ProjectSystem
from npt.editing import NOTIFY_CHANNEL, PUBLIC_COLUMNS

log = logging.getLogger("npt.live")

_ps = cast(Table, ProjectSystem.__table__)
SEQ_MAX: Final = 2**63 - 1


# ---------------------------------------------------------------- Delta (gemeinsam)
@dataclass(frozen=True)
class ProjectMeta:
    id: int
    status: str
    token_generation: int
    change_seq: int
    reset_seq: int

    @property
    def read_only(self) -> bool:
        return self.status != "aktiv"

    def public(self) -> dict[str, Any]:
        return {"status": self.status, "read_only": self.read_only}


@dataclass(frozen=True)
class Delta:
    """Ergebnis eines Delta-Abrufs; ``meta is None`` = Projekt existiert nicht mehr."""

    meta: ProjectMeta | None
    since: int | None
    rows: list[dict[str, Any]] = field(default_factory=list)
    full_reload: bool = False

    @property
    def seq(self) -> int:
        return self.meta.change_seq if self.meta is not None else 0


def parse_cursor(raw: str | None) -> int | None:
    """Cursor aus Query/Header: nicht-negative Ganzzahl, sonst ``None``."""
    if raw is None:
        return None
    value = raw.strip()
    if not value.isdigit() or len(value) > 19:
        return None
    number = int(value)
    return number if number <= SEQ_MAX else None


def read_meta(conn: Connection, project_id: int) -> ProjectMeta | None:
    row = conn.execute(
        select(
            Project.id,
            Project.status,
            Project.token_generation,
            Project.change_seq,
            Project.reset_seq,
        ).where(Project.id == project_id)
    ).first()
    return ProjectMeta(*row) if row is not None else None


def fetch_delta(
    conn: Connection, project_id: int, since: int | None, *, all_rows_on_reload: bool = False
) -> Delta:
    """Delta seit ``since`` (``None`` = nur Metadaten).

    Reihenfolge ist wesentlich: zuerst ``change_seq`` (Cursor), dann die Zeilen.
    ``since`` unter ``reset_seq`` (Restore) oder ueber ``change_seq`` -> ``full_reload``.
    """
    meta = read_meta(conn, project_id)
    if meta is None or since is None:
        return Delta(meta, since)
    cols = [_ps.c[c] for c in PUBLIC_COLUMNS]
    if since < meta.reset_seq or since > meta.change_seq:
        rows: list[dict[str, Any]] = []
        if all_rows_on_reload:
            stmt = select(*cols).where(_ps.c.project_id == project_id).order_by(_ps.c.system_id)
            rows = [dict(r) for r in conn.execute(stmt).mappings()]
        return Delta(meta, since, rows, full_reload=True)
    if since == meta.change_seq:
        return Delta(meta, since)
    stmt = (
        select(*cols)
        .where(_ps.c.project_id == project_id, _ps.c.row_seq > since)
        .order_by(_ps.c.row_seq, _ps.c.system_id)
    )
    return Delta(meta, since, [dict(r) for r in conn.execute(stmt).mappings()])


# ---------------------------------------------------------------- SSE-Frames
def sse_frame(event: str, data: Any, event_id: int | None = None) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {payload}\n\n".encode()


HEARTBEAT_FRAME: Final = b": hb\n\n"


def hello_frame() -> bytes:
    return f"retry: {limits.SSE_RETRY_MS}\n: verbunden\n\n".encode()


def delta_frame(delta: Delta) -> bytes:
    if delta.meta is None:
        raise ValueError("Delta ohne Projekt")
    if delta.full_reload:
        return sse_frame("reload", {"seq": delta.seq}, delta.seq)
    return sse_frame(
        "delta",
        {
            "seq": delta.seq,
            "since": delta.since,
            "project": delta.meta.public(),
            "rows": delta.rows,
        },
        delta.seq,
    )


def gone_frame(reason: str) -> bytes:
    return sse_frame("gone", {"reason": reason})


def parse_notify(payload: str) -> int | None:
    """NOTIFY-Nutzlast ``{"p","s","k"}`` -> Projekt-ID (ungueltig -> ``None``)."""
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    pid = data.get("p") if isinstance(data, dict) else None
    return pid if isinstance(pid, int) and not isinstance(pid, bool) else None


# ---------------------------------------------------------------- Quellen
class LiveSource(Protocol):
    """Datenquelle des Brokers (Postgres bzw. Fake in Unit-Tests)."""

    def listen(self) -> AsyncIterator[int | None]:
        """Liefert Projekt-IDs; ``None`` nach jedem (Neu-)Aufbau der LISTEN-Verbindung."""
        ...

    async def fetch(self, project_id: int, since: int | None) -> Delta: ...

    async def resolve(self, token: str) -> tuple[int, int] | None:
        """Token -> (Projekt-ID, token_generation)."""
        ...

    async def close(self) -> None: ...


class PgLiveSource:
    """LISTEN ueber psycopg ``AsyncConnection``, Abfragen ueber SQLAlchemy Core.

    Die Abfragen laufen auf einer eigenen Engine mit genau einer Verbindung in einem
    eigenen Thread-Limiter (Kapazitaet 1) – sie konkurrieren nicht mit Requests.
    """

    def __init__(self, url: str, engine: Engine | None = None) -> None:
        from npt.db.session import make_engine

        self._url = url
        self._engine = engine or make_engine(url, pool_size=1)
        self._limiter = anyio.CapacityLimiter(1)

    def _connect_kwargs(self) -> dict[str, Any]:
        u = make_url(self._url)
        kw: dict[str, Any] = {
            k: v
            for k, v in u.translate_connect_args(username="user", database="dbname").items()
            if v is not None
        }
        kw.update({k: v for k, v in u.query.items() if isinstance(v, str)})
        kw.update(
            connect_timeout=limits.DB_CONNECT_TIMEOUT_S,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=3,
            application_name="npt-live-listen",
        )
        return kw

    async def listen(self) -> AsyncIterator[int | None]:
        import psycopg
        from psycopg import sql

        conn = await psycopg.AsyncConnection.connect(autocommit=True, **self._connect_kwargs())
        async with conn:
            await conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(NOTIFY_CHANNEL)))
            yield None
            while True:
                async for note in conn.notifies(timeout=limits.LIVE_LISTEN_CHECK_S):
                    pid = parse_notify(note.payload)
                    if pid is not None:
                        yield pid
                # Lebenszeichen: bricht die Verbindung still ab, schlaegt das hier fehl.
                await conn.execute("SELECT 1")

    def _run(self, fn: Callable[[Connection], Any]) -> Any:
        with self._engine.connect() as conn:
            try:
                return fn(conn)
            finally:
                conn.rollback()

    async def fetch(self, project_id: int, since: int | None) -> Delta:
        result: Delta = await anyio.to_thread.run_sync(
            self._run, lambda c: fetch_delta(c, project_id, since), limiter=self._limiter
        )
        return result

    async def resolve(self, token: str) -> tuple[int, int] | None:
        def query(conn: Connection) -> tuple[int, int] | None:
            row = conn.execute(
                select(Project.id, Project.token_generation).where(Project.access_token == token)
            ).first()
            return (int(row[0]), int(row[1])) if row is not None else None

        result: tuple[int, int] | None = await anyio.to_thread.run_sync(
            self._run, query, limiter=self._limiter
        )
        return result

    async def close(self) -> None:
        self._engine.dispose()


# ---------------------------------------------------------------- Broker
CLOSE: Final = b""  # Sentinel in der Client-Queue: Stream beenden


class CapacityError(Exception):
    """SSE-Grenze des Workers erreicht (503 + Retry-After)."""


class UnavailableError(Exception):
    """Broker laeuft nicht (z. B. beim Herunterfahren)."""


@dataclass(eq=False)
class Subscriber:
    project_id: int
    generation: int
    cursor: int = 0
    queue: asyncio.Queue[bytes] = field(
        default_factory=lambda: asyncio.Queue(maxsize=limits.SSE_QUEUE_MAX)
    )
    closed: bool = False
    reason: str | None = None


@dataclass
class BrokerStats:
    subscribers: int = 0
    flushes: int = 0
    frames: int = 0
    overflows: int = 0
    listen_reconnects: int = 0
    last_flush_ms: float = 0.0


class LiveBroker:
    def __init__(
        self,
        source: LiveSource,
        *,
        max_subscribers: int = limits.SSE_MAX_PER_WORKER,
        bundle_s: float = limits.LIVE_BUNDLE_S,
        reconnect_max_s: float = limits.LIVE_RECONNECT_MAX_S,
    ) -> None:
        self.source = source
        self._max = max_subscribers
        self._bundle_s = bundle_s
        self._reconnect_max_s = reconnect_max_s
        self._subs: dict[int, set[Subscriber]] = {}
        self._cursor: dict[int, int] = {}
        self._meta: dict[int, ProjectMeta] = {}
        self._pending: set[int] = set()
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._count = 0
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False
        self.connected = asyncio.Event()
        self.stats = BrokerStats()

    # -- Lebenszyklus -----------------------------------------------------
    async def start(self) -> None:
        self._running = True
        self._tasks = [
            asyncio.create_task(self._listen_loop(), name="npt-live-listen"),
            asyncio.create_task(self._flush_loop(), name="npt-live-flush"),
        ]

    async def stop(self) -> None:
        self._running = False
        for subs in list(self._subs.values()):
            for sub in list(subs):
                self._close(sub, "shutdown")
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        await self.source.close()

    # -- Abos ---------------------------------------------------------------
    @property
    def subscriber_count(self) -> int:
        return self._count

    def has_capacity(self) -> bool:
        return self._running and self._count < self._max

    async def subscribe(self, project_id: int, generation: int, since: int | None) -> Subscriber:
        """Client anmelden; liefert ggf. sofort einen Nachhol-Frame in die Queue.

        Anmeldung und Nachholen laufen unter derselben Sperre wie das Verteilen:
        jede Aenderung kommt entweder im Nachhol-Frame oder in einem spaeteren Delta.
        """
        if not self._running:
            raise UnavailableError
        if self._count >= self._max:
            raise CapacityError
        self._count += 1
        sub = Subscriber(project_id, generation)
        try:
            async with self._lock:
                self._subs.setdefault(project_id, set()).add(sub)
                known = self._cursor.get(project_id)
                if known is None or (since is not None and since < known):
                    delta = await self.source.fetch(project_id, since)
                    if known is None and delta.meta is not None:
                        self._cursor[project_id] = delta.meta.change_seq
                        self._meta[project_id] = delta.meta
                    self._deliver_one(sub, delta)
                else:
                    sub.cursor = max(since or 0, known)
        except BaseException:
            self.unsubscribe(sub)
            raise
        self.stats.subscribers = self._count
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        subs = self._subs.get(sub.project_id)
        if subs is None or sub not in subs:
            return
        subs.discard(sub)
        self._count -= 1
        self.stats.subscribers = self._count
        if not subs:
            del self._subs[sub.project_id]
            self._cursor.pop(sub.project_id, None)
            self._meta.pop(sub.project_id, None)

    async def stream(self, sub: Subscriber) -> AsyncGenerator[bytes]:
        """SSE-Bytes fuer einen Client inkl. Heartbeat; meldet am Ende immer ab."""
        try:
            yield hello_frame()
            while True:
                try:
                    async with asyncio.timeout(limits.SSE_HEARTBEAT_S):
                        item = await sub.queue.get()
                except TimeoutError:
                    yield HEARTBEAT_FRAME
                    continue
                if item is CLOSE:
                    return
                yield item
        finally:
            self.unsubscribe(sub)

    # -- Verteilen ----------------------------------------------------------
    def _put(self, sub: Subscriber, frame: bytes) -> None:
        if sub.closed:
            return
        try:
            sub.queue.put_nowait(frame)
        except asyncio.QueueFull:
            self.stats.overflows += 1
            log.warning("SSE-Queue uebergelaufen, Verbindung wird geschlossen")
            self._close(sub, "overflow")

    def _close(self, sub: Subscriber, reason: str) -> None:
        if sub.closed:
            return
        sub.closed = True
        sub.reason = reason
        while not sub.queue.empty():
            sub.queue.get_nowait()
        sub.queue.put_nowait(CLOSE)

    def _deliver_one(self, sub: Subscriber, delta: Delta) -> None:
        """Nachhol-Frame fuer einen einzelnen Client."""
        if delta.meta is None:
            self._put(sub, gone_frame("geloescht"))
            self._close_after(sub, "geloescht")
            return
        if delta.meta.token_generation != sub.generation:
            self._put(sub, gone_frame("token"))
            self._close_after(sub, "token")
            return
        sub.cursor = delta.meta.change_seq
        if delta.since is not None and (delta.full_reload or delta.rows):
            self._put(sub, delta_frame(delta))

    def _close_after(self, sub: Subscriber, reason: str) -> None:
        if not sub.closed:
            sub.closed = True
            sub.reason = reason
            with suppress(asyncio.QueueFull):
                sub.queue.put_nowait(CLOSE)

    def _broadcast(self, project_id: int, delta: Delta) -> None:
        subs = list(self._subs.get(project_id, ()))
        if delta.meta is None:
            gone = gone_frame("geloescht")
            for sub in subs:
                self._put(sub, gone)
                self._close_after(sub, "geloescht")
            return
        meta = delta.meta
        previous = self._meta.get(project_id)
        self._cursor[project_id] = meta.change_seq
        self._meta[project_id] = meta
        status_changed = previous is not None and previous.status != meta.status
        frame: bytes | None = None
        if delta.full_reload or delta.rows or status_changed:
            frame = delta_frame(delta)
            self.stats.frames += 1
        token_frame: bytes | None = None
        for sub in subs:
            if sub.generation != meta.token_generation:
                token_frame = token_frame or gone_frame("token")
                self._put(sub, token_frame)
                self._close_after(sub, "token")
                continue
            if frame is not None and meta.change_seq > sub.cursor:
                self._put(sub, frame)
            sub.cursor = max(sub.cursor, meta.change_seq)

    async def flush(self) -> None:
        """Eine Buendelrunde: eine Delta-Abfrage je Projekt mit lokalen Clients."""
        async with self._lock:
            pending, self._pending = self._pending, set()
            started = time.perf_counter()
            for pid in sorted(pending):
                if not self._subs.get(pid):
                    continue
                try:
                    delta = await self.source.fetch(pid, self._cursor.get(pid))
                except Exception:
                    log.exception("Delta-Abfrage fehlgeschlagen", extra={"project_id": pid})
                    self._pending.add(pid)  # in der naechsten Runde erneut
                    continue
                self._broadcast(pid, delta)
            self.stats.flushes += 1
            self.stats.last_flush_ms = round((time.perf_counter() - started) * 1000, 2)

    def notify(self, project_id: int | None) -> None:
        """Eingang einer Benachrichtigung (``None`` = Verbindung neu: alles pruefen)."""
        if project_id is None:
            self._pending.update(self._subs.keys())
        else:
            self._pending.add(project_id)
        self._wake.set()

    async def _flush_loop(self) -> None:
        while True:
            await self._wake.wait()
            await asyncio.sleep(self._bundle_s)
            self._wake.clear()
            if self._pending:
                await self.flush()
                if self._pending:  # fehlgeschlagene Abfragen erneut
                    await asyncio.sleep(1.0)
                    self._wake.set()

    async def _listen_loop(self) -> None:
        backoff = 0.5
        while True:
            try:
                async for pid in self.source.listen():
                    if pid is None:
                        self.connected.set()
                        backoff = 0.5
                    self.notify(pid)
                raise ConnectionError("LISTEN-Strom beendet")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected.clear()
                self.stats.listen_reconnects += 1
                log.warning(
                    "LISTEN-Verbindung verloren, neuer Versuch",
                    extra={"error": type(exc).__name__, "retry_s": backoff},
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_max_s)
