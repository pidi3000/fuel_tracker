"""Runs the IMAP mailbox against a real IMAP server. Skipped unless IMAP_TEST_HOST is set.

GreenMail is a handy throwaway server (SMTP on 3025, IMAP on 3143, no TLS):

    docker run --rm --network host -e GREENMAIL_OPTS="-Dgreenmail.setup.test.smtp \\
      -Dgreenmail.setup.test.imap -Dgreenmail.hostname=127.0.0.1 \\
      -Dgreenmail.users=receipts:secret@example.com" greenmail/standalone:2.1.3

    IMAP_TEST_HOST=127.0.0.1 uv run pytest tests/test_imap_integration.py
"""

import email
import os
import smtplib
import threading
import time
import uuid
from email import policy

import pytest

from app.services.mail import ImapMailbox, pdf_attachments
from tests.fake_mailbox import build_email, receipt_pdf

HOST = os.environ.get("IMAP_TEST_HOST")
IMAP_PORT = int(os.environ.get("IMAP_TEST_PORT", "3143"))
SMTP_PORT = int(os.environ.get("IMAP_TEST_SMTP_PORT", "3025"))
pytestmark = pytest.mark.skipif(not HOST, reason="IMAP_TEST_HOST is not set")


def send(raw: bytes) -> None:
    with smtplib.SMTP(HOST, SMTP_PORT) as smtp:
        smtp.sendmail("no-reply@connectedfueling.com", ["receipts@example.com"], raw)


def make_mailbox(processed: str = "Processed") -> ImapMailbox:
    return ImapMailbox(
        host=HOST or "",
        port=IMAP_PORT,
        ssl=False,
        user="receipts",
        password="secret",
        inbox="INBOX",
        processed_folder=processed,
    )


def wait_until(predicate, seconds: float = 10) -> None:
    end = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.1)


def test_receipt_email_round_trip() -> None:
    message_id = f"<integration-{uuid.uuid4()}@test>"
    mailbox = make_mailbox("Processed-test")
    mailbox.connect()  # creates the processed folder
    try:
        send(build_email(pdf=receipt_pdf(), message_id=message_id))

        def mine():
            return [h for h in mailbox.headers() if h.message_id == message_id]

        wait_until(lambda: mine())
        (header,) = mine()
        assert "connectedfueling.com" in header.sender
        assert "PACE Pay" in header.subject

        raw = mailbox.fetch(header.uid)
        message = email.message_from_bytes(raw, policy=policy.default)
        ((name, data),) = pdf_attachments(message)
        assert name == "receipt.pdf" and data == receipt_pdf()

        mailbox.move(header.uid, "Processed-test")
        assert not mine()
    finally:
        mailbox.close()

    # The mail is in the processed folder
    other = make_mailbox("Processed-test")
    other.connect()
    try:
        other._client.select_folder("Processed-test")
        ours = ["HEADER", "Message-ID", message_id]
        assert other._client.search(ours), "the moved email should be in the processed folder"
        assert other._client.search(["SEEN", *ours]), "the moved email should be marked as read"
    finally:
        other.close()


def test_idle_wakes_up_for_new_mail() -> None:
    mailbox = make_mailbox("Processed-test")
    mailbox.connect()
    try:
        before = len(mailbox.headers())
        interrupt = threading.Event()
        raw = build_email(pdf=None, message_id=f"<idle-{uuid.uuid4()}@test>", subject="IDLE test")
        threading.Timer(1.0, send, args=(raw,)).start()
        started = time.monotonic()
        mailbox.wait(20, interrupt)
        assert time.monotonic() - started < 10, "IDLE should return when mail arrives"
        wait_until(lambda: len(mailbox.headers()) == before + 1)
    finally:
        mailbox.close()


def test_wait_can_be_interrupted() -> None:
    mailbox = make_mailbox("Processed-test")
    mailbox.connect()
    try:
        interrupt = threading.Event()
        threading.Timer(0.5, interrupt.set).start()
        started = time.monotonic()
        mailbox.wait(30, interrupt)
        assert time.monotonic() - started < 8
    finally:
        mailbox.close()
