"""Send one real email over SMTP. Standard library only, so the Lambda package gets no new dependency.

This is separate from the demo sign-up in `enroll.py` (which only accepts fictional contacts and sends nothing). It exists
for the "email me this alert" button: a person types their own address, ticks a consent box, and gets one email now.

What it keeps: nothing. The address is used to send that one message. It is not stored, not logged and not echoed (the
caller returns a masked form). Only a salted hash is held in memory, to count sends per recipient.

Settings come from the environment, so no secret is ever in the repository:

    SMTP_HOST       e.g. smtp.gmail.com
    SMTP_PORT       587 (STARTTLS, the default) or 465 (SSL)
    SMTP_USER       the login name, usually the sender's address
    SMTP_PASSWORD   an app password, not a normal account password
    SMTP_FROM       optional; the From address (default: SMTP_USER)

With no settings, `from_env()` returns None and the API says "not configured" instead of pretending it sent anything.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import smtplib
import ssl
import time
from collections import deque
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import formataddr

log = logging.getLogger("concierge.mail")

FROM_NAME = "Outage Concierge (prototype)"
TIMEOUT_SECONDS = 10.0
MAX_ADDRESS_LENGTH = 254
# One plain address. No spaces, quotes, commas, angle brackets or control characters, so nothing can add a header or a
# second recipient.
_ADDRESS = re.compile(r"^[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]{1,64}@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}$")

PER_RECIPIENT_LIMIT = 3          # emails to one address per window
GLOBAL_LIMIT = 30                # emails from this server per window
WINDOW_SECONDS = 3600


class MailError(RuntimeError):
    """The email could not be sent. The message is safe to log (never holds the address), not to show."""


class RateLimited(RuntimeError):
    """Too many emails. The message is safe to return."""


def clean_address(value: object) -> str:
    """The address, trimmed, or raises ValueError with a message that is safe to show."""
    if not isinstance(value, str):
        raise ValueError("Enter your email address.")
    text = value.strip()
    if not text or len(text) > MAX_ADDRESS_LENGTH or not _ADDRESS.match(text):
        raise ValueError("Enter a valid email address, like name@example.com.")
    return text


def mask_address(address: str) -> str:
    """f***@gmail.com. Enough for the person to recognise their own address."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


class RateLimit:
    """Sliding-window counts, in memory. Per recipient (by salted hash, never the address) and for the whole server.
    This is a brake on misuse, not authentication: the API has none. Each Lambda instance counts separately."""

    def __init__(self, per_recipient: int = PER_RECIPIENT_LIMIT, overall: int = GLOBAL_LIMIT,
                 window: float = WINDOW_SECONDS, clock: Callable[[], float] = time.monotonic):
        self.per_recipient, self.overall, self.window, self.clock = per_recipient, overall, window, clock
        self._salt = os.urandom(8)
        self._each: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    def _prune(self, q: deque[float], now: float) -> None:
        while q and now - q[0] >= self.window:
            q.popleft()

    def check(self, address: str) -> str:
        """Raises RateLimited if sending would go over a limit. Returns the key to pass to `record`."""
        now = self.clock()
        key = hashlib.sha256(self._salt + address.lower().encode()).hexdigest()
        self._prune(self._all, now)
        if len(self._all) >= self.overall:
            raise RateLimited("Lots of people are asking for emails right now. Please try again later.")
        q = self._each.setdefault(key, deque())
        self._prune(q, now)
        if len(q) >= self.per_recipient:
            raise RateLimited(f"That address already got {self.per_recipient} emails this hour. Please try again later.")
        return key

    def record(self, key: str) -> None:
        now = self.clock()
        self._each.setdefault(key, deque()).append(now)
        self._all.append(now)
        if len(self._each) > 5000:                                   # keep memory bounded
            for k in [k for k, q in self._each.items() if not q or now - q[-1] >= self.window]:
                del self._each[k]


class Mailer:
    def __init__(self, host: str, port: int, user: str, password: str, from_address: str | None = None,
                 limiter: RateLimit | None = None, smtp_factory: Callable[..., smtplib.SMTP] | None = None):
        self.host, self.port, self.user, self._password = host, port, user, password
        self.from_address = from_address or user
        self.limiter = limiter or RateLimit()
        self._smtp_factory = smtp_factory           # tests pass a fake; None means the real smtplib

    def send(self, to: str, subject: str, body: str) -> None:
        """Send a plain-text email. Raises MailError on any failure, with a message that does not contain the address."""
        msg = EmailMessage()
        msg["Subject"] = subject.replace("\r", " ").replace("\n", " ")
        msg["From"] = formataddr((FROM_NAME, self.from_address))
        msg["To"] = to
        msg.set_content(body)
        try:
            context = ssl.create_default_context()
            if self.port == 465:
                factory = self._smtp_factory or smtplib.SMTP_SSL
                server = factory(self.host, self.port, timeout=TIMEOUT_SECONDS, context=context)
            else:
                factory = self._smtp_factory or smtplib.SMTP
                server = factory(self.host, self.port, timeout=TIMEOUT_SECONDS)
            with server:
                if self.port != 465:
                    server.starttls(context=context)
                server.login(self.user, self._password)
                server.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            raise MailError("the mail server refused the login (check SMTP_USER and SMTP_PASSWORD)") from None
        except Exception as e:  # noqa: BLE001 - DNS, timeouts, refused recipients, TLS
            raise MailError(f"email could not be sent: {type(e).__name__}") from None


def from_env(env: dict[str, str] | None = None) -> Mailer | None:
    """A Mailer from the environment, or None when it is not set up."""
    env = os.environ if env is None else env
    host, user, password = env.get("SMTP_HOST", ""), env.get("SMTP_USER", ""), env.get("SMTP_PASSWORD", "")
    if not (host and user and password):
        return None
    try:
        port = int(env.get("SMTP_PORT", "587"))
    except ValueError:
        log.warning("SMTP_PORT is not a number; email is turned off")
        return None
    return Mailer(host, port, user, password, env.get("SMTP_FROM") or None)
