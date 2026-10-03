"""Alembic environment.

Migrations run on app start-up (see `app.core.database.run_migrations`), which passes an
open connection. For creating new revisions, run from `backend/`:

    uv run alembic revision --autogenerate -m "describe the change"
"""

from alembic import context
from sqlalchemy import create_engine

from app import models  # noqa: F401  (registers the tables)
from app.core.config import get_settings
from app.core.database import Base
from app.core.types import DecimalText, UTCDateTime

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Write migrations with plain SQLAlchemy types, so they never depend on app code."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime()"
    if type_ == "type" and isinstance(obj, DecimalText):
        return "sa.String()"
    return False


def run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite can't alter tables in place
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    run_migrations(connection)
else:
    # Command line use: connect synchronously to the configured database
    url = get_settings().database_url.replace("+aiosqlite", "")
    with create_engine(url).connect() as connection:
        run_migrations(connection)
        connection.commit()
