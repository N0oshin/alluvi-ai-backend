"""Password reset moves from an emailed link to a 6-digit OTP.

`otp_purpose` gains `reset_password`; `password_reset_tokens` goes away. The
dropped rows are short-lived (1 h TTL) recovery tokens, so nothing of lasting
value is lost — but any reset link emailed before this deploy stops working.

Revision ID: f6a7b8c9d0e1
Revises: e5b6c7d8f9a0
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5b6c7d8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres cannot ADD VALUE inside a transaction block on older versions;
    # autocommit_block handles that (and is a no-op elsewhere).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE otp_purpose ADD VALUE IF NOT EXISTS 'reset_password'")
    op.drop_table("password_reset_tokens")


def downgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_password_reset_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_tokens")),
    )
    with op.batch_alter_table("password_reset_tokens", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_password_reset_tokens_token_hash"),
            ["token_hash"],
            unique=True,
        )
        batch_op.create_index(
            batch_op.f("ix_password_reset_tokens_user_id"), ["user_id"], unique=False
        )
    # Postgres has no DROP VALUE for enums; any reset_password rows must be
    # purged before the type can be rebuilt without the value.
    op.execute("DELETE FROM otp_codes WHERE purpose = 'reset_password'")
    op.execute("ALTER TYPE otp_purpose RENAME TO otp_purpose_old")
    op.execute("CREATE TYPE otp_purpose AS ENUM ('verify_email')")
    op.execute(
        "ALTER TABLE otp_codes ALTER COLUMN purpose TYPE otp_purpose "
        "USING purpose::text::otp_purpose"
    )
    op.execute("DROP TYPE otp_purpose_old")
