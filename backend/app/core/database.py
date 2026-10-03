"""Database engine, sessions and migrations (SQLite via SQLAlchemy)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, MetaData, event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


class Base(DeclarativeBase):
    # Named constraints, so migrations can add and drop them on SQLite
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def create_engine(database_url: str) -> AsyncEngine:
    engine = create_async_engine(database_url)

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def _upgrade(connection: Connection) -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def run_migrations(engine: AsyncEngine) -> None:
    """Bring the database schema up to date; called on start-up."""
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade)
