"""change_log: Aktion ``geloescht`` (Restore entfernt Systeme, die nach dem Backup entstanden).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_change_log_action"), "change_log", type_="check")
    op.create_check_constraint(
        op.f("ck_change_log_action"),
        "change_log",
        "action IN ('geaendert', 'angelegt', 'geloescht')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM change_log WHERE action = 'geloescht'")
    op.drop_constraint(op.f("ck_change_log_action"), "change_log", type_="check")
    op.create_check_constraint(
        op.f("ck_change_log_action"), "change_log", "action IN ('geaendert', 'angelegt')"
    )
