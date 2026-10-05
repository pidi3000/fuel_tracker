"""An in-memory mailbox for the mail watcher tests."""

import threading
from email.message import EmailMessage
from pathlib import Path

from app.services.mail import MailHeader

FIXTURES = Path(__file__).parent / "fixtures" / "receipts"
SENDER = "no-reply@connectedfueling.com"
SUBJECT = "Your receipt from Wednesday, September 23, 2026 | PACE Pay"


def receipt_pdf(name: str = "pace_super") -> bytes:
    return (FIXTURES / f"{name}.pdf").read_bytes()


def build_email(
    *,
    pdf: bytes | None,
    sender: str = SENDER,
    subject: str = SUBJECT,
    message_id: str | None = "<m1@connectedfueling.test>",
    filename: str = "receipt.pdf",
) -> bytes:
    message = EmailMessage()
    message["From"] = f"PACE <{sender}>"
    message["To"] = "receipts@example.com"
    message["Subject"] = subject
    if message_id:
        message["Message-ID"] = message_id
    message.set_content("Your receipt is attached.")
    if pdf is not None:
        message.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    return message.as_bytes()


class FakeMailbox:
    def __init__(self) -> None:
        self.messages: dict[int, tuple[MailHeader, bytes]] = {}
        self.moved: list[tuple[int, str]] = []
        self.marked_read: list[int] = []
        self.flagged: list[int] = []
        self.fetched: list[int] = []
        self.activity = threading.Event()  # set when "new mail" arrives
        self.connect_error: Exception | None = None
        self.connects = 0
        self.closed = 0
        self._next_uid = 1

    def deliver(self, raw: bytes, *, sender: str = SENDER, subject: str = SUBJECT, message_id=None):
        uid = self._next_uid
        self._next_uid += 1
        self.messages[uid] = (MailHeader(uid, f"PACE <{sender}>", subject, message_id), raw)
        self.activity.set()
        return uid

    # --- the Mailbox protocol ---

    def connect(self) -> None:
        self.connects += 1
        if self.connect_error is not None:
            raise self.connect_error

    def close(self) -> None:
        self.closed += 1

    def headers(self) -> list[MailHeader]:
        return [header for header, _ in self.messages.values()]

    def fetch(self, uid: int) -> bytes:
        self.fetched.append(uid)
        return self.messages[uid][1]

    def move(self, uid: int, folder: str, *, flagged: bool = False) -> None:
        del self.messages[uid]
        self.marked_read.append(uid)
        if flagged:
            self.flagged.append(uid)
        self.moved.append((uid, folder))

    def wait(self, seconds: float, interrupt: threading.Event) -> None:
        deadline_rounds = int(seconds / 0.02) + 1
        for _ in range(deadline_rounds):
            if interrupt.is_set() or self.activity.is_set():
                break
            interrupt.wait(0.02)
        self.activity.clear()
