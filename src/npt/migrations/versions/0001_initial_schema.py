"""Initiales Schema (Auftrag 3.2, NPT-10).

Autogeneriert und manuell ergaenzt: Sequenz ``new_system_seq`` (Autogenerate
erkennt Sequenzen nicht), LIKE-Muster ohne doppeltes Prozentzeichen.

Revision ID: 0001
Revises:
Create Date: 2026-10-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Globale Sequenz fuer System-IDs NEW-0001 ... (Auftrag 5.6)
    op.execute(sa.schema.CreateSequence(sa.Sequence("new_system_seq", start=1, increment=1)))
    op.create_table(
        "admin_access_requests",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("issuer_norm", sa.Text(), nullable=False),
        sa.Column("id_kind", sa.Text(), nullable=False),
        sa.Column("identifier_norm", sa.Text(), nullable=False),
        sa.Column("subject_dn", sa.Text(), nullable=False),
        sa.Column("issuer_dn", sa.Text(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default="offen", nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "id_kind IN ('subject_dn', 'san_email', 'san_upn', 'fingerprint_sha256')",
            name=op.f("ck_admin_access_requests_id_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('offen', 'bestaetigt', 'abgelehnt')",
            name=op.f("ck_admin_access_requests_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_access_requests")),
    )
    op.create_index(
        "uq_admin_access_requests_open",
        "admin_access_requests",
        ["issuer_norm", "id_kind", "identifier_norm"],
        unique=True,
        postgresql_where=sa.text("status = 'offen'"),
    )
    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("actor_issuer", sa.Text(), nullable=False),
        sa.Column("actor_identifier", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit_log")),
    )
    op.create_index("ix_admin_audit_log_ts", "admin_audit_log", ["ts"], unique=False)
    op.create_table(
        "admin_users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("issuer_norm", sa.Text(), nullable=False),
        sa.Column("id_kind", sa.Text(), nullable=False),
        sa.Column("identifier_norm", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "id_kind IN ('subject_dn', 'san_email', 'san_upn', 'fingerprint_sha256')",
            name=op.f("ck_admin_users_id_kind"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_users")),
        sa.UniqueConstraint(
            "issuer_norm", "id_kind", "identifier_norm", name="uq_admin_users_identity"
        ),
    )
    op.create_table(
        "change_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), server_default="geaendert", nullable=False),
        sa.Column("field", sa.Text(), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("client_name", sa.String(length=100), nullable=True),
        sa.Column("admin_identifier", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "action IN ('geaendert', 'angelegt')", name=op.f("ck_change_log_action")
        ),
        sa.CheckConstraint(
            "source IN ('ui', 'admin_upload', 'admin', 'restore')",
            name=op.f("ck_change_log_source"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_log")),
    )
    op.create_index(
        "ix_change_log_project_changed", "change_log", ["project_id", "changed_at"], unique=False
    )
    op.create_index(
        "ix_change_log_project_system", "change_log", ["project_id", "system_id"], unique=False
    )
    op.create_table(
        "health_probe",
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column(
            "probed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("worker_id", name=op.f("pk_health_probe")),
    )
    op.create_table(
        "master_imports",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("admin_identifier", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), server_default="excel", nullable=False),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("count_new", sa.Integer(), server_default="0", nullable=False),
        sa.Column("count_changed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("count_removed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("count_missing_kept", sa.Integer(), server_default="0", nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("source IN ('excel', 'api')", name=op.f("ck_master_imports_source")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_master_imports")),
    )
    op.create_table(
        "master_systems",
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("system_name", sa.Text(), nullable=True),
        sa.Column("sbf", sa.Text(), nullable=True),
        sa.Column("digital_access_zone", sa.Text(), nullable=True),
        sa.Column("system_description", sa.Text(), nullable=True),
        sa.Column("app_owner", sa.Text(), nullable=True),
        sa.Column("app_type", sa.Text(), nullable=True),
        sa.Column("lifecycle", sa.Text(), nullable=True),
        sa.Column("fqdn", sa.Text(), nullable=True),
        sa.Column("cio_unit", sa.Text(), nullable=True),
        sa.Column("foreign_key", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), server_default="excel", nullable=False),
        sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(system_id) <> ''", name=op.f("ck_master_systems_system_id_not_empty")
        ),
        sa.CheckConstraint("source IN ('excel', 'api')", name=op.f("ck_master_systems_source")),
        sa.CheckConstraint(
            "system_id NOT LIKE 'NEW-%'", name=op.f("ck_master_systems_system_id_not_reserved")
        ),
        sa.PrimaryKeyConstraint("system_id", name=op.f("pk_master_systems")),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="aktiv", nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("token_generation", sa.Integer(), server_default="1", nullable=False),
        sa.Column("change_seq", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("reset_seq", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("backup_interval_min", sa.Integer(), server_default="60", nullable=False),
        sa.Column("backup_retention", sa.Integer(), server_default="48", nullable=False),
        sa.Column("last_backup_seq", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_backup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('aktiv', 'abgeschlossen', 'archiviert')", name=op.f("ck_projects_status")
        ),
        sa.CheckConstraint(
            "backup_interval_min IN (0, 15, 30, 60, 240, 720, 1440)",
            name=op.f("ck_projects_backup_interval"),
        ),
        sa.CheckConstraint(
            "backup_retention BETWEEN 1 AND 10000", name=op.f("ck_projects_backup_retention")
        ),
        sa.CheckConstraint(
            "char_length(access_token) >= 22", name=op.f("ck_projects_token_length")
        ),
        sa.CheckConstraint("reset_seq <= change_seq", name=op.f("ck_projects_reset_le_change")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("access_token", name=op.f("uq_projects_access_token")),
    )
    op.create_table(
        "rate_limit_buckets",
        sa.Column("bucket_key", sa.Text(), nullable=False),
        sa.Column("window_start", sa.BigInteger(), nullable=False),
        sa.Column("hits", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("bucket_key", "window_start", name="pk_rate_limit_buckets"),
        prefixes=["UNLOGGED"],
    )
    op.create_table(
        "backups",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("change_seq", sa.BigInteger(), nullable=False),
        sa.Column("json_path", sa.Text(), nullable=False),
        sa.Column("xlsx_path", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "trigger IN ('zeitgesteuert', 'manuell', 'vor_import', 'vor_restore')",
            name=op.f("ck_backups_trigger"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_backups_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backups")),
    )
    op.create_index(
        "ix_backups_project_created", "backups", ["project_id", "created_at"], unique=False
    )
    op.create_table(
        "mail_campaigns",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("selector", sa.Text(), nullable=False),
        sa.Column("selector_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("send_run", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_by", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_mail_campaigns_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_campaigns")),
    )
    op.create_table(
        "project_systems",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("system_name", sa.Text(), nullable=True),
        sa.Column("sbf", sa.Text(), nullable=True),
        sa.Column("digital_access_zone", sa.Text(), nullable=True),
        sa.Column("affected", sa.Text(), nullable=True),
        sa.Column("installed_version", sa.String(length=255), nullable=True),
        sa.Column("action_taken", sa.Text(), nullable=True),
        sa.Column("remark", sa.String(length=4000), nullable=True),
        sa.Column("system_description", sa.Text(), nullable=True),
        sa.Column("app_owner", sa.Text(), nullable=True),
        sa.Column("app_type", sa.Text(), nullable=True),
        sa.Column("lifecycle", sa.Text(), nullable=True),
        sa.Column("fqdn", sa.Text(), nullable=True),
        sa.Column("cio_unit", sa.Text(), nullable=True),
        sa.Column("foreign_key", sa.Text(), nullable=True),
        sa.Column("origin", sa.Text(), server_default="stammdaten", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("row_seq", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action_taken IS NULL OR action_taken IN ('update_installiert', 'mitigiert', 'isoliert', 'abgeschaltet')",
            name=op.f("ck_project_systems_action_taken"),
        ),
        sa.CheckConstraint(
            "affected IS NULL OR affected IN ('ja', 'nein', 'in_pruefung')",
            name=op.f("ck_project_systems_affected"),
        ),
        sa.CheckConstraint(
            "btrim(system_id) <> ''", name=op.f("ck_project_systems_system_id_not_empty")
        ),
        sa.CheckConstraint(
            "origin IN ('stammdaten', 'manuell_angelegt', 'admin_upload')",
            name=op.f("ck_project_systems_origin"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_systems_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_systems")),
        sa.UniqueConstraint("project_id", "system_id", name="uq_project_systems_project_system"),
    )
    op.create_index(
        "ix_project_systems_project_row_seq",
        "project_systems",
        ["project_id", "row_seq"],
        unique=False,
    )
    op.create_table(
        "staged_uploads",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('master', 'project')", name=op.f("ck_staged_uploads_kind")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_staged_uploads_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staged_uploads")),
    )
    op.create_index("ix_staged_uploads_expires", "staged_uploads", ["expires_at"], unique=False)
    op.create_table(
        "mail_deliveries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("campaign_id", sa.BigInteger(), nullable=False),
        sa.Column("send_run", sa.Integer(), nullable=False),
        sa.Column("email_norm", sa.Text(), nullable=False),
        sa.Column(
            "system_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default="ausstehend", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sending_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ausstehend', 'in_versand', 'gesendet', 'fehlgeschlagen')",
            name=op.f("ck_mail_deliveries_status"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["mail_campaigns.id"],
            name=op.f("fk_mail_deliveries_campaign_id_mail_campaigns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_deliveries")),
        sa.UniqueConstraint(
            "campaign_id", "send_run", "email_norm", name="uq_mail_deliveries_run_email"
        ),
    )
    op.create_index(
        "ix_mail_deliveries_status_next",
        "mail_deliveries",
        ["status", "next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_mail_deliveries_status_next", table_name="mail_deliveries")
    op.drop_table("mail_deliveries")
    op.drop_index("ix_staged_uploads_expires", table_name="staged_uploads")
    op.drop_table("staged_uploads")
    op.drop_index("ix_project_systems_project_row_seq", table_name="project_systems")
    op.drop_table("project_systems")
    op.drop_table("mail_campaigns")
    op.drop_index("ix_backups_project_created", table_name="backups")
    op.drop_table("backups")
    op.drop_table("rate_limit_buckets")
    op.drop_table("projects")
    op.drop_table("master_systems")
    op.drop_table("master_imports")
    op.drop_table("health_probe")
    op.drop_index("ix_change_log_project_system", table_name="change_log")
    op.drop_index("ix_change_log_project_changed", table_name="change_log")
    op.drop_table("change_log")
    op.drop_table("admin_users")
    op.drop_index("ix_admin_audit_log_ts", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
    op.drop_index(
        "uq_admin_access_requests_open",
        table_name="admin_access_requests",
        postgresql_where=sa.text("status = 'offen'"),
    )
    op.drop_table("admin_access_requests")
    op.execute(sa.schema.DropSequence(sa.Sequence("new_system_seq")))
