"""codici email: attestazioni consumate (anti-replay)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-07
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "used_attestations",
        sa.Column("jti", sa.Text(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("used_attestations")
