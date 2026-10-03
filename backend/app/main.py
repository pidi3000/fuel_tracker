"""FastAPI application: API under /api, the built web UI everywhere else."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import create_engine, run_migrations
from app.core.version import get_version
from app.services.auth import AuthError
from app.services.cleanup import clean_up
from app.services.container import Services
from app.services.events import EventBus
from app.services.fuel_ups import Context
from app.services.lubelogger import LubeLoggerClient
from app.services.mail import ImapMailbox, MailWatcher, ReceiptMailHandler
from app.services.processor import Processor
from app.services.ratelimit import FailureLimiter
from app.services.receipts import (
    ReceiptContext,
    expire_waiting_fuel_ups,
    notify_lonely_receipts,
    rematch_waiting,
)
from app.services.runtime_settings import RuntimeSettings
from app.services.vehicles import VehicleDirectory

logger = logging.getLogger("fuel_tracker")


class SpaStaticFiles(StaticFiles):
    """Serve the web UI; unknown paths get index.html so client-side routes work on reload."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            response = None
        if (response is None or response.status_code == 404) and not path.startswith("api/"):
            return await super().get_response("index.html", scope)
        if response is None:
            raise HTTPException(status_code=404)
        return response


def start_mail_watcher(settings: Settings, services: Services) -> MailWatcher:
    """Watch the receipt mailbox in a background thread."""

    def make_mailbox() -> ImapMailbox:
        return ImapMailbox(
            host=settings.imap_host,
            port=settings.imap_port,
            ssl=settings.imap_ssl,
            user=settings.imap_user,
            password=settings.imap_password,
            inbox=settings.imap_inbox,
            processed_folder=settings.imap_processed_folder,
        )

    watcher = MailWatcher(
        make_mailbox,
        ReceiptMailHandler(services.sessionmaker, services.receipts, services.processor.wake),
        asyncio.get_running_loop(),
        sender=settings.receipt_sender,
        subject_pattern=settings.receipt_subject_pattern,
        processed_folder=settings.imap_processed_folder,
        poll_seconds=settings.imap_poll_seconds,
    )
    watcher.start()
    return watcher


def create_app(
    settings: Settings | None = None, *, lubelogger: LubeLoggerClient | None = None
) -> FastAPI:
    """Create the app. `lubelogger` replaces the client built from the settings (for tests)."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine(settings.database_url)
        await run_migrations(engine)
        app.state.engine = engine
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        app.state.sessionmaker = sessionmaker

        runtime = RuntimeSettings(settings, sessionmaker)
        await runtime.load()
        events = EventBus()
        client = lubelogger
        if client is None and settings.lubelogger_configured:
            client = LubeLoggerClient(settings.lubelogger_url, settings.lubelogger_api_key)
        vehicles = VehicleDirectory(client)
        processor = Processor(
            sessionmaker,
            runtime,
            events,
            client,
            gps_field=settings.lubelogger_field_gps,
            address_field=settings.lubelogger_field_address,
            receipt_dir=settings.data_dir / "receipts",
        )
        services = Services(
            settings=settings,
            sessionmaker=sessionmaker,
            runtime=runtime,
            events=events,
            lubelogger=client,
            vehicles=vehicles,
            processor=processor,
            receipts=ReceiptContext(
                fuel=Context(runtime=runtime, events=events, lubelogger=client, vehicles=vehicles),
                data_dir=settings.data_dir,
            ),
        )
        app.state.services = services

        async def match_and_expire() -> None:
            async with sessionmaker() as session:
                await expire_waiting_fuel_ups(session, services.receipts)
                if await rematch_waiting(session, services.receipts):
                    processor.wake()
                await notify_lonely_receipts(session, services.receipts)
                await session.commit()

        last_cleanup = [float("-inf")]  # monotonic time of the last run; none yet

        async def clean() -> None:
            # Housekeeping isn't urgent: at most every 5 minutes
            if time.monotonic() - last_cleanup[0] < 300:
                return
            last_cleanup[0] = time.monotonic()
            async with sessionmaker() as session:
                await clean_up(session, runtime, events, settings.data_dir / "receipts")
                await session.commit()

        processor.periodic_jobs.append(match_and_expire)
        processor.periodic_jobs.append(clean)

        tasks: list[asyncio.Task] = []
        if settings.background_workers:
            tasks.append(asyncio.create_task(processor.run(), name="processor"))
            if settings.imap_configured:
                services.mail_watcher = start_mail_watcher(settings, services)
        logger.info("Fuel Tracker %s started", get_version())
        yield
        if services.mail_watcher is not None:
            await asyncio.to_thread(services.mail_watcher.stop)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if client is not None:
            await client.aclose()
        await engine.dispose()

    app = FastAPI(
        title="Fuel Tracker",
        version=get_version(),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.login_limiter = FailureLimiter()

    @app.exception_handler(AuthError)
    async def auth_error_handler(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    app.include_router(api_router)

    if Path(settings.static_dir, "index.html").is_file():
        app.mount("/", SpaStaticFiles(directory=settings.static_dir, html=True), name="ui")
    else:
        logger.warning("Web UI not found in %s, serving the API only", settings.static_dir)

    return app


app = create_app()
