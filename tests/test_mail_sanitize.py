"""Sanitisierung des Mailtexts (nh3-Allowlist) und Plaintext-Erzeugung (Auftrag 6.3)."""

from __future__ import annotations

import pytest

from npt.mail.sanitize import (
    ContentError,
    clean_body,
    clean_subject,
    html_to_text,
    sanitize_html,
)


@pytest.mark.parametrize(
    "dirty",
    [
        "<p>Hallo<script>alert(1)</script></p>",
        '<p><img src="x" onerror="alert(1)">Hallo</p>',
        '<p onclick="alert(1)">Hallo</p>',
        '<p><a href="javascript:alert(1)">Hallo</a></p>',
        '<p><a href="JaVaScRiPt:alert(1)">Hallo</a></p>',
        '<p><a href=" javascript:alert(1)">Hallo</a></p>',
        '<p><a href="data:text/html;base64,PHNjcmlwdD4=">Hallo</a></p>',
        '<p><a href="vbscript:msgbox(1)">Hallo</a></p>',
        "<p><style>p{color:red}</style>Hallo</p>",
        '<p><iframe src="https://evil.test"></iframe>Hallo</p>',
        '<p style="background:url(javascript:alert(1))">Hallo</p>',
        "<svg><script>alert(1)</script></svg><p>Hallo</p>",
        '<form action="https://evil.test"><input name="x"></form><p>Hallo</p>',
    ],
)
def test_dangerous_markup_removed(dirty: str) -> None:
    out = sanitize_html(dirty)
    low = out.lower()
    for bad in (
        "<script",
        "onerror",
        "onclick",
        "javascript:",
        "vbscript:",
        "data:",
        "<style",
        "<iframe",
        "style=",
        "<svg",
        "<form",
        "<input",
        "<img",
        "alert(1)",
    ):
        assert bad not in low, (dirty, out)
    assert "Hallo" in out


def test_allowed_formats_kept() -> None:
    html = (
        "<h1>Titel</h1><h2>Unter</h2><h3>Klein</h3><p><strong>fett</strong> <em>kursiv</em> "
        "<b>b</b> <i>i</i><br>neu</p><ul><li>eins</li></ul><ol><li>zwei</li></ol>"
        '<p><a href="https://firma.test/x?a=1&amp;b=2">Link</a> '
        '<a href="mailto:sec@firma.test">Mail</a> <a href="http://firma.test">h</a></p>'
    )
    out = sanitize_html(html)
    for tag in (
        "<h1>",
        "<h2>",
        "<h3>",
        "<strong>",
        "<em>",
        "<b>",
        "<i>",
        "<br>",
        "<ul>",
        "<ol>",
        "<li>",
    ):
        assert tag in out
    assert 'href="https://firma.test/x?a=1&amp;b=2"' in out
    assert 'href="mailto:sec@firma.test"' in out
    assert 'href="http://firma.test"' in out
    assert out.count('rel="noopener noreferrer"') == 3


def test_links_without_allowed_scheme_lose_href() -> None:
    for href in ("/relativ", "relativ.html", "//evil.test/x", "ftp://evil.test", "#anker"):
        out = sanitize_html(f'<p><a href="{href}">x</a></p>')
        assert "href" not in out, (href, out)
        assert "x" in out


def test_attributes_and_classes_removed() -> None:
    out = sanitize_html(
        '<p class="ql-align-center" id="x" data-list="bullet"><a href="https://a.test" '
        'target="_blank" title="t">a</a></p>'
    )
    assert "class=" not in out
    assert "id=" not in out
    assert "data-" not in out
    assert "target=" not in out
    assert "title=" not in out


def test_quill_nbsp_becomes_space_and_placeholder_survives() -> None:
    out = sanitize_html("<p>Bitte&nbsp;prüfen:</p><p>[LINK]</p>")
    assert "Bitte prüfen:" in out
    assert "<p>[LINK]</p>" in out


def test_clean_body_requires_text() -> None:
    with pytest.raises(ContentError) as exc:
        clean_body("<p><br></p>")
    assert exc.value.code == "mail_text_fehlt"
    with pytest.raises(ContentError):
        clean_body("<script>alert(1)</script>")
    with pytest.raises(ContentError) as exc:
        clean_body("x" * 200_001)
    assert exc.value.code == "mail_text_zu_lang"


def test_subject_single_line_without_control_chars() -> None:
    assert clean_subject("  Notfall:\r\nBcc: evil@x.test  ") == "Notfall: Bcc: evil@x.test"
    assert clean_subject("a b\x00c\td") == "a b c d"
    with pytest.raises(ContentError) as exc:
        clean_subject(" \r\n ")
    assert exc.value.code == "mail_betreff_fehlt"
    with pytest.raises(ContentError) as exc:
        clean_subject("x" * 201)
    assert exc.value.code == "mail_betreff_zu_lang"


def test_html_to_text_structure() -> None:
    html = (
        "<h1>Notfall</h1><p>Hallo <strong>Team</strong>,<br>bitte prüfen.</p>"
        '<ul><li>eins</li><li>zwei <a href="https://a.test/x">Link</a></li></ul>'
        "<ol><li>erst</li><li>dann</li></ol>"
        '<p><a href="https://a.test/y">https://a.test/y</a> '
        '<a href="mailto:s@a.test">s@a.test</a></p>'
    )
    text = html_to_text(html)
    assert text == (
        "Notfall\n=======\n\nHallo Team,\nbitte prüfen.\n\n- eins\n- zwei Link <https://a.test/x>\n"
        "\n1. erst\n2. dann\n\nhttps://a.test/y s@a.test\n"
    )


def test_html_to_text_nested_lists_and_entities() -> None:
    text = html_to_text("<ul><li>a<ul><li>b</li></ul></li></ul><p>&lt;script&gt; &amp; Co</p>")
    assert "- a\n  - b" in text
    assert "<script> & Co" in text


def test_placeholder_never_inside_attribute() -> None:
    out = sanitize_html('<p><a href="https://a.test/[LINK]">x</a> [LINK]</p>')
    assert "href" not in out
    assert out.count("[LINK]") == 1
