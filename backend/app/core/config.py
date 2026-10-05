"""Application settings, read from environment variables (and a `.env` file in development).

Some of them can be overridden by an admin in the web UI; see `app.services.runtime_settings`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_FUEL_TYPES = ["Diesel", "Super", "Super Plus", "Super E10"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Directory for the SQLite database and stored receipts (a Docker volume in production)
    data_dir: Path = Path("/data")
    # Built web UI; served by FastAPI when present
    static_dir: Path = Path(__file__).resolve().parents[2] / "static"
    log_level: str = "INFO"
    # How long a web UI login lasts
    session_days: int = 30
    # Run the background workers (tests switch them off)
    background_workers: bool = True

    # --- looking for a newer Fuel Tracker image ---
    update_check: bool = True
    # The image to look at (ghcr.io-style registry; the package must be readable without login)
    update_check_image: str = "ghcr.io/pidi3000/fuel_tracker"

    # --- LubeLogger ---
    lubelogger_url: str = ""
    lubelogger_api_key: str = ""
    # Names of the two extra fields (type Location and Text) created for fuel records
    lubelogger_field_gps: str = "GPS Location"
    lubelogger_field_address: str = "Address"

    # --- mailbox with the Pace Drive receipts (IMAP). Empty host: no mail handling ---
    imap_host: str = ""
    imap_port: int = 993
    imap_ssl: bool = True
    imap_user: str = ""
    imap_password: str = ""
    imap_inbox: str = "INBOX"
    # Receipts that were used (or ignored) are moved here
    imap_processed_folder: str = "Processed"
    # Check the inbox at least this often, even if the server doesn't announce new mail
    imap_poll_seconds: int = 300
    # An email is a receipt if it comes from this address and its subject matches
    receipt_sender: str = "no-reply@connectedfueling.com"
    receipt_subject_pattern: str = r"\|\s*PACE Pay\s*$"

    # --- values an admin can override in the web UI ---
    tz: str = "Europe/Berlin"
    fuel_types: Annotated[list[str], NoDecode] = DEFAULT_FUEL_TYPES
    volume_unit: str = "L"
    currency: str = "EUR"
    review_before_send: bool = True
    # How long finished fuel-ups stay in Fuel Tracker before they are deleted
    done_retention_days: int = 7
    # A receipt matches a fuel-up when their times are at most this far apart
    match_window_minutes: int = 10
    # A fuel-up that has waited this long for its receipt fails
    receipt_timeout_minutes: int = 60
    # How the date on a Pace Drive receipt is written (Python strptime format, English app)
    pace_date_format: str = "%m/%d/%Y, %I:%M %p"

    @field_validator("fuel_types", mode="before")
    @classmethod
    def split_fuel_types(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir / 'fuel_tracker.db'}"

    @property
    def imap_configured(self) -> bool:
        return bool(self.imap_host.strip() and self.imap_user.strip())

    @property
    def lubelogger_configured(self) -> bool:
        return bool(self.lubelogger_url.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
