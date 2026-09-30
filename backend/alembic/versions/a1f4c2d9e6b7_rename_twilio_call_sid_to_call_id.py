"""rename voice_call_sessions.twilio_call_sid to call_id

Revision ID: a1f4c2d9e6b7
Revises: b8d4f0a26c35
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1f4c2d9e6b7'
down_revision: Union[str, None] = 'b8d4f0a26c35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('voice_call_sessions', 'twilio_call_sid', new_column_name='call_id')
    op.execute('ALTER INDEX ix_voice_call_sessions_twilio_call_sid RENAME TO ix_voice_call_sessions_call_id')


def downgrade() -> None:
    op.execute('ALTER INDEX ix_voice_call_sessions_call_id RENAME TO ix_voice_call_sessions_twilio_call_sid')
    op.alter_column('voice_call_sessions', 'call_id', new_column_name='twilio_call_sid')
