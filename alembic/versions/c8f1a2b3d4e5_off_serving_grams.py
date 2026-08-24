"""off_products.serving_grams

Pack serving size in grams from Open Food Facts, so the barcode route can
answer "1 bar" instead of "100 g". Nullable — OFF often doesn't know.

Revision ID: c8f1a2b3d4e5
Revises: c8e1a4b6d203
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c8f1a2b3d4e5"
down_revision: str | None = "c8e1a4b6d203"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "off_products", sa.Column("serving_grams", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("off_products", "serving_grams")
