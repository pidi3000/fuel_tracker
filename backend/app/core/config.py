"""Application settings, read from environment variables (and a `.env` file in development)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Directory for the SQLite database and stored receipts (a Docker volume in production)
    data_dir: Path = Path("/data")
    # Built web UI; served by FastAPI when present
    static_dir: Path = Path(__file__).resolve().parents[2] / "static"
    log_level: str = "INFO"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir / 'fuel_tracker.db'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
