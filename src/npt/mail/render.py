"""Mail je Empfaenger rendern: ``[LINK]`` ersetzen, MIME-Nachricht bauen (Auftrag 6.3, NPT-05).

Reihenfolge (NPT-05): Der Kampagnentext ist bereits sanitisiert gespeichert; ``[LINK]``
wird **danach** durch eine Liste von Deep-Links ersetzt, deren Bestandteile (System-
Name, System-ID, URL) einzeln HTML-escaped werden – ein System-Name wie
``<script>`` oder ``"><a href=…`` kann so kein Markup erzeugen.

Deep-Links nutzen ``PUBLIC_BASE_URL`` + Projekt-Token + die Filter-Grammatik der
Hauptliste (``x.system_id=<ID>``; Sammel-Link: alle IDs des Empfaengers).

Nachricht: ``multipart/alternative`` (Plaintext aus dem HTML generiert + HTML), genau
ein Empfaenger, ``Message-ID``/``Date``; Header werden mit der SMTP-Policy erzeugt
(CR/LF in Headerwerten -> Fehler) und vorher auf Steuerzeichen geprueft.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid, parseaddr
from typing import Final
from urllib.parse import urlencode

from npt.config import Settings
from npt.domain import filters
from npt.domain.validation import is_valid_email, normalize_email
from npt.mail.audience import SystemRef
from npt.mail.sanitize import LINK_PLACEHOLDER, clean_subject, html_to_text

# Sammel-Links ueber dieser Laenge werden geteilt (Proxies begrenzen die Request-Zeile,
# nginx-Default 8 KB).
COLLECTIVE_URL_MAX: Final = 6000
_PLACEHOLDER_ANY: Final = re.compile(
    r"<p>\s*" + re.escape(LINK_PLACEHOLDER) + r"\s*</p>|" + re.escape(LINK_PLACEHOLDER)
)
_HEADER_BAD: Final = re.compile(r"[\x00-\x1f\x7f]")
TEST_PREFIX: Final = "[TEST] "


class MailConfigError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- Deep-Links
def list_url(base_url: str, token: str, params: Sequence[tuple[str, str]]) -> str:
    base = f"{base_url.rstrip('/')}/p/{token}"
    return f"{base}/?{urlencode(list(params))}" if params else f"{base}/"


def system_params(system_ids: Sequence[str]) -> list[tuple[str, str]]:
    state = filters.FilterState(exact={"system_id": tuple(system_ids)})
    return filters.to_params(state)


def system_url(base_url: str, token: str, system_id: str) -> str:
    return list_url(base_url, token, system_params([system_id]))


def collective_urls(base_url: str, token: str, system_ids: Sequence[str]) -> list[str]:
    """Sammel-Link(s) „Alle meine Systeme"; bei sehr vielen Systemen in Teilen."""
    chunks: list[list[str]] = [[]]
    for sid in system_ids:
        candidate = [*chunks[-1], sid]
        if chunks[-1] and len(list_url(base_url, token, system_params(candidate))) > (
            COLLECTIVE_URL_MAX
        ):
            chunks.append([sid])
        else:
            chunks[-1] = candidate
    return [list_url(base_url, token, system_params(c)) for c in chunks if c]


def _label(ref: SystemRef) -> str:
    name = (ref.system_name or "").strip()
    return f"{name} ({ref.system_id})" if name else ref.system_id


def links_html(base_url: str, token: str, systems: Sequence[SystemRef]) -> str:
    esc = html.escape
    items = "".join(
        f'<li><a href="{esc(system_url(base_url, token, s.system_id), quote=True)}" '
        f'rel="noopener noreferrer">{esc(_label(s))}</a></li>'
        for s in systems
    )
    urls = collective_urls(base_url, token, [s.system_id for s in systems])
    count = len(systems)
    noun = "System" if count == 1 else "Systeme"
    alls = []
    for n, url in enumerate(urls, start=1):
        part = f" (Teil {n} von {len(urls)})" if len(urls) > 1 else ""
        alls.append(
            f'<a href="{esc(url, quote=True)}" rel="noopener noreferrer">'
            f"Alle meine Systeme{part}</a>"
        )
    return f"<ul>{items}</ul><p>{' · '.join(alls)} ({count} {noun})</p>"


def render_body(body_html: str, base_url: str, token: str, systems: Sequence[SystemRef]) -> str:
    """``[LINK]`` im (bereits sanitisierten) Text ersetzen; ein eigener Absatz wird zum Block.

    Ein Durchlauf: eingesetzte Inhalte (z. B. ein System-Name „[LINK]") werden nicht
    erneut ersetzt.
    """
    block = links_html(base_url, token, systems)
    return _PLACEHOLDER_ANY.sub(lambda _m: block, body_html)


def html_document(subject: str, body: str) -> str:
    return (
        '<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">'
        f"<title>{html.escape(subject)}</title></head><body>{body}</body></html>"
    )


@dataclass(frozen=True)
class RenderedMail:
    subject: str
    html_body: str  # Fragment (fuer die Vorschau)
    text: str


def render_mail(
    subject: str, body_html: str, base_url: str, token: str, systems: Sequence[SystemRef]
) -> RenderedMail:
    body = render_body(body_html, base_url, token, systems)
    return RenderedMail(clean_subject(subject), body, html_to_text(body))


# ---------------------------------------------------------------- SMTP-Konfiguration
def _header_safe(value: str) -> bool:
    return _HEADER_BAD.search(value) is None


def parse_mailbox(value: str) -> Address:
    """``Name <adresse>`` bzw. ``adresse`` -> Address (ohne Steuerzeichen, gueltige Adresse)."""
    if not value or not _header_safe(value):
        raise MailConfigError("smtp_absender_ungueltig")
    name, addr = parseaddr(value)
    if not addr or not is_valid_email(addr):
        raise MailConfigError("smtp_absender_ungueltig")
    local, domain = addr.rsplit("@", 1)
    return Address(display_name=name, username=local, domain=domain)


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    security: str
    user: str
    password: str
    sender: Address
    reply_to: Address | None
    timeout_s: float
    rate_per_sec: float

    @classmethod
    def from_settings(cls, settings: Settings) -> MailConfig:
        if not settings.SMTP_HOST:
            raise MailConfigError("smtp_nicht_konfiguriert")
        if not settings.SMTP_FROM:
            raise MailConfigError("smtp_absender_fehlt")
        if not _header_safe(settings.SMTP_HOST):
            raise MailConfigError("smtp_nicht_konfiguriert")
        reply = parse_mailbox(settings.SMTP_REPLY_TO) if settings.SMTP_REPLY_TO else None
        return cls(
            host=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            security=settings.SMTP_SECURITY,
            user=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD.get_secret_value(),
            sender=parse_mailbox(settings.SMTP_FROM),
            reply_to=reply,
            timeout_s=settings.SMTP_TIMEOUT_S,
            rate_per_sec=settings.MAIL_RATE_PER_SEC,
        )


def build_message(
    config: MailConfig, to: str, mail: RenderedMail, *, now: datetime | None = None
) -> EmailMessage:
    """Eine Nachricht an genau einen Empfaenger (``multipart/alternative``)."""
    addr = normalize_email(to)
    if not is_valid_email(addr):
        raise MailConfigError("empfaenger_ungueltig")
    subject = clean_subject(mail.subject)  # erneut: keine Steuerzeichen im Header
    local, domain = addr.rsplit("@", 1)
    msg = EmailMessage(policy=policy.SMTP)
    msg["From"] = config.sender
    msg["To"] = Address(username=local, domain=domain)
    if config.reply_to is not None:
        msg["Reply-To"] = config.reply_to
    msg["Subject"] = subject
    msg["Date"] = format_datetime(now or datetime.now(tz=UTC))
    msg["Message-ID"] = make_msgid(domain=config.sender.domain)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(mail.text, subtype="plain", charset="utf-8")
    msg.add_alternative(html_document(subject, mail.html_body), subtype="html", charset="utf-8")
    return msg
