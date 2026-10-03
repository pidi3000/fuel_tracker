"""Shared request dependencies: database session, settings and the signed-in user."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import User
from app.services import auth as auth_service
from app.services.container import Services

SESSION_COOKIE = "fuel_tracker_session"


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A database session per request. Endpoints that change data call `session.commit()`."""
    async with request.app.state.sessionmaker() as session:
        yield session


def get_services(request: Request) -> Services:
    return request.app.state.services


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
ServicesDep = Annotated[Services, Depends(get_services)]


def _unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Not signed in.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_user(request: Request, session: SessionDep, settings: SettingsDep) -> User:
    """The signed-in user: from an API token (`Authorization: Bearer ...`) or the session cookie."""
    authorization = request.headers.get("authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    user: User | None = None
    if scheme.lower() == "bearer" and credentials:
        user = await auth_service.get_token_user(session, credentials.strip())
    elif cookie := request.cookies.get(SESSION_COOKIE):
        user = await auth_service.get_session_user(session, cookie, settings.session_days)
    if user is None:
        raise _unauthorized()
    await session.commit()
    return user


async def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
