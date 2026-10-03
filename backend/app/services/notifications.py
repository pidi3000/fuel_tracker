"""Messages for the user in the web UI (and, later, other channels such as Apprise)."""

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, User
from app.services.events import EventBus


async def notify(
    session: AsyncSession,
    events: EventBus | None,
    *,
    level: str,
    title: str,
    message: str = "",
    user_id: int | None = None,
    fuel_up_id: int | None = None,
) -> Notification:
    """Create a notification. `user_id` empty means: for the admins."""
    notification = Notification(
        user_id=user_id, level=level, title=title, message=message, fuel_up_id=fuel_up_id
    )
    session.add(notification)
    await session.flush()
    if events is not None:
        events.publish("notification", id=notification.id)
    return notification


def visible_to(user: User):
    """SQL condition: the notifications this user may see."""
    if user.is_admin:
        return or_(Notification.user_id == user.id, Notification.user_id.is_(None))
    return Notification.user_id == user.id


async def list_for_user(
    session: AsyncSession, user: User, *, unread_only: bool = False, limit: int = 50
) -> list[Notification]:
    query = select(Notification).where(visible_to(user))
    if unread_only:
        query = query.where(Notification.is_read.is_(False))
    query = query.order_by(Notification.id.desc()).limit(limit)
    return list((await session.execute(query)).scalars())


async def mark_read(session: AsyncSession, user: User, ids: list[int] | None = None) -> None:
    query = update(Notification).where(visible_to(user))
    if ids is not None:
        query = query.where(Notification.id.in_(ids))
    await session.execute(query.values(is_read=True))
