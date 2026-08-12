"""portion estimate and confidence

Revision ID: 307b84353afa
Revises: 163ddf491bc5
Create Date: 2026-08-12 18:36:37.441643
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '307b84353afa'
down_revision: str | None = '163ddf491bc5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


portion_confidence = sa.Enum("low", "medium", "high", name="portion_confidence")


def upgrade() -> None:
    # Created explicitly: op.add_column does not reliably emit CREATE TYPE, and
    # the ALTER then fails on Postgres with an unknown type.
    portion_confidence.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "food_analyses",
        sa.Column("estimated_portion_grams", sa.Integer(), nullable=True),
    )
    op.add_column(
        "food_analyses",
        sa.Column(
            "portion_confidence",
            portion_confidence,
            nullable=False,
            # Rows written before portion estimation existed need a value, and
            # a NOT NULL column cannot be added to a populated table without
            # one. "medium" is the same neutral fallback the parser uses.
            server_default="medium",
        ),
    )


def downgrade() -> None:
    op.drop_column("food_analyses", "portion_confidence")
    op.drop_column("food_analyses", "estimated_portion_grams")
    # Dropping the columns leaves the type behind on Postgres, which would
    # break a later re-upgrade with "type already exists".
    portion_confidence.drop(op.get_bind(), checkfirst=True)
