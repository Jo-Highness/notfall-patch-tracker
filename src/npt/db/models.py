"""Vollstaendiges Datenmodell (Auftrag 3.2, NPT-10).

Konventionen:
- Zeitstempel ``timestamptz`` in UTC.
- Enums als ``text`` + CHECK mit ASCII-Schluesseln (siehe ``npt.domain.labels``);
  „Leer" ist NULL.
- ``change_log`` ohne Fremdschluessel (bleibt nach Projektloeschung lesbar).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from npt.domain import labels
from npt.domain.fields import INSTALLED_VERSION_MAX, REMARK_MAX

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }


# Globale Sequenz fuer System-IDs NEW-0001 ... (Auftrag 5.6).
new_system_seq = Sequence("new_system_seq", start=1, increment=1, metadata=Base.metadata)


def _now() -> Any:
    return func.now()


# Feldlisten und Laengengrenzen stehen zentral in npt.domain.fields (Auftrag 3.1).


class MasterSystem(Base):
    __tablename__ = "master_systems"
    __table_args__ = (
        CheckConstraint("system_id NOT LIKE 'NEW-%'", name="system_id_not_reserved"),
        CheckConstraint("btrim(system_id) <> ''", name="system_id_not_empty"),
        CheckConstraint(labels.check_in("source", labels.MASTER_SOURCE), name="source"),
    )

    system_id: Mapped[str] = mapped_column(Text, primary_key=True)
    system_name: Mapped[str | None] = mapped_column(Text)
    sbf: Mapped[str | None] = mapped_column(Text)
    digital_access_zone: Mapped[str | None] = mapped_column(Text)
    system_description: Mapped[str | None] = mapped_column(Text)
    app_owner: Mapped[str | None] = mapped_column(Text)
    app_type: Mapped[str | None] = mapped_column(Text)
    lifecycle: Mapped[str | None] = mapped_column(Text)
    fqdn: Mapped[str | None] = mapped_column(Text)
    cio_unit: Mapped[str | None] = mapped_column(Text)
    foreign_key: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, server_default="excel")
    # Im letzten Import nicht mehr enthalten, aber (Default) behalten und markiert.
    missing_since: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=_now())
    updated_at: Mapped[datetime] = mapped_column(server_default=_now())


class MasterImport(Base):
    __tablename__ = "master_imports"
    __table_args__ = (
        CheckConstraint(labels.check_in("source", labels.MASTER_SOURCE), name="source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    imported_at: Mapped[datetime] = mapped_column(server_default=_now())
    admin_identifier: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, server_default="excel")
    file_name: Mapped[str | None] = mapped_column(Text)
    file_hash: Mapped[str | None] = mapped_column(String(64))
    count_new: Mapped[int] = mapped_column(Integer, server_default="0")
    count_changed: Mapped[int] = mapped_column(Integer, server_default="0")
    count_removed: Mapped[int] = mapped_column(Integer, server_default="0")
    count_missing_kept: Mapped[int] = mapped_column(Integer, server_default="0")
    details: Mapped[dict[str, Any] | None]


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(labels.check_in("status", labels.PROJECT_STATUS), name="status"),
        CheckConstraint(
            labels.check_in("backup_interval_min", labels.BACKUP_INTERVALS_MIN),
            name="backup_interval",
        ),
        CheckConstraint("backup_retention BETWEEN 1 AND 10000", name="backup_retention"),
        CheckConstraint("char_length(access_token) >= 22", name="token_length"),
        CheckConstraint("reset_seq <= change_seq", name="reset_le_change"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="aktiv")
    # token_urlsafe(32), Projekt-URL /p/<access_token>.
    access_token: Mapped[str] = mapped_column(Text, unique=True)
    # Steigt bei jeder Neugenerierung; Teil des Bearbeiter-CSRF-Scope (NPT-08).
    token_generation: Mapped[int] = mapped_column(Integer, server_default="1")
    # Live-Update-Cursor (NPT-03).
    change_seq: Mapped[int] = mapped_column(BigInteger, server_default="0")
    reset_seq: Mapped[int] = mapped_column(BigInteger, server_default="0")
    backup_interval_min: Mapped[int] = mapped_column(Integer, server_default="60")
    backup_retention: Mapped[int] = mapped_column(Integer, server_default="48")
    last_backup_seq: Mapped[int] = mapped_column(BigInteger, server_default="0")
    last_backup_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=_now())
    created_by: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=_now())
    closed_at: Mapped[datetime | None]
    archived_at: Mapped[datetime | None]


class ProjectSystem(Base):
    __tablename__ = "project_systems"
    __table_args__ = (
        UniqueConstraint("project_id", "system_id", name="uq_project_systems_project_system"),
        Index("ix_project_systems_project_row_seq", "project_id", "row_seq"),
        CheckConstraint(
            "affected IS NULL OR " + labels.check_in("affected", labels.AFFECTED), name="affected"
        ),
        CheckConstraint(
            "action_taken IS NULL OR " + labels.check_in("action_taken", labels.ACTION_TAKEN),
            name="action_taken",
        ),
        CheckConstraint(labels.check_in("origin", labels.ORIGIN), name="origin"),
        CheckConstraint("btrim(system_id) <> ''", name="system_id_not_empty"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    system_id: Mapped[str] = mapped_column(Text)
    system_name: Mapped[str | None] = mapped_column(Text)
    sbf: Mapped[str | None] = mapped_column(Text)
    digital_access_zone: Mapped[str | None] = mapped_column(Text)
    affected: Mapped[str | None] = mapped_column(Text)
    installed_version: Mapped[str | None] = mapped_column(String(INSTALLED_VERSION_MAX))
    action_taken: Mapped[str | None] = mapped_column(Text)
    remark: Mapped[str | None] = mapped_column(String(REMARK_MAX))
    system_description: Mapped[str | None] = mapped_column(Text)
    app_owner: Mapped[str | None] = mapped_column(Text)
    app_type: Mapped[str | None] = mapped_column(Text)
    lifecycle: Mapped[str | None] = mapped_column(Text)
    fqdn: Mapped[str | None] = mapped_column(Text)
    cio_unit: Mapped[str | None] = mapped_column(Text)
    foreign_key: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text, server_default="stammdaten")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    row_seq: Mapped[int] = mapped_column(BigInteger, server_default="0")
    created_at: Mapped[datetime] = mapped_column(server_default=_now())
    updated_at: Mapped[datetime] = mapped_column(server_default=_now())


class ChangeLog(Base):
    __tablename__ = "change_log"
    __table_args__ = (
        Index("ix_change_log_project_changed", "project_id", "changed_at"),
        Index("ix_change_log_project_system", "project_id", "system_id"),
        CheckConstraint(labels.check_in("source", labels.CHANGE_SOURCE), name="source"),
        CheckConstraint(labels.check_in("action", labels.CHANGE_ACTION), name="action"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(BigInteger)  # bewusst ohne FK (NPT-10)
    seq: Mapped[int] = mapped_column(BigInteger)
    system_id: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, server_default="geaendert")
    field: Mapped[str | None] = mapped_column(Text)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(server_default=_now())
    source: Mapped[str] = mapped_column(Text)
    client_ip: Mapped[str | None] = mapped_column(String(64))
    client_name: Mapped[str | None] = mapped_column(String(100))
    admin_identifier: Mapped[str | None] = mapped_column(Text)


class Backup(Base):
    __tablename__ = "backups"
    __table_args__ = (
        Index("ix_backups_project_created", "project_id", "created_at"),
        CheckConstraint(labels.check_in("trigger", labels.BACKUP_TRIGGER), name="trigger"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(server_default=_now())
    trigger: Mapped[str] = mapped_column(Text)
    change_seq: Mapped[int] = mapped_column(BigInteger)
    json_path: Mapped[str] = mapped_column(Text)
    xlsx_path: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    created_by: Mapped[str | None] = mapped_column(Text)


class MailCampaign(Base):
    __tablename__ = "mail_campaigns"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    subject: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str] = mapped_column(Text)
    selector: Mapped[str] = mapped_column(Text)
    selector_params: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = mapped_column(server_default=_now())
    created_by: Mapped[str] = mapped_column(Text)
    # Zaehler fuer explizit ausgeloeste (erneute) Versandlaeufe (NPT-05).
    send_run: Mapped[int] = mapped_column(Integer, server_default="0")
    last_sent_at: Mapped[datetime | None]
    last_sent_by: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=_now())
    updated_by: Mapped[str | None] = mapped_column(Text)


class MailRun(Base):
    """Ein explizit gestarteter Versandlauf: eingefrorener Inhalt und Zielgruppe (NPT-05)."""

    __tablename__ = "mail_runs"
    __table_args__ = (
        PrimaryKeyConstraint("campaign_id", "send_run", name="pk_mail_runs"),
        CheckConstraint("mode IN ('alle', 'nur_fehlgeschlagene')", name="mode"),
        CheckConstraint("send_run >= 1", name="send_run_positive"),
    )

    campaign_id: Mapped[int] = mapped_column(ForeignKey("mail_campaigns.id", ondelete="CASCADE"))
    send_run: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str] = mapped_column(Text)
    selector: Mapped[str] = mapped_column(Text)
    selector_params: Mapped[dict[str, Any] | None]
    recipient_count: Mapped[int] = mapped_column(Integer)
    system_count: Mapped[int] = mapped_column(Integer)
    skipped: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))
    started_at: Mapped[datetime] = mapped_column(server_default=_now())
    started_by: Mapped[str] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None]
    cancelled_by: Mapped[str | None] = mapped_column(Text)


class MailDelivery(Base):
    __tablename__ = "mail_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "send_run", "email_norm", name="uq_mail_deliveries_run_email"
        ),
        Index("ix_mail_deliveries_status_next", "status", "next_attempt_at"),
        CheckConstraint(labels.check_in("status", labels.MAIL_DELIVERY_STATUS), name="status"),
        ForeignKeyConstraint(
            ["campaign_id", "send_run"],
            ["mail_runs.campaign_id", "mail_runs.send_run"],
            name="fk_mail_deliveries_run",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("mail_campaigns.id", ondelete="CASCADE"))
    send_run: Mapped[int] = mapped_column(Integer)
    email_norm: Mapped[str] = mapped_column(Text)
    system_ids: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))
    # Eingefrorene Systeme des Empfaengers: [{"id": System-ID, "name": System-Name}].
    systems: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))
    message_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="ausstehend")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=_now())
    last_error: Mapped[str | None] = mapped_column(Text)
    sending_since: Mapped[datetime | None]
    sent_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=_now())
    updated_at: Mapped[datetime] = mapped_column(server_default=_now())


class AdminUser(Base):
    __tablename__ = "admin_users"
    __table_args__ = (
        UniqueConstraint(
            "issuer_norm", "id_kind", "identifier_norm", name="uq_admin_users_identity"
        ),
        CheckConstraint(labels.check_in("id_kind", labels.ID_KIND), name="id_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Normalisierter Issuer-DN oder '*' (beliebiger Aussteller der konfigurierten CA).
    issuer_norm: Mapped[str] = mapped_column(Text)
    id_kind: Mapped[str] = mapped_column(Text)
    identifier_norm: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=_now())
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(server_default=_now())


class AdminAccessRequest(Base):
    __tablename__ = "admin_access_requests"
    __table_args__ = (
        # Pro Identitaet hoechstens eine offene Anfrage (NPT-10).
        Index(
            "uq_admin_access_requests_open",
            "issuer_norm",
            "id_kind",
            "identifier_norm",
            unique=True,
            postgresql_where=text("status = 'offen'"),
        ),
        CheckConstraint(labels.check_in("status", labels.ACCESS_REQUEST_STATUS), name="status"),
        CheckConstraint(labels.check_in("id_kind", labels.ID_KIND), name="id_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    issuer_norm: Mapped[str] = mapped_column(Text)
    id_kind: Mapped[str] = mapped_column(Text)
    identifier_norm: Mapped[str] = mapped_column(Text)
    subject_dn: Mapped[str] = mapped_column(Text)
    issuer_dn: Mapped[str] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(server_default=_now())
    status: Mapped[str] = mapped_column(Text, server_default="offen")
    decided_by: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None]


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"
    __table_args__ = (Index("ix_admin_audit_log_ts", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(server_default=_now())
    actor_issuer: Mapped[str] = mapped_column(Text)
    actor_identifier: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    target: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None]
    client_ip: Mapped[str | None] = mapped_column(String(64))


class StagedUpload(Base):
    """Geparste Upload-Datei zwischen Vorschau und Bestaetigung (NPT-04)."""

    __tablename__ = "staged_uploads"
    __table_args__ = (
        CheckConstraint("kind IN ('master', 'project')", name="kind"),
        Index("ix_staged_uploads_expires", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(server_default=_now())
    created_by: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str | None] = mapped_column(Text)
    file_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]]
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime]


class RateLimitBucket(Base):
    """Feste Zeitfenster je Schluessel; UNLOGGED (Verlust bei Crash ist unkritisch)."""

    __tablename__ = "rate_limit_buckets"
    __table_args__ = (
        PrimaryKeyConstraint("bucket_key", "window_start", name="pk_rate_limit_buckets"),
        {"prefixes": ["UNLOGGED"]},
    )

    bucket_key: Mapped[str] = mapped_column(Text)
    window_start: Mapped[int] = mapped_column(BigInteger)
    hits: Mapped[int] = mapped_column(Integer, server_default="0")


class HealthProbe(Base):
    __tablename__ = "health_probe"

    worker_id: Mapped[str] = mapped_column(Text, primary_key=True)
    probed_at: Mapped[datetime] = mapped_column(server_default=_now())
    latency_ms: Mapped[float | None] = mapped_column(Float)
