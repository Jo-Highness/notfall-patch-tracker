"""Test-Migration fuer den helm-rollback-Test (tests/k8s/kind-test.sh, NPT-12 Testplan 10).

Wird nur in das Wegwerf-Abbild ``notfall-patch-tracker:k8s-mig`` kopiert (als
``0004_k8s_rollback_test.py``), nie in das ausgelieferte Paket. Expand-Schritt: neue
Tabelle, die aeltere Abbilder nicht kennen und nicht brauchen.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_k8s_rollback_test"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE TABLE k8s_rollback_test (id integer PRIMARY KEY)")


def downgrade() -> None:
    op.execute("DROP TABLE k8s_rollback_test")
