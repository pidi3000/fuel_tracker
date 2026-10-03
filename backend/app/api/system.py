"""Health and version endpoints."""

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import AdminUser, ServicesDep
from app.core.version import get_version
from app.services.lubelogger import LubeLoggerError

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
    return StatusOut(
        lubelogger=await _lubelogger_status(services), mailbox=_mailbox_status(services)
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
