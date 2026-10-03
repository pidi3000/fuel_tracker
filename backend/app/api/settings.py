"""Settings an admin can change in the web UI, and the connection details that can't be."""

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import AdminUser, ServicesDep
from app.core.version import get_version
from app.services.runtime_settings import SPECS, SettingError

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingOut(BaseModel):
    key: str
    label: str
    description: str
    kind: str  # "int", "bool", "text" or "list"
    minimum: int | None
    maximum: int | None
    value: Any
    default: Any
    overridden: bool


class SettingIn(BaseModel):
    value: Any


class EnvironmentOut(BaseModel):
    """Settings that are only changed through environment variables (never secrets)."""

    version: str
    lubelogger_url: str
    lubelogger_api_key_set: bool
    lubelogger_field_gps: str
    lubelogger_field_address: str
    imap_host: str
    imap_port: int
    imap_user: str
    imap_inbox: str
    imap_processed_folder: str
    receipt_sender: str
    receipt_subject_pattern: str
    session_days: int


class SettingsOut(BaseModel):
    settings: list[SettingOut]
    environment: EnvironmentOut


def _item(services: ServicesDep, key: str) -> SettingOut:
    return SettingOut(**next(d for d in services.runtime.describe() if d["key"] == key))


@router.get("")
async def get_settings(_: AdminUser, services: ServicesDep) -> SettingsOut:
    env = services.settings
    return SettingsOut(
        settings=[SettingOut(**d) for d in services.runtime.describe()],
        environment=EnvironmentOut(
            version=get_version(),
            lubelogger_url=env.lubelogger_url,
            lubelogger_api_key_set=bool(env.lubelogger_api_key),
            lubelogger_field_gps=env.lubelogger_field_gps,
            lubelogger_field_address=env.lubelogger_field_address,
            imap_host=env.imap_host,
            imap_port=env.imap_port,
            imap_user=env.imap_user,
            imap_inbox=env.imap_inbox,
            imap_processed_folder=env.imap_processed_folder,
            receipt_sender=env.receipt_sender,
            receipt_subject_pattern=env.receipt_subject_pattern,
            session_days=env.session_days,
        ),
    )


@router.put("/{key}")
async def change_setting(
    key: str, body: SettingIn, _: AdminUser, services: ServicesDep
) -> SettingOut:
    """Override a setting. It takes effect at once."""
    if key not in SPECS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such setting.")
    try:
        await services.runtime.set(key, body.value)
    except SettingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    services.processor.wake()
    return _item(services, key)


@router.delete("/{key}")
async def reset_setting(key: str, _: AdminUser, services: ServicesDep) -> SettingOut:
    """Go back to the value from the environment variable."""
    if key not in SPECS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such setting.")
    await services.runtime.reset(key)
    services.processor.wake()
    return _item(services, key)
