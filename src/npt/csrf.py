"""Zustandsloser CSRF-Schutz ohne Cookies (NPT-08).

Token = ``<ts>.<hex(HMAC-SHA256(CSRF_SECRET, scope || "|" || ts))>``, gueltig 12 h.
Scope Admin: Issuer + Identifier; Scope Bearbeiter: Projekt + token_generation.
Zusaetzlich muss der Origin-Header exakt der Basis-URL entsprechen.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from npt import limits
from npt.config import normalize_origin


class CsrfError(Exception):
    """CSRF-Pruefung fehlgeschlagen (Grund nur fuers Log, nicht fuer den Client)."""


def admin_scope(issuer_norm: str, kind: str, identifier: str) -> str:
    return f"admin\x1f{issuer_norm}\x1f{kind}\x1f{identifier}"


def project_scope(project_id: int, token_generation: int) -> str:
    return f"project\x1f{project_id}\x1f{token_generation}"


def _mac(secret: bytes, scope: str, ts: int) -> str:
    msg = f"{scope}|{ts}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def issue_token(secret: bytes, scope: str, now: float | None = None) -> str:
    ts = int(now if now is not None else time.time())
    return f"{ts}.{_mac(secret, scope, ts)}"


def verify_token(
    secret: bytes,
    scope: str,
    token: str | None,
    now: float | None = None,
    ttl_s: int = limits.CSRF_TTL_S,
) -> None:
    if not token or "." not in token or len(token) > 128:
        raise CsrfError("Token fehlt oder Format ungueltig")
    ts_text, mac = token.split(".", 1)
    if not ts_text.isdigit():
        raise CsrfError("Zeitstempel ungueltig")
    ts = int(ts_text)
    current = int(now if now is not None else time.time())
    if ts > current + limits.CSRF_FUTURE_SKEW_S:
        raise CsrfError("Zeitstempel in der Zukunft")
    if current - ts > ttl_s:
        raise CsrfError("Token abgelaufen")
    expected = _mac(secret, scope, ts)
    if not hmac.compare_digest(expected, mac):
        raise CsrfError("Signatur ungueltig")


def verify_origin(origin_header: str | None, expected_origin: str) -> None:
    """Origin muss exakt (normalisiert) der Basis-URL entsprechen; fehlt er, wird abgelehnt."""
    if not origin_header or origin_header == "null":
        raise CsrfError("Origin fehlt")
    try:
        got = normalize_origin(origin_header)
    except ValueError as exc:
        raise CsrfError("Origin ungueltig") from exc
    if not hmac.compare_digest(got, normalize_origin(expected_origin)):
        raise CsrfError("Origin passt nicht zur Basis-URL")
