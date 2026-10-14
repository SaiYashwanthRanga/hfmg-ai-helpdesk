"""add the COLLECT_EXTENSION call state

Employee calls now ask once for an office extension when no verified one is known
(COLLECT_EXTENSION). Stored on voice_call_sessions and in the simulator's turn log, both of which
use voice_call_state_enum. Nothing else changes: the extension is kept in the call's `collected`
and written into the ticket's intake details, so no table gains a column.

Downgrade moves sessions in that state to COLLECT_EMAIL (the next question the call would have
asked), then rebuilds the enum without the value.

Revision ID: b2d6f8a4c917
Revises: a9c3e7f1b546
Create Date: 2026-10-14 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2d6f8a4c917'
down_revision: Union[str, None] = 'a9c3e7f1b546'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PREVIOUS_STATE_VALUES = (
    "'GREETING', 'IDENTIFY_CALLER', 'PATIENT_INTENT', 'PATIENT_DETAILS', 'COLLECT_DESCRIPTION', "
    "'CLASSIFY_CALLER_TYPE', 'CONFIRM_IDENTITY', 'COLLECT_DETAILS', 'COLLECT_NAME', 'CONFIRM_NAME', "
    "'COLLECT_PHONE', 'CONFIRM_CALLBACK_NUMBER', 'COLLECT_ALTERNATE_CALLBACK_NUMBER', 'COLLECT_EMAIL', "
    "'CONFIRM_EMAIL', 'CONFIRM_CATEGORY', 'CONFIRM_SUMMARY', 'ANYTHING_ELSE', 'ESCALATED', 'COMPLETED', "
    "'ABANDONED'"
)
_NEW_STATE = "COLLECT_EXTENSION"
_STATE_COLUMNS = (
    ("voice_call_sessions", "state"),
    ("voice_simulator_turns", "state_before"),
    ("voice_simulator_turns", "state_after"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"ALTER TYPE voice_call_state_enum ADD VALUE IF NOT EXISTS '{_NEW_STATE}' "
            "AFTER 'COLLECT_ALTERNATE_CALLBACK_NUMBER'"
        )


def downgrade() -> None:
    for table, column in _STATE_COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = 'COLLECT_EMAIL' WHERE {column}::text = '{_NEW_STATE}'")
    op.execute("ALTER TYPE voice_call_state_enum RENAME TO voice_call_state_enum_old")
    op.execute(f"CREATE TYPE voice_call_state_enum AS ENUM ({_PREVIOUS_STATE_VALUES})")
    for table, column in _STATE_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE voice_call_state_enum "
            f"USING {column}::text::voice_call_state_enum"
        )
    op.execute("DROP TYPE voice_call_state_enum_old")
