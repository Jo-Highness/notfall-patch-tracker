"""Erstinbetriebnahme im Bearbeiter-Bereich (NPT-15).

- ``GET  /einrichtung``  Code-Eingabe (nur solange die Einrichtung offen ist, sonst 404)
- ``POST /einrichtung``  alle Schritte; Feld ``aktion`` waehlt den Schritt

Jeder POST traegt den Einrichtungscode als Formularfeld (wirkt zugleich als
CSRF-Token) und muss den Origin ``PUBLIC_BASE_URL`` haben. Fehlversuche beim Code
zaehlen je Client-IP (429). Nach dem Abschluss antworten alle Routen 404.
Dieses Modul importiert nichts aus dem Admin-Bereich (NPT-02).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from npt import csrf, instance, limits, pki, ratelimit
from npt.config import Settings
from npt.domain import labels
from npt.web.admission import Admission
from npt.web.common import client_ip

log = logging.getLogger("npt.einrichtung")

HEADERS = {"cache-control": "no-store", "referrer-policy": "same-origin"}
FIELD_MAX = 4_000

MESSAGES: dict[str, str] = {
    "code_falsch": "Der Einrichtungscode stimmt nicht.",
    "rate_limit": "Zu viele Fehlversuche – bitte einige Minuten warten.",
    "origin": "Aufruf über eine andere Adresse als PUBLIC_BASE_URL.",
    "seitenname_fehlt": "Bitte einen Seitennamen angeben.",
    "seitenname_zu_lang": f"Der Seitenname ist zu lang (max. {limits.SITE_NAME_MAX} Zeichen).",
    "seitenname_ungueltig": "Der Seitenname enthält Steuerzeichen.",
    "datei_leer": "Bitte eine Datei auswählen.",
    "datei_zu_gross": "Die Datei ist zu groß.",
    "privater_schluessel": (
        "Die Datei enthält einen privaten Schlüssel (oder ist PKCS#12/.p12). Bitte nur das "
        "öffentliche Zertifikat hochladen – private Schlüssel gehören nie auf den Server."
    ),
    "kein_zertifikat": "Die Datei ist kein Zertifikat im Format PEM oder DER.",
    "zu_viele_zertifikate": f"Höchstens {limits.CA_BUNDLE_MAX_CERTS} Zertifikate je Bundle.",
    "keine_ca": (
        "Mindestens ein Zertifikat ist kein CA-Zertifikat (basicConstraints CA:TRUE fehlt)."
    ),
    "ca_abgelaufen": "Mindestens ein CA-Zertifikat ist abgelaufen.",
    "keine_root": (
        "Das Bundle enthält keine selbstsignierte Root-CA. Bitte die Root mit hochladen "
        "(Intermediates allein genügen dem Gateway nicht)."
    ),
    "keine_crl": "Die Datei ist keine Sperrliste (CRL) im Format PEM oder DER.",
    "crl_fremd": "Die Sperrliste ist von keiner CA des Bundles signiert.",
    "crl_abgelaufen": (
        "Die Sperrliste ist abgelaufen (nextUpdate in der Vergangenheit) – das Gateway "
        "würde damit alle Zertifikate ablehnen."
    ),
    "erst_ca": "Bitte zuerst das CA-Bundle hochladen (oder „CA per Datei“ bestätigen).",
    "ist_ca": "Das ist ein CA-Zertifikat – bitte das persönliche Admin-Zertifikat hochladen.",
    "zertifikat_abgelaufen": "Das Admin-Zertifikat ist abgelaufen oder noch nicht gültig.",
    "nicht_von_ca": "Das Admin-Zertifikat wurde nicht von einer CA des Bundles ausgestellt.",
    "kein_identifier": (
        "Aus dem Zertifikat lässt sich kein Identifier der eingestellten Art "
        "(ADMIN_CERT_ID_ATTRIBUTE) ermitteln."
    ),
    "identifier_ungueltig": "Der Identifier bzw. Aussteller-DN ist ungültig.",
    "aussteller_fehlt": (
        "Bitte den Aussteller-DN angeben – oder ausdrücklich „jede CA“ wählen. Ohne Aussteller "
        "gilt der Admin für jede CA des Gateways, auch für später ergänzte."
    ),
    "kette_zu_tief": (
        "Die Zertifikatskette ist länger als ADMIN_VERIFY_DEPTH erlaubt – das Gateway würde "
        "das Zertifikat ablehnen. ADMIN_VERIFY_DEPTH in der .env erhöhen."
    ),
    "zertifikat_gesperrt": "Das Admin-Zertifikat steht auf der Sperrliste.",
    "zertifikat_defekt": "Das Zertifikat ist defekt oder nutzt ein nicht unterstütztes Verfahren.",
    "crl_unvollstaendig": (
        "Für jede CA des Bundles wird eine Sperrliste gebraucht: Mit Sperrliste prüft das "
        "Gateway jede CA der Kette und weist sonst alle Zertifikate ab. Alle CRLs (z. B. Root "
        "und Intermediate) hintereinander in eine PEM-Datei legen."
    ),
    "crl_passt_nicht": (
        "Die hinterlegte Sperrliste passt nicht zu diesem CA-Bundle. Bitte zuerst die "
        "Sperrliste entfernen oder ersetzen."
    ),
    "name_zu_lang": "Der Anzeigename ist zu lang (max. 200 Zeichen).",
    "unvollstaendig": "Es fehlen noch Pflichtangaben.",
    "bereits_eingerichtet": "Die Einrichtung ist bereits abgeschlossen.",
    "existiert_bereits": "Dieser Admin existiert bereits.",
    "aktion_unbekannt": "Unbekannte Aktion.",
}
OK_MESSAGES: dict[str, str] = {
    "seitenname": "Seitenname übernommen.",
    "ca": "CA-Bundle geprüft und übernommen.",
    "ca_extern": "Vermerkt: Die CA wird per Datei (ADMIN_PKI_DIR) bereitgestellt.",
    "crl": "Sperrliste geprüft und übernommen.",
    "crl_entfernen": "Sperrliste entfernt.",
    "admin_zertifikat": "Admin aus dem Zertifikat übernommen.",
    "admin_manuell": "Admin-Identifier übernommen.",
}


class _RejectError(Exception):
    def __init__(self, status: int, code: str, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.retry_after = retry_after


def build_setup_router(  # noqa: PLR0915 - Routen-Registrierung in der Factory
    settings: Settings, templates: Jinja2Templates, admission: Admission, session_factory: Any
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(admission)])
    trusted = settings.trusted_proxy_networks
    kind = settings.ADMIN_CERT_ID_ATTRIBUTE
    code_limit = ratelimit.Limit(
        "einrichtung_code", limits.SETUP_CODE_ATTEMPTS, limits.SETUP_CODE_WINDOW_S
    )

    def not_found(_request: Request) -> Response:
        # Nach Abschluss endgueltig weg: schlichte 404 ohne Hinweis auf den Installer.
        return PlainTextResponse(
            "Nicht gefunden.\n", status_code=404, headers={"cache-control": "no-store"}
        )

    def checklist(db: Session) -> list[dict[str, Any]]:
        row = instance.get_row(db)
        admin_url = (settings.ADMIN_BASE_URL or "").rstrip("/")
        fp = row.gateway_cert_sha256
        return [
            {
                "ok": True,
                "name": "PUBLIC_BASE_URL",
                "text": f"{settings.PUBLIC_BASE_URL} – passt zum Aufruf dieser Seite.",
            },
            {
                "ok": bool(admin_url),
                "name": "ADMIN_BASE_URL",
                "text": (
                    f"{admin_url}/admin/"
                    if admin_url
                    else "leer – der Admin-Bereich prüft den Origin gegen den Host-Header "
                    "des Gateways. Empfohlen: setzen (z. B. https://npt-admin.firma.de:8443)."
                ),
            },
            {
                "ok": True,
                "name": "ADMIN_CERT_ID_ATTRIBUTE",
                "text": f"Admins werden erkannt an: {labels.ID_KIND[kind]}",
            },
            {
                "ok": bool(settings.SMTP_HOST and settings.SMTP_FROM),
                "name": "SMTP",
                "text": (
                    f"{settings.SMTP_HOST}:{settings.SMTP_PORT} ({settings.SMTP_SECURITY})"
                    if settings.SMTP_HOST
                    else "nicht eingerichtet – Mailversand deaktiviert (optional)."
                ),
            },
            {
                "ok": bool(settings.TRUSTED_PROXY_IPS),
                "name": "TRUSTED_PROXY_IPS",
                "text": settings.TRUSTED_PROXY_IPS
                or "leer – hinter einem Reverse Proxy zählt das Rate-Limit alle Nutzer als "
                "eine IP. Empfohlen: Adresse des Proxys eintragen.",
            },
            {
                "ok": True,
                "name": "Gateway-Serverzertifikat",
                "text": (
                    "Ohne eigenes Zertifikat (ADMIN_PKI_DIR) nutzt das Gateway ein "
                    "selbstsigniertes mit SHA-256-Fingerprint "
                    + pki.fingerprint_display(fp)
                    + " – bitte beim ersten Aufruf im Browser vergleichen."
                    if fp
                    else "eigenes Zertifikat per Datei oder selbstsigniertes wird noch erzeugt "
                    "(Jobs-Prozess, wenige Sekunden)."
                ),
            },
        ]

    def view(db: Session) -> dict[str, Any]:
        row = instance.get_row(db)
        d = instance.draft(row)
        ca = [pki.info(c) for c in pki.load_bundle_pem(d.get("ca_pem"))]
        return {
            "d": d,
            "ca": ca,
            "missing": instance.missing(d, kind),
            "kind": kind,
            "kind_label": labels.ID_KIND[kind],
            "checks": checklist(db),
        }

    def page(
        request: Request,
        context: dict[str, Any],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "public/einrichtung.html",
            context,
            status_code=status_code,
            headers={**HEADERS, **(headers or {})},
        )

    def check_origin(request: Request) -> None:
        try:
            csrf.verify_origin(request.headers.get("origin"), settings.PUBLIC_BASE_URL)
        except csrf.CsrfError as exc:
            raise _RejectError(403, "origin") from exc

    def verify_code(db: Session, request: Request, code: str) -> None:
        ip = client_ip(request.scope, trusted) or "unbekannt"
        try:
            # Gesperrt heisst gesperrt: kein Auswerten des Codes, solange das Fenster voll ist.
            ratelimit.peek(db, code_limit, ip)
        except ratelimit.RateLimitedError as exc:
            raise _RejectError(429, "rate_limit", exc.retry_after) from exc
        if instance.check_code(db, code):
            return
        db.rollback()
        try:
            ratelimit.hit(db, code_limit, ip)
        except ratelimit.RateLimitedError as exc:
            raise _RejectError(429, "rate_limit", exc.retry_after) from exc
        raise _RejectError(403, "code_falsch")

    def dispatch(db: Session, action: str, form: dict[str, Any]) -> str | None:
        """Fuehrt den Schritt aus; liefert ``"fertig"`` nach dem Abschluss."""
        if action == "anmelden":
            return None
        if action == "seitenname":
            instance.set_site_name(db, form["site_name"])
        elif action == "ca":
            instance.set_ca(db, form["ca_datei"], external=False)
        elif action == "ca_extern":
            instance.set_ca(db, None, external=True)
        elif action == "crl":
            if not form["crl_datei"]:
                raise instance.SetupError("datei_leer")
            instance.set_crl(db, form["crl_datei"])
        elif action == "crl_entfernen":
            instance.set_crl(db, None)
        elif action == "admin_zertifikat":
            if not form["admin_datei"]:
                raise instance.SetupError("datei_leer")
            instance.set_admin_cert(
                db, kind, form["admin_datei"], form["display_name"], settings.ADMIN_VERIFY_DEPTH
            )
        elif action == "admin_manuell":
            instance.set_admin_manual(
                db,
                kind,
                form["identifier"],
                form["issuer"],
                form["display_name"],
                any_issuer=form["issuer_any"] == "ja",
            )
        elif action == "abschliessen":
            instance.finish(db, kind, form["client_ip"])
            return "fertig"
        else:
            raise instance.SetupError("aktion_unbekannt")
        return None

    def process(request: Request, form: dict[str, Any]) -> Response:
        with session_factory() as db:
            try:
                if not instance.setup_open(db):
                    return not_found(request)
                ctx: dict[str, Any] = {"code": "", "authed": False}
                try:
                    check_origin(request)
                    verify_code(db, request, form["code"])
                except _RejectError as rej:
                    db.rollback()
                    extra = {"retry-after": str(rej.retry_after)} if rej.retry_after else None
                    ctx["msg_err"] = MESSAGES[rej.code]
                    if rej.code == "origin":
                        ctx["origin_hint"] = settings.PUBLIC_BASE_URL
                    return page(request, ctx, rej.status, extra)
                ctx.update({"code": instance.normalize_code(form["code"]), "authed": True})
                action = form["aktion"]
                try:
                    result = dispatch(db, action, form)
                    db.commit()
                except (instance.SetupError, pki.PkiError) as exc:
                    db.rollback()
                    ctx["msg_err"] = MESSAGES.get(exc.code, exc.code)
                    ctx["error_section"] = action
                    ctx.update(view(db))
                    return page(request, ctx, 422)
                except pki.CRYPTO_ERRORS:
                    # Defekte Zertifikate (kaputte Erweiterungen o. ae.): feste Meldung
                    db.rollback()
                    ctx["msg_err"] = MESSAGES["zertifikat_defekt"]
                    ctx["error_section"] = action
                    ctx.update(view(db))
                    return page(request, ctx, 422)
                if result == "fertig":
                    log.info("Erstinbetriebnahme abgeschlossen")
                    admin_url = (settings.ADMIN_BASE_URL or "").rstrip("/")
                    return page(
                        request,
                        {
                            "done": True,
                            "admin_url": admin_url,
                            "fingerprint": instance.get_row(db).gateway_cert_sha256,
                            "fp_display": pki.fingerprint_display,
                        },
                    )
                ctx["msg_ok"] = OK_MESSAGES.get(action)
                ctx.update(view(db))
                return page(request, ctx)
            finally:
                if db.in_transaction():
                    db.rollback()

    @router.get("/einrichtung", response_class=HTMLResponse, include_in_schema=False)
    def setup_get(request: Request) -> Response:
        with session_factory() as db:
            try:
                if not instance.setup_open(db):
                    return not_found(request)
            finally:
                db.rollback()
        return page(request, {"code": "", "authed": False})

    async def read_file(value: Any, max_bytes: int) -> bytes:
        if not isinstance(value, UploadFile):
            return b""
        data = await value.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise pki.PkiError("datei_zu_gross")
        return data

    closed = {"value": False}  # einmal abgeschlossen = fuer immer (je Prozess merken)

    def is_closed() -> bool:
        if closed["value"]:
            return True
        with session_factory() as db:
            try:
                closed["value"] = not instance.setup_open(db)
            finally:
                db.rollback()
        return closed["value"]

    @router.post("/einrichtung", response_class=HTMLResponse, include_in_schema=False)
    async def setup_post(request: Request) -> Response:
        # Vor dem Multipart-Parsen: nach dem Abschluss keine Uploads mehr annehmen.
        if await run_in_threadpool(is_closed):
            return not_found(request)
        try:
            raw = await request.form(max_files=3, max_fields=20)
        except Exception:  # kaputtes Multipart / zu viele Teile
            return page(
                request, {"code": "", "authed": False, "msg_err": "Ungültige Anfrage."}, 400
            )

        def text(name: str) -> str:
            v = raw.get(name)
            return v[:FIELD_MAX] if isinstance(v, str) else ""

        try:
            form: dict[str, Any] = {
                "code": text("code"),
                "aktion": text("aktion") or "anmelden",
                "site_name": text("site_name"),
                "identifier": text("identifier"),
                "issuer": text("issuer"),
                "display_name": text("display_name"),
                "issuer_any": text("issuer_any"),
                "client_ip": client_ip(request.scope, trusted),
                "ca_datei": await read_file(raw.get("ca_datei"), limits.CA_BUNDLE_MAX_BYTES),
                "crl_datei": await read_file(raw.get("crl_datei"), limits.CRL_MAX_BYTES),
                "admin_datei": await read_file(
                    raw.get("admin_datei"), limits.CERT_UPLOAD_MAX_BYTES
                ),
            }
        except pki.PkiError:
            return page(
                request,
                {"code": "", "authed": False, "msg_err": MESSAGES["datei_zu_gross"]},
                413,
            )
        finally:
            await raw.close()
        return await run_in_threadpool(process, request, form)

    return router
