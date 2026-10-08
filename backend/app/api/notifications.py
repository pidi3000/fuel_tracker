from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator

from app.api.auth import validate_email
from app.api.deps import CurrentUser, ServicesDep, SessionDep
from app.services import notifications

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: int
    kind: str
    level: str
    title: str
    message: str
    fuel_up_id: int | None
    receipt_id: int | None
    is_read: bool
    created_at: datetime


class MarkReadIn(BaseModel):
    """The notifications to mark as read; all of them if left out."""

    ids: list[int] | None = None


@router.get("")
async def list_notifications(
    user: CurrentUser, session: SessionDep, unread: bool = False
) -> list[NotificationOut]:
    items = await notifications.list_for_user(session, user, unread_only=unread)
    return [NotificationOut.model_validate(n, from_attributes=True) for n in items]


@router.post("/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(body: MarkReadIn, user: CurrentUser, session: SessionDep) -> None:
    await notifications.mark_read(session, user, body.ids)
    await session.commit()


class EmailKindOut(BaseModel):
    kind: str
    label: str
    description: str
    enabled: bool


class EmailSettingsOut(BaseModel):
    """What the user gets by email. `available` is false while the server can't send emails."""

    available: bool
    email: str | None
    kinds: list[EmailKindOut]


class EmailSettingsIn(BaseModel):
    email: str | None = None
    kinds: list[str]  # the kinds wanted by email

    _email = field_validator("email")(validate_email)


class EmailTestOut(BaseModel):
    state: str  # "ok" or "error"
    message: str


def _email_settings(user: CurrentUser, services: ServicesDep) -> EmailSettingsOut:
    return EmailSettingsOut(
        available=services.email.ready,
        email=user.email,
        kinds=[
            EmailKindOut(
                kind=info.kind,
                label=info.label,
                description=info.description,
                enabled=user.wants_email(info.kind),
            )
            for info in notifications.kinds_for(user)
        ],
    )


@router.get("/email")
async def get_email_settings(user: CurrentUser, services: ServicesDep) -> EmailSettingsOut:
    return _email_settings(user, services)


@router.put("/email")
async def set_email_settings(
    body: EmailSettingsIn, user: CurrentUser, session: SessionDep, services: ServicesDep
) -> EmailSettingsOut:
    """Set the user's own email address and which notifications they want by email."""
    try:
        await notifications.set_email_preferences(
            session, user, email=body.email, enabled=body.kinds
        )
    except notifications.UnknownKind as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await session.commit()
    return _email_settings(user, services)


@router.post("/email/test")
async def send_test_email(user: CurrentUser, services: ServicesDep) -> EmailTestOut:
    """Send a test email to the user's own address."""
    if not user.email:
        return EmailTestOut(
            state="error",
            message="Add your email address on the Account page and save it first, then test.",
        )
    if error := await services.email.send_test(user.email):
        return EmailTestOut(state="error", message=error)
    return EmailTestOut(state="ok", message=f"A test email was sent to {user.email}.")
