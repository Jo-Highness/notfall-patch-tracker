"""SMTP-Transport mit Fehlerklassifikation (NPT-05).

- ``starttls``: Verbindung + STARTTLS (Pflicht, Zertifikatspruefung mit System-CAs),
  ``tls``: implizites TLS, ``none``: unverschluesselt (nur interne Relays/Test-SMTP).
- Eine Verbindung wird ueber mehrere Mails weiterverwendet und bei jedem Fehler
  geschlossen (naechster Versand baut neu auf).
- Klassifikation: SMTP-Antwort 4xx, Verbindungsabbruch, Timeout und Netzwerkfehler sind
  **voruebergehend** (Retry mit Backoff); 5xx ist **endgueltig**.
"""

from __future__ import annotations

import contextlib
import smtplib
import ssl
from email.message import EmailMessage
from typing import Final, Protocol

from npt.mail.render import MailConfig

ERROR_TEXT_MAX: Final = 500


class SendError(Exception):
    def __init__(self, message: str, *, temporary: bool, code: int | None = None) -> None:
        super().__init__(message)
        self.temporary = temporary
        self.code = code


class Transport(Protocol):
    def send(self, msg: EmailMessage, to: str) -> None: ...

    def close(self) -> None: ...


def _text(value: bytes | str) -> str:
    raw = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    return " ".join(raw.split())[:ERROR_TEXT_MAX]


def classify(exc: BaseException) -> SendError:
    """Ausnahme beim Versand -> :class:`SendError` (temporaer ja/nein)."""
    if isinstance(exc, SendError):
        return exc
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [c for c, _ in exc.recipients.values()]
        code = max(codes) if codes else 550
        msg = "; ".join(f"{c} {_text(m)}" for c, m in exc.recipients.values())
        return SendError(
            f"Empfänger abgelehnt: {msg}"[:ERROR_TEXT_MAX], temporary=code < 500, code=code
        )
    if isinstance(exc, smtplib.SMTPResponseException):
        code = int(exc.smtp_code)
        text = f"SMTP {code} {_text(exc.smtp_error)}"
        return SendError(text[:ERROR_TEXT_MAX], temporary=not 500 <= code < 600, code=code)
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return SendError(f"Verbindung getrennt: {_text(str(exc))}", temporary=True)
    if isinstance(exc, TimeoutError):
        return SendError("Zeitüberschreitung beim SMTP-Versand", temporary=True)
    # Sonstige SMTP-/Netzwerkfehler (Verbindung, TLS, ...) gelten als voruebergehend.
    return SendError(f"{type(exc).__name__}: {_text(str(exc))}", temporary=True)


class SmtpTransport:
    def __init__(self, config: MailConfig) -> None:
        self.config = config
        self._conn: smtplib.SMTP | None = None

    def _connect(self) -> smtplib.SMTP:
        cfg = self.config
        conn: smtplib.SMTP
        if cfg.security == "tls":
            conn = smtplib.SMTP_SSL(
                cfg.host, cfg.port, timeout=cfg.timeout_s, context=ssl.create_default_context()
            )
        else:
            conn = smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout_s)
        try:
            conn.ehlo()
            if cfg.security == "starttls":
                conn.starttls(context=ssl.create_default_context())
                conn.ehlo()
            if cfg.user:
                conn.login(cfg.user, cfg.password)
        except Exception:
            _quiet_close(conn)
            raise
        return conn

    def send(self, msg: EmailMessage, to: str) -> None:
        try:
            if self._conn is None:
                self._conn = self._connect()
            refused = self._conn.send_message(
                msg, from_addr=self.config.sender.addr_spec, to_addrs=[to]
            )
            if refused:  # pragma: no cover - bei einem Empfaenger wirft smtplib selbst
                raise smtplib.SMTPRecipientsRefused(refused)
        except Exception as exc:
            self.close()
            raise classify(exc) from exc

    def close(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            try:
                conn.quit()
            except (smtplib.SMTPException, OSError):
                _quiet_close(conn)


def _quiet_close(conn: smtplib.SMTP) -> None:
    with contextlib.suppress(OSError):
        conn.close()
