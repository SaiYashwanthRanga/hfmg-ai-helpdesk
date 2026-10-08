"""add dual intake: caller_type on tickets, CLASSIFY_CALLER_TYPE state, patient categories

Voice agent now classifies callers as INTERNAL_IT or PATIENT_SUPPORT. Patient
calls use a shorter intake flow and land in patient-specific categories.

Ticket.caller_type (nullable) records the classification for reporting; NULL for
web tickets and calls from before this migration.

Downgrade drops the column, moves sessions in CLASSIFY_CALLER_TYPE to
COLLECT_DESCRIPTION, rebuilds the state enum, removes the caller_type_enum,
and deletes the patient categories (only if empty).

Revision ID: c4d8f2a1b593
Revises: b3e7a1c9d052
Create Date: 2026-10-08 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4d8f2a1b593'
down_revision: Union[str, None] = 'b3e7a1c9d052'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PREVIOUS_STATE_VALUES = (
    "'GREETING', 'COLLECT_DESCRIPTION', 'COLLECT_DETAILS', 'COLLECT_NAME', 'CONFIRM_NAME', "
    "'COLLECT_PHONE', 'CONFIRM_CALLBACK_NUMBER', 'COLLECT_ALTERNATE_CALLBACK_NUMBER', "
    "'COLLECT_EMAIL', 'CONFIRM_EMAIL', 'CONFIRM_CATEGORY', 'CONFIRM_SUMMARY', "
    "'ANYTHING_ELSE', 'ESCALATED', 'COMPLETED', 'ABANDONED'"
)
_NEW_STATE = "CLASSIFY_CALLER_TYPE"
_STATE_COLUMNS = (
    ("voice_call_sessions", "state"),
    ("voice_simulator_turns", "state_before"),
    ("voice_simulator_turns", "state_after"),
)

PATIENT_CATEGORIES = [
    ("Appointment Booking", "Patient needs help booking, rescheduling, or cancelling an appointment."),
    ("Patient Portal Login", "Patient cannot log in to the patient portal."),
    ("Website Error", "Patient is seeing errors or issues on the HFMG website."),
    ("Insurance / Billing Portal", "Patient has issues with insurance forms or billing portal."),
    ("Medical Records Portal", "Patient needs help accessing medical records online."),
    ("Prescription Refill Portal", "Patient has issues with the prescription refill portal."),
    ("Other Patient Support", "Patient support issue that does not fit another patient category."),
]


def upgrade() -> None:
    op.execute("CREATE TYPE caller_type_enum AS ENUM ('INTERNAL_IT', 'PATIENT_SUPPORT')")
    op.add_column(
        "tickets",
        sa.Column("caller_type", sa.Enum("INTERNAL_IT", "PATIENT_SUPPORT", name="caller_type_enum"), nullable=True),
    )
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE voice_call_state_enum ADD VALUE IF NOT EXISTS "
            f"'{_NEW_STATE}' AFTER 'COLLECT_DESCRIPTION'"
        )
    for name, description in PATIENT_CATEGORIES:
        op.execute(
            sa.text(
                "INSERT INTO categories (id, name, description, default_priority, is_active) "
                "VALUES (gen_random_uuid(), :name, :description, 'MEDIUM', true) "
                "ON CONFLICT (name) DO NOTHING"
            ).bindparams(name=name, description=description)
        )


def downgrade() -> None:
    op.drop_column("tickets", "caller_type")
    op.execute("DROP TYPE IF EXISTS caller_type_enum")
    for name, _ in PATIENT_CATEGORIES:
        op.execute(
            sa.text(
                "DELETE FROM categories WHERE name = :name "
                "AND NOT EXISTS (SELECT 1 FROM tickets WHERE category_id = categories.id)"
            ).bindparams(name=name)
        )
    for table, column in _STATE_COLUMNS:
        op.execute(
            f"UPDATE {table} SET {column} = 'COLLECT_DESCRIPTION' "
            f"WHERE {column}::text = '{_NEW_STATE}'"
        )
    op.execute("ALTER TYPE voice_call_state_enum RENAME TO voice_call_state_enum_old")
    op.execute(f"CREATE TYPE voice_call_state_enum AS ENUM ({_PREVIOUS_STATE_VALUES})")
    for table, column in _STATE_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE voice_call_state_enum "
            f"USING {column}::text::voice_call_state_enum"
        )
    op.execute("DROP TYPE voice_call_state_enum_old")
