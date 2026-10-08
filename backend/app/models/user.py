import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.types import UTCDateTime, utcnow


class Role(enum.StrEnum):
    ADMIN = "admin"
    USER = "user"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # NOCASE: "Alice" and "alice" are the same user
    username: Mapped[str] = mapped_column(String(50, collation="NOCASE"), unique=True)
    email: Mapped[str | None] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(10), default=Role.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # The kinds of notification this user does not want by email (all others are sent)
    email_disabled_kinds: Mapped[list[str]] = mapped_column(JSON, default=list)

    vehicles: Mapped[list["UserVehicle"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def vehicle_ids(self) -> list[int]:
        return sorted(v.vehicle_id for v in self.vehicles)

    def wants_email(self, kind: str) -> bool:
        return kind not in self.email_disabled_kinds

    def can_access_vehicle(self, vehicle_id: int) -> bool:
        return self.is_admin or vehicle_id in self.vehicle_ids


class UserVehicle(Base):
    """A LubeLogger vehicle a user may log fuel-ups for (admins may use all)."""

    __tablename__ = "user_vehicles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    vehicle_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class ApiToken(Base):
    """Personal token for clients like the Apple Shortcut. Only a hash is stored."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class UserSession(Base):
    """A web UI login, identified by the hash of the token in the session cookie."""

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
