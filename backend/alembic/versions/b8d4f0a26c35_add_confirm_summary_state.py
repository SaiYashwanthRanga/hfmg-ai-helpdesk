"""add CONFIRM_SUMMARY voice call state

The agent now reads the whole ticket back (who, what, since when, priority and
why) and lets the caller correct it before the ticket is created
(docs/reviews/VOICE_CONVERSATION_REVIEW.md).

Downgrade moves any session in CONFIRM_SUMMARY to ANYTHING_ELSE and rebuilds
the type without the value.

Revision ID: b8d4f0a26c35
Revises: a7c3e9f15b24
Create Date: 2026-09-29 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8d4f0a26c35'
down_revision: Union[str, None] = 'a7c3e9f15b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PREVIOUS_VALUES = (
    "'GREETING', 'COLLECT_DESCRIPTION', 'COLLECT_DETAILS', 'COLLECT_NAME', 'CONFIRM_NAME', 'COLLECT_PHONE', "
    "'COLLECT_EMAIL', 'CONFIRM_EMAIL', 'CONFIRM_CATEGORY', 'ANYTHING_ELSE', 'ESCALATED', 'COMPLETED', 'ABANDONED'"
)
_COLUMNS = (
    ("voice_call_sessions", "state"),
    ("voice_simulator_turns", "state_before"),
    ("voice_simulator_turns", "state_after"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE voice_call_state_enum ADD VALUE IF NOT EXISTS 'CONFIRM_SUMMARY' AFTER 'CONFIRM_CATEGORY'"
        )


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = 'ANYTHING_ELSE' WHERE {column}::text = 'CONFIRM_SUMMARY'")
    op.execute("ALTER TYPE voice_call_state_enum RENAME TO voice_call_state_enum_old")
    op.execute(f"CREATE TYPE voice_call_state_enum AS ENUM ({_PREVIOUS_VALUES})")
    for table, column in _COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE voice_call_state_enum "
            f"USING {column}::text::voice_call_state_enum"
        )
    op.execute("DROP TYPE voice_call_state_enum_old")
