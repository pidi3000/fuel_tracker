"""Sends the notifications by email, with Apprise.

A notification goes to the user it is for, if that user has an email address. Otherwise, and for
notifications that aren't for one user (a receipt without a fuel-up, an update), it goes to the
admins that have one.

Sending is a background job and not part of creating the notification: a slow or broken mail
server never holds up a fuel-up, and a notification whose transaction was rolled back is never
sent. The notification table is the queue.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import apprise
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.types import utcnow
from app.models import EmailState, Notification, Role, User

logger = logging.getLogger(__name__)

# Waiting times before trying again when the mail server doesn't take a message; after the
# last one the message is given up
RETRY_DELAYS = (60, 300, 900)
# Notifications taken from the queue per round
BATCH_SIZE = 20
SUBJECT_PREFIX = "Fuel Tracker: "
EMAIL_SCHEMES = ("mailto", "mailtos")

NOTIFY_TYPES = {
    "info": apprise.NotifyType.INFO,
    "warning": apprise.NotifyType.WARNING,
    "error": apprise.NotifyType.FAILURE,
}


def recipient_url(base_url: str, address: str) -> str:
    """The Apprise URL from the settings, with the address to send to."""
    parts = urlsplit(base_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() != "to"
    ]
    query.append(("to", address))
    return urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))


class EmailSender(Protocol):
    async def send(self, url: str, *, title: str, body: str, level: str) -> str | None:
        """Send one email. Returns None, or a short description of what went wrong."""


class AppriseSender:
    # Apprise reports the real reason (e.g. "Authentication credentials invalid") only in its
    # debug log. It is captured while sending, one message at a time, and kept off the console.
    _lock = threading.Lock()

    async def send(self, url: str, *, title: str, body: str, level: str) -> str | None:
        return await asyncio.to_thread(self._send, url, title, body, level)

    def _send(self, url: str, title: str, body: str, level: str) -> str | None:
        apprise_logger = logging.getLogger("apprise")
        with self._lock:
            propagate = apprise_logger.propagate
            apprise_logger.propagate = False
            try:
                with apprise.LogCapture(
                    level=logging.DEBUG, fmt="%(levelname)s|%(message)s"
                ) as log:
                    sender = apprise.Apprise()
                    if not sender.add(url):
                        return "The email URL isn't valid."
                    sent = sender.notify(
                        title=title,
                        body=body,
                        body_format=apprise.NotifyFormat.TEXT,
                        notify_type=NOTIFY_TYPES.get(level, apprise.NotifyType.INFO),
                    )
            finally:
                apprise_logger.propagate = propagate
        return None if sent else problem_from_log(log.getvalue())


def problem_from_log(log: str) -> str:
    """What went wrong, from Apprise's log: its warning and the error behind it."""
    problem = detail = ""
    for line in log.splitlines():
        level, _, text = line.partition("|")
        if level in ("WARNING", "ERROR"):
            problem = text
        elif text.startswith("Socket Exception:"):
            detail = text.removeprefix("Socket Exception:").strip()
    if problem and detail:
        return f"{problem}: {detail}"
    return problem or detail or "The mail server did not accept the email."


@dataclass
class EmailStatus:
    state: str  # "ok", "error" or "not_configured"
    message: str = ""


@dataclass
class _Outgoing:
    id: int
    level: str
    title: str
    body: str
    addresses: list[str]


async def recipients_for(session: AsyncSession, notification: Notification) -> list[str]:
    """The addresses a notification goes to; empty if nobody who could get it has one."""
    if notification.user_id is not None:
        user = await session.get(User, notification.user_id)
        if user is not None and user.is_active and user.email:
            return [user.email]
    return await admin_addresses(session)


async def admin_addresses(session: AsyncSession) -> list[str]:
    result = await session.execute(
        select(User.email)
        .where(User.role == Role.ADMIN, User.is_active.is_(True), User.email.is_not(None))
        .order_by(User.id)
    )
    seen: set[str] = set()
    addresses = []
    for email in result.scalars():
        if email and email.lower() not in seen:
            seen.add(email.lower())
            addresses.append(email)
    return addresses


class EmailNotifier:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        base_url: str,
        *,
        sender: EmailSender | None = None,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
        interval: float = 10,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._base_url = base_url.strip()
        self._sender: EmailSender = sender or AppriseSender()
        self._retry_delays = retry_delays
        self._interval = interval
        self._clock = clock
        self._problem = self._check_url()
        # What happened with the last message, for the settings page
        self._last_error: str | None = None
        # Not kept in the database: after a restart, everything pending is tried again
        self._retry_at: dict[int, datetime] = {}
        self._sent_to: dict[int, set[str]] = {}
        if self._problem:
            logger.warning("%s Emails are not sent.", self._problem)

    def _check_url(self) -> str | None:
        if not self._base_url:
            return None
        scheme = urlsplit(self._base_url).scheme.lower()
        if (
            scheme not in EMAIL_SCHEMES
            or apprise.Apprise.instantiate(recipient_url(self._base_url, "test@example.org"))
            is None
        ):
            return "APPRISE_EMAIL_URL isn't an Apprise email URL (mailto:// or mailtos://)."
        return None

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    @property
    def ready(self) -> bool:
        return self.configured and self._problem is None

    async def status(self) -> EmailStatus:
        if not self.configured:
            return EmailStatus("not_configured", "APPRISE_EMAIL_URL is not set.")
        if self._problem:
            return EmailStatus("error", self._problem)
        async with self._sessionmaker() as session:
            if not await admin_addresses(session):
                return EmailStatus(
                    "error",
                    "No admin has an email address, so notifications that aren't for one "
                    "user can't be sent. Add one under Users.",
                )
        if self._last_error:
            return EmailStatus("error", f"The last email wasn't sent: {self._last_error}")
        return EmailStatus("ok")

    async def send_test(self, address: str) -> str | None:
        """Send a test email. Returns None, or what went wrong."""
        if not self.configured:
            return "APPRISE_EMAIL_URL is not set."
        if self._problem:
            return self._problem
        error = await self._sender.send(
            recipient_url(self._base_url, address),
            title=f"{SUBJECT_PREFIX}Test email",
            body="Email notifications from Fuel Tracker work.",
            level="info",
        )
        self._last_error = error
        return error

    async def run(self) -> None:
        while True:
            try:
                await self.deliver_pending()
            except Exception:
                logger.exception("Sending notification emails failed")
            await asyncio.sleep(self._interval)

    async def deliver_pending(self) -> int:
        """Send the notifications that were not sent yet. Returns how many were dealt with."""
        now = self._clock()
        due: list[_Outgoing] = []
        dealt_with = 0
        # No database transaction is open while the mail server is being talked to
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Notification)
                .where(Notification.email_state == EmailState.PENDING)
                .order_by(Notification.id)
                .limit(BATCH_SIZE)
            )
            for notification in result.scalars():
                if not self.ready:
                    # Not set up: nothing is sent, and it won't be sent later either (a flood of
                    # old messages the day someone sets it up would help no one)
                    notification.email_state = EmailState.SKIPPED
                    dealt_with += 1
                    continue
                if self._retry_at.get(notification.id, now) > now:
                    continue
                addresses = await recipients_for(session, notification)
                if not addresses:
                    logger.warning(
                        "Notification %r was not emailed: no admin has an email address",
                        notification.title,
                    )
                    notification.email_state = EmailState.SKIPPED
                    dealt_with += 1
                    continue
                due.append(
                    _Outgoing(
                        notification.id,
                        notification.level,
                        f"{SUBJECT_PREFIX}{notification.title}",
                        notification.message or notification.title,
                        addresses,
                    )
                )
            await session.commit()

        outcomes = [(message, await self._send(message)) for message in due]
        if outcomes:
            async with self._sessionmaker() as session:
                for message, error in outcomes:
                    notification = await session.get(Notification, message.id)
                    if notification is not None:
                        self._record(notification, error)
                        if notification.email_state != EmailState.PENDING:
                            dealt_with += 1
                await session.commit()
        return dealt_with

    async def _send(self, message: _Outgoing) -> str | None:
        """Send to every address that did not get it yet. Returns the first error, if any."""
        sent_to = self._sent_to.setdefault(message.id, set())
        first_error = None
        for address in message.addresses:
            if address in sent_to:
                continue
            error = await self._sender.send(
                recipient_url(self._base_url, address),
                title=message.title,
                body=message.body,
                level=message.level,
            )
            if error is None:
                sent_to.add(address)
            elif first_error is None:
                first_error = error
        self._last_error = first_error
        return first_error

    def _record(self, notification: Notification, error: str | None) -> None:
        if error is None:
            notification.email_state = EmailState.SENT
            self._retry_at.pop(notification.id, None)
            self._sent_to.pop(notification.id, None)
            return
        notification.email_attempts += 1
        if notification.email_attempts > len(self._retry_delays):
            notification.email_state = EmailState.FAILED
            self._retry_at.pop(notification.id, None)
            self._sent_to.pop(notification.id, None)
            logger.warning("Notification %r was not emailed: %s", notification.title, error)
            return
        delay = self._retry_delays[notification.email_attempts - 1]
        self._retry_at[notification.id] = self._clock() + timedelta(seconds=delay)
        logger.info("Emailing %r failed (%s), trying in %ss", notification.title, error, delay)
