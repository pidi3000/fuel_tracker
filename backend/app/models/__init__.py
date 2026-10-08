"""Database models. Import them all here so Alembic sees them."""

from app.models.fuel_up import (
    Attention,
    EmailState,
    FuelUp,
    Notification,
    PaymentSource,
    SettingOverride,
    Status,
)
from app.models.receipt import Receipt, ReceiptState
from app.models.user import ApiToken, Role, User, UserSession, UserVehicle

__all__ = [
    "ApiToken",
    "Attention",
    "EmailState",
    "FuelUp",
    "Notification",
    "PaymentSource",
    "Receipt",
    "ReceiptState",
    "Role",
    "SettingOverride",
    "Status",
    "User",
    "UserSession",
    "UserVehicle",
]
