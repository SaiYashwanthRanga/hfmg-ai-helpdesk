"""add the COLLECT_DIAGNOSTICS call state

Employee calls now get a few issue-specific questions (COLLECT_DIAGNOSTICS) after the problem is described:
which device, the exact error, which server and drive. No troubleshooting is done on the call. Stored on
voice_call_sessions and in the simulator's turn log, both of which use voice_call_state_enum. The answers
are kept in the call's `collected` and written into the ticket's intake details; no table gains a column.

Downgrade moves sessions in that state to COLLECT_NAME (the next question the call would have asked),
then rebuilds the enum without the value.

Revision ID: c3e9a1f7d208
Revises: b2d6f8a4c917
Create Date: 2026-10-14 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3e9a1f7d208'
down_revision: Union[str, None] = 'b2d6f8a4c917'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PREVIOUS_STATE_VALUES = (
    "'GREETING', 'IDENTIFY_CALLER', 'PATIENT_INTENT', 'PATIENT_DETAILS', 'COLLECT_DESCRIPTION', "
    "'CLASSIFY_CALLER_TYPE', 'CONFIRM_IDENTITY', 'COLLECT_DETAILS', 'COLLECT_NAME', 'CONFIRM_NAME', "
    "'COLLECT_PHONE', 'CONFIRM_CALLBACK_NUMBER', 'COLLECT_ALTERNATE_CALLBACK_NUMBER', 'COLLECT_EXTENSION', "
    "'COLLECT_EMAIL', 'CONFIRM_EMAIL', 'CONFIRM_CATEGORY', 'CONFIRM_SUMMARY', 'ANYTHING_ELSE', 'ESCALATED', "
    "'COMPLETED', 'ABANDONED'"
)
_NEW_STATE = "COLLECT_DIAGNOSTICS"
_STATE_COLUMNS = (
    ("voice_call_sessions", "state"),
    ("voice_simulator_turns", "state_before"),
    ("voice_simulator_turns", "state_after"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE voice_call_state_enum ADD VALUE IF NOT EXISTS '{_NEW_STATE}' AFTER 'COLLECT_DETAILS'")


def downgrade() -> None:
    for table, column in _STATE_COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = 'COLLECT_NAME' WHERE {column}::text = '{_NEW_STATE}'")
    op.execute("ALTER TYPE voice_call_state_enum RENAME TO voice_call_state_enum_old")
    op.execute(f"CREATE TYPE voice_call_state_enum AS ENUM ({_PREVIOUS_STATE_VALUES})")
    for table, column in _STATE_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE voice_call_state_enum "
            f"USING {column}::text::voice_call_state_enum"
        )
    op.execute("DROP TYPE voice_call_state_enum_old")
