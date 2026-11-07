"""connettori Google reali: nuovi kind + credenziali cifrate

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KINDS_NEW = "kind IN ('calendar_mock','email_mock','google_calendar','google_gmail')"
_KINDS_OLD = "kind IN ('calendar_mock','email_mock')"


def upgrade() -> None:
    op.drop_constraint("connectors_kind_check", "connectors", type_="check")
    op.create_check_constraint("connectors_kind_check", "connectors", _KINDS_NEW)
    op.create_table(
        "connector_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("connector_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("connectors.id"), nullable=False, unique=True),
        sa.Column("access_token_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token_enc", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("connector_credentials")
    op.execute("DELETE FROM connectors WHERE kind IN ('google_calendar','google_gmail')")
    op.drop_constraint("connectors_kind_check", "connectors", type_="check")
    op.create_check_constraint("connectors_kind_check", "connectors", _KINDS_OLD)
