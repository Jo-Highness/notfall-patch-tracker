"""Audit-Log fuer Admin-Aktionen (NPT-06): Akteur und Eintrag in derselben Transaktion.

Bewusst ohne Abhaengigkeit zum Admin-Modul, damit auch der Bearbeiter-Prozess
(Erstinbetriebnahme, NPT-15) auditieren kann, ohne ``npt.admins`` zu laden (NPT-02).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from npt.db.models import AdminAuditLog
from npt.identity import CertIdentity

log = logging.getLogger("npt.admins")

SYSTEM_ACTOR_ISSUER = "system"


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
