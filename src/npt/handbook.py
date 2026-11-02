"""Benutzerhandbuecher (Auftrag 11.2): eine Markdown-Quelle, serverseitig gerendert.

Quelle sind ``docs/handbuch/bearbeiter.md`` und ``docs/handbuch/admin.md`` im Repository.
Beim Paketbau landen sie als ``npt/handbuch`` im Paket (``force-include`` in
``pyproject.toml``); im Quellbaum (Tests) wird direkt ``docs/handbuch`` gelesen.

Sicherheit: markdown-it rendert mit ``html=False`` (rohes HTML in der Quelle wird als
Text ausgegeben); das Ergebnis laeuft zusaetzlich durch eine nh3-Allowlist. Platzhalter
(``{{projekt}}`` …) werden erst **danach** und HTML-escaped eingesetzt, Nutzerdaten
(Projektname) erzeugen also nie Markup.
"""

from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlsplit

import nh3
from markdown_it import MarkdownIt

Handbook = Literal["bearbeiter", "admin"]

_PKG_DIR: Final = Path(__file__).resolve().parent
_CANDIDATES: Final = (_PKG_DIR / "handbuch", _PKG_DIR.parent.parent / "docs" / "handbuch")

ALLOWED_TAGS: Final = frozenset(
    {
        "h1", "h2", "h3", "h4", "p", "br", "hr", "strong", "em", "code", "pre",
        "ul", "ol", "li", "a", "blockquote", "table", "thead", "tbody", "tr", "th", "td",
    }
)  # fmt: skip
ALLOWED_ATTRIBUTES: Final = {
    "a": {"href"},
    "h2": {"id"},
    "h3": {"id"},
}  # kein style-Attribut: CSP style-src 'self' wuerde es ohnehin blockieren
_PLACEHOLDER_RE: Final = re.compile(r"\{\{([a-z_]+)\}\}")
_SLUG_RE: Final = re.compile(r"[^a-z0-9äöüß]+")


def handbook_dir() -> Path:
    for cand in _CANDIDATES:
        if (cand / "bearbeiter.md").is_file():
            return cand
    raise FileNotFoundError("Handbuch-Quellen nicht gefunden (docs/handbuch)")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.casefold()).strip("-")[:60] or "abschnitt"


def _attribute_filter(element: str, attribute: str, value: str) -> str | None:
    if element == "a" and attribute == "href":
        href = value.strip()
        try:
            parts = urlsplit(href)
        except ValueError:
            return None
        # relative Ziele (z. B. „uebersicht" relativ zu /p/<token>/hilfe), Anker,
        # http(s) und mailto; alles andere (javascript:, data: …) entfaellt.
        if parts.scheme.lower() in ("", "http", "https", "mailto"):
            return href
        return None
    if attribute == "id":
        return value if re.fullmatch(r"[a-z0-9äöüß-]{1,60}", value) else None
    if element == "a" and attribute == "rel":
        return "noopener noreferrer"
    return None


def _markdown() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    md.enable("table")
    return md


@lru_cache(maxsize=4)
def _rendered(name: Handbook) -> tuple[str, str]:
    """(Titel, sanitisiertes HTML mit Platzhaltern) – je Prozess einmal gerendert."""
    source = (handbook_dir() / f"{name}.md").read_text(encoding="utf-8")
    md = _markdown()
    tokens = md.parse(source)
    title = "Hilfe"
    seen: set[str] = set()
    for i, tok in enumerate(tokens):
        if tok.type != "heading_open":
            continue
        inline = tokens[i + 1].content if i + 1 < len(tokens) else ""
        if tok.tag == "h1" and title == "Hilfe":
            title = inline
        if tok.tag in ("h2", "h3"):
            slug = base = _slug(inline)
            n = 2
            while slug in seen:
                slug, n = f"{base}-{n}", n + 1
            seen.add(slug)
            tok.attrSet("id", slug)
    raw = md.renderer.render(tokens, md.options, {})
    clean = nh3.clean(
        raw,
        tags=set(ALLOWED_TAGS),
        attributes={k: set(v) for k, v in ALLOWED_ATTRIBUTES.items()},
        attribute_filter=_attribute_filter,
        link_rel="noopener noreferrer",
        url_schemes={"http", "https", "mailto"},
        strip_comments=True,
    )
    return title, clean


def render(name: Handbook, values: dict[str, str]) -> tuple[str, str]:
    """Titel (Klartext) und HTML des Handbuchs mit ersetzten ``{{schluessel}}``.

    Im HTML wird HTML-escaped ersetzt. Der Titel ist Klartext (Inhalt der ersten
    Ueberschrift) und wird erst vom Template (Autoescape) escaped – vorher escapen
    ergaebe ``&amp;lt;`` im ``<title>`` (QA ea7c23a1). Unbekannte Platzhalter bleiben
    sichtbar stehen (fallen beim Lesen auf).
    """
    title, body = _rendered(name)

    def repl_html(m: re.Match[str]) -> str:
        key = m.group(1)
        return html.escape(values[key], quote=True) if key in values else m.group(0)

    def repl_text(m: re.Match[str]) -> str:
        return values.get(m.group(1), m.group(0))

    return _PLACEHOLDER_RE.sub(repl_text, title), _PLACEHOLDER_RE.sub(repl_html, body)
