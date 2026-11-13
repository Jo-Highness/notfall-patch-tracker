"""Admin: News und FAQ je Projekt pflegen (NPT-13).

- ``GET  /admin/projekte/{pid}/news``                 Liste der Beitraege
- ``GET  /admin/projekte/{pid}/news/neu``             Editor (neu)
- ``POST /admin/projekte/{pid}/news``                 anlegen
- ``GET  /admin/projekte/{pid}/news/{id}``            Editor (bearbeiten)
- ``POST /admin/projekte/{pid}/news/{id}``            speichern
- ``POST /admin/projekte/{pid}/news/{id}/loeschen``   loeschen

FAQ entsprechend unter ``/faq``. Alle Schreibzugriffe mit CSRF (Router-Dependency)
und Audit-Eintrag. Erlaubt auch in abgeschlossenen/archivierten Projekten.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from npt import content, projects
from npt.admins import audit
from npt.config import Settings
from npt.db.models import AdminUser, Project
from npt.identity import CertIdentity
from npt.web.admin_deps import (
    MESSAGES_ERR,
    AdminDep,
    DbDep,
    PrincipalDep,
    Renderer,
    actor_of,
    redirect,
    require_csrf,
)

Kind = Literal["news", "faq"]
TEXT_FORM_MAX = 2_000
HTML_FORM_MAX = 400_000


def build_router(render: Renderer, settings: Settings) -> APIRouter:  # noqa: PLR0915
    router = APIRouter(prefix="/admin/projekte", dependencies=[Depends(require_csrf)])
    public_base = settings.PUBLIC_BASE_URL.rstrip("/")

    def base(pid: int, kind: Kind) -> str:
        return f"/admin/projekte/{pid}/{kind}"

    def public_url(project: Project, kind: Kind) -> str:
        return f"{public_base}/p/{project.access_token}/{kind}"

    def list_page(
        request: Request,
        principal: CertIdentity,
        admin: AdminUser,
        db: Session,
        pid: int,
        kind: Kind,
    ) -> Response:
        try:
            project = projects.get_project(db, pid)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)
        items: list[Any] = (
            content.list_news(db, pid) if kind == "news" else content.list_faq(db, pid)
        )
        return render(
            request,
            "admin/inhalte.html",
            principal,
            {
                "admin": admin,
                "p": project,
                "kind": kind,
                "items": items,
                "base": base(pid, kind),
                "public_url": public_url(project, kind),
            },
        )

    def editor_page(
        request: Request,
        principal: CertIdentity,
        admin: AdminUser,
        project: Project,
        kind: Kind,
        item_id: int | None,
        form: dict[str, Any],
        *,
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        return render(
            request,
            "admin/inhalt_bearbeiten.html",
            principal,
            {
                "admin": admin,
                "p": project,
                "kind": kind,
                "item_id": item_id,
                "form": form,
                "base": base(project.id, kind),
                "form_error": MESSAGES_ERR.get(error or "") if error else None,
            },
            status_code=status_code,
        )

    def load(db: Session, pid: int) -> Project | Response:
        try:
            return projects.get_project(db, pid)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)

    # ------------------------------------------------------------ News
    @router.get("/{pid}/news", response_class=HTMLResponse)
    def news_list(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int
    ) -> Response:
        return list_page(request, principal, admin, db, pid, "news")

    @router.get("/{pid}/news/neu", response_class=HTMLResponse)
    def news_new(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int
    ) -> Response:
        project = load(db, pid)
        if isinstance(project, Response):
            return project
        return editor_page(
            request, principal, admin, project, "news", None, {"title": "", "body_html": ""}
        )

    def save_news(
        request: Request,
        principal: CertIdentity,
        admin: AdminUser,
        db: Session,
        pid: int,
        news_id: int | None,
        title: str,
        body_html: str,
    ) -> Response:
        project = load(db, pid)
        if isinstance(project, Response):
            return project
        form = {"title": title, "body_html": body_html}
        actor = actor_of(request, principal)
        try:
            if news_id is not None:
                content.get_news(db, pid, news_id)  # erst Existenz, dann Eingabe pruefen
            data = content.validate_news(title, body_html)
            if news_id is None:
                item = content.create_news(db, pid, data, actor.identifier)
                action = "news_angelegt"
            else:
                item = content.update_news(db, pid, news_id, data, actor.identifier)
                action = "news_geaendert"
            audit(db, actor, action, data.title, {"project_id": pid, "news_id": item.id})
            db.commit()
        except content.ContentError as exc:
            db.rollback()
            if exc.code == "news_nicht_gefunden":
                return redirect(base(pid, "news"), fehler=exc.code)
            return editor_page(
                request,
                principal,
                admin,
                project,
                "news",
                news_id,
                form,
                error=exc.code,
                status_code=422,
            )
        return redirect(base(pid, "news"), ok="news_gespeichert")

    @router.post("/{pid}/news")
    def news_create(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        title: Annotated[str, Form(max_length=TEXT_FORM_MAX)] = "",
        body_html: Annotated[str, Form(max_length=HTML_FORM_MAX)] = "",
    ) -> Response:
        return save_news(request, principal, admin, db, pid, None, title, body_html)

    @router.get("/{pid}/news/{news_id}", response_class=HTMLResponse)
    def news_edit(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        news_id: str,
    ) -> Response:
        project = load(db, pid)
        if isinstance(project, Response):
            return project
        try:
            item = content.get_news(db, pid, content.parse_id(news_id))
        except content.ContentError as exc:
            return redirect(base(pid, "news"), fehler=exc.code)
        form = {"title": item.title, "body_html": content.render_html(item.body_html)}
        return editor_page(request, principal, admin, project, "news", item.id, form)

    @router.post("/{pid}/news/{news_id}")
    def news_update(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        news_id: str,
        title: Annotated[str, Form(max_length=TEXT_FORM_MAX)] = "",
        body_html: Annotated[str, Form(max_length=HTML_FORM_MAX)] = "",
    ) -> Response:
        return save_news(
            request, principal, admin, db, pid, content.parse_id(news_id), title, body_html
        )

    @router.post("/{pid}/news/{news_id}/loeschen")
    def news_delete(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        news_id: str,
    ) -> Response:
        try:
            nid = content.parse_id(news_id)
            title = content.delete_news(db, pid, nid)
            audit(
                db,
                actor_of(request, principal),
                "news_geloescht",
                title,
                {"project_id": pid, "news_id": nid},
            )
            db.commit()
        except content.ContentError as exc:
            db.rollback()
            return redirect(base(pid, "news"), fehler=exc.code)
        return redirect(base(pid, "news"), ok="news_geloescht")

    # ------------------------------------------------------------ FAQ
    @router.get("/{pid}/faq", response_class=HTMLResponse)
    def faq_list(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int
    ) -> Response:
        return list_page(request, principal, admin, db, pid, "faq")

    @router.get("/{pid}/faq/neu", response_class=HTMLResponse)
    def faq_new(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int
    ) -> Response:
        project = load(db, pid)
        if isinstance(project, Response):
            return project
        form = {
            "question": "",
            "answer_html": "",
            "sort_order": str(content.next_sort_order(db, pid)),
        }
        return editor_page(request, principal, admin, project, "faq", None, form)

    def save_faq(
        request: Request,
        principal: CertIdentity,
        admin: AdminUser,
        db: Session,
        pid: int,
        faq_id: int | None,
        form: dict[str, str],
    ) -> Response:
        project = load(db, pid)
        if isinstance(project, Response):
            return project
        actor = actor_of(request, principal)
        try:
            if faq_id is not None:
                content.get_faq(db, pid, faq_id)  # erst Existenz, dann Eingabe pruefen
            data = content.validate_faq(form["question"], form["answer_html"], form["sort_order"])
            if faq_id is None:
                item = content.create_faq(db, pid, data, actor.identifier)
                action = "faq_angelegt"
            else:
                item = content.update_faq(db, pid, faq_id, data, actor.identifier)
                action = "faq_geaendert"
            audit(db, actor, action, data.question, {"project_id": pid, "faq_id": item.id})
            db.commit()
        except content.ContentError as exc:
            db.rollback()
            if exc.code == "faq_nicht_gefunden":
                return redirect(base(pid, "faq"), fehler=exc.code)
            return editor_page(
                request,
                principal,
                admin,
                project,
                "faq",
                faq_id,
                form,
                error=exc.code,
                status_code=422,
            )
        return redirect(base(pid, "faq"), ok="faq_gespeichert")

    @router.post("/{pid}/faq")
    def faq_create(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        question: Annotated[str, Form(max_length=TEXT_FORM_MAX)] = "",
        answer_html: Annotated[str, Form(max_length=HTML_FORM_MAX)] = "",
        sort_order: Annotated[str, Form(max_length=20)] = "",
    ) -> Response:
        form = {"question": question, "answer_html": answer_html, "sort_order": sort_order}
        return save_faq(request, principal, admin, db, pid, None, form)

    @router.get("/{pid}/faq/{faq_id}", response_class=HTMLResponse)
    def faq_edit(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        faq_id: str,
    ) -> Response:
        project = load(db, pid)
        if isinstance(project, Response):
            return project
        try:
            item = content.get_faq(db, pid, content.parse_id(faq_id))
        except content.ContentError as exc:
            return redirect(base(pid, "faq"), fehler=exc.code)
        form = {
            "question": item.question,
            "answer_html": content.render_html(item.answer_html),
            "sort_order": str(item.sort_order),
        }
        return editor_page(request, principal, admin, project, "faq", item.id, form)

    @router.post("/{pid}/faq/{faq_id}")
    def faq_update(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        faq_id: str,
        question: Annotated[str, Form(max_length=TEXT_FORM_MAX)] = "",
        answer_html: Annotated[str, Form(max_length=HTML_FORM_MAX)] = "",
        sort_order: Annotated[str, Form(max_length=20)] = "",
    ) -> Response:
        form = {"question": question, "answer_html": answer_html, "sort_order": sort_order}
        return save_faq(request, principal, admin, db, pid, content.parse_id(faq_id), form)

    @router.post("/{pid}/faq/{faq_id}/loeschen")
    def faq_delete(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        faq_id: str,
    ) -> Response:
        try:
            fid = content.parse_id(faq_id)
            question = content.delete_faq(db, pid, fid)
            audit(
                db,
                actor_of(request, principal),
                "faq_geloescht",
                question,
                {"project_id": pid, "faq_id": fid},
            )
            db.commit()
        except content.ContentError as exc:
            db.rollback()
            return redirect(base(pid, "faq"), fehler=exc.code)
        return redirect(base(pid, "faq"), ok="faq_geloescht")

    return router
