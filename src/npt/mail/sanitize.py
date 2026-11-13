"""Sanitisierung des Mailtexts – Implementierung in :mod:`npt.richtext` (NPT-13, Auflage 3)."""

from npt.richtext import (
    BODY_INPUT_MAX,
    LINK_PLACEHOLDER,
    LINK_REL,
    SUBJECT_MAX,
    ContentError,
    clean_body,
    clean_subject,
    html_to_text,
    sanitize_html,
)

__all__ = [
    "BODY_INPUT_MAX",
    "LINK_PLACEHOLDER",
    "LINK_REL",
    "SUBJECT_MAX",
    "ContentError",
    "clean_body",
    "clean_subject",
    "html_to_text",
    "sanitize_html",
]
