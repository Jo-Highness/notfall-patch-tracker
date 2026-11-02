"""Admin: Projekte anlegen und verwalten (Auftrag 5.1, 5.6, 6.4, 7)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from npt import backups, editing, export, projects
from npt.admins import audit
from npt.config import Settings
from npt.db.models import AdminUser
from npt.domain import fields
from npt.identity import CertIdentity
from npt.web.admin_deps import (
    AdminDep,
    DbDep,
    PrincipalDep,
    Renderer,
    actor_of,
    redirect,
    require_csrf,
)

log = logging.getLogger("npt.admin.projects")

TEXT_FORM_MAX = 10_000


def project_url(settings: Settings, token: str) -> str:
    return f"{settings.PUBLIC_BASE_URL}/p/{token}"


def write_error_code(exc: editing.WriteError) -> str:
    return {
        editing.NotFoundError: "system_nicht_gefunden",
        editing.ReadOnlyError: "schreibgeschuetzt",
        editing.BusyError: "gesperrt",
        editing.ConflictError: "system_konflikt",
    }.get(type(exc), "projekt_nicht_gefunden")


def build_router(render: Renderer, settings: Settings) -> APIRouter:  # noqa: PLR0915
    router = APIRouter(prefix="/admin/projekte", dependencies=[Depends(require_csrf)])
    tz = ZoneInfo(settings.DISPLAY_TZ)

    def detail_path(pid: int) -> str:
        return f"/admin/projekte/{pid}"

    @router.post("")
    def create(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        name: Annotated[str, Form(max_length=1000)] = "",
        description: Annotated[str, Form(max_length=TEXT_FORM_MAX)] = "",
    ) -> Response:
        try:
            project = projects.create_project(db, actor_of(request, principal), name, description)
            db.commit()
        except projects.ProjectError as exc:
            db.rollback()
            return redirect("/admin/", fehler=exc.code)
        return redirect(detail_path(project.id), ok="projekt_angelegt")

    @router.get("/{pid}", response_class=HTMLResponse)
    def detail(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int, q: str = ""
    ) -> Response:
        try:
            summary = projects.get_summary(db, pid)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)
        q = q[:200]
        return render(
            request,
            "admin/projekt.html",
            principal,
            {
                "admin": admin,
                "s": summary,
                "p": summary.project,
                "url": project_url(settings, summary.project.access_token),
                "systems": projects.find_systems(db, pid, q),
                "q": q,
                "transitions": sorted(projects.STATUS_TRANSITIONS[summary.project.status]),
                "backups": backups.list_backups(db, pid),
                "has_changes": backups.has_changes(db, pid),
            },
        )

    @router.post("/{pid}/token")
    def new_token(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int
    ) -> Response:
        try:
            projects.regenerate_token(db, actor_of(request, principal), pid)
            db.commit()
        except (projects.ProjectError, editing.WriteError):
            db.rollback()
            return redirect("/admin/", fehler="projekt_nicht_gefunden")
        return redirect(detail_path(pid), ok="token_neu")

    @router.post("/{pid}/status")
    def change_status(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        status: Annotated[str, Form(max_length=20)],
    ) -> Response:
        try:
            projects.set_status(db, actor_of(request, principal), pid, status)
            db.commit()
        except projects.ProjectError as exc:
            db.rollback()
            return redirect(detail_path(pid), fehler=exc.code)
        except editing.WriteError:
            db.rollback()
            return redirect("/admin/", fehler="projekt_nicht_gefunden")
        return redirect(detail_path(pid), ok="status_geaendert")

    @router.post("/{pid}/loeschen")
    def delete(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        confirm_name: Annotated[str, Form(max_length=1000)] = "",
    ) -> Response:
        try:
            projects.delete_project(db, actor_of(request, principal), pid, confirm_name)
            db.commit()
        except projects.ProjectError as exc:
            db.rollback()
            if exc.code == "name_bestaetigung_falsch":
                return redirect(detail_path(pid), fehler=exc.code)
            return redirect("/admin/", fehler=exc.code)
        except editing.WriteError:
            db.rollback()
            return redirect("/admin/", fehler="projekt_nicht_gefunden")
        # Backups gehoeren zum Projekt (Zeilen per Cascade geloescht) -> Dateien entfernen.
        backups.remove_project_dir(Path(settings.BACKUP_DIR), pid)
        return redirect("/admin/", ok="projekt_geloescht")

    def edit_page(
        request: Request,
        principal: CertIdentity,
        admin: AdminUser,
        db: Session,
        pid: int,
        sid: str,
        *,
        values: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
        status_code: int = 200,
    ) -> Response:
        try:
            project = projects.get_project(db, pid)
            system = projects.get_system(db, pid, sid)
        except projects.ProjectError as exc:
            return redirect(
                detail_path(pid) if exc.code != "projekt_nicht_gefunden" else "/admin/",
                fehler=exc.code,
            )
        current = {f: getattr(system, f) for f in fields.MASTER_DATA_FIELDS}
        return render(
            request,
            "admin/projekt_system.html",
            principal,
            {
                "admin": admin,
                "p": project,
                "system": system,
                "values": values or current,
                "errors": errors or {},
                "fields": fields,
            },
            status_code=status_code,
        )

    @router.get("/{pid}/system", response_class=HTMLResponse)
    def edit_system(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int, sid: str
    ) -> Response:
        return edit_page(request, principal, admin, db, pid, sid[:200])

    @router.post("/{pid}/system")
    async def save_system(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int
    ) -> Response:
        form = await request.form()
        sid = str(form.get("sid", ""))[:200]
        version_raw = str(form.get("version", ""))
        data = {f: str(form.get(f, ""))[:TEXT_FORM_MAX] for f in fields.MASTER_DATA_FIELDS}

        def work() -> Response:
            if not version_raw.isdigit():
                return redirect(detail_path(pid), fehler="system_nicht_gefunden")
            actor = actor_of(request, principal)
            try:
                changed = editing.admin_update_system(
                    db,
                    pid,
                    sid,
                    data,
                    int(version_raw),
                    admin_identifier=actor.identifier,
                    client_ip=actor.client_ip,
                )
                if changed:
                    audit(
                        db,
                        actor,
                        "projekt_system_bearbeitet",
                        sid,
                        {"project_id": pid, "felder": changed},
                    )
                db.commit()
            except editing.InputError as exc:
                db.rollback()
                return edit_page(
                    request,
                    principal,
                    admin,
                    db,
                    pid,
                    sid,
                    values=data,
                    errors=exc.errors,
                    status_code=422,
                )
            except editing.WriteError as exc:
                db.rollback()
                code = write_error_code(exc)
                return redirect(f"{detail_path(pid)}/system", sid=sid, fehler=code)
            finally:
                if db.in_transaction():
                    db.rollback()
            return redirect(
                f"{detail_path(pid)}/system",
                sid=sid,
                ok="system_gespeichert" if changed else "system_unveraendert",
            )

        return await run_in_threadpool(work)

    @router.get("/{pid}/manuell.xlsx")
    def export_manual(admin: AdminDep, db: DbDep, pid: int) -> Response:
        try:
            project = projects.get_project(db, pid)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)
        data = export.systems_workbook(projects.manual_systems(db, pid))
        name = export.download_name(project.name, "manuell-angelegt", datetime.now(tz=tz))
        return Response(data, media_type=export.XLSX_MIME, headers=export.attachment_headers(name))

    return router
