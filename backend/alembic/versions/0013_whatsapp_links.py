"""whatsapp_links: collegamento WhatsApp linked-device per utente

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("jid", sa.Text(), nullable=True),
        sa.Column("pushname", sa.Text(), nullable=True),
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("boot_id", sa.Text(), nullable=True),
        sa.Column("replied_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("processed_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_event_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("whatsapp_links")
