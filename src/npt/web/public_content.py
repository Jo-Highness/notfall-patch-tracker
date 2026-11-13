"""News und FAQ eines Projekts fuer Bearbeiter (nur lesen, NPT-13).

- ``GET /p/<token>/news?seite=``  Beitraege, neueste zuerst (20 je Seite)
- ``GET /p/<token>/news/<id>``    ein Beitrag (Permalink)
- ``GET /p/<token>/faq?q=``       FAQ, serverseitig durchsuchbar (zusaetzlich JS-Live-Filter)

Rich-Text wird vor der Ausgabe erneut sanitisiert (:func:`npt.content.render_html`).
Dieses Modul importiert nichts aus dem Admin-Bereich (NPT-02).
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from npt import content, limits
from npt.web.admission import Admission
from npt.web.public_project import NO_STORE, DbDep, ProjectDep, ProjectNotFoundError


def build_content_router(templates: Jinja2Templates, admission: Admission) -> APIRouter:
    router = APIRouter(prefix="/p/{token}", dependencies=[Depends(admission)])

    def news_view(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"] if row["edited"] else None,
            "html": content.render_html(row["body_html"]),
        }

    @router.get("/news", response_class=HTMLResponse, include_in_schema=False)
    def news_page(
        request: Request, project: ProjectDep, db: DbDep, seite: str = ""
    ) -> HTMLResponse:
        total = content.count_news(db, project.id)
        pages = max(1, math.ceil(total / limits.NEWS_PAGE_SIZE))
        # Ungueltige/zu grosse Seitenzahl -> naechstgelegene gueltige Seite (HTML, kein 422).
        wanted = int(seite) if seite.isascii() and seite.isdigit() and len(seite) < 6 else 1
        page = min(max(wanted, 1), pages)
        items = [news_view(n) for n in content.public_news_page(db, project.id, page)]
        return templates.TemplateResponse(
            request,
            "public/news.html",
            {
                "project": project,
                "items": items,
                "single": False,
                "page": page,
                "pages": pages,
                "total": total,
                "nav_active": "news",
            },
            headers=NO_STORE,
        )

    @router.get("/news/{news_id}", response_class=HTMLResponse, include_in_schema=False)
    def news_item(request: Request, project: ProjectDep, db: DbDep, news_id: str) -> HTMLResponse:
        try:
            item = content.public_news_item(db, project.id, content.parse_id(news_id))
        except content.ContentError as exc:
            raise ProjectNotFoundError from exc
        return templates.TemplateResponse(
            request,
            "public/news.html",
            {
                "project": project,
                "items": [news_view(item)],
                "single": True,
                "nav_active": "news",
            },
            headers=NO_STORE,
        )

    @router.get("/faq", response_class=HTMLResponse, include_in_schema=False)
    def faq_page(
        request: Request,
        project: ProjectDep,
        db: DbDep,
        q: str = "",
    ) -> HTMLResponse:
        query = content.normalize_query(q[: content.SEARCH_MAX * 4])  # kuerzt auf 200 Zeichen
        items = [
            {
                "id": f["id"],
                "question": f["question"],
                "html": content.render_html(f["answer_html"]),
            }
            for f in content.public_faq(db, project.id, query)
        ]
        total = content.count_faq(db, project.id) if query else len(items)
        return templates.TemplateResponse(
            request,
            "public/faq.html",
            {
                "project": project,
                "items": items,
                "q": query,
                "total": total,
                "nav_active": "faq",
            },
            headers=NO_STORE,
        )

    return router
