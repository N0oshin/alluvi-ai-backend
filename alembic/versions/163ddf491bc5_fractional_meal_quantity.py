"""fractional meal quantity

Revision ID: 163ddf491bc5
Revises: a79fd5dc2965
Create Date: 2026-08-12 17:08:25.522149
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '163ddf491bc5'
down_revision: str | None = 'a79fd5dc2965'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Integer -> Float so a user can log half a plate. Widening, so every
    # existing row converts cleanly (1 -> 1.0).
    # batch_alter_table because SQLite cannot ALTER a column type in place;
    # on Postgres this emits a plain ALTER COLUMN.
    with op.batch_alter_table("meals") as batch_op:
        batch_op.alter_column(
            "quantity",
            existing_type=sa.Integer(),
            type_=sa.Float(),
            existing_nullable=False,
            postgresql_using="quantity::double precision",
        )


def downgrade() -> None:
    # Narrowing: any fractional quantity is truncated by the cast, and the
    # stored totals are left as they are — they stay correct, but they will no
    # longer equal per_serving * quantity for those rows.
    with op.batch_alter_table("meals") as batch_op:
        batch_op.alter_column(
            "quantity",
            existing_type=sa.Float(),
            type_=sa.Integer(),
            existing_nullable=False,
            postgresql_using="quantity::integer",
        )
