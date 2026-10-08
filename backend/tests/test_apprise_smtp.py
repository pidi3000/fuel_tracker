"""Sending through the real Apprise, to a small SMTP server that runs inside the test.

The other email tests use a fake sender. These check what only the real thing can show, and
what a newer Apprise could change: how the email URL is read, what the email looks like, and
the reason it gives when sending fails.
"""

import socket
from collections.abc import Iterator
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword

from app.models import EmailState
from app.services.email_notifications import AppriseSender, EmailNotifier, recipient_url
from tests.conftest import AppUnderTest
from tests.fake_email import PUBLIC_URL
from tests.helpers import add_notification, email_state, sign_in_admin

LOGIN = "fuel"
PASSWORD = "p@ss word"  # with characters that have to be written as %XX in the URL


@dataclass
class Received:
    mail_from: str
    to: list[str]
    message: EmailMessage

    @property
    def text(self) -> str:
        body = self.message.get_body(preferencelist=("plain",))
        assert body is not None
        return str(body.get_content()).replace("\r\n", "\n").strip()


@dataclass
class SmtpServer:
    port: int
    received: list[Received] = field(default_factory=list)

    def url(self, password: str = "p%40ss%20word", port: int | None = None) -> str:
        return (
            f"mailto://{LOGIN}:{password}@127.0.0.1:{port or self.port}"
            "?mode=insecure&from=fuel@example.org&name=Fuel%20Tracker"
        )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def smtp() -> Iterator[SmtpServer]:
    server = SmtpServer(free_port())

    class Handler:
        async def handle_DATA(self, _server, _session, envelope) -> str:
            message = message_from_bytes(envelope.content, policy=policy.default)
            server.received.append(Received(envelope.mail_from, envelope.rcpt_tos, message))
            return "250 OK"

    def authenticate(_server, _session, _envelope, _mechanism, data) -> AuthResult:
        right = (
            isinstance(data, LoginPassword)
            and data.login == LOGIN.encode()
            and data.password == PASSWORD.encode()
        )
        return AuthResult(success=right, handled=False)

    controller = Controller(
        Handler(),
        hostname="127.0.0.1",
        port=server.port,
        authenticator=authenticate,
        auth_require_tls=False,  # the test server has no certificate
    )
    controller.start()
    try:
        yield server
    finally:
        controller.stop()


async def test_an_email_arrives_as_it_should(smtp: SmtpServer) -> None:
    error = await AppriseSender().send(
        recipient_url(smtp.url(), "bob@example.org"),
        title="Fuel Tracker: Fuel-up #3 failed",
        body="Golf, 12345: No receipt arrived.\n\nOpen it in Fuel Tracker:\nhttps://fuel.test/x",
        level="error",
    )

    assert error is None
    (mail,) = smtp.received
    assert mail.mail_from == "fuel@example.org" and mail.to == ["bob@example.org"]
    assert mail.message["Subject"] == "Fuel Tracker: Fuel-up #3 failed"
    assert mail.message["From"] == "Fuel Tracker <fuel@example.org>"
    assert mail.message["To"] == "bob@example.org"
    assert mail.text == (
        "Golf, 12345: No receipt arrived.\n\nOpen it in Fuel Tracker:\nhttps://fuel.test/x"
    )


async def test_an_address_with_a_plus_and_a_subject_with_umlauts_survive(
    smtp: SmtpServer,
) -> None:
    error = await AppriseSender().send(
        recipient_url(smtp.url(), "bob+fuel@example.org"),
        title="Fuel Tracker: Tankstopp für den Golf",
        body="Zähler: 12345",
        level="info",
    )

    assert error is None
    (mail,) = smtp.received
    assert mail.to == ["bob+fuel@example.org"]
    assert mail.message["Subject"] == "Fuel Tracker: Tankstopp für den Golf"
    assert mail.text == "Zähler: 12345"


async def test_a_wrong_password_is_reported_with_the_reason(smtp: SmtpServer) -> None:
    error = await AppriseSender().send(
        recipient_url(smtp.url(password="wrong"), "bob@example.org"),
        title="t",
        body="b",
        level="info",
    )

    assert error is not None and "Authentication credentials invalid" in error
    assert smtp.received == []


async def test_a_server_that_is_down_is_reported_with_the_reason() -> None:
    error = await AppriseSender().send(
        recipient_url(SmtpServer(free_port()).url(), "bob@example.org"),
        title="t",
        body="b",
        level="info",
    )

    assert error is not None and "Connection refused" in error


async def test_a_notification_is_sent_through_the_real_sender(
    api: AppUnderTest, smtp: SmtpServer
) -> None:
    await sign_in_admin(api, email="alice@example.org")
    note = await add_notification(api, title="No receipt for fuel-up #5", fuel_up_id=5)
    notifier = EmailNotifier(api.services.sessionmaker, smtp.url(), public_url=PUBLIC_URL)

    assert await notifier.deliver_pending() == 1

    (mail,) = smtp.received
    assert mail.to == ["alice@example.org"]
    assert mail.message["Subject"] == "Fuel Tracker: No receipt for fuel-up #5"
    assert mail.text.endswith(f"Open it in Fuel Tracker:\n{PUBLIC_URL}/fuel-ups/5")
    assert await email_state(api, note) == EmailState.SENT
    assert (await notifier.status()).state == "ok"


async def test_a_wrong_password_shows_on_the_status_and_the_email_waits(
    api: AppUnderTest, smtp: SmtpServer
) -> None:
    await sign_in_admin(api, email="alice@example.org")
    note = await add_notification(api)
    notifier = EmailNotifier(api.services.sessionmaker, smtp.url(password="wrong"))

    assert await notifier.deliver_pending() == 0

    status = await notifier.status()
    assert status.state == "error" and "Authentication credentials invalid" in status.message
    assert await email_state(api, note) == EmailState.PENDING  # it is tried again later
    assert smtp.received == []
