"""Gemeinsame Bausteine fuer die public- und die admin-App.

Hier stehen nur Dinge, die beide Apps brauchen. Admin-spezifisches (Header-
Auswertung des Gateways) liegt ausschliesslich in ``npt.web.admin*`` und wird von
der public-App nie importiert (NPT-02/06).
"""

from __future__ import annotations

import ipaddress
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from npt import limits
from npt.config import Settings
from npt.db.session import DB_UNAVAILABLE_ERRORS
from npt.health import HealthMonitor
from npt.logging_setup import mask_secrets

PKG_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = PKG_DIR / "templates"
STATIC_DIR = PKG_DIR / "static"

access_log = logging.getLogger("npt.access")
db_log = logging.getLogger("npt.db")

# DB_UNAVAILABLE_ERRORS (npt.db.session) -> 503 mit Retry-After statt unbehandelter
# Ausnahme (500 text/plain mit Traceback im Log). Lock-Timeouts im Schreibpfad faengt
# vorher ``editing.busy_as_error`` (503 gesperrt), den erschoepften Pool
# ``sqlalchemy.exc.TimeoutError`` (503 ueberlastet, kein OperationalError).
# Andere DB-Fehler (Integritaet, Programmierfehler) bleiben 500.

# CSP ohne unsafe-inline (Auftrag 8.2, NPT-01): alle Assets lokal.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


class NptFastAPI(FastAPI):
    """FastAPI mit festen aeusseren Schichten (Security-Header, Access-Log, Body-Grenze).

    Mit ``add_middleware`` registrierte Middleware liegt in Starlette *innerhalb* der
    ``ServerErrorMiddleware``: deren 500-Antwort bei unbehandelten Ausnahmen bekaeme
    dann weder Security-Header noch einen Access-Log-Eintrag. Die Schichten in
    ``outer`` umschliessen deshalb den kompletten Stack (erste = aeusserste).
    """

    def __init__(self, *args: Any, outer: Sequence[Callable[[ASGIApp], ASGIApp]] = (), **kw: Any):
        self._outer = list(outer)
        super().__init__(*args, **kw)

    def build_middleware_stack(self) -> ASGIApp:
        app = super().build_middleware_stack()
        for wrap in reversed(self._outer):
            app = wrap(app)
        return app


class BodyLimitMiddleware:
    """Begrenzt die Groesse jedes Request-Bodys (auch ohne Content-Length/chunked).

    Content-Length ueber der Grenze -> sofort 413. Sonst wird beim Lesen mitgezaehlt;
    bei Ueberschreitung antwortet die Middleware selbst mit 413, meldet der App einen
    Verbindungsabbruch und verwirft deren weitere Ausgaben (FastAPI wuerde eine
    Ausnahme im ``receive`` sonst als 400 „Body nicht lesbar" beantworten).
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for key, value in scope.get("headers", []):
            if key == b"content-length" and (not value.isdigit() or int(value) > self.max_bytes):
                await self._send_413(send)
                return
        received = 0
        started = False
        rejected = False

        async def limited_receive() -> Message:
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    rejected = True
                    if not started:
                        await self._send_413(send)
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if rejected:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, limited_receive, tracking_send)

    @staticmethod
    async def _send_413(send: Send) -> None:
        body = b"Anfrage zu gross.\n"
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    """Setzt Security-Header auf jede Antwort (reines ASGI, kein Body-Puffern)."""

    def __init__(
        self, app: ASGIApp, *, referrer_policy: str = "no-referrer", no_store: bool = False
    ) -> None:
        self.app = app
        headers = {
            b"content-security-policy": CSP.encode(),
            b"x-content-type-options": b"nosniff",
            b"x-frame-options": b"DENY",
            b"referrer-policy": referrer_policy.encode(),
            b"cross-origin-opener-policy": b"same-origin",
            b"cross-origin-resource-policy": b"same-origin",
            b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=()",
            b"x-robots-tag": b"noindex, nofollow",
        }
        if no_store:
            headers[b"cache-control"] = b"no-store"
        self._headers = headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in self._headers.items() if k not in existing]
                message["headers"] = [*message.get("headers", []), *extra]
            await send(message)

        await self.app(scope, receive, send_wrapper)


def direct_peer(scope: Scope) -> str | None:
    client = scope.get("client")
    return client[0] if client else None


def ip_in(ip: str | None, networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def client_ip(
    scope: Scope, trusted: list[ipaddress.IPv4Network | ipaddress.IPv6Network]
) -> str | None:
    """Client-IP; X-Forwarded-For nur, wenn der direkte Absender ein vertrauter Proxy ist.

    Es wird von rechts der erste nicht vertraute Eintrag genommen (NPT-09).
    """
    peer = direct_peer(scope)
    if not trusted or not ip_in(peer, trusted):
        return peer
    xff_values = [v for k, v in scope.get("headers", []) if k == b"x-forwarded-for"]
    if not xff_values:
        return peer
    chain = [p.strip() for v in xff_values for p in v.decode("latin-1").split(",") if p.strip()]
    for hop in reversed(chain):
        if not ip_in(hop, trusted):
            try:
                return str(ipaddress.ip_address(hop))
            except ValueError:
                return peer
    return chain[0] if chain else peer


class AccessLogMiddleware:
    """Eigenes Access-Log mit maskierten Pfaden (Projekt-Token nie im Log)."""

    def __init__(self, app: ASGIApp, *, settings: Settings, component: str) -> None:
        self.app = app
        self._trusted = settings.trusted_proxy_networks
        self._component = component

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if path not in ("/healthz", "/readyz"):
                qs = scope.get("query_string", b"").decode("latin-1")
                access_log.info(
                    "request",
                    extra={
                        "component": self._component,
                        "method": scope.get("method"),
                        "path": mask_secrets(path + ("?" + qs if qs else "")),
                        "status": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                        "client_ip": client_ip(scope, self._trusted),
                    },
                )


def log_db_unavailable(component: str, request: Request, exc: BaseException) -> None:
    """Eine Warnzeile ohne Traceback (Pfad maskiert, kein Ausnahmetext mit SQL)."""
    orig = getattr(exc, "orig", None) or exc
    db_log.warning(
        "datenbank_nicht_erreichbar",
        extra={
            "component": component,
            "method": request.method,
            "path": mask_secrets(request.url.path),
            "error": type(orig).__name__,
            "sqlstate": getattr(orig, "sqlstate", None),
        },
    )


def register_db_unavailable(
    app: FastAPI, component: str, respond: Callable[[Request], Response]
) -> None:
    """Registriert den 503-Handler fuer :data:`DB_UNAVAILABLE_ERRORS` (beide Apps)."""

    async def handler(request: Request, exc: Exception) -> Response:
        log_db_unavailable(component, request, exc)
        return respond(request)

    for cls in DB_UNAVAILABLE_ERRORS:
        app.add_exception_handler(cls, handler)


def make_templates(settings: Settings, static_prefix: str) -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    env = templates.env
    env.autoescape = True
    tz = ZoneInfo(settings.DISPLAY_TZ)

    def fmt_dt(value: datetime | None) -> str:
        if value is None:
            return ""
        return value.astimezone(tz).strftime("%d.%m.%Y %H:%M:%S")

    env.filters["dt"] = fmt_dt
    env.globals["static_prefix"] = static_prefix
    return templates


def health_router(get_monitor: Callable[[], HealthMonitor | None]) -> APIRouter:
    """``/healthz`` (Liveness) und ``/readyz`` (DB erreichbar) – nur Schnappschuss."""
    router = APIRouter()

    @router.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        mon = get_monitor()
        if mon is None:
            return JSONResponse({"status": "ok", "monitor": "aus"})
        snap = mon.snapshot()
        code = 200 if snap.alive else 503
        return JSONResponse(
            {"status": "ok" if snap.alive else "stalled"},
            status_code=code,
            headers={"cache-control": "no-store"},
        )

    @router.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        mon = get_monitor()
        snap = mon.snapshot() if mon is not None else None
        if snap is not None and snap.ready:
            return JSONResponse({"status": "ready"}, headers={"cache-control": "no-store"})
        return JSONResponse(
            {"status": "not_ready", "reason": "db"},
            status_code=503,
            headers={
                "cache-control": "no-store",
                "retry-after": str(limits.READY_RETRY_AFTER_S),
            },
        )

    return router


def robots_route(app: FastAPI) -> None:
    @app.get("/robots.txt", include_in_schema=False)
    async def robots() -> PlainTextResponse:
        return PlainTextResponse("User-agent: *\nDisallow: /\n")


LifespanPart = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def monitored_lifespan(
    role: str, enable_monitor: bool, parts: Sequence[LifespanPart] = ()
) -> Callable[[FastAPI], Any]:
    """Lifespan: Grenzen pruefen, Health-Monitor und weitere Teile starten/stoppen."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        limits.check_ranking()
        monitor: HealthMonitor | None = None
        if enable_monitor:
            monitor = HealthMonitor(role=role)
            monitor.start()
        app.state.health_monitor = monitor
        try:
            async with AsyncExitStack() as stack:
                for part in parts:
                    await stack.enter_async_context(part(app))
                yield
        finally:
            if monitor is not None:
                monitor.stop()

    return lifespan


def monitor_getter(app: FastAPI) -> Callable[[], HealthMonitor | None]:
    def get() -> HealthMonitor | None:
        return getattr(app.state, "health_monitor", None)

    return get
