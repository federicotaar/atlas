"""signup proofs: stato server-held tra verifica codice e creazione account

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-07
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "signup_proofs",
        sa.Column("jti", sa.Text(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("uq_signup_proofs_token_hash", "signup_proofs", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("signup_proofs")
