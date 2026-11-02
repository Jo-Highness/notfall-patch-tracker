"""Admin: Daten-Upload in ein Projekt mit Konfliktbestaetigung (Auftrag 6.1)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from npt import editing, projects
from npt.admins import audit
from npt.config import Settings
from npt.db.models import AdminUser
from npt.domain import fields
from npt.identity import CertIdentity
from npt.masterdata import service as master_service
from npt.masterdata.source import MasterDataError
from npt.project_upload import service
from npt.project_upload.parse import enum_display, parse_upload
from npt.web.admin_deps import (
    FORM_MAX_FIELDS,
    AdminDep,
    DbDep,
    PrincipalDep,
    Renderer,
    actor_of,
    redirect,
    require_csrf,
)
from npt.web.admin_master import safe_file_name
from npt.web.admin_projects import write_error_code

log = logging.getLogger("npt.admin.upload")

# Formularwerte: Konflikt-Schluessel bzw. System-IDs; Laenge grosszuegig begrenzt.
FORM_VALUE_MAX = 400


def build_router(render: Renderer, settings: Settings) -> APIRouter:  # noqa: PLR0915
    router = APIRouter(prefix="/admin/projekte", dependencies=[Depends(require_csrf)])
    max_bytes = settings.UPLOAD_MAX_MB * 1024 * 1024
    backup_dir = Path(settings.BACKUP_DIR)

    def base(pid: int) -> str:
        return f"/admin/projekte/{pid}/upload"

    def upload_page(
        request: Request,
        principal: CertIdentity,
        admin: AdminUser,
        db: Session,
        pid: int,
        *,
        upload_error: str | None = None,
        status_code: int = 200,
    ) -> Response:
        try:
            project = projects.get_project(db, pid)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)
        return render(
            request,
            "admin/projekt_upload.html",
            principal,
            {
                "admin": admin,
                "p": project,
                "stages": service.list_open_stages(db, pid),
                "upload_error": upload_error,
                "upload_max_mb": settings.UPLOAD_MAX_MB,
                "fields": fields,
            },
            status_code=status_code,
        )

    def preview_page(
        request: Request,
        principal: CertIdentity,
        admin: AdminUser,
        db: Session,
        pid: int,
        stage_id: str,
        *,
        stale: bool = False,
        selected: set[str] | None = None,
        create_selected: set[str] | None = None,
    ) -> Response:
        try:
            project = projects.get_project(db, pid)
            pv = service.preview(db, pid, stage_id)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)
        except service.UploadError as exc:
            return redirect(base(pid), fehler=exc.code)
        keys = pv.diff.conflict_keys()
        return render(
            request,
            "admin/projekt_upload_vorschau.html",
            principal,
            {
                "admin": admin,
                "p": project,
                "pv": pv,
                "fields": fields,
                "show": enum_display,
                "stale": stale,
                "selected": (selected or set()) & keys,
                "create_selected": (create_selected or set()) & pv.diff.creatable_ids,
            },
            status_code=409 if stale else 200,
        )

    @router.get("/{pid}/upload", response_class=HTMLResponse)
    def upload_form(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int
    ) -> Response:
        return upload_page(request, principal, admin, db, pid)

    @router.post("/{pid}/upload")
    async def upload(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        datei: UploadFile = File(),  # noqa: B008 - FastAPI-Konvention
    ) -> Response:
        file_name = safe_file_name(datei.filename)
        data = await datei.read(max_bytes + 1)
        await datei.close()

        def process() -> Response:
            try:
                projects.get_project(db, pid)
            except projects.ProjectError as exc:
                return redirect("/admin/", fehler=exc.code)
            error: str | None = None
            if len(data) > max_bytes:
                error = f"Die Datei ist zu groß (höchstens {settings.UPLOAD_MAX_MB} MB)."
            elif not file_name or not file_name.lower().endswith(".xlsx"):
                error = "Bitte eine Excel-Datei im Format .xlsx hochladen."
            elif not data:
                error = "Die Datei ist leer."
            if error is None:
                try:
                    parsed = parse_upload(data)
                except MasterDataError as exc:
                    error = str(exc)
                else:
                    stage_id = service.stage(
                        db,
                        pid,
                        parsed,
                        created_by=principal.primary,
                        file_name=file_name,
                        file_hash=master_service.file_sha256(data),
                    )
                    audit(
                        db,
                        actor_of(request, principal),
                        "projekt_upload_hochgeladen",
                        file_name,
                        {
                            "project_id": pid,
                            "datensaetze": len(parsed.records),
                            "fehler": parsed.report.error_count,
                        },
                    )
                    db.commit()
                    return redirect(f"{base(pid)}/{stage_id}")
            db.rollback()
            return upload_page(
                request, principal, admin, db, pid, upload_error=error, status_code=422
            )

        return await run_in_threadpool(process)

    @router.get("/{pid}/upload/{stage_id}", response_class=HTMLResponse)
    def preview(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        stage_id: str,
    ) -> Response:
        return preview_page(request, principal, admin, db, pid, stage_id)

    @router.post("/{pid}/upload/{stage_id}/uebernehmen")
    async def apply_upload(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        stage_id: str,
    ) -> Response:
        form = await request.form(max_fields=FORM_MAX_FIELDS)

        def values(name: str) -> set[str]:
            return {str(v)[:FORM_VALUE_MAX] for v in form.getlist(name)}

        fingerprint = str(form.get("fingerprint", ""))[:64]
        selection = service.Selection(
            accept_all=str(form.get("alle", "")) == "1",
            conflicts=frozenset(values("c")),
            create=frozenset(values("anlegen")),
        )

        def work() -> Response:
            actor = actor_of(request, principal)
            try:
                result = service.apply(
                    db, actor, pid, stage_id, fingerprint, selection, backup_dir=backup_dir
                )
                db.commit()
            except service.StaleUploadError:
                db.rollback()
                return preview_page(
                    request,
                    principal,
                    admin,
                    db,
                    pid,
                    stage_id,
                    stale=True,
                    selected=set(selection.conflicts),
                    create_selected=set(selection.create),
                )
            except service.UploadError as exc:
                db.rollback()
                target = (
                    base(pid) if exc.code == "upload_nicht_gefunden" else f"{base(pid)}/{stage_id}"
                )
                return redirect(target, fehler=exc.code)
            except editing.WriteError as exc:
                db.rollback()
                return redirect(f"{base(pid)}/{stage_id}", fehler=write_error_code(exc))
            finally:
                if db.in_transaction():
                    db.rollback()
            log.info(
                "Projekt-Upload uebernommen",
                extra={"project_id": pid, "systeme": result.systems_changed},
            )
            ok = "upload_uebernommen" if result.seq is not None else "upload_nichts_geaendert"
            return redirect(f"/admin/projekte/{pid}", ok=ok)

        return await run_in_threadpool(work)

    @router.post("/{pid}/upload/{stage_id}/verwerfen")
    def discard(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        stage_id: str,
    ) -> Response:
        service.discard(db, actor_of(request, principal), pid, stage_id)
        db.commit()
        return redirect(base(pid), ok="vorschau_verworfen")

    return router
