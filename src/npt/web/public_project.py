"""Bearbeiter-Bereich eines Projekts unter ``/p/<token>`` (Auftrag 5.2/5.3/5.6).

- ``GET  /p/<token>``                      Hauptliste (HTML, Tabulator)
- ``GET  /p/<token>/api/systems``           alle Zeilen als JSON (Allowlist-DTO),
  mit ``?updated_since=<seq>`` nur das Delta (Polling-Fallback, NPT-03)
- ``PATCH /p/<token>/api/systems/<id>``     ein *-Feld schreiben (NPT-04)
- ``POST /p/<token>/api/systems``           neues System ``NEW-xxxx`` anlegen
- ``GET  /p/<token>/export.xlsx``           Export (gefilterte Sicht oder alle)
- ``GET  /p/<token>/hilfe``                 Bearbeiter-Handbuch (docs/handbuch/bearbeiter.md)
- Uebersicht (Pivot): ``npt.web.public_overview``, Live-Stream: ``npt.web.public_live``

Schreibzugriffe: JSON-Content-Type, CSRF-Token (Scope Projekt + token_generation)
im Header ``X-CSRF-Token``; ein vorhandener, nicht-``null`` Origin muss exakt der
Basis-URL entsprechen (Entscheidung B1, DECISIONS.md). Keine CORS-Header.
Dieses Modul importiert nichts aus dem Admin-Bereich (NPT-02).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Final, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from npt import csrf, editing, export, handbook, live, ratelimit
from npt.config import Settings
from npt.db.models import Project
from npt.domain import fields, filters, labels
from npt.web.admission import Admission
from npt.web.common import client_ip

log = logging.getLogger("npt.public")

TOKEN_RE: Final = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
JSON_BODY_MAX: Final = 64 * 1024
TEXT_IN_MAX: Final = 10_000
NO_STORE: Final = {"cache-control": "no-store"}


class PublicError(Exception):
    def __init__(
        self, status: int, reason: str, headers: dict[str, str] | None = None, **extra: Any
    ) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.headers = headers or {}
        self.extra = extra


class ProjectNotFoundError(Exception):
    """Unbekannter/ungueltiger Token – HTML- bzw. JSON-404."""


@dataclass(frozen=True)
class ProjectCtx:
    id: int
    name: str
    description: str | None
    status: str
    token_generation: int
    base: str  # "/p/<token>" fuer Links und API-Aufrufe

    @property
    def read_only(self) -> bool:
        return self.status != "aktiv"


class PatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal["affected", "installed_version", "action_taken", "remark"]
    value: str | None = Field(max_length=TEXT_IN_MAX)
    expected: str | None = Field(max_length=TEXT_IN_MAX)
    client_name: str | None = Field(default=None, max_length=200)


class NewSystemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_name: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    sbf: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    digital_access_zone: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    system_description: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    app_owner: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    app_type: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    lifecycle: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    fqdn: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    cio_unit: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    foreign_key: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    affected: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    installed_version: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    action_taken: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    remark: str | None = Field(default=None, max_length=TEXT_IN_MAX)
    client_name: str | None = Field(default=None, max_length=200)


def get_db(request: Request) -> Any:
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


DbDep = Annotated[Session, Depends(get_db)]


def resolve_project(token: str, db: DbDep) -> ProjectCtx:
    if not TOKEN_RE.fullmatch(token):
        raise ProjectNotFoundError
    row = db.execute(
        select(
            Project.id, Project.name, Project.description, Project.status, Project.token_generation
        ).where(Project.access_token == token)
    ).first()
    if row is None:
        raise ProjectNotFoundError
    return ProjectCtx(*row, base=f"/p/{token}")


ProjectDep = Annotated[ProjectCtx, Depends(resolve_project)]


def project_config(project: ProjectCtx) -> dict[str, Any]:
    """Konfiguration fuer das Grid (als JSON-Datenblock in die Seite eingebettet)."""
    cols = []
    for name in fields.LIST_FIELDS:
        enum = fields.ENUM_FIELDS.get(name)
        cols.append(
            {
                "field": name,
                "title": fields.DISPLAY_NAMES[name],
                "editable": name in fields.EDITABLE_FIELDS and not project.read_only,
                "enum": dict(enum) if enum is not None else None,
                "hint": labels.FIELD_HINTS.get(name),
                "maxlength": {
                    "installed_version": fields.INSTALLED_VERSION_MAX,
                    "remark": fields.REMARK_MAX,
                }.get(name),
            }
        )
    return {
        "base": project.base,
        "columns": cols,
        "readOnly": project.read_only,
        "emptyKey": filters.EMPTY_KEY,
        "emptyLabel": labels.EMPTY_LABEL,
        "masterFields": list(fields.MASTER_DATA_FIELDS),
        "editableFields": list(fields.EDITABLE_FIELDS),
        "suggestFields": ["digital_access_zone", "sbf", "app_type", "lifecycle", "cio_unit"],
        "masterMax": {k: v for k, v in fields.MASTER_FIELD_MAX.items() if k != "system_id"},
    }


def check_write(request: Request, project: ProjectDep) -> ProjectCtx:
    """CSRF/Origin/Content-Type/Groesse fuer Bearbeiter-Schreibzugriffe (B1).

    Als Dependency, damit die Pruefung vor dem Validieren des Bodys greift.
    """
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise PublicError(415, "content_type")
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > JSON_BODY_MAX:
        raise PublicError(413, "zu_gross")
    settings: Settings = request.app.state.settings
    secret = settings.CSRF_SECRET.get_secret_value().encode()
    fetch_site = request.headers.get("sec-fetch-site")
    try:
        if fetch_site is not None and fetch_site not in ("same-origin", "none"):
            raise csrf.CsrfError(f"Sec-Fetch-Site {fetch_site}")
        origin = request.headers.get("origin")
        if origin is not None and origin != "null":
            csrf.verify_origin(origin, settings.PUBLIC_BASE_URL)
        csrf.verify_token(
            secret,
            csrf.project_scope(project.id, project.token_generation),
            request.headers.get("x-csrf-token"),
        )
    except csrf.CsrfError as exc:
        log.warning("CSRF abgelehnt (Bearbeiter)", extra={"reason": str(exc)})
        raise PublicError(403, "csrf") from exc
    return project


WriteProjectDep = Annotated[ProjectCtx, Depends(check_write)]


def systems_payload(db: Session, project: ProjectCtx, updated_since: str | None) -> dict[str, Any]:
    """Alle Zeilen oder Delta seit Cursor ``updated_since`` (NPT-03, Abweichung A1).

    Der Cursor (``seq``) wird **vor** den Zeilen gelesen, damit keine Aenderung
    zwischen beiden Abfragen verloren geht. Cursor unter ``reset_seq`` (oder ueber
    dem Projektstand) -> ``full_reload: true`` mit allen Zeilen.
    """
    conn = db.connection()
    if updated_since is None:
        meta = live.read_meta(conn, project.id)
        delta = live.Delta(meta, None, editing.list_rows(db, project.id), full_reload=True)
    else:
        since = live.parse_cursor(updated_since)
        if since is None:
            raise PublicError(
                422, "validierung", errors={"updated_since": "Ganzzahl >= 0 erwartet"}
            )
        delta = live.fetch_delta(conn, project.id, since, all_rows_on_reload=True)
    if delta.meta is None:
        raise ProjectNotFoundError
    return {
        "project": {"name": project.name, **delta.meta.public()},
        "seq": delta.seq,
        "full_reload": delta.full_reload,
        "rows": delta.rows,
    }


def build_router(settings: Settings, templates: Jinja2Templates, admission: Admission) -> APIRouter:
    router = APIRouter(prefix="/p/{token}", dependencies=[Depends(admission)])
    secret = settings.CSRF_SECRET.get_secret_value().encode()
    tz = ZoneInfo(settings.DISPLAY_TZ)
    trusted = settings.trusted_proxy_networks
    write_limit = ratelimit.Limit(
        "w", settings.RATE_LIMIT_WRITES, settings.RATE_LIMIT_WRITES_WINDOW_S
    )
    create_limit = ratelimit.Limit(
        "c", settings.RATE_LIMIT_CREATE, settings.RATE_LIMIT_CREATE_WINDOW_S
    )

    def csrf_token_for(project: ProjectCtx) -> str:
        return csrf.issue_token(secret, csrf.project_scope(project.id, project.token_generation))

    def limited(db: Session, ip: str | None, *limits_: ratelimit.Limit) -> None:
        for lim in limits_:
            try:
                ratelimit.hit(db, lim, ip or "unbekannt")
            except ratelimit.RateLimitedError as exc:
                raise PublicError(429, "rate_limit", {"retry-after": str(exc.retry_after)}) from exc

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    def main_list(request: Request, project: ProjectDep) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "public/hauptliste.html",
            {
                "project": project,
                "status_label": labels.PROJECT_STATUS.get(project.status, project.status),
                "csrf_token": csrf_token_for(project),
                "config": project_config(project),
                "fields": fields,
                "labels": labels,
                "nav_active": "liste",
            },
            headers=NO_STORE,
        )

    @router.get("/api/systems")
    def list_systems(
        project: ProjectDep, db: DbDep, updated_since: str | None = None
    ) -> JSONResponse:
        return JSONResponse(systems_payload(db, project, updated_since), headers=NO_STORE)

    @router.patch("/api/systems/{system_id:path}")
    def patch_system(
        request: Request, project: WriteProjectDep, db: DbDep, system_id: str, body: PatchBody
    ) -> JSONResponse:
        ip = client_ip(request.scope, trusted)
        limited(db, ip, write_limit)
        try:
            result = editing.update_field(
                db,
                project.id,
                system_id,
                body.field,
                body.value,
                body.expected,
                client_ip=ip,
                client_name=body.client_name,
            )
            db.commit()
        finally:
            if db.in_transaction():
                db.rollback()
        return JSONResponse(
            {"ok": True, "changed": result.changed, "seq": result.seq, "row": result.row},
            headers=NO_STORE,
        )

    @router.post("/api/systems", status_code=201)
    def create_system(
        request: Request, project: WriteProjectDep, db: DbDep, body: NewSystemBody
    ) -> JSONResponse:
        if project.read_only:
            raise editing.ReadOnlyError("Projekt ist abgeschlossen oder archiviert")
        ip = client_ip(request.scope, trusted)
        limited(db, ip, write_limit, create_limit)
        data = body.model_dump(exclude={"client_name"})
        try:
            row = editing.create_system(
                db, project.id, data, client_ip=ip, client_name=body.client_name
            )
            db.commit()
        finally:
            if db.in_transaction():
                db.rollback()
        return JSONResponse({"ok": True, "row": row}, status_code=201, headers=NO_STORE)

    @router.get("/export.xlsx")
    def export_xlsx(request: Request, project: ProjectDep, db: DbDep) -> Response:
        params = [(k, v) for k, v in request.query_params.multi_items() if k != "umfang"]
        scope = request.query_params.get("umfang", "alle")
        rows = editing.list_rows(db, project.id)
        if scope == "sicht":
            rows = [dict(r) for r in filters.apply(rows, filters.parse(params))]
        data = export.systems_workbook(rows)
        name = export.download_name(
            project.name, "sicht" if scope == "sicht" else "alle", datetime.now(tz=tz)
        )
        return Response(data, media_type=export.XLSX_MIME, headers=export.attachment_headers(name))

    @router.get("/hilfe", response_class=HTMLResponse, include_in_schema=False)
    def help_page(request: Request, project: ProjectDep) -> HTMLResponse:
        # Bearbeiter-Handbuch (11.2): Markdown-Quelle, sicher gerendert, Platzhalter escaped.
        title, body = handbook.render(
            "bearbeiter",
            {
                "projekt": project.name,
                "projekt_url": settings.PUBLIC_BASE_URL.rstrip("/") + project.base + "/",
            },
        )
        return templates.TemplateResponse(
            request,
            "public/hilfe.html",
            {"project": project, "nav_active": "hilfe", "title": title, "handbook": body},
            headers=NO_STORE,
        )

    return router
