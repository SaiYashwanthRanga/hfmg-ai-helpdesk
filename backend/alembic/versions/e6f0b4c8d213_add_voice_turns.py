"""add voice_turns: recorded outcomes of /turn requests that carry a turn_id

One row per (call, turn_id), written in the same transaction as the turn's effects, so a
repeated turn_id can be answered from the saved response without running the turn again.
Rows are removed with their call (ON DELETE CASCADE).

Revision ID: e6f0b4c8d213
Revises: d5e9a3b7c102
Create Date: 2026-10-12 15:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e6f0b4c8d213'
down_revision: Union[str, None] = 'd5e9a3b7c102'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "voice_turns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["voice_call_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "turn_id", name="uq_voice_turns_session_turn"),
    )


def downgrade() -> None:
    op.drop_table("voice_turns")
