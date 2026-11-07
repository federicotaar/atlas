"""fase 6: indici per le query piu frequenti (consolidamento prestazioni)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-07
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEXES = [
    ("ix_events_user_event_at", "events", ["user_id", "event_at"]),
    ("ix_events_user_project_event_at", "events", ["user_id", "project_id", "event_at"]),
    ("ix_memories_user_recorded_at", "memories", ["user_id", "recorded_at"]),
    ("ix_memories_user_kind", "memories", ["user_id", "kind"]),
    ("ix_predictions_user_status_verify", "predictions", ["user_id", "status", "verify_at"]),
    ("ix_engine_actions_user_status", "engine_actions", ["user_id", "status"]),
    ("ix_agent_runs_user_created", "agent_runs", ["user_id", "created_at"]),
    ("ix_action_logs_user_created", "action_logs", ["user_id", "created_at"]),
    ("ix_mock_calendar_user", "mock_calendar_events", ["user_id"]),
    ("ix_mock_email_user", "mock_email_drafts", ["user_id"]),
]


def upgrade() -> None:
    for name, table, cols in INDEXES:
        op.create_index(name, table, cols)


def downgrade() -> None:
    for name, _table, _cols in INDEXES:
        op.drop_index(name)
