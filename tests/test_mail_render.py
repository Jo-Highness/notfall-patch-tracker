"""``[LINK]``-Ersetzung, Escaping, MIME-Struktur und Header-Schutz (Auftrag 6.3, NPT-05)."""

from __future__ import annotations

import re
from email import message_from_bytes, policy
from email.message import EmailMessage
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

from npt.domain import filters
from npt.mail import render
from npt.mail.audience import SystemRef, build_audience
from npt.mail.render import MailConfigError
from npt.mail.sanitize import clean_body
from tests.mail_helpers import mail_config, mail_settings

BASE = "https://npt.firma.test"
TOKEN = "t" * 43  # Test-Token (Format wie token_urlsafe(32), ohne Bedeutung)
BODY = clean_body("<p>Hallo,</p><p>bitte prüfen:</p><p>[LINK]</p><p>Danke</p>")


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text = ""
        self.tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        if tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._text = ""

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, self._text))
            self._href = None


def links_of(html: str) -> _Links:
    p = _Links()
    p.feed(html)
    return p


def ids_of(url: str) -> list[str]:
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}" == BASE
    assert parts.path == f"/p/{TOKEN}/"
    state = filters.parse(parse_qsl(parts.query, keep_blank_values=True))
    assert not state.contains
    assert not state.enums
    assert state.q is None
    return list(state.exact["system_id"])


def test_link_list_per_system_and_collective() -> None:
    systems = [SystemRef("APP-00001", "Kundenportal"), SystemRef("APP-00007", None)]
    out = render.render_body(BODY, BASE, TOKEN, systems)
    assert "[LINK]" not in out
    assert "<p><ul>" not in out  # eigener Absatz wird durch den Block ersetzt
    links = links_of(out).links
    assert [t for _, t in links] == ["Kundenportal (APP-00001)", "APP-00007", "Alle meine Systeme"]
    assert ids_of(links[0][0]) == ["APP-00001"]
    assert ids_of(links[1][0]) == ["APP-00007"]
    assert ids_of(links[2][0]) == ["APP-00001", "APP-00007"]
    assert "(2 Systeme)" in out


def test_links_only_contain_recipients_systems() -> None:
    rows: list[dict[str, Any]] = [
        {"system_id": "A", "system_name": "a", "app_owner": "x@f.test; y@f.test", "affected": None},
        {"system_id": "B", "system_name": "b", "app_owner": "y@f.test", "affected": None},
        {"system_id": "C", "system_name": "c", "app_owner": "x@f.test", "affected": "ja"},
        {"system_id": "D", "system_name": "d", "app_owner": "z@f.test", "affected": None},
    ]
    a = build_audience(rows, filters.parse([("f.affected", "leer")]))
    got = {}
    for r in a.recipients:
        mail = render.render_mail("Betreff", BODY, BASE, TOKEN, r.systems)
        links = links_of(mail.html_body).links
        got[r.email] = ids_of(links[-1][0])
        assert sorted(ids_of(h)[0] for h, _ in links[:-1]) == got[r.email]
    assert got == {"x@f.test": ["A"], "y@f.test": ["A", "B"], "z@f.test": ["D"]}


@pytest.mark.parametrize(
    "name",
    [
        "<script>alert(1)</script>",
        '"><img src=x onerror=alert(1)>',
        '</a><a href="javascript:alert(1)">klick',
        "Name & Co <b>fett</b>",
        "[LINK]",
    ],
)
def test_system_names_are_escaped(name: str) -> None:
    sid = 'ID"&<x>'
    out = render.render_body(BODY, BASE, TOKEN, [SystemRef(sid, name)])
    parsed = links_of(out)
    assert set(parsed.tags) <= {"ul", "li", "a", "p"}
    assert len(parsed.links) == 2
    href, label = parsed.links[0]
    assert label == f"{name} ({sid})"
    assert ids_of(href) == [sid]
    assert "<script" not in out
    assert all(h.startswith(f"{BASE}/p/{TOKEN}/?") for h, _ in parsed.links)
    assert out.count("[LINK]") == (1 if name == "[LINK]" else 0)


def test_placeholder_inline_and_multiple() -> None:
    body = clean_body("<p>Systeme: [LINK]</p><p>nochmal [LINK]</p>")
    out = render.render_body(body, BASE, TOKEN, [SystemRef("S1", "eins")])
    assert "[LINK]" not in out
    assert len(links_of(out).links) == 4
    no_placeholder = render.render_body(clean_body("<p>ohne</p>"), BASE, TOKEN, [])
    assert no_placeholder == "<p>ohne</p>"


def test_collective_link_split_for_many_systems() -> None:
    systems = [SystemRef(f"APP-{i:05d}", f"S{i}") for i in range(600)]
    urls = render.collective_urls(BASE, TOKEN, [s.system_id for s in systems])
    assert len(urls) > 1
    assert all(len(u) <= render.COLLECTIVE_URL_MAX for u in urls)
    assert [sid for u in urls for sid in ids_of(u)] == [s.system_id for s in systems]
    out = render.render_body(BODY, BASE, TOKEN, systems)
    assert f"Alle meine Systeme (Teil 1 von {len(urls)})" in out


def parse_bytes(msg: EmailMessage) -> EmailMessage:
    data = msg.as_bytes()
    assert b"\r\n" in data
    parsed = message_from_bytes(data, policy=policy.default)
    assert isinstance(parsed, EmailMessage)
    return parsed


def test_mime_structure_and_headers() -> None:
    cfg = mail_config()
    mail = render.render_mail(
        "Notfall CVE-2026-1", BODY, BASE, TOKEN, [SystemRef("APP-1", "Portal <x>")]
    )
    msg = parse_bytes(render.build_message(cfg, "  Anna@Firma.TEST ", mail))
    assert msg.get_content_type() == "multipart/alternative"
    parts = list(msg.iter_parts())
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert msg["To"] == "anna@firma.test"
    assert len(msg.get_all("To") or []) == 1
    assert msg["Cc"] is None
    assert msg["Bcc"] is None
    assert msg["From"] == "Notfall-Patch-Tracker <npt@npt.test>"
    assert msg["Reply-To"] == "security@npt.test"
    assert msg["Subject"] == "Notfall CVE-2026-1"
    assert re.fullmatch(r"<[^<>@\s]+@npt\.test>", msg["Message-ID"])
    assert msg["Date"]
    assert msg["MIME-Version"] == "1.0"
    text_part = parts[0].get_content()
    html_part = parts[1].get_content()
    assert "Portal <x> (APP-1)" in text_part
    assert f"{BASE}/p/{TOKEN}/?x.system_id=APP-1" in text_part
    assert "Portal &lt;x&gt; (APP-1)" in html_part
    assert html_part.startswith("<!DOCTYPE html>")
    assert "[LINK]" not in text_part + html_part


def test_header_injection_prevented() -> None:
    cfg = mail_config()
    mail = render.RenderedMail("Hallo\r\nBcc: evil@x.test", "<p>x</p>", "x\n")
    msg = parse_bytes(render.build_message(cfg, "a@f.test", mail))
    assert msg["Bcc"] is None
    assert msg["Subject"] == "Hallo Bcc: evil@x.test"
    for bad in ("a@f.test\r\nBcc: evil@x.test", "a@f.test, b@f.test", "kein-mail"):
        with pytest.raises(MailConfigError):
            render.build_message(cfg, bad, mail)
    for bad_from in ("npt@npt.test\r\nBcc: evil@x.test", "kein absender", "a@b.test, c@d.test"):
        with pytest.raises(MailConfigError):
            render.MailConfig.from_settings(mail_settings(SMTP_FROM=bad_from))
    with pytest.raises(MailConfigError) as exc:
        render.MailConfig.from_settings(mail_settings(SMTP_HOST=""))
    assert exc.value.code == "smtp_nicht_konfiguriert"
    with pytest.raises(MailConfigError) as exc:
        render.MailConfig.from_settings(mail_settings(SMTP_FROM=""))
    assert exc.value.code == "smtp_absender_fehlt"


def test_non_ascii_subject_and_body_encoded() -> None:
    cfg = mail_config()
    mail = render.render_mail("Prüfung nötig – Ä", BODY, BASE, TOKEN, [SystemRef("S", "Übersicht")])
    raw = render.build_message(cfg, "a@f.test", mail).as_bytes()
    assert all(b < 128 for b in raw.split(b"\r\n\r\n", 1)[0])  # Header 7-Bit (RFC 2047)
    parsed = parse_bytes(render.build_message(cfg, "a@f.test", mail))
    assert parsed["Subject"] == "Prüfung nötig – Ä"
