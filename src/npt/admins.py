"""Admin-Verwaltung per Zertifikats-Identifier (Auftrag 8.1, NPT-06/07/10).

Alle Funktionen arbeiten in der Transaktion der uebergebenen Session; das
Audit-Log wird in DERSELBEN Transaktion geschrieben. Commit macht der Aufrufer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from npt.db.models import AdminAccessRequest, AdminAuditLog, AdminUser
from npt.identity import (
    ANY_ISSUER,
    CertIdentity,
    IdKind,
    normalize_identifier,
    normalize_issuer,
    parse_bootstrap_entry,
)

log = logging.getLogger("npt.admins")

SYSTEM_ACTOR_ISSUER = "system"
DISPLAY_NAME_MAX = 200


class AdminError(Exception):
    """Fachlicher Fehler; ``code`` ist ein fester Meldungsschluessel fuer die UI."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Actor:
    issuer: str
    identifier: str
    client_ip: str | None = None

    @classmethod
    def from_identity(cls, ident: CertIdentity, client_ip: str | None) -> Actor:
        return cls(ident.issuer_norm, ident.primary, client_ip)

    @classmethod
    def system(cls, what: str) -> Actor:
        return cls(SYSTEM_ACTOR_ISSUER, what, None)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def audit(
    session: Session,
    actor: Actor,
    action: str,
    target: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AdminAuditLog(
            actor_issuer=actor.issuer,
            actor_identifier=actor.identifier,
            action=action,
            target=target,
            details=details,
            client_ip=actor.client_ip,
        )
    )
    log.info(
        "admin-aktion",
        extra={"action": action, "actor": actor.identifier, "target": target},
    )


def find_active_admin(session: Session, ident: CertIdentity) -> AdminUser | None:
    if not ident.candidates:
        return None
    stmt = (
        select(AdminUser)
        .where(
            AdminUser.active.is_(True),
            AdminUser.id_kind == ident.kind,
            AdminUser.identifier_norm.in_(ident.candidates),
            or_(AdminUser.issuer_norm == ident.issuer_norm, AdminUser.issuer_norm == ANY_ISSUER),
        )
        .order_by(AdminUser.id)
        .limit(1)
    )
    return session.scalars(stmt).first()


def list_admins(session: Session) -> list[AdminUser]:
    stmt = select(AdminUser).order_by(AdminUser.active.desc(), AdminUser.created_at)
    return list(session.scalars(stmt))


def list_open_requests(session: Session) -> list[AdminAccessRequest]:
    stmt = (
        select(AdminAccessRequest)
        .where(AdminAccessRequest.status == "offen")
        .order_by(AdminAccessRequest.requested_at)
    )
    return list(session.scalars(stmt))


def list_audit(session: Session, limit: int = 200) -> list[AdminAuditLog]:
    stmt = select(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(limit)
    return list(session.scalars(stmt))


def _clean_display_name(value: str | None) -> str | None:
    if value is None:
        return None
    v = " ".join(value.split())
    if len(v) > DISPLAY_NAME_MAX:
        raise AdminError("name_zu_lang")
    return v or None


def add_admin(
    session: Session,
    actor: Actor,
    kind: IdKind,
    identifier_raw: str,
    issuer_raw: str = "",
    display_name: str | None = None,
) -> AdminUser:
    try:
        ident = normalize_identifier(kind, identifier_raw)
        issuer = normalize_issuer(issuer_raw)
    except ValueError as exc:
        raise AdminError("identifier_ungueltig") from exc
    name = _clean_display_name(display_name)
    stmt = (
        insert(AdminUser)
        .values(
            issuer_norm=issuer,
            id_kind=kind,
            identifier_norm=ident,
            display_name=name,
            created_by=actor.identifier,
        )
        .on_conflict_do_nothing(constraint="uq_admin_users_identity")
        .returning(AdminUser.id)
    )
    new_id = session.execute(stmt).scalar_one_or_none()
    if new_id is None:
        raise AdminError("existiert_bereits")
    audit(
        session,
        actor,
        "admin_hinzugefuegt",
        target=ident,
        details={"issuer": issuer, "kind": kind, "display_name": name},
    )
    admin = session.get(AdminUser, new_id)
    if admin is None:  # pragma: no cover - gerade eingefuegt
        raise AdminError("nicht_gefunden")
    return admin


def _lock_admins(session: Session, kind: IdKind, admin_id: int) -> tuple[AdminUser, int]:
    """Sperrt alle aktiven Admins der wirksamen Art und das Ziel (FOR UPDATE).

    Liefert Ziel und Anzahl wirksamer aktiver Admins. Dieselbe Sperrreihenfolge
    (aufsteigende id) in allen Pfaden verhindert Deadlocks.
    """
    active = session.scalars(
        select(AdminUser)
        .where(AdminUser.active.is_(True), AdminUser.id_kind == kind)
        .order_by(AdminUser.id)
        .with_for_update()
    ).all()
    target = session.scalars(
        select(AdminUser).where(AdminUser.id == admin_id).with_for_update()
    ).first()
    if target is None:
        raise AdminError("nicht_gefunden")
    return target, len(active)


def _guard_last_admin(target: AdminUser, kind: IdKind, active_count: int) -> None:
    if target.active and target.id_kind == kind and active_count <= 1:
        raise AdminError("letzter_admin")


def set_admin_active(
    session: Session, actor: Actor, kind: IdKind, admin_id: int, active: bool
) -> None:
    target, count = _lock_admins(session, kind, admin_id)
    if target.active == active:
        return
    if not active:
        _guard_last_admin(target, kind, count)
    session.execute(
        update(AdminUser).where(AdminUser.id == admin_id).values(active=active, updated_at=_now())
    )
    audit(
        session,
        actor,
        "admin_aktiviert" if active else "admin_deaktiviert",
        target=target.identifier_norm,
        details={"admin_id": admin_id, "issuer": target.issuer_norm},
    )


def remove_admin(session: Session, actor: Actor, kind: IdKind, admin_id: int) -> None:
    target, count = _lock_admins(session, kind, admin_id)
    _guard_last_admin(target, kind, count)
    snapshot = {
        "admin_id": admin_id,
        "issuer": target.issuer_norm,
        "kind": target.id_kind,
        "display_name": target.display_name,
        "active": target.active,
    }
    session.delete(target)
    audit(session, actor, "admin_entfernt", target=target.identifier_norm, details=snapshot)


def create_access_request(session: Session, ident: CertIdentity, client_ip: str | None) -> bool:
    """Legt eine offene Anfrage an; ``False``, wenn bereits eine offen ist."""
    if not ident.candidates:
        raise AdminError("kein_identifier")
    stmt = (
        insert(AdminAccessRequest)
        .values(
            issuer_norm=ident.issuer_norm,
            id_kind=ident.kind,
            identifier_norm=ident.primary,
            subject_dn=ident.subject_dn,
            issuer_dn=ident.issuer_dn,
        )
        .on_conflict_do_nothing(
            index_elements=["issuer_norm", "id_kind", "identifier_norm"],
            index_where=AdminAccessRequest.status == "offen",
        )
        .returning(AdminAccessRequest.id)
    )
    created = session.execute(stmt).scalar_one_or_none()
    if created is not None:
        audit(
            session,
            Actor.from_identity(ident, client_ip),
            "zugang_angefragt",
            target=ident.primary,
            details={"request_id": created, "subject_dn": ident.subject_dn},
        )
    return created is not None


def open_request_for(session: Session, ident: CertIdentity) -> AdminAccessRequest | None:
    if not ident.candidates:
        return None
    stmt = select(AdminAccessRequest).where(
        AdminAccessRequest.status == "offen",
        AdminAccessRequest.issuer_norm == ident.issuer_norm,
        AdminAccessRequest.id_kind == ident.kind,
        AdminAccessRequest.identifier_norm == ident.primary,
    )
    return session.scalars(stmt).first()


def decide_request(session: Session, actor: Actor, request_id: int, approve: bool) -> None:
    req = session.scalars(
        select(AdminAccessRequest).where(AdminAccessRequest.id == request_id).with_for_update()
    ).first()
    if req is None or req.status != "offen":
        raise AdminError("anfrage_nicht_offen")
    req.status = "bestaetigt" if approve else "abgelehnt"
    req.decided_by = actor.identifier
    req.decided_at = _now()
    if approve:
        stmt = (
            insert(AdminUser)
            .values(
                issuer_norm=req.issuer_norm,
                id_kind=req.id_kind,
                identifier_norm=req.identifier_norm,
                display_name=None,
                created_by=actor.identifier,
            )
            .on_conflict_do_update(
                constraint="uq_admin_users_identity",
                set_={"active": True, "updated_at": _now()},
            )
        )
        session.execute(stmt)
    audit(
        session,
        actor,
        "anfrage_bestaetigt" if approve else "anfrage_abgelehnt",
        target=req.identifier_norm,
        details={"request_id": request_id, "issuer": req.issuer_norm},
    )


def bootstrap_admins(session: Session, kind: IdKind, entries: list[str]) -> int:
    """Legt Admins aus ADMIN_BOOTSTRAP_IDS an, falls noch nicht vorhanden.

    Bestehende Eintraege werden nicht veraendert: ein bewusst deaktivierter
    Admin wird durch einen Neustart nicht reaktiviert. Ein ENTFERNTER
    Bootstrap-Admin wird beim naechsten Start wieder angelegt (Auftrag: „falls
    noch nicht vorhanden") – dauerhaft entfernen heisst: aus der Env streichen.
    Liefert die Anzahl neu angelegter Admins.
    """
    created = 0
    actor = Actor.system("bootstrap")
    for entry in entries:
        issuer, ident = parse_bootstrap_entry(kind, entry)
        stmt = (
            insert(AdminUser)
            .values(
                issuer_norm=issuer,
                id_kind=kind,
                identifier_norm=ident,
                display_name="Bootstrap",
                created_by="bootstrap",
            )
            .on_conflict_do_nothing(constraint="uq_admin_users_identity")
            .returning(AdminUser.id)
        )
        if session.execute(stmt).scalar_one_or_none() is not None:
            created += 1
            audit(session, actor, "admin_bootstrap", target=ident, details={"issuer": issuer})
    return created
