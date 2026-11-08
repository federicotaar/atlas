"""telegram_links: collegamento bot Telegram ufficiale per utente

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("bot_token_enc", sa.Text(), nullable=False),
        sa.Column("bot_username", sa.Text(), nullable=False),
        sa.Column("pairing_code", sa.Text(), nullable=True),
        sa.Column("pairing_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("update_offset", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_update_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("telegram_links")
