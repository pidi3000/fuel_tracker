"""Health and version endpoints."""

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.core.version import get_version

router = APIRouter(tags=["system"])


class VersionOut(BaseModel):
    version: str


class HealthOut(BaseModel):
    status: str
    database: str


@router.get("/version")
async def version() -> VersionOut:
    return VersionOut(version=get_version())


@router.get("/health")
async def health(request: Request, response: Response) -> HealthOut:
    try:
        async with request.app.state.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "error"

    healthy = database == "ok"
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthOut(status="ok" if healthy else "error", database=database)
