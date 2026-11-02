"""Admin: Mailversand an Anwendungsverantwortliche je Projekt (Auftrag 6.3, NPT-05).

Editor (Quill 2, lokal), Vorschau (Empfaenger, Systeme, Beispielmail, uebersprungene
Adressen), Testmail, Versand ueber die Queue im jobs-Prozess, Status je Empfaenger.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session

from npt import projects
from npt.config import Settings
from npt.db.models import AdminUser, MailCampaign, Project
from npt.identity import CertIdentity
from npt.mail import audience as aud
from npt.mail import campaigns
from npt.mail.render import MailConfig, MailConfigError, list_url, render_mail
from npt.mail.transport import SmtpTransport, Transport
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

log = logging.getLogger("npt.admin.mail")

SUBJECT_FORM_MAX = 2000
BODY_FORM_MAX = 400_000
QUERY_FORM_MAX = 40_000
SKIPPED_SHOW_MAX = 500


def build_router(render: Renderer, settings: Settings) -> APIRouter:  # noqa: PLR0915
    router = APIRouter(prefix="/admin/projekte", dependencies=[Depends(require_csrf)])

    def base(pid: int) -> str:
        return f"/admin/projekte/{pid}/mail"

    def mail_config() -> tuple[MailConfig | None, str | None]:
        try:
            return MailConfig.from_settings(settings), None
        except MailConfigError as exc:
            return None, exc.code

    def transport_for(request: Request, config: MailConfig) -> Transport:
        factory: Callable[[MailConfig], Transport] = getattr(
            request.app.state, "mail_transport_factory", SmtpTransport
        )
        return factory(config)

    def load_project(db: Session, pid: int) -> Project:
        return projects.get_project(db, pid)

    def editor_page(
        request: Request,
        principal: CertIdentity,
        admin: AdminUser,
        project: Project,
        campaign: MailCampaign | None,
        form: dict[str, str],
        *,
        error: str | None = None,
        status_code: int = 200,
        extra: dict[str, Any] | None = None,
    ) -> HTMLResponse:
        ctx: dict[str, Any] = {
            "admin": admin,
            "p": project,
            "c": campaign,
            "form": form,
            "selectors": list(aud.SELECTORS.values()),
            "custom_key": aud.CUSTOM,
            "form_error": MESSAGES_ERR.get(error or "") if error else None,
            "list_base": list_url(settings.PUBLIC_BASE_URL, project.access_token, []),
        }
        ctx.update(extra or {})
        return render(request, "admin/mail_kampagne.html", principal, ctx, status_code=status_code)

    def form_of(campaign: MailCampaign) -> dict[str, str]:
        return {
            "subject": campaign.subject,
            "body_html": campaign.body_html,
            "selector": campaign.selector,
            "custom_query": campaigns.custom_query_of(campaign.selector_params),
        }

    # ------------------------------------------------------------ Liste / Neu
    @router.get("/{pid}/mail", response_class=HTMLResponse)
    def overview(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int
    ) -> Response:
        try:
            project = load_project(db, pid)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)
        _, cfg_error = mail_config()
        return render(
            request,
            "admin/mail.html",
            principal,
            {
                "admin": admin,
                "p": project,
                "items": campaigns.list_campaigns(db, pid),
                "smtp_error": cfg_error,
                "selectors": aud.SELECTORS,
            },
        )

    @router.get("/{pid}/mail/neu", response_class=HTMLResponse)
    def new_form(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        selektor: str = "betroffen_leer",
        filter_query: Annotated[str, Query(alias="filter")] = "",
    ) -> Response:
        try:
            project = load_project(db, pid)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)
        form = {
            "subject": "",
            "body_html": "",
            "selector": selektor if selektor in aud.SELECTORS else "betroffen_leer",
            "custom_query": filter_query[:QUERY_FORM_MAX],
        }
        return editor_page(request, principal, admin, project, None, form)

    def _input(subject: str, body_html: str, selector: str, custom_query: str) -> dict[str, str]:
        return {
            "subject": subject,
            "body_html": body_html,
            "selector": selector,
            "custom_query": custom_query,
        }

    @router.post("/{pid}/mail")
    def create(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        subject: Annotated[str, Form(max_length=SUBJECT_FORM_MAX)] = "",
        body_html: Annotated[str, Form(max_length=BODY_FORM_MAX)] = "",
        selector: Annotated[str, Form(max_length=100)] = "",
        custom_query: Annotated[str, Form(max_length=QUERY_FORM_MAX)] = "",
    ) -> Response:
        try:
            project = load_project(db, pid)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)
        data = campaigns.CampaignInput(subject, body_html, selector, custom_query)
        try:
            campaign = campaigns.create_campaign(db, actor_of(request, principal), pid, data)
            db.commit()
        except campaigns.CampaignError as exc:
            db.rollback()
            form = _input(subject, body_html, selector, custom_query)
            return editor_page(
                request, principal, admin, project, None, form, error=exc.code, status_code=422
            )
        return redirect(f"{base(pid)}/{campaign.id}", ok="mail_gespeichert")

    # ------------------------------------------------------------ Kampagne
    @router.get("/{pid}/mail/{cid}", response_class=HTMLResponse)
    def detail(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        cid: int,
        empfaenger: str = "",
        lauf: int = 0,
    ) -> Response:
        try:
            project = load_project(db, pid)
            campaign = campaigns.get_campaign(db, pid, cid)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)
        except campaigns.CampaignError as exc:
            return redirect(base(pid), fehler=exc.code)
        return editor_page(
            request,
            principal,
            admin,
            project,
            campaign,
            form_of(campaign),
            extra=preview_context(db, project, campaign, empfaenger, lauf),
        )

    def preview_context(
        db: Session, project: Project, campaign: MailCampaign, sample_email: str, run_no: int
    ) -> dict[str, Any]:
        config, cfg_error = mail_config()
        audience = campaigns.audience_for(db, campaign)
        sample = audience.find(sample_email) if sample_email else None
        if sample is None and audience.recipients:
            sample = audience.recipients[0]
        rendered = (
            render_mail(
                campaign.subject,
                campaign.body_html,
                settings.PUBLIC_BASE_URL,
                project.access_token,
                sample.systems,
            )
            if sample
            else None
        )
        params = aud.selector_params(
            campaign.selector, campaigns.custom_query_of(campaign.selector_params)
        )
        runs = campaigns.list_runs(db, campaign.id)
        shown_run = run_no if 0 < run_no <= campaign.send_run else campaign.send_run
        status = campaigns.run_status(db, campaign.id, shown_run) if shown_run else None
        failed_last = (
            len(campaigns.failed_recipients(db, campaign.id, campaign.send_run))
            if campaign.send_run
            else 0
        )
        skipped_total = sum(len(e.system_ids) for e in audience.skipped)
        return {
            "audience": audience,
            "sample": sample,
            "rendered": rendered,
            "selector_link": list_url(settings.PUBLIC_BASE_URL, project.access_token, params),
            "selector_params": params,
            "has_placeholder": campaigns.has_placeholder(campaign.body_html),
            "skipped": audience.skipped[:SKIPPED_SHOW_MAX],
            "skipped_total": skipped_total,
            "smtp_error": cfg_error,
            "smtp_ok": config is not None,
            "runs": runs,
            "status": status,
            "shown_run": shown_run,
            "failed_last": failed_last,
            "status_url": f"{base(project.id)}/{campaign.id}/status.json?lauf={shown_run}",
        }

    @router.post("/{pid}/mail/{cid}")
    def save(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        cid: int,
        subject: Annotated[str, Form(max_length=SUBJECT_FORM_MAX)] = "",
        body_html: Annotated[str, Form(max_length=BODY_FORM_MAX)] = "",
        selector: Annotated[str, Form(max_length=100)] = "",
        custom_query: Annotated[str, Form(max_length=QUERY_FORM_MAX)] = "",
    ) -> Response:
        try:
            project = load_project(db, pid)
            data = campaigns.CampaignInput(subject, body_html, selector, custom_query)
            campaigns.update_campaign(db, actor_of(request, principal), pid, cid, data)
            db.commit()
        except projects.ProjectError as exc:
            db.rollback()
            return redirect("/admin/", fehler=exc.code)
        except campaigns.CampaignError as exc:
            db.rollback()
            if exc.code == "mail_kampagne_nicht_gefunden":
                return redirect(base(pid), fehler=exc.code)
            campaign = campaigns.get_campaign(db, pid, cid)
            form = _input(subject, body_html, selector, custom_query)
            return editor_page(
                request, principal, admin, project, campaign, form, error=exc.code, status_code=422
            )
        return redirect(f"{base(pid)}/{cid}", ok="mail_gespeichert")

    @router.post("/{pid}/mail/{cid}/test")
    def test_mail(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        cid: int,
        to: Annotated[str, Form(max_length=320)] = "",
        sample: Annotated[str, Form(max_length=320)] = "",
    ) -> Response:
        config, cfg_error = mail_config()
        target = f"{base(pid)}/{cid}"
        if config is None:
            return redirect(target, fehler=cfg_error or "smtp_nicht_konfiguriert")
        try:
            campaigns.send_test(
                db,
                actor_of(request, principal),
                pid,
                cid,
                to=to,
                sample_email=sample,
                config=config,
                transport=transport_for(request, config),
                base_url=settings.PUBLIC_BASE_URL,
            )
            db.commit()
        except campaigns.MailTestError as exc:
            db.commit()  # Audit des Fehlversuchs behalten
            log.warning("Testmail fehlgeschlagen", extra={"campaign_id": cid, "fehler": exc.detail})
            return redirect(target, fehler=exc.code)
        except campaigns.CampaignError as exc:
            db.rollback()
            return redirect(target, fehler=exc.code)
        return redirect(target, ok="mail_test_gesendet")

    @router.post("/{pid}/mail/{cid}/senden")
    def start(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        cid: int,
        mode: Annotated[str, Form(max_length=30)] = "alle",
        expected_run: Annotated[int, Form()] = -1,
        expected_count: Annotated[int, Form()] = -1,
        confirm: Annotated[str, Form(max_length=10)] = "",
    ) -> Response:
        target = f"{base(pid)}/{cid}"
        if confirm != "ja":
            return redirect(target, fehler="mail_unbestaetigt")
        config, cfg_error = mail_config()
        if config is None:
            return redirect(target, fehler=cfg_error or "smtp_nicht_konfiguriert")
        try:
            run = campaigns.start_run(
                db,
                actor_of(request, principal),
                pid,
                cid,
                mode=mode,
                expected_run=expected_run,
                expected_count=expected_count,
            )
            db.commit()
        except campaigns.CampaignError as exc:
            db.rollback()
            return redirect(target, fehler=exc.code)
        log.info(
            "Mailversand gestartet",
            extra={"campaign_id": cid, "send_run": run.send_run, "empfaenger": run.recipient_count},
        )
        return redirect(target, lauf=str(run.send_run), ok="mail_versand_gestartet")

    @router.post("/{pid}/mail/{cid}/abbrechen")
    def cancel(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int, cid: int
    ) -> Response:
        try:
            campaigns.cancel_run(db, actor_of(request, principal), pid, cid)
            db.commit()
        except campaigns.CampaignError as exc:
            db.rollback()
            return redirect(base(pid), fehler=exc.code)
        return redirect(f"{base(pid)}/{cid}", ok="mail_versand_abgebrochen")

    @router.post("/{pid}/mail/{cid}/loeschen")
    def remove(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int, cid: int
    ) -> Response:
        try:
            campaigns.delete_campaign(db, actor_of(request, principal), pid, cid)
            db.commit()
        except campaigns.CampaignError as exc:
            db.rollback()
            if exc.code == "mail_versand_laeuft":
                return redirect(f"{base(pid)}/{cid}", fehler=exc.code)
            return redirect(base(pid), fehler=exc.code)
        return redirect(base(pid), ok="mail_kampagne_geloescht")

    @router.get("/{pid}/mail/{cid}/status.json")
    def status_json(admin: AdminDep, db: DbDep, pid: int, cid: int, lauf: int = 0) -> Response:
        try:
            campaign = campaigns.get_campaign(db, pid, cid)
        except campaigns.CampaignError:
            return JSONResponse({"reason": "nicht_gefunden"}, status_code=404)
        run_no = lauf if 0 < lauf <= campaign.send_run else campaign.send_run
        status = campaigns.run_status(db, cid, run_no)
        return JSONResponse(status.as_json(), headers={"cache-control": "no-store"})

    return router
