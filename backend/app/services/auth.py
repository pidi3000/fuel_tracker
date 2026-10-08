"""Users, sessions and API tokens."""

from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TOKEN_PREFIX, hash_password, hash_token, new_token, verify_password
from app.core.types import utcnow
from app.models import ApiToken, Role, User, UserSession, UserVehicle


class AuthError(Exception):
    """A rule was broken; the message is safe to show to the user."""


async def count_users(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(User))).scalar_one()


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    role: Role = Role.USER,
    email: str | None = None,
    vehicle_ids: list[int] | None = None,
) -> User:
    if await get_user_by_username(session, username):
        raise AuthError("That username is already taken.")
    user = User(
        username=username,
        email=email or None,
        password_hash=hash_password(password),
        role=role,
    )
    user.vehicles = [UserVehicle(vehicle_id=v) for v in sorted(set(vehicle_ids or []))]
    session.add(user)
    await session.flush()
    return user


async def authenticate(session: AsyncSession, username: str, password: str) -> User | None:
    user = await get_user_by_username(session, username)
    ok = verify_password(password, user.password_hash if user else None)
    if user is None or not ok or not user.is_active:
        return None
    return user


async def _active_admin_count(session: AsyncSession, excluding: int | None = None) -> int:
    query = (
        select(func.count())
        .select_from(User)
        .where(User.role == Role.ADMIN, User.is_active.is_(True))
    )
    if excluding is not None:
        query = query.where(User.id != excluding)
    return (await session.execute(query)).scalar_one()


async def ensure_admin_remains(session: AsyncSession, user: User) -> None:
    """Refuse changes that would leave nobody able to administrate."""
    if await _active_admin_count(session, excluding=user.id) == 0:
        raise AuthError("There must be at least one active admin.")


async def set_password(
    session: AsyncSession, user: User, password: str, keep_token: str | None = None
) -> None:
    """Change the password and sign the user out everywhere (except `keep_token`)."""
    user.password_hash = hash_password(password)
    await delete_sessions(session, user.id, keep_token=keep_token)


async def update_user(
    session: AsyncSession,
    user: User,
    *,
    role: Role | None = None,
    is_active: bool | None = None,
    password: str | None = None,
    vehicle_ids: list[int] | None = None,
) -> User:
    losing_admin = (role is not None and role != Role.ADMIN and user.is_admin) or (
        is_active is False and user.is_admin and user.is_active
    )
    if losing_admin:
        await ensure_admin_remains(session, user)
    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    if password is not None:
        await set_password(session, user, password)
    if is_active is False:
        await delete_sessions(session, user.id)
    if vehicle_ids is not None:
        wanted = set(vehicle_ids)
        user.vehicles = [v for v in user.vehicles if v.vehicle_id in wanted] + [
            UserVehicle(vehicle_id=v) for v in sorted(wanted - set(user.vehicle_ids))
        ]
    await session.flush()
    return user


async def delete_user(session: AsyncSession, user: User) -> None:
    if user.is_admin and user.is_active:
        await ensure_admin_remains(session, user)
    await session.delete(user)
    await session.flush()


# --- web sessions ---


async def create_session(session: AsyncSession, user: User, lifetime_days: int) -> str:
    token = new_token()
    now = utcnow()
    session.add(
        UserSession(
            token_hash=hash_token(token),
            user_id=user.id,
            created_at=now,
            expires_at=now + timedelta(days=lifetime_days),
        )
    )
    await session.flush()
    return token


async def get_session_user(session: AsyncSession, token: str, lifetime_days: int) -> User | None:
    record = await session.get(UserSession, hash_token(token))
    if record is None:
        return None
    now = utcnow()
    if record.expires_at <= now:
        await session.delete(record)
        return None
    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        return None
    # Sliding expiry, written at most once a day
    new_expiry = now + timedelta(days=lifetime_days)
    if new_expiry - record.expires_at > timedelta(days=1):
        record.expires_at = new_expiry
    return user


async def delete_session(session: AsyncSession, token: str) -> None:
    await session.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))


async def delete_sessions(
    session: AsyncSession, user_id: int, keep_token: str | None = None
) -> None:
    query = delete(UserSession).where(UserSession.user_id == user_id)
    if keep_token:
        query = query.where(UserSession.token_hash != hash_token(keep_token))
    await session.execute(query)


async def delete_expired_sessions(session: AsyncSession) -> None:
    await session.execute(delete(UserSession).where(UserSession.expires_at <= utcnow()))


# --- API tokens ---


async def create_api_token(session: AsyncSession, user: User, name: str) -> tuple[ApiToken, str]:
    token = new_token(TOKEN_PREFIX)
    record = ApiToken(user_id=user.id, name=name, token_hash=hash_token(token))
    session.add(record)
    await session.flush()
    return record, token


async def get_token_user(session: AsyncSession, token: str) -> User | None:
    result = await session.execute(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
    record = result.scalar_one_or_none()
    if record is None:
        return None
    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        return None
    now = utcnow()
    if record.last_used_at is None or now - record.last_used_at > timedelta(minutes=5):
        record.last_used_at = now
    return user


async def list_api_tokens(session: AsyncSession, user: User) -> list[ApiToken]:
    result = await session.execute(
        select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at)
    )
    return list(result.scalars())


async def delete_api_token(session: AsyncSession, user: User, token_id: int) -> bool:
    record = await session.get(ApiToken, token_id)
    if record is None or record.user_id != user.id:
        return False
    await session.delete(record)
    return True
