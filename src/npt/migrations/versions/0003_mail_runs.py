"""Mailversand (Phase 8): Versandlaeufe ``mail_runs``, eingefrorene Systeme je Empfaenger.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mail_campaigns",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column("mail_campaigns", sa.Column("updated_by", sa.Text(), nullable=True))
    op.create_table(
        "mail_runs",
        sa.Column("campaign_id", sa.BigInteger(), nullable=False),
        sa.Column("send_run", sa.Integer(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("selector", sa.Text(), nullable=False),
        sa.Column("selector_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column("system_count", sa.Integer(), nullable=False),
        sa.Column(
            "skipped",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_by", sa.Text(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "mode IN ('alle', 'nur_fehlgeschlagene')", name=op.f("ck_mail_runs_mode")
        ),
        sa.CheckConstraint("send_run >= 1", name=op.f("ck_mail_runs_send_run_positive")),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["mail_campaigns.id"],
            name=op.f("fk_mail_runs_campaign_id_mail_campaigns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("campaign_id", "send_run", name="pk_mail_runs"),
    )
    op.add_column(
        "mail_deliveries",
        sa.Column(
            "systems",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("mail_deliveries", sa.Column("message_id", sa.Text(), nullable=True))
    # Vor Phase 8 gab es keine Sendungen; verwaiste Zeilen ohne Lauf waeren ungueltig.
    op.execute("DELETE FROM mail_deliveries")
    op.create_foreign_key(
        "fk_mail_deliveries_run",
        "mail_deliveries",
        "mail_runs",
        ["campaign_id", "send_run"],
        ["campaign_id", "send_run"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_mail_deliveries_run", "mail_deliveries", type_="foreignkey")
    op.drop_column("mail_deliveries", "message_id")
    op.drop_column("mail_deliveries", "systems")
    op.drop_table("mail_runs")
    op.drop_column("mail_campaigns", "updated_by")
    op.drop_column("mail_campaigns", "updated_at")
