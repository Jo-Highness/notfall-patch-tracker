"""Hilfen fuer Mail-Tests: Fake-Transport, Mail-Konfiguration, Mini-SMTP-Server (nur lokal).

Es werden **nie** echte Mails versendet: der Fake-Transport sammelt Nachrichten im
Speicher, der Mini-SMTP-Server lauscht nur auf 127.0.0.1 und speichert eingehende
Nachrichten in einer Liste.
"""

from __future__ import annotations

import smtplib
import socketserver
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage
from typing import Any

from npt.config import Settings
from npt.mail.render import MailConfig
from tests.conftest import make_settings

MAIL_SETTINGS: dict[str, Any] = {
    "SMTP_HOST": "127.0.0.1",
    "SMTP_PORT": 2525,
    "SMTP_SECURITY": "none",
    "SMTP_FROM": "Notfall-Patch-Tracker <npt@npt.test>",
    "SMTP_REPLY_TO": "security@npt.test",
    "MAIL_RATE_PER_SEC": 5.0,
    "PUBLIC_BASE_URL": "http://npt.test",
}


def mail_settings(**overrides: Any) -> Settings:
    return make_settings(**{**MAIL_SETTINGS, **overrides})


def mail_config(**overrides: Any) -> MailConfig:
    return MailConfig.from_settings(mail_settings(**overrides))


@dataclass
class FakeTransport:
    """Sammelt Nachrichten; ``script`` liefert je Versand optional eine Ausnahme."""

    sent: list[tuple[str, EmailMessage]] = field(default_factory=list)
    script: Callable[[str, int], BaseException | None] | None = None
    calls: int = 0
    closed: int = 0

    def send(self, msg: EmailMessage, to: str) -> None:
        self.calls += 1
        if self.script is not None:
            exc = self.script(to, self.calls)
            if exc is not None:
                raise exc
        self.sent.append((to, msg))

    def close(self) -> None:
        self.closed += 1

    def factory(self, _config: MailConfig) -> FakeTransport:
        return self


def temp_error(code: int = 451) -> smtplib.SMTPRecipientsRefused:
    return smtplib.SMTPRecipientsRefused({"x@y.test": (code, b"try again later")})


def perm_error(code: int = 550) -> smtplib.SMTPRecipientsRefused:
    return smtplib.SMTPRecipientsRefused({"x@y.test": (code, b"no such user")})


# ---------------------------------------------------------------- Mini-SMTP-Server
class _Handler(socketserver.StreamRequestHandler):
    server: _SmtpServer

    def _reply(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self) -> None:  # noqa: PLR0912 - kleines Protokoll in einer Schleife
        self._reply("220 npt-test ESMTP")
        rcpts: list[str] = []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").strip()
            verb = line.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self._reply("250-npt-test")
                self._reply("250 8BITMIME")
            elif verb == "MAIL":
                rcpts = []
                self._reply("250 OK")
            elif verb == "RCPT":
                addr = line.split(":", 1)[1].strip().strip("<>")
                code = self.server.rcpt_code(addr)
                if code == 250:
                    rcpts.append(addr)
                    self._reply("250 OK")
                else:
                    self._reply(f"{code} rejected for test")
            elif verb == "DATA":
                self._reply("354 go ahead")
                data = bytearray()
                while True:
                    chunk = self.rfile.readline()
                    if not chunk or chunk == b".\r\n":
                        break
                    data += chunk[1:] if chunk.startswith(b"..") else chunk
                self.server.messages.append((list(rcpts), bytes(data)))
                self._reply("250 queued")
            elif verb in ("RSET", "NOOP"):
                self._reply("250 OK")
            elif verb == "QUIT":
                self._reply("221 bye")
                return
            else:
                self._reply("502 not implemented")


class _SmtpServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.messages: list[tuple[list[str], bytes]] = []
        self.codes: dict[str, int] = {}

    def rcpt_code(self, addr: str) -> int:
        return self.codes.get(addr, 250)

    def parsed(self) -> list[EmailMessage]:
        out: list[EmailMessage] = []
        for _, data in self.messages:
            msg = message_from_bytes(data, policy=policy.default)
            assert isinstance(msg, EmailMessage)
            out.append(msg)
        return out


@contextmanager
def smtp_server() -> Iterator[_SmtpServer]:
    server = _SmtpServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
