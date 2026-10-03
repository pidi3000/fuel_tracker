from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, SessionDep
from app.services import notifications

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: int
    level: str
    title: str
    message: str
    fuel_up_id: int | None
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
