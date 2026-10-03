"""FastAPI application: API under /api, the built web UI everywhere else."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import create_engine, run_migrations
from app.core.version import get_version

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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine(settings.database_url)
        await run_migrations(engine)
        app.state.engine = engine
        logger.info("Fuel Tracker %s started", get_version())
        yield
        await engine.dispose()

    app = FastAPI(
        title="Fuel Tracker",
        version=get_version(),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.include_router(api_router)

    if Path(settings.static_dir, "index.html").is_file():
        app.mount("/", SpaStaticFiles(directory=settings.static_dir, html=True), name="ui")
    else:
        logger.warning("Web UI not found in %s, serving the API only", settings.static_dir)

    return app


app = create_app()
