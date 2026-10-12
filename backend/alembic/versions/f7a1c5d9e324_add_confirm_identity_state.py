"""add the CONFIRM_IDENTITY call state

The voice agent can propose a name from the employee directory ("Am I speaking with ...?")
and waits for the caller to confirm it. The state is stored on voice_call_sessions and in
the simulator's turn log, both of which use voice_call_state_enum.

Downgrade moves sessions in that state to COLLECT_NAME (the question they would be asked
next), then rebuilds the enum without the value.

Revision ID: f7a1c5d9e324
Revises: e6f0b4c8d213
Create Date: 2026-10-14 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f7a1c5d9e324'
down_revision: Union[str, None] = 'e6f0b4c8d213'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PREVIOUS_STATE_VALUES = (
    "'GREETING', 'COLLECT_DESCRIPTION', 'CLASSIFY_CALLER_TYPE', 'COLLECT_DETAILS', 'COLLECT_NAME', "
    "'CONFIRM_NAME', 'COLLECT_PHONE', 'CONFIRM_CALLBACK_NUMBER', 'COLLECT_ALTERNATE_CALLBACK_NUMBER', "
    "'COLLECT_EMAIL', 'CONFIRM_EMAIL', 'CONFIRM_CATEGORY', 'CONFIRM_SUMMARY', "
    "'ANYTHING_ELSE', 'ESCALATED', 'COMPLETED', 'ABANDONED'"
)
_NEW_STATE = "CONFIRM_IDENTITY"
_STATE_COLUMNS = (
    ("voice_call_sessions", "state"),
    ("voice_simulator_turns", "state_before"),
    ("voice_simulator_turns", "state_after"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE voice_call_state_enum ADD VALUE IF NOT EXISTS "
            f"'{_NEW_STATE}' AFTER 'CLASSIFY_CALLER_TYPE'"
        )


def downgrade() -> None:
    for table, column in _STATE_COLUMNS:
        op.execute(
            f"UPDATE {table} SET {column} = 'COLLECT_NAME' WHERE {column}::text = '{_NEW_STATE}'"
        )
    op.execute("ALTER TYPE voice_call_state_enum RENAME TO voice_call_state_enum_old")
    op.execute(f"CREATE TYPE voice_call_state_enum AS ENUM ({_PREVIOUS_STATE_VALUES})")
    for table, column in _STATE_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE voice_call_state_enum "
            f"USING {column}::text::voice_call_state_enum"
        )
    op.execute("DROP TYPE voice_call_state_enum_old")
