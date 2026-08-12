"""portion scale reference and container hint

Revision ID: 84a2d4cd5b34
Revises: 307b84353afa
Create Date: 2026-08-12 19:06:20.691569
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '84a2d4cd5b34'
down_revision: str | None = '307b84353afa'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Both nullable: rows predating portion calibration have no value, and
    # either field is legitimately absent when nothing was seen or supplied.
    op.add_column(
        "food_analyses",
        sa.Column("scale_reference", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "food_analyses",
        sa.Column("container_hint", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("food_analyses", "container_hint")
    op.drop_column("food_analyses", "scale_reference")
