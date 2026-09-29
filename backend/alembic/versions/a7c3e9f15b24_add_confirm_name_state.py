"""add CONFIRM_NAME voice call state

Names are read back spelled and, if wrong, spelled by the caller letter by
letter (docs/reviews/VOICE_LATENCY_ACCURACY_REPORT.md: transcription
"corrects" unfamiliar names into other words).

Downgrade moves any session in CONFIRM_NAME to COLLECT_PHONE (the question
after it) and rebuilds the type without the value.

Revision ID: a7c3e9f15b24
Revises: f2a9c4d7e813
Create Date: 2026-09-28 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f15b24'
down_revision: Union[str, None] = 'f2a9c4d7e813'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PREVIOUS_VALUES = (
    "'GREETING', 'COLLECT_DESCRIPTION', 'COLLECT_DETAILS', 'COLLECT_NAME', 'COLLECT_PHONE', 'COLLECT_EMAIL', "
    "'CONFIRM_EMAIL', 'CONFIRM_CATEGORY', 'ANYTHING_ELSE', 'ESCALATED', 'COMPLETED', 'ABANDONED'"
)
_COLUMNS = (
    ("voice_call_sessions", "state"),
    ("voice_simulator_turns", "state_before"),
    ("voice_simulator_turns", "state_after"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE voice_call_state_enum ADD VALUE IF NOT EXISTS 'CONFIRM_NAME' AFTER 'COLLECT_NAME'")


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = 'COLLECT_PHONE' WHERE {column}::text = 'CONFIRM_NAME'")
    op.execute("ALTER TYPE voice_call_state_enum RENAME TO voice_call_state_enum_old")
    op.execute(f"CREATE TYPE voice_call_state_enum AS ENUM ({_PREVIOUS_VALUES})")
    for table, column in _COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE voice_call_state_enum "
            f"USING {column}::text::voice_call_state_enum"
        )
    op.execute("DROP TYPE voice_call_state_enum_old")
