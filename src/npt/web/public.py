"""Bearbeiter-App (oeffentlicher Port).

Diese Factory importiert bewusst keine Admin-Router und wertet keine
``X-SSL-Client-*``-Header aus (NPT-02/06). Admin-Routen existieren hier nicht;
``/admin*`` liefert 404 (Auftrag 8.1).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from functools import partial
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from npt import editing, limits, live
from npt.config import Settings, get_settings
from npt.web.admission import Admission, OverloadedError
from npt.web.common import (
    STATIC_DIR,
    AccessLogMiddleware,
    BodyLimitMiddleware,
    NptFastAPI,
    SecurityHeadersMiddleware,
    health_router,
    make_templates,
    monitor_getter,
    monitored_lifespan,
    register_db_unavailable,
    robots_route,
)
from npt.web.public_live import build_live_router
from npt.web.public_overview import build_overview_router
from npt.web.public_project import (
    JSON_BODY_MAX,
    ProjectNotFoundError,
    PublicError,
    build_router,
)


def _is_api(request: Request) -> bool:
    return "/api/" in request.url.path


def _json_error(
    status: int, reason: str, headers: dict[str, str] | None = None, **extra: Any
) -> JSONResponse:
    hdrs = {"cache-control": "no-store", **(headers or {})}
    return JSONResponse({"reason": reason, **extra}, status_code=status, headers=hdrs)


def live_lifespan(settings: Settings) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Ein Broker (eine LISTEN-Verbindung) je Worker-Prozess (NPT-03)."""

    @asynccontextmanager
    async def part(app: FastAPI) -> AsyncIterator[None]:
        broker = live.LiveBroker(live.PgLiveSource(settings.sqlalchemy_url))
        await broker.start()
        app.state.live = broker
        try:
            yield
        finally:
            app.state.live = None
            await broker.stop()

    return part


def create_public_app(
    settings: Settings | None = None,
    *,
    session_factory: Any = None,
    enable_monitor: bool = True,
    enable_live: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    if session_factory is None:
        from npt.db.session import new_session

        session_factory = new_session

    app = NptFastAPI(
        # Aeussere Schichten (erste = aussen): Access-Log, Security-Header (auch auf
        # 500-Antworten der ServerErrorMiddleware), Body-Grenze (auch chunked).
        outer=[
            partial(AccessLogMiddleware, settings=settings, component="public"),
            partial(SecurityHeadersMiddleware, referrer_policy="no-referrer"),
            # Rueckfallnetz ueber der fachlichen 64-KB-Pruefung (check_write, JSON-413).
            partial(BodyLimitMiddleware, max_bytes=2 * JSON_BODY_MAX),
        ],
        title="Notfall-Patch-Tracker",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=monitored_lifespan(
            "public", enable_monitor, [live_lifespan(settings)] if enable_live else []
        ),
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    templates = make_templates(settings, static_prefix="/static")

    app.include_router(health_router(monitor_getter(app)))
    robots_route(app)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "public/index.html", {})

    admission = Admission()
    app.include_router(build_router(settings, templates, admission))
    app.include_router(build_overview_router(templates, admission))
    app.include_router(build_live_router())

    @app.exception_handler(ProjectNotFoundError)
    async def not_found_handler(request: Request, exc: ProjectNotFoundError) -> Response:
        if _is_api(request):
            return _json_error(404, "nicht_gefunden")
        return templates.TemplateResponse(
            request,
            "public/not_found.html",
            {},
            status_code=404,
            headers={"cache-control": "no-store"},
        )

    @app.exception_handler(PublicError)
    async def public_error_handler(request: Request, exc: PublicError) -> JSONResponse:
        return _json_error(exc.status, exc.reason, exc.headers, **exc.extra)

    @app.exception_handler(editing.WriteError)
    async def write_error_handler(request: Request, exc: editing.WriteError) -> JSONResponse:
        extra: dict[str, Any] = {"message": exc.message} if exc.message else {}
        headers: dict[str, str] = {}
        if isinstance(exc, editing.ConflictError):
            extra.update(field=exc.field, current=exc.current, row=exc.row)
        elif isinstance(exc, editing.InputError):
            extra["errors"] = exc.errors
        elif isinstance(exc, editing.BusyError):
            headers["retry-after"] = str(limits.BUSY_RETRY_AFTER_S)
        return _json_error(exc.status, exc.reason, headers, **extra)

    @app.exception_handler(OverloadedError)
    async def overloaded_handler(request: Request, exc: OverloadedError) -> JSONResponse:
        return _json_error(
            503, "ueberlastet", {"retry-after": str(limits.OVERLOADED_RETRY_AFTER_S)}
        )

    @app.exception_handler(PoolTimeoutError)
    async def pool_timeout_handler(request: Request, exc: PoolTimeoutError) -> JSONResponse:
        return _json_error(
            503, "ueberlastet", {"retry-after": str(limits.OVERLOADED_RETRY_AFTER_S)}
        )

    def db_unavailable(request: Request) -> Response:
        # Gleiches Muster wie der pool_timeout-Pfad, eigener Grund und laengeres Retry-After.
        retry = {"retry-after": str(limits.DB_UNAVAILABLE_RETRY_AFTER_S)}
        if _is_api(request):
            return _json_error(503, "datenbank", retry)
        return templates.TemplateResponse(
            request,
            "public/unavailable.html",
            {},
            status_code=503,
            headers={"cache-control": "no-store", **retry},
        )

    register_db_unavailable(app, "public", db_unavailable)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors: dict[str, str] = {}
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", ()) if p != "body"]
            key = loc[-1] if loc else "body"
            if err.get("type") == "extra_forbidden":
                errors[key] = "Feld ist nicht erlaubt"
            elif key == "field" and err.get("type") == "literal_error":
                errors[key] = "Feld ist nicht schreibbar"
            else:
                errors[key] = str(err.get("msg", "ungültig"))[:200]
        return _json_error(422, "validierung", errors=errors)

    return app


def _create_default() -> FastAPI:
    return create_public_app()


# Einstiegspunkt fuer Gunicorn: ``npt.web.public:app``.
app = _create_default()
