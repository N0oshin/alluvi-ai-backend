"""nutrition_per_100g view

Flattens usda_food_nutrients (one row per nutrient) into one row per food
with kcal / protein / fat / carbs columns — what the matcher and the
nutrition computation read. A plain view, not materialized: ~15k foods
joins instantly and it is always fresh after a re-import.

Postgres-only (FILTER + the tables it reads are only populated there);
no-ops elsewhere, same as the other guarded revisions.

Revision ID: b3d5e7f90112
Revises: 9c41f0d7aa21
"""

from __future__ import annotations

from alembic import op

revision: str = "b3d5e7f90112"
down_revision: str | None = "9c41f0d7aa21"
branch_labels = None
depends_on = None

CREATE_VIEW = """
CREATE OR REPLACE VIEW nutrition_per_100g AS
SELECT
    f.fdc_id,
    f.description,
    f.data_type,
    f.category,
    MAX(n.amount) FILTER (WHERE n.nutrient_id = 1008) AS kcal,
    MAX(n.amount) FILTER (WHERE n.nutrient_id = 1003) AS protein_g,
    MAX(n.amount) FILTER (WHERE n.nutrient_id = 1004) AS fat_g,
    MAX(n.amount) FILTER (WHERE n.nutrient_id = 1005) AS carbs_g
FROM usda_foods f
JOIN usda_food_nutrients n ON n.fdc_id = f.fdc_id
GROUP BY f.fdc_id, f.description, f.data_type, f.category
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(CREATE_VIEW)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP VIEW IF EXISTS nutrition_per_100g")
