"""A stand-in for Apprise: remembers the emails instead of sending them."""

from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit


@dataclass
class SentEmail:
    to: str
    title: str
    body: str
    level: str


class FakeEmailSender:
    def __init__(self) -> None:
        self.sent: list[SentEmail] = []
        # Set to a text to make every send fail with it; `fail_for` fails only for those addresses
        self.error: str | None = None
        self.fail_for: set[str] = set()

    async def send(self, url: str, *, title: str, body: str, level: str) -> str | None:
        address = parse_qs(urlsplit(url).query)["to"][0]
        if self.error or address in self.fail_for:
            return self.error or "Connection error"
        self.sent.append(SentEmail(address, title, body, level))
        return None

    @property
    def addresses(self) -> list[str]:
        return [email.to for email in self.sent]
