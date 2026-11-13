"""Einstellungen der Instanz und Erstinbetriebnahme (NPT-15).

Bestandsinstallationen mit Admins gelten als eingerichtet (Installer geschlossen).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instance_settings",
        sa.Column("id", sa.Integer(), server_default="1", nullable=False),
        sa.Column("site_name", sa.Text(), server_default="Notfall-Patch-Tracker", nullable=False),
        sa.Column("admin_ca_pem", sa.Text(), nullable=True),
        sa.Column("admin_crl_pem", sa.Text(), nullable=True),
        sa.Column("trust_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("setup_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("setup_code_hash", sa.Text(), nullable=True),
        sa.Column("setup_draft", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("gateway_cert_sha256", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_instance_settings_singleton")),
        sa.CheckConstraint(
            "char_length(site_name) BETWEEN 1 AND 80",
            name=op.f("ck_instance_settings_site_name_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instance_settings")),
    )
    op.execute(
        "INSERT INTO instance_settings (id, setup_completed_at) "
        "SELECT 1, CASE WHEN EXISTS (SELECT 1 FROM admin_users) THEN now() END"
    )


def downgrade() -> None:
    op.drop_table("instance_settings")
