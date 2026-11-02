"""Admin: Stammdatenpflege (Auftrag 4, 7) – Upload, Vorschau, Uebernahme, Historie."""

from __future__ import annotations

import logging
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from npt.admins import audit
from npt.config import Settings
from npt.db.models import AdminUser
from npt.domain import fields
from npt.identity import CertIdentity
from npt.masterdata import service
from npt.masterdata.excel import XLSX_MIME, ExcelMasterDataSource, build_template
from npt.masterdata.source import MasterDataError
from npt.web.admin_deps import (
    AdminDep,
    DbDep,
    PrincipalDep,
    Renderer,
    actor_of,
    redirect,
    require_csrf,
)

log = logging.getLogger("npt.admin.master")

FILE_NAME_MAX = 200


def safe_file_name(name: str | None) -> str | None:
    if not name:
        return None
    base = PurePath(name.replace("\\", "/")).name
    cleaned = "".join(ch for ch in base if ch.isprintable()).strip()
    return cleaned[:FILE_NAME_MAX] or None


def build_router(render: Renderer, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/admin/stammdaten", dependencies=[Depends(require_csrf)])
    max_bytes = settings.UPLOAD_MAX_MB * 1024 * 1024

    def overview(
        request: Request,
        principal: CertIdentity,
        admin: AdminUser,
        db: Session,
        *,
        upload_error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        return render(
            request,
            "admin/stammdaten.html",
            principal,
            {
                "admin": admin,
                "stats": service.stats(db),
                "imports": service.list_imports(db),
                "stages": service.list_open_stages(db),
                "missing": service.list_missing(db),
                "upload_error": upload_error,
                "upload_max_mb": settings.UPLOAD_MAX_MB,
                "fields": fields,
            },
            status_code=status_code,
        )

    @router.get("", response_class=HTMLResponse)
    def master_overview(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep
    ) -> HTMLResponse:
        return overview(request, principal, admin, db)

    @router.get("/vorlage.xlsx")
    def template_download(admin: AdminDep) -> Response:
        return Response(
            build_template(),
            media_type=XLSX_MIME,
            headers={"content-disposition": 'attachment; filename="stammdaten-vorlage.xlsx"'},
        )

    @router.post("/upload")
    async def upload(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        datei: Annotated[UploadFile, File()],
    ) -> Response:
        file_name = safe_file_name(datei.filename)
        data = await datei.read(max_bytes + 1)
        await datei.close()

        def process() -> Response:
            error: str | None = None
            if len(data) > max_bytes:
                error = f"Die Datei ist zu groß (höchstens {settings.UPLOAD_MAX_MB} MB)."
            elif not file_name or not file_name.lower().endswith(".xlsx"):
                error = "Bitte eine Excel-Datei im Format .xlsx hochladen."
            elif not data:
                error = "Die Datei ist leer."
            if error is None:
                try:
                    result = ExcelMasterDataSource(data, file_name).load()
                except MasterDataError as exc:
                    error = str(exc)
                else:
                    stage_id = service.stage(
                        db,
                        result,
                        created_by=principal.primary,
                        file_name=file_name,
                        file_hash=service.file_sha256(data),
                    )
                    audit(
                        db,
                        actor_of(request, principal),
                        "stammdaten_hochgeladen",
                        file_name,
                        {"datensaetze": len(result.records), "fehler": result.report.error_count},
                    )
                    db.commit()
                    return redirect(f"/admin/stammdaten/vorschau/{stage_id}")
            db.rollback()
            return overview(request, principal, admin, db, upload_error=error, status_code=422)

        return await run_in_threadpool(process)

    @router.get("/vorschau/{stage_id}", response_class=HTMLResponse)
    def preview_page(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, stage_id: str
    ) -> Response:
        try:
            pv = service.preview(db, stage_id)
        except service.MasterImportError as exc:
            return redirect("/admin/stammdaten", fehler=exc.code)
        return render(
            request,
            "admin/stammdaten_vorschau.html",
            principal,
            {"admin": admin, "pv": pv, "fields": fields},
        )

    @router.post("/vorschau/{stage_id}/uebernehmen")
    def apply_import(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        stage_id: str,
        fingerprint: Annotated[str, Form(max_length=64)],
        remove: Annotated[list[str] | None, Form()] = None,
    ) -> Response:
        try:
            service.apply(
                db, actor_of(request, principal), stage_id, fingerprint, set(remove or [])
            )
            db.commit()
        except service.MasterImportError as exc:
            db.rollback()
            if exc.code == "vorschau_nicht_gefunden":
                return redirect("/admin/stammdaten", fehler=exc.code)
            return redirect(f"/admin/stammdaten/vorschau/{stage_id}", fehler=exc.code)
        return redirect("/admin/stammdaten", ok="import_uebernommen")

    @router.post("/vorschau/{stage_id}/verwerfen")
    def discard(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, stage_id: str
    ) -> Response:
        service.discard(db, actor_of(request, principal), stage_id)
        db.commit()
        return redirect("/admin/stammdaten", ok="vorschau_verworfen")

    return router
