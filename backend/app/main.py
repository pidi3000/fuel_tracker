"""FastAPI application: API under /api, the built web UI everywhere else."""

import asyncio
import logging
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
from app.services.container import Services
from app.services.events import EventBus
from app.services.lubelogger import LubeLoggerClient
from app.services.processor import Processor
from app.services.ratelimit import FailureLimiter
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
        )
        app.state.services = Services(
            settings=settings,
            sessionmaker=sessionmaker,
            runtime=runtime,
            events=events,
            lubelogger=client,
            vehicles=vehicles,
            processor=processor,
        )

        tasks: list[asyncio.Task] = []
        if settings.background_workers:
            tasks.append(asyncio.create_task(processor.run(), name="processor"))
        logger.info("Fuel Tracker %s started", get_version())
        yield
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
