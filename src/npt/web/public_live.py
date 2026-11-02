"""SSE-Endpunkt je Projekt: ``GET /p/<token>/api/events`` (NPT-03, Auftrag 5.5).

Bewusst ohne Zulassungs-Semaphor und ohne Request-Session: ein Stream belegt
weder einen Semaphor-Platz noch eine Pool-Verbindung. Token-Aufloesung und
Nachholen laufen ueber die eigene Verbindung des Brokers. Grenze je Worker:
``limits.SSE_MAX_PER_WORKER``, darueber 503 mit ``Retry-After``.

Cursor: ``Last-Event-ID`` (automatisches Wiederverbinden des Browsers) hat Vorrang
vor ``?since=<seq>`` (erster Aufbau bzw. manueller Neuaufbau nach Abbruch).
Antwort-Header: ``X-Accel-Buffering: no``, ``Cache-Control: no-store``; der Stream
wird nie komprimiert (es gibt keine GZip-Middleware, Proxy-Doku: Buffering aus).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect
from starlette.types import Receive, Scope, Send

from npt import limits, live
from npt.web.public_project import TOKEN_RE, ProjectNotFoundError, PublicError

SSE_HEADERS: Final = {
    "cache-control": "no-store",
    "x-accel-buffering": "no",
}


class SseResponse(StreamingResponse):
    """Stream, der das Abo in jedem Fall abmeldet (auch wenn er nie startet)."""

    def __init__(self, broker: live.LiveBroker, sub: live.Subscriber) -> None:
        self._broker = broker
        self._sub = sub
        body: AsyncIterator[bytes] = broker.stream(sub)
        super().__init__(body, media_type="text/event-stream", headers=SSE_HEADERS)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        except ClientDisconnect:
            pass  # Browser hat die Verbindung geschlossen (normal bei SSE)
        finally:
            self._broker.unsubscribe(self._sub)


def _busy() -> PublicError:
    return PublicError(503, "ueberlastet", {"retry-after": str(limits.SSE_RETRY_AFTER_S)})


def build_live_router() -> APIRouter:
    router = APIRouter(prefix="/p/{token}")

    @router.get("/api/events", include_in_schema=False)
    async def events(request: Request, token: str, since: str | None = None) -> SseResponse:
        broker: live.LiveBroker | None = getattr(request.app.state, "live", None)
        if broker is None or not broker.has_capacity():
            raise _busy()
        if not TOKEN_RE.fullmatch(token):
            raise ProjectNotFoundError
        resolved = await broker.source.resolve(token)
        if resolved is None:
            raise ProjectNotFoundError
        project_id, generation = resolved
        cursor = live.parse_cursor(request.headers.get("last-event-id"))
        if cursor is None:
            cursor = live.parse_cursor(since)
        try:
            sub = await broker.subscribe(project_id, generation, cursor)
        except (live.CapacityError, live.UnavailableError) as exc:
            raise _busy() from exc
        return SseResponse(broker, sub)

    return router
