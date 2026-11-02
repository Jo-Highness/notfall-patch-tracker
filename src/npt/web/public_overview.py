"""Uebersicht (Pivot) eines Projekts (Auftrag 5.4).

- ``GET /p/<token>/uebersicht``   Seite (serverseitig gerendert, live per SSE)
- ``GET /p/<token>/api/pivot``    Pivot + Kennzahlen als JSON (Neuladen gebuendelt 1 s)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from npt import live, pivot
from npt.web.admission import Admission
from npt.web.public_project import (
    NO_STORE,
    DbDep,
    ProjectCtx,
    ProjectDep,
    ProjectNotFoundError,
)


def build_overview_router(templates: Jinja2Templates, admission: Admission) -> APIRouter:
    router = APIRouter(prefix="/p/{token}", dependencies=[Depends(admission)])
    cache = pivot.PivotCache()

    def current(db: DbDep, project: ProjectCtx) -> pivot.Pivot:
        meta = live.read_meta(db.connection(), project.id)
        if meta is None:
            raise ProjectNotFoundError
        return cache.get(db, project.id, meta.change_seq, project.base)

    @router.get("/uebersicht", response_class=HTMLResponse, include_in_schema=False)
    def overview_page(request: Request, project: ProjectDep, db: DbDep) -> HTMLResponse:
        data = current(db, project)
        return templates.TemplateResponse(
            request,
            "public/uebersicht.html",
            {
                "project": project,
                "pivot": data,
                "column_label": pivot.column_label,
                "config": {"base": project.base, "seq": data.seq},
                "nav_active": "uebersicht",
            },
            headers=NO_STORE,
        )

    @router.get("/api/pivot")
    def pivot_json(project: ProjectDep, db: DbDep) -> JSONResponse:
        return JSONResponse(current(db, project).as_json(), headers=NO_STORE)

    return router
