"""add index on tickets category_id

`tickets.category_id` is a foreign key (Postgres does not auto-index FK
columns the way it does primary keys) and is filtered directly by
list_tickets() (app/services/ticket_service.py, GET /tickets?category_id=)
and joined on by get_tickets_by_category() (app/services/analytics_service.py,
GET /analytics/tickets-by-category -- also reused by ai_insights_service).

Confirmed missing via `\\d tickets` (no index on category_id besides the
FK constraint itself) and via `EXPLAIN` on a category_id-filtered query,
which shows a Seq Scan on tickets (plan shape only -- this dev DB has 3
ticket rows, so timings are not evidence of anything). At the "low
thousands of tickets/year" volume DATABASE_DESIGN.md sizes this system for,
that scan grows linearly with total ticket count for every
category-filtered list and every analytics category breakdown.

Revision ID: dd3a82a4a05a
Revises: c78e93327925
Create Date: 2026-09-21 16:49:18.580120

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dd3a82a4a05a'
down_revision: Union[str, None] = 'c78e93327925'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f('ix_tickets_category_id'), 'tickets', ['category_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tickets_category_id'), table_name='tickets')
