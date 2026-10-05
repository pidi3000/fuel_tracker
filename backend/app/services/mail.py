"""Watching the mailbox for Pace Drive receipts.

An IMAP connection is kept open and waits with IDLE, so the mail server announces new mail within
seconds. The inbox is also checked every few minutes, in case an announcement gets lost. A receipt
email is stored, moved to the processed folder and then matched to a fuel-up.

IMAP is blocking, so the watcher runs in its own thread and hands each email to the app's
event loop for the database work.
"""

from __future__ import annotations

import asyncio
import contextlib
import email
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.types import utcnow
from app.models import Receipt
from app.services import notifications
from app.services import receipts as receipt_service

logger = logging.getLogger(__name__)

RECONNECT_DELAYS = (10, 30, 60, 300)


@dataclass(frozen=True)
class MailHeader:
    uid: int
    sender: str
    subject: str
    message_id: str | None


class Mailbox(Protocol):
    """A connection to the inbox. All methods block."""

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def headers(self) -> list[MailHeader]:
        """The emails in the inbox."""

    def fetch(self, uid: int) -> bytes:
        """The whole email."""

    def move(self, uid: int, folder: str) -> None:
        """Mark the email as read and move it to `folder`."""

    def wait(self, seconds: float, interrupt: threading.Event) -> None:
        """Wait until new mail arrives, `seconds` have passed or `interrupt` is set."""


class ImapMailbox:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        ssl: bool,
        user: str,
        password: str,
        inbox: str,
        processed_folder: str,
    ) -> None:
        self._host, self._port, self._ssl = host, port, ssl
        self._user, self._password = user, password
        self._inbox, self._processed = inbox, processed_folder
        self._client = None

    def connect(self) -> None:
        from imapclient import IMAPClient

        client = IMAPClient(self._host, port=self._port, ssl=self._ssl, timeout=60)
        try:
            client.login(self._user, self._password)
            if not client.folder_exists(self._processed):
                client.create_folder(self._processed)
                client.subscribe_folder(self._processed)
            client.select_folder(self._inbox)
        except Exception:
            self._logout(client)
            raise
        self._client = client

    @staticmethod
    def _logout(client) -> None:
        with contextlib.suppress(Exception):
            client.logout()

    def close(self) -> None:
        if self._client is not None:
            self._logout(self._client)
            self._client = None

    def headers(self) -> list[MailHeader]:
        client = self._client
        uids = client.search("ALL")
        if not uids:
            return []
        fields = "BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID)]"
        result = []
        for uid, data in client.fetch(uids, [fields]).items():
            raw = next(v for k, v in data.items() if k.startswith(b"BODY[HEADER"))
            message = email.message_from_bytes(raw, policy=policy.default)
            result.append(
                MailHeader(
                    uid=uid,
                    sender=str(message["From"] or ""),
                    subject=str(message["Subject"] or ""),
                    message_id=str(message["Message-ID"] or "") or None,
                )
            )
        return result

    def fetch(self, uid: int) -> bytes:
        data = self._client.fetch([uid], ["BODY.PEEK[]"])
        return next(v for k, v in data[uid].items() if k.startswith(b"BODY[]"))

    def move(self, uid: int, folder: str) -> None:
        """Mark the email as read and move it to `folder`."""
        from imapclient import SEEN
        from imapclient.exceptions import IMAPClientError

        client = self._client
        try:
            client.add_flags([uid], [SEEN])  # the flag goes along to the other folder
        except IMAPClientError:
            logger.warning("Couldn't mark email %s as read; moving it anyway.", uid)
        if client.has_capability("MOVE"):
            client.move([uid], folder)
        else:
            client.copy([uid], folder)
            client.delete_messages([uid])
            client.expunge([uid]) if client.has_capability("UIDPLUS") else client.expunge()

    def wait(self, seconds: float, interrupt: threading.Event) -> None:
        client = self._client
        if not client.has_capability("IDLE"):
            interrupt.wait(seconds)
            return
        client.idle()
        try:
            end = time.monotonic() + seconds
            while not interrupt.is_set() and (remaining := end - time.monotonic()) > 0:
                # Short rounds, so a stop request is noticed quickly
                if client.idle_check(timeout=min(remaining, 5)):
                    break
        finally:
            client.idle_done()


@dataclass
class WatcherStatus:
    state: str = "disabled"  # "disabled", "connecting", "ok" or "error"
    message: str = ""
    last_check: datetime | None = None


def pdf_attachments(message: EmailMessage) -> list[tuple[str, bytes]]:
    """The PDF files attached to an email, as (file name, content)."""
    found = []
    for part in message.walk():
        name = part.get_filename() or ""
        if part.get_content_type() == "application/pdf" or name.lower().endswith(".pdf"):
            data = part.get_payload(decode=True)
            if data:
                found.append((name or "receipt.pdf", data))
    return found


class ReceiptMailHandler:
    """What happens to one receipt email (on the app's event loop)."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        ctx: receipt_service.ReceiptContext,
        on_change: Callable[[], None],
    ) -> None:
        self._sessionmaker = sessionmaker
        self._ctx = ctx
        self._on_change = on_change

    async def handle(self, raw: bytes, header_message_id: str | None) -> bool:
        """Store the receipt and match it. Returns True if the email can be moved away."""
        message = email.message_from_bytes(raw, policy=policy.default)
        subject = str(message["Subject"] or "")
        message_id = receipt_service.message_id_of(
            str(message["Message-ID"] or "") or header_message_id, raw
        )
        pdfs = pdf_attachments(message)
        if not pdfs:
            logger.warning("Email %r looks like a receipt but has no PDF attached", subject)
            return False

        stored_ids: list[int] = []
        async with self._sessionmaker() as session:
            if await receipt_service.find_by_message_id(session, message_id):
                return True  # stored earlier, only the move was missing
            for index, (name, data) in enumerate(pdfs):
                receipt = await receipt_service.store_receipt(
                    session,
                    self._ctx,
                    message_id=message_id if index == 0 else f"{message_id}#{index}",
                    subject=subject,
                    pdf=data,
                    pdf_name=name,
                )
                if receipt is None:
                    continue
                stored_ids.append(receipt.id)
                if receipt.parse_error:
                    await notifications.notify(
                        session,
                        self._ctx.events,
                        level="error",
                        title="A receipt email couldn't be read",
                        message=f"{subject}: {receipt.parse_error} Ignore it in the receipts list "
                        "or log the fuel-up by hand.",
                    )
            await session.commit()
        for receipt_id in stored_ids:
            self._ctx.events.publish("receipt", id=receipt_id)

        # Matching has its own transaction: a problem there must not undo the stored receipt.
        # (Waiting fuel-ups are also matched again regularly.)
        try:
            async with self._sessionmaker() as session:
                for receipt_id in stored_ids:
                    receipt = await session.get(Receipt, receipt_id)
                    if receipt is not None:
                        await receipt_service.try_match_receipt(session, self._ctx, receipt)
                await session.commit()
        except Exception:
            logger.exception("Matching a new receipt failed")
        self._on_change()
        return True


class MailWatcher:
    def __init__(
        self,
        mailbox_factory: Callable[[], Mailbox],
        handler: ReceiptMailHandler,
        loop: asyncio.AbstractEventLoop,
        *,
        sender: str,
        subject_pattern: str,
        processed_folder: str,
        poll_seconds: float = 300,
        reconnect_delays: tuple[float, ...] = RECONNECT_DELAYS,
    ) -> None:
        self._factory = mailbox_factory
        self._handler = handler
        self._loop = loop
        self._sender = sender.lower()
        self._subject = re.compile(subject_pattern, re.IGNORECASE)
        self._processed_folder = processed_folder
        self._poll_seconds = poll_seconds
        self._reconnect_delays = reconnect_delays
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.status = WatcherStatus(state="connecting")

    # --- control ---

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mail-watcher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def check_now(self) -> None:
        """Look at the inbox right away (e.g. because a fuel-up was just created)."""
        self._wake.set()

    # --- the thread ---

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            mailbox = self._factory()
            try:
                self.status = WatcherStatus(state="connecting", last_check=self.status.last_check)
                mailbox.connect()
                self.status = WatcherStatus(state="ok", last_check=utcnow())
                attempt = 0
                self._watch(mailbox)
            except Exception as exc:
                if self._stop.is_set():
                    break
                self.status = WatcherStatus(
                    state="error",
                    message=f"{type(exc).__name__}: {exc}",
                    last_check=self.status.last_check,
                )
                logger.warning("Mailbox problem: %s", exc)
                delay = self._reconnect_delays[min(attempt, len(self._reconnect_delays) - 1)]
                attempt += 1
                self._stop.wait(delay)
            finally:
                mailbox.close()
        self.status = WatcherStatus(state="disabled", message="stopped")

    def _watch(self, mailbox: Mailbox) -> None:
        skipped: set[int] = set()  # emails left alone; their ids are only valid on this connection
        while not self._stop.is_set():
            self._wake.clear()
            self.process_inbox(mailbox, skipped)
            self.status = WatcherStatus(state="ok", last_check=utcnow())
            # Returns on new mail, a check request, a stop or after the poll interval
            mailbox.wait(self._poll_seconds, self._wake)

    def is_receipt(self, header: MailHeader) -> bool:
        sender = parseaddr(header.sender)[1].lower() or header.sender.lower()
        return self._sender in sender and bool(self._subject.search(header.subject))

    def process_inbox(self, mailbox: Mailbox, skipped: set[int] | None = None) -> int:
        """Handle the receipt emails in the inbox. Returns how many were moved away."""
        skipped = skipped if skipped is not None else set()
        moved = 0
        for header in mailbox.headers():
            if header.uid in skipped or not self.is_receipt(header):
                continue
            try:
                raw = mailbox.fetch(header.uid)
                done = self._call(self._handler.handle(raw, header.message_id))
                if done:
                    mailbox.move(header.uid, self._processed_folder)
                    moved += 1
                else:
                    skipped.add(header.uid)
            except Exception:
                # The mail stays where it is and is tried again at the next check
                logger.exception("Handling the email %r failed", header.subject)
        return moved

    def _call(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result(timeout=300)
