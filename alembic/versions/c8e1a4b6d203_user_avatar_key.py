"""user avatar key

Revision ID: c8e1a4b6d203
Revises: b3d5e7f90112
Create Date: 2026-08-21 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c8e1a4b6d203"
down_revision: str | None = "b3d5e7f90112"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_key", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_key")
