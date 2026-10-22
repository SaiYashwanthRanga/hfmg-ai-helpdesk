"""add ticket_reports: uploaded Freshworks exports, their AI analysis and email status

Revision ID: e9a4c7b13d62
Revises: d8f3b6a2c415
Create Date: 2026-10-22 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e9a4c7b13d62'
down_revision: Union[str, None] = 'd8f3b6a2c415'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "ticket_reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=8), nullable=False),
        sa.Column("source_row_count", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("detected_columns", _JSONB, nullable=False),
        sa.Column("validation", _JSONB, nullable=False),
        sa.Column("dataset", _JSONB, nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress", _JSONB, nullable=True),
        sa.Column("analysis", _JSONB, nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("analysis_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("analysis_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_status", sa.String(length=16), nullable=False),
        sa.Column("email_recipient", sa.String(length=320), nullable=True),
        sa.Column("email_attempts", sa.Integer(), nullable=False),
        sa.Column("email_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_error", sa.String(length=300), nullable=True),
        sa.Column("email_log", _JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ticket_reports_created_at", "ticket_reports", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ticket_reports_created_at", table_name="ticket_reports")
    op.drop_table("ticket_reports")
