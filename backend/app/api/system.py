"""Health and version endpoints."""

from datetime import datetime

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import AdminUser, ServicesDep
from app.core.version import get_version
from app.services.lubelogger import LubeLoggerError
from app.services.updates import announce_update

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


class ConnectionStatus(BaseModel):
    state: str  # "ok", "error" or "not_configured"
    message: str = ""


class StatusOut(BaseModel):
    lubelogger: ConnectionStatus
    mailbox: ConnectionStatus
    email: ConnectionStatus


def _mailbox_status(services: ServicesDep) -> ConnectionStatus:
    if not services.settings.imap_configured:
        return ConnectionStatus(
            state="not_configured", message="IMAP_HOST and IMAP_USER are not set."
        )
    watcher = services.mail_watcher
    if watcher is None:
        return ConnectionStatus(state="error", message="The mailbox isn't being watched.")
    state = watcher.status
    if state.state == "ok":
        return ConnectionStatus(state="ok")
    if state.state == "connecting":
        return ConnectionStatus(state="connecting", message="Connecting to the mailbox…")
    return ConnectionStatus(state="error", message=state.message or "Not connected.")


@router.get("/status")
async def connection_status(_: AdminUser, services: ServicesDep) -> StatusOut:
    """Whether the connected systems work. Slower than /health, for the admin UI."""
    email = await services.email.status()
    return StatusOut(
        lubelogger=await _lubelogger_status(services),
        mailbox=_mailbox_status(services),
        email=ConnectionStatus(state=email.state, message=email.message),
    )


async def _lubelogger_status(services: ServicesDep) -> ConnectionStatus:
    client = services.lubelogger
    if client is None:
        return ConnectionStatus(state="not_configured", message="LUBELOGGER_URL is not set.")
    try:
        await client.check()
        defined = await client.extra_field_names()
    except LubeLoggerError as exc:
        return ConnectionStatus(state="error", message=str(exc))
    wanted = {services.settings.lubelogger_field_gps, services.settings.lubelogger_field_address}
    if missing := sorted(wanted - defined):
        return ConnectionStatus(
            state="error",
            message=(
                "Create these extra fields for fuel records in LubeLogger "
                f"(Settings, Manage Extra Fields): {', '.join(missing)}."
            ),
        )
    return ConnectionStatus(state="ok")


class UpdateOut(BaseModel):
    enabled: bool
    channel: str | None = None  # "release", "test" or "dev" (a build that can't be checked)
    current: str
    latest: str | None = None
    available: bool = False
    checked_at: datetime | None = None
    error: str | None = None


def _update_out(services: ServicesDep) -> UpdateOut:
    checker = services.updates
    if checker is None:
        return UpdateOut(enabled=False, current=get_version())
    return UpdateOut(enabled=True, **vars(checker.status))


@router.get("/update")
async def update_status(_: AdminUser, services: ServicesDep) -> UpdateOut:
    """What the last look for a newer image found."""
    return _update_out(services)


@router.post("/update/check")
async def check_for_update(_: AdminUser, services: ServicesDep) -> UpdateOut:
    """Look for a newer image now."""
    if services.updates is not None:
        status = await services.updates.check()
        async with services.sessionmaker() as session:
            if await announce_update(session, services.events, status):
                await session.commit()
    return _update_out(services)
