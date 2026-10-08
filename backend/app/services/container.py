"""The shared services, created at start-up and kept on `app.state.services`."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.email_notifications import EmailNotifier
from app.services.events import EventBus
from app.services.fuel_ups import Context
from app.services.lubelogger import LubeLoggerClient
from app.services.mail import MailWatcher
from app.services.processor import Processor
from app.services.receipts import ReceiptContext
from app.services.runtime_settings import RuntimeSettings
from app.services.updates import UpdateChecker
from app.services.vehicles import VehicleDirectory


@dataclass
class Services:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    runtime: RuntimeSettings
    events: EventBus
    lubelogger: LubeLoggerClient | None
    vehicles: VehicleDirectory
    processor: Processor
    receipts: ReceiptContext
    email: EmailNotifier
    mail_watcher: MailWatcher | None = None
    updates: UpdateChecker | None = None

    @property
    def context(self) -> Context:
        return Context(
            runtime=self.runtime,
            events=self.events,
            lubelogger=self.lubelogger,
            vehicles=self.vehicles,
        )
