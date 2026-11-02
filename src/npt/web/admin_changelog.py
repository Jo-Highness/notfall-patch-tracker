"""Admin: Aenderungsprotokoll eines Projekts (Auftrag 6.4) – Ansicht und .xlsx-Export."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from npt import changelog, export, projects
from npt.config import Settings
from npt.domain import fields
from npt.web.admin_deps import AdminDep, DbDep, PrincipalDep, Renderer, redirect, require_csrf


def build_router(render: Renderer, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/admin/projekte", dependencies=[Depends(require_csrf)])
    tz = ZoneInfo(settings.DISPLAY_TZ)

    @router.get("/{pid}/aenderungen", response_class=HTMLResponse)
    def log_page(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        seite: int = 1,
    ) -> Response:
        try:
            project = projects.get_project(db, pid)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)
        flt = changelog.parse_filter(dict(request.query_params))
        page = changelog.query(db, pid, flt, tz, seite)
        params = flt.params()

        def page_link(n: int) -> str:
            return f"/admin/projekte/{pid}/aenderungen?{urlencode({**params, 'seite': str(n)})}"

        return render(
            request,
            "admin/projekt_aenderungen.html",
            principal,
            {
                "admin": admin,
                "p": project,
                "flt": flt,
                "page": page,
                "page_link": page_link,
                "export_link": f"/admin/projekte/{pid}/aenderungen.xlsx?{urlencode(params)}",
                "log_fields": changelog.LOG_FIELDS,
                "field_created": changelog.FIELD_CREATED,
                "fields": fields,
                "cl": changelog,
            },
        )

    @router.get("/{pid}/aenderungen.xlsx")
    def log_export(request: Request, admin: AdminDep, db: DbDep, pid: int) -> Response:
        try:
            project = projects.get_project(db, pid)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)
        flt = changelog.parse_filter(dict(request.query_params))
        data = changelog.workbook(changelog.export_entries(db, pid, flt, tz), tz)
        name = export.download_name(project.name, "aenderungen", datetime.now(tz=tz))
        return Response(data, media_type=export.XLSX_MIME, headers=export.attachment_headers(name))

    return router
