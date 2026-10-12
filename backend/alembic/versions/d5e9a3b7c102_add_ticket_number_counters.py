"""add ticket_number_counters: database-allocated ticket numbers

Ticket numbers (HFMG-<year>-<6 digits>) were count(tickets this year) + 1, so two
tickets created at once got the same number and one creation failed on the unique
index; a deleted ticket also freed its number for reuse. The number now comes from
one atomic upsert on this table (one row per year), keeping the same format.

The counter is seeded from the HIGHEST number already issued each year (not the
count), so the next ticket continues after every existing one. SIM-... numbers
and any ticket number not in the HFMG-<year>-<digits> form are ignored.

Deploy note: run this before starting the new application version. The old
version ignores the table, but tickets it creates after this migration are not
counted in it; if old and new versions overlap, re-run the seed statement below.

Revision ID: d5e9a3b7c102
Revises: c4d8f2a1b593
Create Date: 2026-10-12 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd5e9a3b7c102'
down_revision: Union[str, None] = 'c4d8f2a1b593'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ticket_number_counters",
        sa.Column("year", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("year"),
    )
    op.execute(
        """
        INSERT INTO ticket_number_counters (year, last_value)
        SELECT substring(ticket_number FROM '^HFMG-(\\d{4})-\\d+$')::int,
               max(substring(ticket_number FROM '^HFMG-\\d{4}-(\\d+)$')::int)
        FROM tickets
        WHERE ticket_number ~ '^HFMG-\\d{4}-\\d+$'
        GROUP BY 1
        """
    )


def downgrade() -> None:
    op.drop_table("ticket_number_counters")
