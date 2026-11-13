"""News und FAQ je Projekt (NPT-13), Aenderungsquelle ``mcp`` (NPT-14).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCES_OLD = "source IN ('ui', 'admin_upload', 'admin', 'restore')"
_SOURCES_NEW = "source IN ('ui', 'admin_upload', 'admin', 'restore', 'mcp')"


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Text(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "project_news",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 200", name=op.f("ck_project_news_title_length")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_news_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_news")),
    )
    op.create_index("ix_project_news_project_created", "project_news", ["project_id", "created_at"])
    op.create_table(
        "project_faq",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer_html", sa.Text(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "char_length(question) BETWEEN 1 AND 500",
            name=op.f("ck_project_faq_question_length"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_faq_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_faq")),
    )
    op.create_index("ix_project_faq_project_sort", "project_faq", ["project_id", "sort_order"])
    op.drop_constraint(op.f("ck_change_log_source"), "change_log", type_="check")
    op.create_check_constraint(op.f("ck_change_log_source"), "change_log", _SOURCES_NEW)


def downgrade() -> None:
    op.execute("UPDATE change_log SET source = 'ui' WHERE source = 'mcp'")
    op.drop_constraint(op.f("ck_change_log_source"), "change_log", type_="check")
    op.create_check_constraint(op.f("ck_change_log_source"), "change_log", _SOURCES_OLD)
    op.drop_index("ix_project_faq_project_sort", table_name="project_faq")
    op.drop_table("project_faq")
    op.drop_index("ix_project_news_project_created", table_name="project_news")
    op.drop_table("project_news")
