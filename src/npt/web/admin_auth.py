"""Admin-Middleware: Vertrauensmodell gegenueber dem admin-gateway (NPT-06).

Reihenfolge der Pruefungen (jede Abweichung -> 403, nichts wird weitergereicht):

1. Direkte Absender-IP (Socket, NICHT X-Forwarded-For) liegt in ``ADMIN_GATEWAY_CIDR``.
2. ``X-SSL-Client-Verify == SUCCESS``.
3. PEM aus ``X-SSL-Client-Cert`` (URL-kodiert, ``$ssl_client_escaped_cert``) parsen;
   der SHA-1 des DER muss zu ``X-SSL-Client-Fingerprint`` passen. Subject und
   Issuer stammen ausschliesslich aus dem geparsten Zertifikat.
4. Identifier gemaess ``ADMIN_CERT_ID_ATTRIBUTE`` bilden -> ``scope["state"]["principal"]``.

Ob der Principal Admin ist, entscheidet die Route (DB-Abgleich); Nicht-Admins
sehen die Anfrage-Seite.
"""

from __future__ import annotations

import hmac
import logging
from urllib.parse import unquote

from cryptography import x509
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from npt import limits
from npt.config import Settings
from npt.identity import CertIdentity, IdKind, identity_from_cert, sha1_fingerprint
from npt.web.common import direct_peer, ip_in

log = logging.getLogger("npt.admin.auth")

H_VERIFY = b"x-ssl-client-verify"
H_CERT = b"x-ssl-client-cert"
H_FINGERPRINT = b"x-ssl-client-fingerprint"
_CERT_HEADERS = (H_VERIFY, H_CERT, H_FINGERPRINT)


class AuthRejectedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _single_header(scope: Scope, name: bytes) -> str | None:
    values = [v for k, v in scope.get("headers", []) if k == name]
    if not values:
        return None
    if len(values) > 1:
        # Mehrfach gesetzte Vertrauens-Header sind ein Manipulationsindiz.
        raise AuthRejectedError(f"Header {name.decode()} mehrfach")
    raw: bytes = values[0]
    return raw.decode("latin-1")


def _normalize_fp(value: str) -> str:
    return value.replace(":", "").strip().lower()


def authenticate(scope: Scope, settings: Settings) -> CertIdentity:
    """Prueft die Gateway-Angaben und liefert die Zertifikats-Identitaet."""
    peer = direct_peer(scope)
    if not ip_in(peer, settings.gateway_networks):
        raise AuthRejectedError("Absender ist nicht das Gateway")
    verify = _single_header(scope, H_VERIFY)
    if verify != "SUCCESS":
        raise AuthRejectedError("Client-Verifikation nicht SUCCESS")
    raw_cert = _single_header(scope, H_CERT)
    fingerprint = _single_header(scope, H_FINGERPRINT)
    if not raw_cert or not fingerprint:
        raise AuthRejectedError("Zertifikat oder Fingerprint fehlt")
    if len(raw_cert) > limits.MAX_CERT_HEADER_BYTES:
        raise AuthRejectedError("Zertifikat-Header zu gross")
    try:
        cert = x509.load_pem_x509_certificate(unquote(raw_cert).encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise AuthRejectedError("Zertifikat nicht lesbar") from exc
    if not hmac.compare_digest(sha1_fingerprint(cert), _normalize_fp(fingerprint)):
        raise AuthRejectedError("Fingerprint passt nicht zum Zertifikat")
    kind: IdKind = settings.ADMIN_CERT_ID_ATTRIBUTE
    return identity_from_cert(cert, kind)


class AdminAuthMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            principal = authenticate(scope, self.settings)
        except AuthRejectedError as exc:
            log.warning(
                "Admin-Zugriff abgelehnt",
                extra={"reason": exc.reason, "peer": direct_peer(scope), "path": scope.get("path")},
            )
            response = PlainTextResponse(
                "Zugriff verweigert.\n", status_code=403, headers={"cache-control": "no-store"}
            )
            await response(scope, receive, send)
            return
        # Vertrauens-Header nach der Auswertung entfernen, damit nichts Nachgelagertes
        # versehentlich rohe Header statt des geprueften Principals nutzt.
        scope["headers"] = [
            (k, v)
            for k, v in scope.get("headers", [])
            if k not in _CERT_HEADERS and not k.startswith(b"x-ssl-client-")
        ]
        scope.setdefault("state", {})["principal"] = principal
        await self.app(scope, receive, send)
