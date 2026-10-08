"""Messages for the user in the web UI (and, by email, see `email_notifications`)."""

from dataclasses import dataclass

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, NotificationKind, User
from app.services.events import EventBus


@dataclass(frozen=True)
class KindInfo:
    kind: NotificationKind
    label: str
    description: str
    admins_only: bool = False


# The kinds a user can switch on and off for emails, in the order they are shown
KINDS = (
    KindInfo(
        NotificationKind.FUEL_UP_FAILED,
        "A fuel-up failed",
        "No receipt arrived in time, or LubeLogger refused the fuel-up or couldn't be reached.",
    ),
    KindInfo(
        NotificationKind.NEEDS_ATTENTION,
        "A fuel-up needs attention",
        "A value on the receipt couldn't be read or doesn't match your units, or its date had "
        "to be taken from the PDF.",
    ),
    KindInfo(
        NotificationKind.REVIEW_READY,
        "A fuel-up is ready for review",
        "The receipt arrived and the fuel-up waits for your approval (with Review before "
        "sending turned on).",
    ),
    KindInfo(
        NotificationKind.RECEIPT_WITHOUT_FUEL_UP,
        "A receipt has no fuel-up",
        "A receipt arrived that matches no fuel-up. Complete it, match it to a record or "
        "ignore it.",
        admins_only=True,
    ),
    KindInfo(
        NotificationKind.RECEIPT_EMAIL_PROBLEM,
        "A receipt email has a problem",
        "A receipt email couldn't be read, or the same receipt arrived again.",
        admins_only=True,
    ),
    KindInfo(
        NotificationKind.UPDATE_AVAILABLE,
        "A new version is available",
        "A newer Fuel Tracker image can be pulled.",
        admins_only=True,
    ),
)


def kinds_for(user: User) -> list[KindInfo]:
    """The kinds that can reach this user: the ones for admins only are for admins only."""
    return [info for info in KINDS if user.is_admin or not info.admins_only]


def link_path(notification: Notification) -> str:
    """The page of the web UI a notification is about."""
    if notification.fuel_up_id is not None:
        return f"/fuel-ups/{notification.fuel_up_id}"
    if notification.receipt_id is not None:
        return f"/receipts/{notification.receipt_id}"
    if notification.kind == NotificationKind.UPDATE_AVAILABLE:
        return "/admin/settings"
    return "/notifications"


class UnknownKind(ValueError):
    pass


async def set_email_preferences(
    session: AsyncSession, user: User, *, email: str | None, enabled: list[str]
) -> None:
    """Save the user's email address and the kinds of notification they want by email."""
    offered = {info.kind.value for info in kinds_for(user)}
    if unknown := sorted(set(enabled) - offered):
        raise UnknownKind(f"Unknown notification kind: {unknown[0]}")
    user.email = email or None
    # What was switched off for a kind the user can't see (e.g. before losing admin) stays
    user.email_disabled_kinds = sorted(
        {kind for kind in offered if kind not in enabled}
        | {kind for kind in user.email_disabled_kinds if kind not in offered}
    )
    await session.flush()


async def notify(
    session: AsyncSession,
    events: EventBus | None,
    *,
    kind: NotificationKind,
    level: str,
    title: str,
    message: str = "",
    user_id: int | None = None,
    fuel_up_id: int | None = None,
    receipt_id: int | None = None,
) -> Notification:
    """Create a notification. `user_id` empty means: for the admins."""
    notification = Notification(
        user_id=user_id,
        kind=kind,
        level=level,
        title=title,
        message=message,
        fuel_up_id=fuel_up_id,
        receipt_id=receipt_id,
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
