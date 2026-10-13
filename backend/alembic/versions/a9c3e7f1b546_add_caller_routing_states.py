"""add the IDENTIFY_CALLER, PATIENT_INTENT and PATIENT_DETAILS call states

The voice agent now opens by asking whether the caller is a patient or an employee
(IDENTIFY_CALLER) and, for patients, asks what they need (PATIENT_INTENT) and one optional
follow-up (PATIENT_DETAILS). Stored on voice_call_sessions and in the simulator's turn log, both
of which use voice_call_state_enum.

Downgrade moves sessions in those states to COLLECT_DESCRIPTION (where a call without a known
caller type begins), then rebuilds the enum without the values.

Revision ID: a9c3e7f1b546
Revises: f7a1c5d9e324
Create Date: 2026-10-13 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a9c3e7f1b546'
down_revision: Union[str, None] = 'f7a1c5d9e324'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PREVIOUS_STATE_VALUES = (
    "'GREETING', 'COLLECT_DESCRIPTION', 'CLASSIFY_CALLER_TYPE', 'CONFIRM_IDENTITY', 'COLLECT_DETAILS', "
    "'COLLECT_NAME', 'CONFIRM_NAME', 'COLLECT_PHONE', 'CONFIRM_CALLBACK_NUMBER', "
    "'COLLECT_ALTERNATE_CALLBACK_NUMBER', 'COLLECT_EMAIL', 'CONFIRM_EMAIL', 'CONFIRM_CATEGORY', "
    "'CONFIRM_SUMMARY', 'ANYTHING_ELSE', 'ESCALATED', 'COMPLETED', 'ABANDONED'"
)
_NEW_STATES = ("IDENTIFY_CALLER", "PATIENT_INTENT", "PATIENT_DETAILS")
_STATE_COLUMNS = (
    ("voice_call_sessions", "state"),
    ("voice_simulator_turns", "state_before"),
    ("voice_simulator_turns", "state_after"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in _NEW_STATES:
            op.execute(f"ALTER TYPE voice_call_state_enum ADD VALUE IF NOT EXISTS '{value}' AFTER 'GREETING'")


def downgrade() -> None:
    quoted = ", ".join(f"'{value}'" for value in _NEW_STATES)
    for table, column in _STATE_COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = 'COLLECT_DESCRIPTION' WHERE {column}::text IN ({quoted})")
    op.execute("ALTER TYPE voice_call_state_enum RENAME TO voice_call_state_enum_old")
    op.execute(f"CREATE TYPE voice_call_state_enum AS ENUM ({_PREVIOUS_STATE_VALUES})")
    for table, column in _STATE_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE voice_call_state_enum "
            f"USING {column}::text::voice_call_state_enum"
        )
    op.execute("DROP TYPE voice_call_state_enum_old")
