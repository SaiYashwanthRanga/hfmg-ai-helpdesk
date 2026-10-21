"""add the PROVIDER caller type and the Providers category

Provider calls (CALL_FLOW.md 2.5, VOICE_PROVIDER_FLOW) are stored with caller type PROVIDER on the ticket
(caller_type_enum) and filed under one new category, Providers. Sessions keep the caller type in their
`collected` JSON, so no other column changes.

Downgrade moves PROVIDER tickets to INTERNAL_IT (they are IT tickets), rebuilds caller_type_enum without the
value, and deletes the Providers category only if no ticket uses it.

Revision ID: d8f3b6a2c415
Revises: c3e9a1f7d208
Create Date: 2026-10-21 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd8f3b6a2c415'
down_revision: Union[str, None] = 'c3e9a1f7d208'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROVIDERS_CATEGORY = (
    "Providers",
    "Problems reported by providers: eClinicalWorks, e-prescribing, dictation, results, telehealth, fax and referrals.",
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE caller_type_enum ADD VALUE IF NOT EXISTS 'PROVIDER'")
    name, description = PROVIDERS_CATEGORY
    op.execute(
        sa.text(
            "INSERT INTO categories (id, name, description, default_priority, is_active) "
            "VALUES (gen_random_uuid(), :name, :description, 'HIGH', true) "
            "ON CONFLICT (name) DO NOTHING"
        ).bindparams(name=name, description=description)
    )


def downgrade() -> None:
    op.execute("UPDATE tickets SET caller_type = 'INTERNAL_IT' WHERE caller_type::text = 'PROVIDER'")
    op.execute("ALTER TYPE caller_type_enum RENAME TO caller_type_enum_old")
    op.execute("CREATE TYPE caller_type_enum AS ENUM ('INTERNAL_IT', 'PATIENT_SUPPORT')")
    op.execute(
        "ALTER TABLE tickets ALTER COLUMN caller_type TYPE caller_type_enum USING caller_type::text::caller_type_enum"
    )
    op.execute("DROP TYPE caller_type_enum_old")
    op.execute(
        sa.text(
            "DELETE FROM categories WHERE name = :name "
            "AND NOT EXISTS (SELECT 1 FROM tickets WHERE tickets.category_id = categories.id)"
        ).bindparams(name=PROVIDERS_CATEGORY[0])
    )
