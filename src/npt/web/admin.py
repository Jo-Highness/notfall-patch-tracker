"""Admin-App (interner Port, nur ueber das admin-gateway erreichbar).

Alle Routen liegen unter ``/admin``. Die Authentisierung erledigt
``AdminAuthMiddleware``; Autorisierung (Admin ja/nein) und CSRF pruefen
Dependencies. Nicht-Admins mit gueltigem Zertifikat sehen die Anfrage-Seite.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from npt import admins, handbook, limits, projects
from npt.config import Settings, get_settings
from npt.db.session import DB_UNAVAILABLE_ERRORS
from npt.identity import CertIdentity, IdKind
from npt.web import (
    admin_backups,
    admin_changelog,
    admin_mail,
    admin_master,
    admin_projects,
    admin_upload,
)
from npt.web.admin_auth import AdminAuthMiddleware
from npt.web.admin_deps import (
    AdminDep,
    BodyTooLargeError,
    CsrfRejectedError,
    DbDep,
    NotAdminError,
    PrincipalDep,
    Renderer,
    actor_of,
    get_principal,
    redirect,
    require_csrf,
)
from npt.web.common import (
    STATIC_DIR,
    AccessLogMiddleware,
    NptFastAPI,
    SecurityHeadersMiddleware,
    log_db_unavailable,
    make_templates,
    monitored_lifespan,
    register_db_unavailable,
)

log = logging.getLogger("npt.admin")


# ---------------------------------------------------------------------------
# App-Factory
# ---------------------------------------------------------------------------
def create_admin_app(  # noqa: PLR0915 - Routen-Registrierung in der Factory
    settings: Settings | None = None,
    *,
    session_factory: Any = None,
    enable_monitor: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    if session_factory is None:
        from npt.db.session import new_session

        session_factory = new_session
    app = NptFastAPI(
        # Aeussere Schichten (erste = aussen), auch fuer 500-Antworten. Admin-Seiten
        # enthalten keine Token-URLs im Pfad; „same-origin" ist noetig, damit Browser
        # bei Formular-POSTs einen Origin-Header senden (CSRF, NPT-08, B1).
        outer=[
            partial(AccessLogMiddleware, settings=settings, component="admin"),
            partial(SecurityHeadersMiddleware, referrer_policy="same-origin", no_store=True),
        ],
        title="Notfall-Patch-Tracker Admin",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=monitored_lifespan("admin", enable_monitor),
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    templates = make_templates(settings, static_prefix="/admin/static")
    kind: IdKind = settings.ADMIN_CERT_ID_ATTRIBUTE
    secret = settings.CSRF_SECRET.get_secret_value().encode()
    render = Renderer(templates, secret)

    def access_page(request: Request, principal: CertIdentity, db: Session) -> HTMLResponse:
        pending = admins.open_request_for(db, principal)
        return render(
            request,
            "admin/access.html",
            principal,
            {"pending": pending},
            status_code=403,
        )

    router = APIRouter(prefix="/admin", dependencies=[Depends(require_csrf)])

    @router.get("", include_in_schema=False)
    async def admin_root() -> RedirectResponse:
        return RedirectResponse(url="/admin/", status_code=308)

    @router.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep
    ) -> HTMLResponse:
        return render(
            request,
            "admin/index.html",
            principal,
            {"admin": admin, "projects": projects.list_projects(db)},
        )

    @router.get("/hilfe", response_class=HTMLResponse)
    def help_page(request: Request, principal: PrincipalDep, admin: AdminDep) -> HTMLResponse:
        # Admin-Handbuch (11.2): nur auf dem Admin-Port, nur fuer Admins.
        title, body = handbook.render(
            "admin",
            {
                "public_base_url": settings.PUBLIC_BASE_URL.rstrip("/"),
                "admin_base_url": (settings.ADMIN_BASE_URL or "").rstrip("/"),
            },
        )
        return render(
            request,
            "admin/hilfe.html",
            principal,
            {"admin": admin, "title": title, "handbook": body},
        )

    @router.get("/benutzer", response_class=HTMLResponse)
    def users(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep
    ) -> HTMLResponse:
        return render(
            request,
            "admin/users.html",
            principal,
            {
                "admin": admin,
                "admins": admins.list_admins(db),
                "requests": admins.list_open_requests(db),
                "active_kind": kind,
            },
        )

    @router.post("/benutzer/neu")
    def user_add(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        identifier: Annotated[str, Form(max_length=4096)],
        issuer: Annotated[str, Form(max_length=4096)] = "",
        display_name: Annotated[str, Form(max_length=1000)] = "",
    ) -> Response:
        try:
            admins.add_admin(
                db, actor_of(request, principal), kind, identifier, issuer, display_name
            )
            db.commit()
        except admins.AdminError as exc:
            db.rollback()
            return redirect("/admin/benutzer", fehler=exc.code)
        return redirect("/admin/benutzer", ok="hinzugefuegt")

    def _toggle(
        request: Request, principal: CertIdentity, db: Session, uid: int, on: bool
    ) -> Response:
        try:
            admins.set_admin_active(db, actor_of(request, principal), kind, uid, on)
            db.commit()
        except admins.AdminError as exc:
            db.rollback()
            return redirect("/admin/benutzer", fehler=exc.code)
        return redirect("/admin/benutzer", ok="aktiviert" if on else "deaktiviert")

    @router.post("/benutzer/{admin_id}/deaktivieren")
    def user_deactivate(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, admin_id: int
    ) -> Response:
        return _toggle(request, principal, db, admin_id, False)

    @router.post("/benutzer/{admin_id}/aktivieren")
    def user_activate(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, admin_id: int
    ) -> Response:
        return _toggle(request, principal, db, admin_id, True)

    @router.post("/benutzer/{admin_id}/entfernen")
    def user_remove(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, admin_id: int
    ) -> Response:
        try:
            admins.remove_admin(db, actor_of(request, principal), kind, admin_id)
            db.commit()
        except admins.AdminError as exc:
            db.rollback()
            return redirect("/admin/benutzer", fehler=exc.code)
        return redirect("/admin/benutzer", ok="entfernt")

    def _decide(
        request: Request, principal: CertIdentity, db: Session, rid: int, approve: bool
    ) -> Response:
        try:
            admins.decide_request(db, actor_of(request, principal), rid, approve)
            db.commit()
        except admins.AdminError as exc:
            db.rollback()
            return redirect("/admin/benutzer", fehler=exc.code)
        return redirect("/admin/benutzer", ok="bestaetigt" if approve else "abgelehnt")

    @router.post("/anfragen/{request_id}/bestaetigen")
    def request_approve(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, request_id: int
    ) -> Response:
        return _decide(request, principal, db, request_id, True)

    @router.post("/anfragen/{request_id}/ablehnen")
    def request_reject(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, request_id: int
    ) -> Response:
        return _decide(request, principal, db, request_id, False)

    @router.get("/audit", response_class=HTMLResponse)
    def audit_log(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep
    ) -> HTMLResponse:
        return render(
            request,
            "admin/audit.html",
            principal,
            {"admin": admin, "entries": admins.list_audit(db)},
        )

    @router.get("/zugang", response_class=HTMLResponse)
    def access(request: Request, principal: PrincipalDep, db: DbDep) -> Response:
        if admins.find_active_admin(db, principal) is not None:
            return redirect("/admin/")
        return access_page(request, principal, db)

    @router.post("/zugang/anfrage")
    def access_request(request: Request, principal: PrincipalDep, db: DbDep) -> Response:
        if admins.find_active_admin(db, principal) is not None:
            return redirect("/admin/")
        try:
            created = admins.create_access_request(
                db, principal, actor_of(request, principal).client_ip
            )
            db.commit()
        except admins.AdminError as exc:
            db.rollback()
            return redirect("/admin/zugang", fehler=exc.code)
        return redirect("/admin/zugang", ok="angefragt" if created else "bereits_angefragt")

    app.include_router(router)
    app.include_router(admin_master.build_router(render, settings))
    app.include_router(admin_projects.build_router(render, settings))
    app.include_router(admin_upload.build_router(render, settings))
    app.include_router(admin_changelog.build_router(render, settings))
    app.include_router(admin_backups.build_router(render, settings))
    app.include_router(admin_mail.build_router(render, settings))
    app.mount("/admin/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def db_unavailable(request: Request) -> Response:
        return PlainTextResponse(
            "Die Datenbank ist vorübergehend nicht erreichbar. "
            "Bitte in einigen Sekunden erneut versuchen.\n",
            status_code=503,
            headers={"retry-after": str(limits.DB_UNAVAILABLE_RETRY_AFTER_S)},
        )

    register_db_unavailable(app, "admin", db_unavailable)

    @app.exception_handler(PoolTimeoutError)
    async def pool_timeout_handler(request: Request, exc: PoolTimeoutError) -> Response:
        return PlainTextResponse(
            "Der Server ist ausgelastet. Bitte in einigen Sekunden erneut versuchen.\n",
            status_code=503,
            headers={"retry-after": str(limits.OVERLOADED_RETRY_AFTER_S)},
        )

    @app.exception_handler(NotAdminError)
    async def not_admin_handler(request: Request, exc: NotAdminError) -> Response:
        principal = get_principal(request)

        def _render() -> HTMLResponse:
            session: Session = app.state.session_factory()
            try:
                return access_page(request, principal, session)
            finally:
                session.close()

        # Fehler in einem Exception-Handler landen sonst als 500 in der ServerErrorMiddleware.
        try:
            return await run_in_threadpool(_render)
        except DB_UNAVAILABLE_ERRORS as db_exc:
            log_db_unavailable("admin", request, db_exc)
            return db_unavailable(request)

    @app.exception_handler(CsrfRejectedError)
    async def csrf_handler(request: Request, exc: CsrfRejectedError) -> PlainTextResponse:
        return PlainTextResponse(
            "Sicherheitsprüfung (CSRF) fehlgeschlagen. Bitte Seite neu laden.\n", status_code=403
        )

    @app.exception_handler(BodyTooLargeError)
    async def too_large_handler(request: Request, exc: BodyTooLargeError) -> PlainTextResponse:
        return PlainTextResponse(
            f"Die Datei ist zu groß (höchstens {settings.UPLOAD_MAX_MB} MB).\n", status_code=413
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException) -> PlainTextResponse:
        text = {404: "Nicht gefunden.", 403: "Zugriff verweigert.", 405: "Methode nicht erlaubt."}
        return PlainTextResponse(
            text.get(exc.status_code, "Fehler.") + "\n", status_code=exc.status_code
        )

    # Auth liegt innerhalb der aeusseren Schichten, damit Security-Header und
    # Access-Log auch fuer abgelehnte Requests greifen.
    app.add_middleware(AdminAuthMiddleware, settings=settings)
    return app


def _create_default() -> FastAPI:
    return create_admin_app()


# Einstiegspunkt fuer Gunicorn: ``npt.web.admin:app``.
app = _create_default()
