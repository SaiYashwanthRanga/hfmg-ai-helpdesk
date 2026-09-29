"""add voice simulator

Adds the AI Call Simulator's storage (VOICE_SIMULATOR_DESIGN.md §4):

- `SIMULATOR` value on ticket_source_enum, so simulator tickets can be
  excluded from the queue, dashboards and analytics.
- `voice_call_sessions.is_simulated`, the same isolation for call sessions.
- `voice_simulator_sessions`: per-session simulator options.
- `voice_simulator_turns`: per-turn transcript, timings and debug trace.
  Audio is never stored.

Downgrade note: Postgres cannot drop a value from an enum type. Downgrade
deletes SIMULATOR tickets and rebuilds ticket_source_enum without the value.

Revision ID: e41f7a2c9b10
Revises: dd3a82a4a05a
Create Date: 2026-09-28 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e41f7a2c9b10'
down_revision: Union[str, None] = 'dd3a82a4a05a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATE_ENUM = postgresql.ENUM(name='voice_call_state_enum', create_type=False)


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on
    # older Postgres versions; the autocommit block keeps it portable.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE ticket_source_enum ADD VALUE IF NOT EXISTS 'SIMULATOR'")

    op.add_column(
        'voice_call_sessions',
        sa.Column('is_simulated', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.create_index(
        'ix_voice_call_sessions_is_simulated',
        'voice_call_sessions',
        ['is_simulated'],
        unique=False,
        postgresql_where=sa.text('is_simulated'),
    )

    op.create_table(
        'voice_simulator_sessions',
        sa.Column('session_id', sa.UUID(), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=True),
        sa.Column('tts_enabled', sa.Boolean(), nullable=False),
        sa.Column('send_notifications', sa.Boolean(), nullable=False),
        sa.Column('end_reason', sa.String(length=16), nullable=True),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['voice_call_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('session_id'),
    )
    op.create_index(
        op.f('ix_voice_simulator_sessions_created_at'), 'voice_simulator_sessions', ['created_at'], unique=False
    )

    op.create_table(
        'voice_simulator_turns',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('session_id', sa.UUID(), nullable=False),
        sa.Column('turn_client_id', sa.UUID(), nullable=False),
        sa.Column('turn_index', sa.SmallInteger(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('input_mode', sa.String(length=8), nullable=False),
        sa.Column('utterance', sa.Text(), nullable=True),
        sa.Column('stt_raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('stt_confidence', sa.Float(), nullable=True),
        sa.Column('state_before', _STATE_ENUM, nullable=True),
        sa.Column('state_after', _STATE_ENUM, nullable=True),
        sa.Column('intent', sa.String(length=32), nullable=True),
        sa.Column('agent_text', sa.Text(), nullable=True),
        sa.Column('call_ended', sa.Boolean(), nullable=False),
        sa.Column('collected_after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('ticket_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('llm_trace', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('errors', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('stt_ms', sa.Float(), nullable=True),
        sa.Column('llm_ms', sa.Float(), nullable=True),
        sa.Column('tts_ms', sa.Float(), nullable=True),
        sa.Column('ticket_create_ms', sa.Float(), nullable=True),
        sa.Column('server_total_ms', sa.Float(), nullable=True),
        sa.Column('utterance_ms', sa.Float(), nullable=True),
        sa.Column('capture_ms', sa.Float(), nullable=True),
        sa.Column('queue_wait_ms', sa.Float(), nullable=True),
        sa.Column('playback_start_ms', sa.Float(), nullable=True),
        sa.Column('playback_duration_ms', sa.Float(), nullable=True),
        sa.Column('turn_total_ms', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['voice_call_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('turn_client_id'),
    )
    op.create_index(op.f('ix_voice_simulator_turns_session_id'), 'voice_simulator_turns', ['session_id'], unique=False)
    op.create_index(op.f('ix_voice_simulator_turns_created_at'), 'voice_simulator_turns', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_voice_simulator_turns_created_at'), table_name='voice_simulator_turns')
    op.drop_index(op.f('ix_voice_simulator_turns_session_id'), table_name='voice_simulator_turns')
    op.drop_table('voice_simulator_turns')
    op.drop_index(op.f('ix_voice_simulator_sessions_created_at'), table_name='voice_simulator_sessions')
    op.drop_table('voice_simulator_sessions')

    # Simulated sessions and their tickets are test data; drop them rather
    # than leave rows the previous schema cannot represent.
    op.execute("DELETE FROM voice_call_sessions WHERE is_simulated")
    op.drop_index('ix_voice_call_sessions_is_simulated', table_name='voice_call_sessions')
    op.drop_column('voice_call_sessions', 'is_simulated')

    op.execute("DELETE FROM tickets WHERE source = 'SIMULATOR'")
    op.execute("ALTER TABLE tickets ALTER COLUMN source DROP DEFAULT")
    op.execute("ALTER TYPE ticket_source_enum RENAME TO ticket_source_enum_old")
    op.execute("CREATE TYPE ticket_source_enum AS ENUM ('WEB', 'PHONE', 'EMAIL', 'WALK_IN')")
    op.execute(
        "ALTER TABLE tickets ALTER COLUMN source TYPE ticket_source_enum "
        "USING source::text::ticket_source_enum"
    )
    op.execute("ALTER TABLE tickets ALTER COLUMN source SET DEFAULT 'WEB'")
    op.execute("DROP TYPE ticket_source_enum_old")
