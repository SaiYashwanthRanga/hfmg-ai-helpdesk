"""add callback number confirmation: ticket caller_number/callback_number, two voice states

The agent now reads the callback number back before filing a ticket. Tickets keep
the inbound caller ID (`caller_number`, for audit) and the number the caller
confirmed (`callback_number`); `phone_number` stays the number to call, so
existing readers of it are unaffected. Both new columns are nullable: existing
tickets and web tickets have neither.

Downgrade drops the columns, moves any session in a new state to COLLECT_EMAIL
(the question before the callback check) and rebuilds the enum without them.

Revision ID: b3e7a1c9d052
Revises: a1f4c2d9e6b7
Create Date: 2026-10-07 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b3e7a1c9d052'
down_revision: Union[str, None] = 'a1f4c2d9e6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PREVIOUS_VALUES = (
    "'GREETING', 'COLLECT_DESCRIPTION', 'COLLECT_DETAILS', 'COLLECT_NAME', 'CONFIRM_NAME', 'COLLECT_PHONE', "
    "'COLLECT_EMAIL', 'CONFIRM_EMAIL', 'CONFIRM_CATEGORY', 'CONFIRM_SUMMARY', 'ANYTHING_ELSE', "
    "'ESCALATED', 'COMPLETED', 'ABANDONED'"
)
_NEW_STATES = ("CONFIRM_CALLBACK_NUMBER", "COLLECT_ALTERNATE_CALLBACK_NUMBER")
_COLUMNS = (
    ("voice_call_sessions", "state"),
    ("voice_simulator_turns", "state_before"),
    ("voice_simulator_turns", "state_after"),
)


def upgrade() -> None:
    op.add_column("tickets", sa.Column("caller_number", sa.String(length=32), nullable=True))
    op.add_column("tickets", sa.Column("callback_number", sa.String(length=32), nullable=True))
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE voice_call_state_enum ADD VALUE IF NOT EXISTS 'CONFIRM_CALLBACK_NUMBER' AFTER 'COLLECT_PHONE'")
        op.execute(
            "ALTER TYPE voice_call_state_enum ADD VALUE IF NOT EXISTS 'COLLECT_ALTERNATE_CALLBACK_NUMBER' "
            "AFTER 'CONFIRM_CALLBACK_NUMBER'"
        )


def downgrade() -> None:
    op.drop_column("tickets", "callback_number")
    op.drop_column("tickets", "caller_number")
    in_new_states = ", ".join(f"'{state}'" for state in _NEW_STATES)
    for table, column in _COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = 'COLLECT_EMAIL' WHERE {column}::text IN ({in_new_states})")
    op.execute("ALTER TYPE voice_call_state_enum RENAME TO voice_call_state_enum_old")
    op.execute(f"CREATE TYPE voice_call_state_enum AS ENUM ({_PREVIOUS_VALUES})")
    for table, column in _COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE voice_call_state_enum "
            f"USING {column}::text::voice_call_state_enum"
        )
    op.execute("DROP TYPE voice_call_state_enum_old")
