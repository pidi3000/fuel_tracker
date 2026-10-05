"""receipt email moved

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-05 04:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Until now every receipt email was moved as soon as it was stored, so the
    # receipts that exist are all moved
    with op.batch_alter_table("receipts") as batch_op:
        batch_op.add_column(
            sa.Column("email_moved", sa.Boolean(), nullable=False, server_default=sa.true())
        )
    with op.batch_alter_table("receipts") as batch_op:
        batch_op.alter_column("email_moved", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("receipts") as batch_op:
        batch_op.drop_column("email_moved")
