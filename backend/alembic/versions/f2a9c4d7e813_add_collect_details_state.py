"""add COLLECT_DETAILS voice call state

The voice agent now asks when a problem started and whether the caller can
work, unless they already said so (docs/reviews/VOICE_LATENCY_ACCURACY_REPORT.md).
That is a new position in the conversation state machine.

Downgrade: Postgres cannot drop an enum value in place, so the type is
rebuilt. Any session currently in COLLECT_DETAILS is moved to COLLECT_NAME,
the question the agent would have asked next.

Revision ID: f2a9c4d7e813
Revises: e41f7a2c9b10
Create Date: 2026-09-28 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f2a9c4d7e813'
down_revision: Union[str, None] = 'e41f7a2c9b10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_VALUES = (
    "'GREETING', 'COLLECT_DESCRIPTION', 'COLLECT_NAME', 'COLLECT_PHONE', 'COLLECT_EMAIL', "
    "'CONFIRM_EMAIL', 'CONFIRM_CATEGORY', 'ANYTHING_ELSE', 'ESCALATED', 'COMPLETED', 'ABANDONED'"
)
_COLUMNS = (
    ("voice_call_sessions", "state"),
    ("voice_simulator_turns", "state_before"),
    ("voice_simulator_turns", "state_after"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE voice_call_state_enum ADD VALUE IF NOT EXISTS 'COLLECT_DETAILS' AFTER 'COLLECT_DESCRIPTION'"
        )


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = 'COLLECT_NAME' WHERE {column}::text = 'COLLECT_DETAILS'")
    op.execute("ALTER TYPE voice_call_state_enum RENAME TO voice_call_state_enum_old")
    op.execute(f"CREATE TYPE voice_call_state_enum AS ENUM ({_OLD_VALUES})")
    for table, column in _COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE voice_call_state_enum "
            f"USING {column}::text::voice_call_state_enum"
        )
    op.execute("DROP TYPE voice_call_state_enum_old")
