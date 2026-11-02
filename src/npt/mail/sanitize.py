"""Serverseitige Sanitisierung des Mailtexts (Auftrag 6.3, NPT-01) und Plaintext-Erzeugung.

- Allowlist mit ``nh3``: nur die Formate des Editors (Absatz, Zeilenumbruch, fett, kursiv,
  Listen, Links, Ueberschriften 1–3). Alles andere – ``script``/``style`` samt Inhalt,
  Event-Handler, ``style``-/``class``-Attribute, Bilder, Formulare – wird entfernt.
- Links nur mit ``http``, ``https`` oder ``mailto`` (keine relativen Ziele, kein
  ``javascript:``/``data:``); ``rel="noopener noreferrer"`` wird gesetzt.
- ``html_to_text`` erzeugt den Plaintext-Teil aus dem (fertig ersetzten) HTML.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Final
from urllib.parse import urlsplit

import nh3

ALLOWED_TAGS: Final = frozenset(
    {"p", "br", "strong", "b", "em", "i", "ul", "ol", "li", "a", "h1", "h2", "h3"}
)
ALLOWED_ATTRIBUTES: Final = {"a": {"href"}}
URL_SCHEMES: Final = frozenset({"http", "https", "mailto"})
BODY_INPUT_MAX: Final = 200_000
SUBJECT_MAX: Final = 200
LINK_PLACEHOLDER: Final = "[LINK]"
LINK_REL: Final = "noopener noreferrer"

_CTRL_RE: Final = re.compile(r"[\x00-\x1f\x7f  \u0085]")
_SPACES_RE: Final = re.compile(r"\s+")
_NBSP_RE: Final = re.compile(r"&nbsp;|&#160;|&#xa0;|\xa0", re.IGNORECASE)
_TEXT_RE: Final = re.compile(r"<[^>]*>")


class ContentError(ValueError):
    """Ungueltiger Betreff/Text; ``code`` ist ein fester Meldungsschluessel."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _attribute_filter(element: str, attribute: str, value: str) -> str | None:
    if element == "a" and attribute == "href":
        href = value.strip()
        if LINK_PLACEHOLDER in href:  # [LINK] nur im Text, nie in einem Attribut
            return None
        try:
            scheme = urlsplit(href).scheme.lower()
        except ValueError:
            return None
        return href if scheme in URL_SCHEMES else None
    if element == "a" and attribute == "rel":
        return LINK_REL  # von nh3 gesetzt (link_rel), Wert fest
    return None  # alle anderen Attribute (auch nh3-Standard wie title/lang) entfernen


def sanitize_html(html: str) -> str:
    """Allowlist-Sanitisierung; Ergebnis ist sicher zum Einbetten und Versenden."""
    # Quill 2 schreibt Leerzeichen teils als &nbsp; – als normales Leerzeichen behandeln.
    prepared = _NBSP_RE.sub(" ", html[:BODY_INPUT_MAX])
    cleaned = nh3.clean(
        prepared,
        tags=set(ALLOWED_TAGS),
        attributes={"*": set(), **{k: set(v) for k, v in ALLOWED_ATTRIBUTES.items()}},
        attribute_filter=_attribute_filter,
        url_schemes=set(URL_SCHEMES),
        link_rel=LINK_REL,
        strip_comments=True,
    )
    return cleaned.strip()


def clean_body(html: str) -> str:
    """Sanitisierter Mailtext; leerer Text -> :class:`ContentError`."""
    if len(html) > BODY_INPUT_MAX:
        raise ContentError("mail_text_zu_lang")
    cleaned = sanitize_html(html)
    if not _TEXT_RE.sub("", cleaned).strip():
        raise ContentError("mail_text_fehlt")
    return cleaned


def clean_subject(value: str) -> str:
    """Betreff als einzeiliger Klartext (Header-Injection unmoeglich: keine Steuerzeichen)."""
    text = _SPACES_RE.sub(" ", _CTRL_RE.sub(" ", value)).strip()
    if not text:
        raise ContentError("mail_betreff_fehlt")
    if len(text) > SUBJECT_MAX:
        raise ContentError("mail_betreff_zu_lang")
    return text


# ---------------------------------------------------------------- HTML -> Plaintext
class _TextBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.current = ""
        self.lists: list[list[int]] = []  # je Ebene: [nummer] (ol) bzw. [-1] (ul)
        self.href: str | None = None
        self.link_text = ""

    # -- Zeilenverwaltung
    def _flush(self) -> None:
        line = self.current.rstrip()
        if line.strip():
            self.lines.append(line)
        self.current = ""

    def _blank(self) -> None:
        self._flush()
        if self.lines and self.lines[-1] != "":
            self.lines.append("")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("p", "h1", "h2", "h3", "div"):
            self._blank()
        elif tag in ("ul", "ol"):
            if not self.lists:
                self._blank()
            else:
                self._flush()
            self.lists.append([0 if tag == "ol" else -1])
        elif tag == "li":
            self._flush()
            depth = max(len(self.lists) - 1, 0)
            marker = "- "
            if self.lists and self.lists[-1][0] >= 0:
                self.lists[-1][0] += 1
                marker = f"{self.lists[-1][0]}. "
            self.current = "  " * depth + marker
        elif tag == "br":
            self._flush()
        elif tag == "a":
            self.href = dict(attrs).get("href")
            self.link_text = ""

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h1", "h2", "h3"):
            title = self.current.strip()
            self._flush()
            if title:
                self.lines.append(("=" if tag == "h1" else "-") * min(len(title), 72))
            self._blank()
        elif tag in ("p", "div"):
            self._blank()
        elif tag in ("ul", "ol"):
            self._flush()
            if self.lists:
                self.lists.pop()
            if not self.lists:
                self._blank()
        elif tag == "li":
            self._flush()
        elif tag == "a":
            href = (self.href or "").strip()
            text = self.link_text.strip()
            target = href[7:] if href.lower().startswith("mailto:") else href
            if href and target != text:
                self.current += f" <{target}>" if text else target
            self.href = None

    def handle_data(self, data: str) -> None:
        text = _SPACES_RE.sub(" ", data)
        if not self.current or self.current.endswith(" "):
            text = text.lstrip(" ")
        self.current += text
        if self.href is not None:
            self.link_text += text

    def result(self) -> str:
        self._flush()
        while self.lines and self.lines[-1] == "":
            self.lines.pop()
        return "\n".join(self.lines) + "\n"


def html_to_text(html: str) -> str:
    """Plaintext-Fallback: Absaetze, Listen (``-``/``1.``), Ueberschriften unterstrichen,
    Links als ``Text <URL>``."""
    builder = _TextBuilder()
    builder.feed(html)
    builder.close()
    return builder.result()
