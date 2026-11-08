"""key_unusable: marca chiavi migrate non decifrabili

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-08
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_model_settings",
        sa.Column("key_unusable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("user_model_settings", "key_unusable")
