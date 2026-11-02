"""CSRF-Token und Origin-Pruefung (NPT-08)."""

from __future__ import annotations

import pytest

from npt import csrf

SECRET = b"s" * 32
SCOPE = csrf.admin_scope("C=de,CN=ca", "subject_dn", "CN=admin")


def _flip_last(token: str) -> str:
    return token[:-1] + ("1" if token[-1] == "0" else "0")


def test_valid_token_roundtrip() -> None:
    token = csrf.issue_token(SECRET, SCOPE, now=1_000_000)
    csrf.verify_token(SECRET, SCOPE, token, now=1_000_000 + 3600)


@pytest.mark.parametrize(
    ("token_fn", "now"),
    [
        (lambda: csrf.issue_token(SECRET, SCOPE, now=1_000_000), 1_000_000 + 12 * 3600 + 1),
        (lambda: csrf.issue_token(SECRET, SCOPE, now=1_000_000 + 4000), 1_000_000),
        (lambda: csrf.issue_token(b"t" * 32, SCOPE, now=1_000_000), 1_000_000),
        (lambda: csrf.issue_token(SECRET, SCOPE + "x", now=1_000_000), 1_000_000),
        (lambda: "", 1_000_000),
        (lambda: "abc", 1_000_000),
        (lambda: "x.y", 1_000_000),
        (lambda: _flip_last(csrf.issue_token(SECRET, SCOPE, now=1_000_000)), 1_000_000),
    ],
    ids=[
        "abgelaufen",
        "zukunft",
        "falsches-secret",
        "falscher-scope",
        "leer",
        "format",
        "ts-keine-zahl",
        "manipuliert",
    ],
)
def test_invalid_tokens_rejected(token_fn: object, now: int) -> None:
    token = token_fn()  # type: ignore[operator]
    with pytest.raises(csrf.CsrfError):
        csrf.verify_token(SECRET, SCOPE, token, now=now)


def test_project_scope_changes_with_token_generation() -> None:
    t = csrf.issue_token(SECRET, csrf.project_scope(1, 1), now=10)
    csrf.verify_token(SECRET, csrf.project_scope(1, 1), t, now=10)
    with pytest.raises(csrf.CsrfError):
        csrf.verify_token(SECRET, csrf.project_scope(1, 2), t, now=10)


@pytest.mark.parametrize(
    "origin", ["https://admin.example:443", "HTTPS://Admin.Example", "https://admin.example"]
)
def test_origin_accepted(origin: str) -> None:
    csrf.verify_origin(origin, "https://admin.example")


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "",
        "null",
        "https://evil.example",
        "http://admin.example",
        "https://admin.example:8443",
        "https://admin.example.evil.example",
        "nonsense",
    ],
)
def test_origin_rejected(origin: str | None) -> None:
    with pytest.raises(csrf.CsrfError):
        csrf.verify_origin(origin, "https://admin.example")
