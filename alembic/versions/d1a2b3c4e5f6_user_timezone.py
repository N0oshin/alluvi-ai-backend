"""user timezone

Reminder scheduling and quiet hours are local-time concepts; the client
reports its IANA zone on every addUserToken call. Existing rows default to
UTC until their device checks in.

Revision ID: d1a2b3c4e5f6
Revises: c8f1a2b3d4e5
Create Date: 2026-08-25 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d1a2b3c4e5f6"
down_revision: str | None = "c8f1a2b3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
    )


def downgrade() -> None:
    op.drop_column("users", "timezone")
