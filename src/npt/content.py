"""News und FAQ je Projekt (NPT-13): Validierung, Sanitisierung, Lesen, Schreiben.

- Rich-Text kommt aus dem Quill-Editor der Admin-App und wird mit der Allowlist aus
  :mod:`npt.richtext` (nh3) **beim Speichern** bereinigt. Die Lesepfade
  (:func:`render_html`) sanitisieren **beim Ausgeben** noch einmal (Defense in depth,
  z. B. gegen direkt in der DB veraenderte Inhalte).
- FAQ-Antworten bekommen zusaetzlich einen Plaintext (``answer_text``) fuer die Suche.
- Schreiben ist Admin-Sache; das Audit schreibt der Admin-Router (dieses Modul importiert
  nichts aus dem Admin-Bereich und wird auch von der public-App genutzt, NPT-02).
- News/FAQ sind keine Systemdaten: kein ``change_seq``, kein Live-NOTIFY, nicht Teil der
  Projekt-Backups; beim Loeschen des Projekts entfernt der Fremdschluessel sie mit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from npt import limits
from npt.db.models import Project, ProjectFaq, ProjectNews
from npt.richtext import BODY_INPUT_MAX, html_to_text, sanitize_html

TITLE_MAX: Final = 200
QUESTION_MAX: Final = 500
SEARCH_MAX: Final = 200
SORT_MIN: Final = -100_000
SORT_MAX: Final = 100_000
LIST_LIMIT: Final = max(limits.NEWS_MAX_PER_PROJECT, limits.FAQ_MAX_PER_PROJECT)

_CTRL_RE: Final = re.compile(r"[\x00-\x1f\x7f\u0085  ]")
_SPACES_RE: Final = re.compile(r"\s+")
_TAG_RE: Final = re.compile(r"<[^>]*>")


class ContentError(ValueError):
    """Ungueltige Eingabe bzw. nicht gefundener Eintrag; ``code`` ist ein Meldungsschluessel."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- Validierung
def clean_line(value: str, *, max_len: int, missing: str, too_long: str) -> str:
    """Einzeiliger Klartext (Titel/Frage): Steuerzeichen und Mehrfach-Leerraum entfernt."""
    text = _SPACES_RE.sub(" ", _CTRL_RE.sub(" ", value)).strip()
    if not text:
        raise ContentError(missing)
    if len(text) > max_len:
        raise ContentError(too_long)
    return text


def clean_rich_text(html: str, *, missing: str, too_long: str) -> str:
    """Sanitisierter Rich-Text; leer (auch nach dem Bereinigen) -> :class:`ContentError`."""
    if len(html) > BODY_INPUT_MAX:
        raise ContentError(too_long)
    cleaned = sanitize_html(html)
    if not _TAG_RE.sub("", cleaned).strip():
        raise ContentError(missing)
    return cleaned


def render_html(stored: str) -> str:
    """Ausgabe gespeicherten Rich-Texts: immer erneut sanitisiert (nur so ``|safe``)."""
    return sanitize_html(stored)


def clean_sort(value: str | int | None) -> int:
    if value is None or value == "":
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ContentError("faq_reihenfolge_ungueltig") from exc
    if not SORT_MIN <= number <= SORT_MAX:
        raise ContentError("faq_reihenfolge_ungueltig")
    return number


def parse_id(raw: str) -> int:
    """ID aus dem URL-Pfad; ungueltig (keine Ziffern, ausserhalb bigint) -> 0 (existiert nie,
    fuehrt zu „nicht gefunden" statt 422/500)."""
    if raw.isascii() and raw.isdigit() and 0 < len(raw) <= 18:
        return int(raw)
    return 0


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _require_project(session: Session, project_id: int) -> None:
    # FOR UPDATE: gleichzeitige Anlagen zaehlen gegen die Obergrenze nacheinander.
    row = session.execute(
        select(Project.id).where(Project.id == project_id).with_for_update()
    ).first()
    if row is None:
        raise ContentError("projekt_nicht_gefunden")


def _count(session: Session, model: type[ProjectNews] | type[ProjectFaq], project_id: int) -> int:
    stmt = select(func.count()).select_from(model).where(model.project_id == project_id)
    return int(session.execute(stmt).scalar_one())


# Oeffentliche Lesepfade: Spalten ausdruecklich, ohne created_by/updated_by (Admin-
# Identifier sind PII, Standard 6e9c7ea1, NPT-13 Auflage 4).
_NEWS_PUBLIC = (
    ProjectNews.id,
    ProjectNews.title,
    ProjectNews.body_html,
    ProjectNews.created_at,
    ProjectNews.updated_at,
    (ProjectNews.updated_by.is_not(None)).label("edited"),
)
_FAQ_PUBLIC = (
    ProjectFaq.id,
    ProjectFaq.question,
    ProjectFaq.answer_html,
    ProjectFaq.answer_text,
    ProjectFaq.sort_order,
)


# ---------------------------------------------------------------- News
@dataclass(frozen=True)
class NewsInput:
    title: str
    body_html: str


def validate_news(title: str, body_html: str) -> NewsInput:
    return NewsInput(
        clean_line(
            title, max_len=TITLE_MAX, missing="news_titel_fehlt", too_long="news_titel_zu_lang"
        ),
        clean_rich_text(body_html, missing="news_text_fehlt", too_long="news_text_zu_lang"),
    )


def list_news(session: Session, project_id: int, limit: int = LIST_LIMIT) -> list[ProjectNews]:
    stmt = (
        select(ProjectNews)
        .where(ProjectNews.project_id == project_id)
        .order_by(ProjectNews.created_at.desc(), ProjectNews.id.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def count_news(session: Session, project_id: int) -> int:
    return _count(session, ProjectNews, project_id)


def public_news_page(
    session: Session, project_id: int, page: int, page_size: int = limits.NEWS_PAGE_SIZE
) -> list[dict[str, Any]]:
    """Eine Seite Beitraege (neueste zuerst) fuer die Bearbeiter-Ansicht."""
    stmt = (
        select(*_NEWS_PUBLIC)
        .where(ProjectNews.project_id == project_id)
        .order_by(ProjectNews.created_at.desc(), ProjectNews.id.desc())
        .offset(max(page - 1, 0) * page_size)
        .limit(page_size)
    )
    return [dict(r) for r in session.execute(stmt).mappings()]


def public_news_item(session: Session, project_id: int, news_id: int) -> dict[str, Any]:
    stmt = select(*_NEWS_PUBLIC).where(
        ProjectNews.project_id == project_id, ProjectNews.id == news_id
    )
    row = session.execute(stmt).mappings().first()
    if row is None:
        raise ContentError("news_nicht_gefunden")
    return dict(row)


def get_news(session: Session, project_id: int, news_id: int) -> ProjectNews:
    item = session.get(ProjectNews, news_id)
    if item is None or item.project_id != project_id:
        raise ContentError("news_nicht_gefunden")
    return item


def create_news(session: Session, project_id: int, data: NewsInput, actor: str) -> ProjectNews:
    _require_project(session, project_id)
    if _count(session, ProjectNews, project_id) >= limits.NEWS_MAX_PER_PROJECT:
        raise ContentError("news_zu_viele")
    item = ProjectNews(
        project_id=project_id, title=data.title, body_html=data.body_html, created_by=actor
    )
    session.add(item)
    session.flush()
    return item


def update_news(
    session: Session, project_id: int, news_id: int, data: NewsInput, actor: str
) -> ProjectNews:
    item = get_news(session, project_id, news_id)
    item.title = data.title
    item.body_html = data.body_html
    item.updated_at = _now()
    item.updated_by = actor
    session.flush()
    return item


def delete_news(session: Session, project_id: int, news_id: int) -> str:
    item = get_news(session, project_id, news_id)
    title = item.title
    session.execute(delete(ProjectNews).where(ProjectNews.id == item.id))
    return title


# ---------------------------------------------------------------- FAQ
@dataclass(frozen=True)
class FaqInput:
    question: str
    answer_html: str
    answer_text: str
    sort_order: int


def validate_faq(question: str, answer_html: str, sort_order: str | int | None) -> FaqInput:
    q = clean_line(
        question,
        max_len=QUESTION_MAX,
        missing="faq_frage_fehlt",
        too_long="faq_frage_zu_lang",
    )
    html = clean_rich_text(answer_html, missing="faq_antwort_fehlt", too_long="faq_antwort_zu_lang")
    return FaqInput(q, html, html_to_text(html).strip(), clean_sort(sort_order))


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def normalize_query(query: str | None) -> str:
    return _SPACES_RE.sub(" ", _CTRL_RE.sub(" ", query or "")).strip()[:SEARCH_MAX]


def _faq_where(stmt: Any, project_id: int, query: str | None) -> Any:
    """Suche: jedes Wort muss in Frage oder Antwort-Plaintext vorkommen (ILIKE, escaped)."""
    stmt = stmt.where(ProjectFaq.project_id == project_id)
    for word in normalize_query(query).split(" "):
        if word:
            pattern = _like_pattern(word)
            stmt = stmt.where(
                or_(
                    ProjectFaq.question.ilike(pattern, escape="\\"),
                    ProjectFaq.answer_text.ilike(pattern, escape="\\"),
                )
            )
    return stmt.order_by(ProjectFaq.sort_order, ProjectFaq.id)


def list_faq(
    session: Session, project_id: int, query: str | None = None, limit: int = LIST_LIMIT
) -> list[ProjectFaq]:
    """FAQ (Admin) in Anzeigereihenfolge, optional gefiltert (siehe :func:`_faq_where`)."""
    return list(session.scalars(_faq_where(select(ProjectFaq), project_id, query).limit(limit)))


def public_faq(
    session: Session, project_id: int, query: str | None = None, limit: int = LIST_LIMIT
) -> list[dict[str, Any]]:
    """FAQ fuer die Bearbeiter-Ansicht (ohne Admin-Identifier)."""
    stmt = _faq_where(select(*_FAQ_PUBLIC), project_id, query).limit(limit)
    return [dict(r) for r in session.execute(stmt).mappings()]


def count_faq(session: Session, project_id: int) -> int:
    return _count(session, ProjectFaq, project_id)


def get_faq(session: Session, project_id: int, faq_id: int) -> ProjectFaq:
    item = session.get(ProjectFaq, faq_id)
    if item is None or item.project_id != project_id:
        raise ContentError("faq_nicht_gefunden")
    return item


def next_sort_order(session: Session, project_id: int) -> int:
    stmt = select(func.max(ProjectFaq.sort_order)).where(ProjectFaq.project_id == project_id)
    current = session.execute(stmt).scalar()
    return min(int(current) + 10, SORT_MAX) if current is not None else 10


def create_faq(session: Session, project_id: int, data: FaqInput, actor: str) -> ProjectFaq:
    _require_project(session, project_id)
    if _count(session, ProjectFaq, project_id) >= limits.FAQ_MAX_PER_PROJECT:
        raise ContentError("faq_zu_viele")
    item = ProjectFaq(
        project_id=project_id,
        question=data.question,
        answer_html=data.answer_html,
        answer_text=data.answer_text,
        sort_order=data.sort_order,
        created_by=actor,
    )
    session.add(item)
    session.flush()
    return item


def update_faq(
    session: Session, project_id: int, faq_id: int, data: FaqInput, actor: str
) -> ProjectFaq:
    item = get_faq(session, project_id, faq_id)
    item.question = data.question
    item.answer_html = data.answer_html
    item.answer_text = data.answer_text
    item.sort_order = data.sort_order
    item.updated_at = _now()
    item.updated_by = actor
    session.flush()
    return item


def delete_faq(session: Session, project_id: int, faq_id: int) -> str:
    item = get_faq(session, project_id, faq_id)
    question = item.question
    session.execute(delete(ProjectFaq).where(ProjectFaq.id == item.id))
    return question
