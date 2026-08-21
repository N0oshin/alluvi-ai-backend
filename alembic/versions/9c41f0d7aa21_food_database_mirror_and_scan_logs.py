"""food database mirror and scan logs

Foundation for the self-built calorie-analysis pipeline: a local mirror of
USDA FoodData Central and Open Food Facts, a curated `custom_foods` table
for regional dishes, and `scan_logs` for request auditing / caching.

Postgres-only pieces (pg_trgm extension, GIN trigram indexes, RLS) are
dialect-guarded; the SQLite test schema is built by create_all and never
runs this file.

Revision ID: 9c41f0d7aa21
Revises: 614f47603235
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "9c41f0d7aa21"
down_revision: str | None = "614f47603235"
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"

    if is_pg:
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "usda_foods",
        sa.Column("fdc_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("data_type", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("fdc_id", name=op.f("pk_usda_foods")),
    )
    op.create_index(
        op.f("ix_usda_foods_data_type"), "usda_foods", ["data_type"], unique=False
    )

    op.create_table(
        "usda_food_nutrients",
        sa.Column("fdc_id", sa.BigInteger(), nullable=False),
        sa.Column("nutrient_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["fdc_id"],
            ["usda_foods.fdc_id"],
            name=op.f("fk_usda_food_nutrients_fdc_id_usda_foods"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "fdc_id", "nutrient_id", name=op.f("pk_usda_food_nutrients")
        ),
    )

    op.create_table(
        "off_products",
        sa.Column("barcode", sa.String(length=64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=True),
        sa.Column("kcal_100g", sa.Float(), nullable=True),
        sa.Column("protein_100g", sa.Float(), nullable=True),
        sa.Column("carbs_100g", sa.Float(), nullable=True),
        sa.Column("fat_100g", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("barcode", name=op.f("pk_off_products")),
    )

    op.create_table(
        "custom_foods",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("aliases", JSON_TYPE, nullable=False),
        sa.Column("kcal_100g", sa.Float(), nullable=False),
        sa.Column("protein_100g", sa.Float(), nullable=False),
        sa.Column("carbs_100g", sa.Float(), nullable=False),
        sa.Column("fat_100g", sa.Float(), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custom_foods")),
    )

    op.create_table(
        "scan_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("route", sa.String(length=16), nullable=False),
        sa.Column("image_sha256", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("model_used", sa.String(length=64), nullable=True),
        sa.Column("raw_model_output", JSON_TYPE, nullable=True),
        sa.Column("matched_foods", JSON_TYPE, nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_scan_logs_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_logs")),
    )
    op.create_index(
        op.f("ix_scan_logs_image_sha256"), "scan_logs", ["image_sha256"], unique=False
    )
    op.create_index(
        "ix_scan_logs_user_created",
        "scan_logs",
        ["user_id", "created_at"],
        unique=False,
    )

    if is_pg:
        # Trigram GIN indexes — what the fuzzy matcher actually searches.
        op.execute(
            "CREATE INDEX ix_usda_foods_description_trgm ON usda_foods "
            "USING gin (description gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX ix_off_products_name_trgm ON off_products "
            "USING gin (name gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX ix_custom_foods_name_trgm ON custom_foods "
            "USING gin (name gin_trgm_ops)"
        )
        # Revision 614f47603235 enabled RLS on every table that existed then;
        # these came later, so they get the same posture explicitly. The
        # backend connects as the table owner, which bypasses RLS.
        for table in (
            "usda_foods",
            "usda_food_nutrients",
            "off_products",
            "custom_foods",
            "scan_logs",
        ):
            op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("scan_logs")
    op.drop_table("custom_foods")
    op.drop_table("off_products")
    op.drop_table("usda_food_nutrients")
    op.drop_table("usda_foods")
