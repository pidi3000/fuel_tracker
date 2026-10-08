"""notifications are also sent by email

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-08 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The notifications that exist now are old news: they must not be emailed (the default
    # applies to them only; new notifications start as "pending")
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.add_column(
            sa.Column("email_state", sa.String(10), nullable=False, server_default="skipped")
        )
        batch_op.add_column(
            sa.Column("email_attempts", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.drop_column("email_attempts")
        batch_op.drop_column("email_state")
