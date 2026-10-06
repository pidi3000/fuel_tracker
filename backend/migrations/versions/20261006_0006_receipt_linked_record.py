"""receipt linked to an existing fuel record

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06 04:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("receipts") as batch_op:
        batch_op.add_column(sa.Column("linked_vehicle_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("linked_record_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("linked_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("receipts") as batch_op:
        batch_op.drop_column("linked_at")
        batch_op.drop_column("linked_record_id")
        batch_op.drop_column("linked_vehicle_id")
