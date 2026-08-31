"""scan_logs.total_latency_ms — whole-request wall clock next to the model
latency, so slow-model and slow-everything-else are distinguishable.

Revision ID: e5b6c7d8f9a0
Revises: d1a2b3c4e5f6
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e5b6c7d8f9a0"
down_revision: str | None = "d1a2b3c4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scan_logs", sa.Column("total_latency_ms", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("scan_logs", "total_latency_ms")
