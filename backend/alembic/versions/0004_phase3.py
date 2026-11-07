"""fase 3: predictions

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "predictions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("estimate_central", sa.Float(), nullable=True),
        sa.Column("estimate_low", sa.Float(), nullable=True),
        sa.Column("estimate_high", sa.Float(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=False, server_default=""),
        sa.Column("probability", sa.Float(), nullable=True),
        sa.Column("data_used", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("assumptions", sa.Text(), nullable=False, server_default=""),
        sa.Column("risk_factors", sa.Text(), nullable=False, server_default=""),
        sa.Column("verify_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("outcome", sa.Float(), nullable=True),
        sa.Column("outcome_note", sa.Text(), nullable=True),
        sa.Column("error", sa.Float(), nullable=True),
        sa.Column("brier", sa.Float(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('open','verified','refused')", name="predictions_status_check"),
        sa.CheckConstraint("probability IS NULL OR (probability >= 0 AND probability <= 1)",
                           name="predictions_probability_check"),
    )
    op.create_index("ix_predictions_user_status", "predictions", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_predictions_user_status", table_name="predictions")
    op.drop_table("predictions")
