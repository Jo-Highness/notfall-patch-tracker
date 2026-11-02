"""Kampagnen: anlegen, bearbeiten, Vorschau, Versand starten/abbrechen, Status (Auftrag 6.3).

Doppelversand-Schutz:
- Start nur mit der zuletzt gesehenen Laufnummer (``expected_run``): ein doppelt
  abgeschicktes Formular findet beim zweiten Mal ``send_run`` bereits erhoeht vor und
  legt keinen zweiten Lauf an. Zusaetzlich muss die bestaetigte Empfaengerzahl noch
  stimmen; sonst wird die aktualisierte Vorschau gezeigt.
- Erneuter Versand nur explizit als neuer Lauf (``send_run + 1``) – alle Empfaenger
  oder nur die zuletzt fehlgeschlagenen – und nur, wenn kein Lauf mehr offen ist.
- In der Datenbank: UNIQUE ``(campaign_id, send_run, email_norm)``.

Beim Start werden Inhalt (``mail_runs``) und Empfaenger samt Systemen
(``mail_deliveries.systems``) eingefroren. Alle Funktionen schreiben das Audit-Log in
derselben Transaktion; Commit macht der Aufrufer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlencode

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from npt.admins import Actor, audit
from npt.db.models import MailCampaign, MailDelivery, MailRun, Project
from npt.domain import labels
from npt.domain.validation import is_valid_email, normalize_email
from npt.editing import list_rows
from npt.mail.audience import (
    CUSTOM,
    SELECTORS,
    Audience,
    Recipient,
    SelectorError,
    SystemRef,
    build_audience,
    custom_params,
    selector_state,
    skipped_to_json,
)
from npt.mail.queue import CANCELLED_TEXT
from npt.mail.render import TEST_PREFIX, MailConfig, build_message, render_mail
from npt.mail.sanitize import LINK_PLACEHOLDER, ContentError, clean_body, clean_subject
from npt.mail.transport import SendError, Transport, classify

ACTIVE_STATUSES: Final = ("ausstehend", "in_versand")
MODES: Final = ("alle", "nur_fehlgeschlagene")


class CampaignError(Exception):
    """Fachlicher Fehler; ``code`` ist ein fester Meldungsschluessel fuer die UI."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AudienceChangedError(CampaignError):
    """Die Empfaengerzahl weicht von der bestaetigten ab (neue Vorschau noetig)."""

    def __init__(self, count: int) -> None:
        super().__init__("mail_empfaenger_geaendert")
        self.count = count


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class CampaignInput:
    subject: str
    body_html: str
    selector: str
    custom_query: str = ""


def _validated(data: CampaignInput) -> tuple[str, str, str, dict[str, Any] | None]:
    try:
        subject = clean_subject(data.subject)
        body = clean_body(data.body_html)
    except ContentError as exc:
        raise CampaignError(exc.code) from exc
    if data.selector not in SELECTORS:
        raise CampaignError("mail_selektor_ungueltig")
    params: dict[str, Any] | None = None
    if data.selector == CUSTOM:
        try:
            canonical = custom_params(data.custom_query)
        except SelectorError as exc:
            raise CampaignError(exc.code) from exc
        params = {"query": [list(p) for p in canonical]}
    return subject, body, data.selector, params


def custom_query_of(params: dict[str, Any] | None) -> str:
    """Gespeicherte Filterparameter -> Query-String (fuer Formular und Selektor)."""
    pairs = [(str(k), str(v)) for k, v in (params or {}).get("query", [])]
    return urlencode(pairs)


def create_campaign(
    session: Session, actor: Actor, project_id: int, data: CampaignInput
) -> MailCampaign:
    project = session.get(Project, project_id)
    if project is None:
        raise CampaignError("projekt_nicht_gefunden")
    subject, body, selector, params = _validated(data)
    campaign = MailCampaign(
        project_id=project_id,
        subject=subject,
        body_html=body,
        selector=selector,
        selector_params=params,
        created_by=actor.identifier,
        updated_by=actor.identifier,
    )
    session.add(campaign)
    session.flush()
    audit(
        session,
        actor,
        "mail_kampagne_angelegt",
        project.name,
        {"project_id": project_id, "campaign_id": campaign.id, "selector": selector},
    )
    return campaign


def get_campaign(session: Session, project_id: int, campaign_id: int) -> MailCampaign:
    campaign = session.get(MailCampaign, campaign_id)
    if campaign is None or campaign.project_id != project_id:
        raise CampaignError("mail_kampagne_nicht_gefunden")
    return campaign


def _lock_campaign(session: Session, project_id: int, campaign_id: int) -> MailCampaign:
    campaign = session.scalars(
        select(MailCampaign)
        .where(MailCampaign.id == campaign_id, MailCampaign.project_id == project_id)
        .with_for_update()
    ).first()
    if campaign is None:
        raise CampaignError("mail_kampagne_nicht_gefunden")
    session.refresh(campaign)
    return campaign


def update_campaign(
    session: Session, actor: Actor, project_id: int, campaign_id: int, data: CampaignInput
) -> MailCampaign:
    campaign = _lock_campaign(session, project_id, campaign_id)
    subject, body, selector, params = _validated(data)
    campaign.subject = subject
    campaign.body_html = body
    campaign.selector = selector
    campaign.selector_params = params
    campaign.updated_at = _now()
    campaign.updated_by = actor.identifier
    audit(
        session,
        actor,
        "mail_kampagne_geaendert",
        None,
        {"project_id": project_id, "campaign_id": campaign_id, "selector": selector},
    )
    return campaign


def delete_campaign(session: Session, actor: Actor, project_id: int, campaign_id: int) -> None:
    campaign = _lock_campaign(session, project_id, campaign_id)
    if _active_count(session, campaign_id):
        raise CampaignError("mail_versand_laeuft")
    session.delete(campaign)
    audit(
        session,
        actor,
        "mail_kampagne_geloescht",
        campaign.subject,
        {"project_id": project_id, "campaign_id": campaign_id},
    )


# ---------------------------------------------------------------- Zielgruppe / Vorschau
def audience_for(session: Session, campaign: MailCampaign) -> Audience:
    state = selector_state(campaign.selector, custom_query_of(campaign.selector_params))
    return build_audience(list_rows(session, campaign.project_id), state)


def has_placeholder(body_html: str) -> bool:
    return LINK_PLACEHOLDER in body_html


# ---------------------------------------------------------------- Laeufe
def _active_count(session: Session, campaign_id: int) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(MailDelivery)
            .where(
                MailDelivery.campaign_id == campaign_id,
                MailDelivery.status.in_(ACTIVE_STATUSES),
            )
        )
        or 0
    )


def failed_recipients(session: Session, campaign_id: int, send_run: int) -> list[Recipient]:
    rows = session.execute(
        select(MailDelivery.email_norm, MailDelivery.systems)
        .where(
            MailDelivery.campaign_id == campaign_id,
            MailDelivery.send_run == send_run,
            MailDelivery.status == "fehlgeschlagen",
        )
        .order_by(MailDelivery.email_norm)
    ).all()
    return [Recipient(email, [SystemRef.from_json(s) for s in systems]) for email, systems in rows]


@dataclass
class RunPlan:
    mode: str
    recipients: list[Recipient]
    system_count: int
    skipped: list[dict[str, Any]] = field(default_factory=list)


def plan_run(session: Session, campaign: MailCampaign, mode: str) -> RunPlan:
    if mode == "nur_fehlgeschlagene":
        if campaign.send_run < 1:
            raise CampaignError("mail_kein_lauf")
        recipients = failed_recipients(session, campaign.id, campaign.send_run)
        ids = {s.system_id for r in recipients for s in r.systems}
        return RunPlan(mode, recipients, len(ids))
    if mode != "alle":
        raise CampaignError("mail_modus_ungueltig")
    audience = audience_for(session, campaign)
    return RunPlan(
        mode, audience.recipients, audience.system_count, skipped_to_json(audience.skipped)
    )


def start_run(
    session: Session,
    actor: Actor,
    project_id: int,
    campaign_id: int,
    *,
    mode: str,
    expected_run: int,
    expected_count: int,
) -> MailRun:
    """Neuen Versandlauf anlegen (Empfaenger und Inhalt einfrieren)."""
    project = session.get(Project, project_id)
    if project is None:
        raise CampaignError("projekt_nicht_gefunden")
    campaign = _lock_campaign(session, project_id, campaign_id)
    if campaign.send_run != expected_run:
        raise CampaignError("mail_bereits_gestartet")
    if project.status != "aktiv":
        raise CampaignError("schreibgeschuetzt")
    if _active_count(session, campaign_id):
        raise CampaignError("mail_versand_laeuft")
    plan = plan_run(session, campaign, mode)
    if not plan.recipients:
        raise CampaignError("mail_keine_empfaenger")
    if len(plan.recipients) != expected_count:
        raise AudienceChangedError(len(plan.recipients))
    new_run = campaign.send_run + 1
    run = MailRun(
        campaign_id=campaign_id,
        send_run=new_run,
        mode=mode,
        subject=campaign.subject,
        body_html=campaign.body_html,
        selector=campaign.selector,
        selector_params=campaign.selector_params,
        recipient_count=len(plan.recipients),
        system_count=plan.system_count,
        skipped=plan.skipped,
        started_by=actor.identifier,
    )
    session.add(run)
    session.flush()
    session.execute(
        insert(MailDelivery),
        [
            {
                "campaign_id": campaign_id,
                "send_run": new_run,
                "email_norm": r.email,
                "system_ids": [s.system_id for s in r.systems],
                "systems": [s.as_json() for s in r.systems],
                "status": "ausstehend",
            }
            for r in plan.recipients
        ],
    )
    now = _now()
    session.execute(
        update(MailCampaign)
        .where(MailCampaign.id == campaign_id)
        .values(send_run=new_run, last_sent_at=now, last_sent_by=actor.identifier)
    )
    session.refresh(campaign)
    audit(
        session,
        actor,
        "mail_versand_gestartet",
        campaign.subject,
        {
            "project_id": project_id,
            "campaign_id": campaign_id,
            "send_run": new_run,
            "mode": mode,
            "empfaenger": len(plan.recipients),
            "systeme": plan.system_count,
            "uebersprungen": sum(len(s["system_ids"]) for s in plan.skipped),
        },
    )
    return run


def cancel_run(session: Session, actor: Actor, project_id: int, campaign_id: int) -> int:
    """Noch ausstehende Sendungen des laufenden Laufs abbrechen (in Versand laeuft fertig)."""
    campaign = _lock_campaign(session, project_id, campaign_id)
    result = session.execute(
        update(MailDelivery)
        .where(
            MailDelivery.campaign_id == campaign_id,
            MailDelivery.send_run == campaign.send_run,
            MailDelivery.status == "ausstehend",
        )
        .values(status="fehlgeschlagen", last_error=CANCELLED_TEXT, updated_at=_now())
        .execution_options(synchronize_session=False)
    )
    count = int(getattr(result, "rowcount", 0) or 0)
    session.execute(
        update(MailRun)
        .where(MailRun.campaign_id == campaign_id, MailRun.send_run == campaign.send_run)
        .values(cancelled_at=_now(), cancelled_by=actor.identifier)
    )
    audit(
        session,
        actor,
        "mail_versand_abgebrochen",
        campaign.subject,
        {
            "project_id": project_id,
            "campaign_id": campaign_id,
            "send_run": campaign.send_run,
            "abgebrochen": count,
        },
    )
    return count


# ---------------------------------------------------------------- Status
@dataclass
class RunStatus:
    run: MailRun | None
    counts: dict[str, int]
    deliveries: list[MailDelivery]

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def active(self) -> bool:
        return any(self.counts.get(s, 0) for s in ACTIVE_STATUSES)

    def as_json(self) -> dict[str, Any]:
        return {
            "send_run": self.run.send_run if self.run else 0,
            "active": self.active,
            "total": self.total,
            "counts": self.counts,
            "deliveries": [
                {
                    "id": d.id,
                    "email": d.email_norm,
                    "status": d.status,
                    "status_label": labels.MAIL_DELIVERY_STATUS.get(d.status, d.status),
                    "attempts": d.attempts,
                    "systems": len(d.system_ids or []),
                    "error": d.last_error or "",
                    "sent_at": d.sent_at.isoformat() if d.sent_at else None,
                }
                for d in self.deliveries
            ],
        }


def list_runs(session: Session, campaign_id: int) -> list[MailRun]:
    return list(
        session.scalars(
            select(MailRun).where(MailRun.campaign_id == campaign_id).order_by(MailRun.send_run)
        )
    )


def run_status(session: Session, campaign_id: int, send_run: int) -> RunStatus:
    run = session.get(MailRun, (campaign_id, send_run))
    deliveries = list(
        session.scalars(
            select(MailDelivery)
            .where(MailDelivery.campaign_id == campaign_id, MailDelivery.send_run == send_run)
            .order_by(MailDelivery.email_norm)
        )
    )
    counts = dict.fromkeys(labels.MAIL_DELIVERY_STATUS, 0)
    for d in deliveries:
        counts[d.status] = counts.get(d.status, 0) + 1
    return RunStatus(run, counts, deliveries)


@dataclass
class CampaignSummary:
    campaign: MailCampaign
    counts: dict[str, int]


def list_campaigns(session: Session, project_id: int) -> list[CampaignSummary]:
    campaigns = list(
        session.scalars(
            select(MailCampaign)
            .where(MailCampaign.project_id == project_id)
            .order_by(MailCampaign.id.desc())
        )
    )
    out: list[CampaignSummary] = []
    for c in campaigns:
        rows = session.execute(
            select(MailDelivery.status, func.count())
            .where(MailDelivery.campaign_id == c.id, MailDelivery.send_run == c.send_run)
            .group_by(MailDelivery.status)
        ).all()
        out.append(CampaignSummary(c, {str(s): int(n) for s, n in rows}))
    return out


# ---------------------------------------------------------------- Testmail
@dataclass(frozen=True)
class MailTestResult:
    sample: str | None  # Empfaenger, dessen Systeme eingesetzt wurden
    systems: int


def send_test(
    session: Session,
    actor: Actor,
    project_id: int,
    campaign_id: int,
    *,
    to: str,
    sample_email: str,
    config: MailConfig,
    transport: Transport,
    base_url: str,
) -> MailTestResult:
    """Testmail sofort (ohne Queue) an eine frei eingegebene Adresse.

    Eingesetzt werden die Systeme des gewaehlten Beispiel-Empfaengers (bzw. des ersten);
    der Betreff erhaelt das Praefix ``[TEST]``. Die Testmail zaehlt nicht als Lauf.
    """
    address = normalize_email(to)
    if not is_valid_email(address):
        raise CampaignError("mail_test_adresse_ungueltig")
    campaign = get_campaign(session, project_id, campaign_id)
    project = session.get(Project, project_id)
    if project is None:
        raise CampaignError("projekt_nicht_gefunden")
    audience = audience_for(session, campaign)
    sample = audience.find(sample_email) if sample_email else None
    if sample is None and audience.recipients:
        sample = audience.recipients[0]
    systems = sample.systems if sample else []
    mail = render_mail(
        TEST_PREFIX + campaign.subject, campaign.body_html, base_url, project.access_token, systems
    )
    details: dict[str, Any] = {
        "project_id": project_id,
        "campaign_id": campaign_id,
        "an": address,
        "beispiel": sample.email if sample else None,
    }
    try:
        transport.send(build_message(config, address, mail), address)
    except Exception as exc:
        err = exc if isinstance(exc, SendError) else classify(exc)
        details["fehler"] = str(err)
        audit(session, actor, "mail_testmail_fehlgeschlagen", campaign.subject, details)
        raise MailTestError(str(err)) from exc
    finally:
        transport.close()
    audit(session, actor, "mail_testmail", campaign.subject, details)
    return MailTestResult(sample.email if sample else None, len(systems))


class MailTestError(CampaignError):
    def __init__(self, detail: str) -> None:
        super().__init__("mail_test_fehlgeschlagen")
        self.detail = detail
