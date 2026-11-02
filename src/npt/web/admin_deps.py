"""Gemeinsame Dependencies und Hilfen der Admin-App (Auth-Ergebnis, CSRF, Rendern).

Wird nur von ``npt.web.admin*`` importiert, nie von der public-App (NPT-02/06).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from npt import admins, csrf
from npt.config import Settings
from npt.db.models import AdminUser
from npt.domain import labels
from npt.identity import CertIdentity
from npt.web.common import direct_peer

log = logging.getLogger("npt.admin")

# Feste Meldungen (keine Reflexion von Nutzereingaben ueber die URL).
MESSAGES_OK: dict[str, str] = {
    "hinzugefuegt": "Admin wurde hinzugefügt.",
    "deaktiviert": "Admin wurde deaktiviert.",
    "aktiviert": "Admin wurde aktiviert.",
    "entfernt": "Admin wurde entfernt.",
    "bestaetigt": "Anfrage wurde bestätigt, der Zugang ist freigeschaltet.",
    "abgelehnt": "Anfrage wurde abgelehnt.",
    "angefragt": "Ihre Anfrage wurde gespeichert. Ein Admin muss sie bestätigen.",
    "bereits_angefragt": "Es liegt bereits eine offene Anfrage für Ihr Zertifikat vor.",
    # Stammdaten (Phase 2)
    "import_uebernommen": "Der Stammdatenimport wurde übernommen.",
    "vorschau_verworfen": "Die Vorschau wurde verworfen.",
    # Projekte (Phase 3)
    "projekt_angelegt": "Das Projekt wurde angelegt. Verteilen Sie jetzt die Projekt-URL.",
    "token_neu": "Neue Projekt-URL erzeugt. Die bisherige URL ist ab sofort ungültig.",
    "status_geaendert": "Der Projektstatus wurde geändert.",
    "projekt_geloescht": "Das Projekt wurde gelöscht.",
    "system_gespeichert": "Die Stammdaten des Systems wurden gespeichert.",
    "system_unveraendert": "Keine Änderung – es wurde nichts gespeichert.",
    # Projekt-Upload (Phase 6)
    "upload_uebernommen": (
        "Der Upload wurde übernommen (vorher Backup „vor Import“). Details im Änderungsprotokoll."
    ),
    "upload_nichts_geaendert": (
        "Upload abgeschlossen – es gab nichts zu übernehmen (keine Neubefüllungen, keine "
        "ausgewählten Konflikte oder anzulegenden Systeme)."
    ),
    # Backups (Phase 7)
    "backup_einstellungen": "Die Backup-Einstellungen wurden gespeichert.",
    "backup_erstellt": "Das Backup wurde erstellt.",
    "restore_ok": (
        "Das Backup wurde wiederhergestellt (vorher Backup „vor Wiederherstellung“). Alle "
        "geöffneten Listen laden neu."
    ),
    # Mailversand (Phase 8)
    "mail_gespeichert": "Die Kampagne wurde gespeichert. Vorschau unten prüfen.",
    "mail_test_gesendet": "Die Testmail wurde versendet.",
    "mail_versand_gestartet": (
        "Der Versand wurde gestartet. Die Mails werden im Hintergrund gedrosselt versendet; "
        "der Status je Empfänger aktualisiert sich automatisch."
    ),
    "mail_versand_abgebrochen": "Die noch ausstehenden Mails wurden abgebrochen.",
    "mail_kampagne_geloescht": "Die Kampagne wurde gelöscht.",
}
MESSAGES_ERR: dict[str, str] = {
    "letzter_admin": "Der letzte aktive Admin kann nicht deaktiviert oder entfernt werden.",
    "existiert_bereits": "Für diesen Identifier existiert bereits ein Admin-Eintrag.",
    "identifier_ungueltig": "Der Identifier bzw. Aussteller-DN ist ungültig.",
    "name_zu_lang": "Der Anzeigename ist zu lang (max. 200 Zeichen).",
    "nicht_gefunden": "Eintrag nicht gefunden.",
    "anfrage_nicht_offen": "Die Anfrage ist nicht mehr offen.",
    "kein_identifier": "Aus Ihrem Zertifikat lässt sich kein Identifier ermitteln.",
    # Stammdaten (Phase 2)
    "vorschau_nicht_gefunden": "Die Vorschau ist abgelaufen oder existiert nicht mehr.",
    "vorschau_veraltet": (
        "Der Stammdatenbestand hat sich seit der Vorschau geändert. Bitte die "
        "aktualisierte Vorschau prüfen und erneut bestätigen."
    ),
    "vorschau_hat_fehler": "Die Vorschau enthält Fehler; eine Übernahme ist nicht möglich.",
    # Projekte (Phase 3)
    "projektname_fehlt": "Bitte einen Projektnamen angeben.",
    "projektname_zu_lang": "Der Projektname ist zu lang (max. 200 Zeichen).",
    "beschreibung_zu_lang": "Die Beschreibung ist zu lang (max. 2000 Zeichen).",
    "status_ungueltig": "Dieser Statuswechsel ist nicht möglich.",
    "name_bestaetigung_falsch": (
        "Der eingegebene Projektname stimmt nicht überein – nichts gelöscht."
    ),
    "projekt_nicht_gefunden": "Projekt nicht gefunden.",
    "system_nicht_gefunden": "System nicht gefunden.",
    "system_konflikt": (
        "Das System wurde zwischenzeitlich geändert. Bitte die aktuellen Werte prüfen und "
        "erneut speichern."
    ),
    "schreibgeschuetzt": (
        "Das Projekt ist abgeschlossen oder archiviert und daher schreibgeschützt."
    ),
    "gesperrt": "Der Datensatz ist gerade gesperrt. Bitte erneut versuchen.",
    # Projekt-Upload (Phase 6)
    "upload_nicht_gefunden": "Die Upload-Vorschau ist abgelaufen oder existiert nicht mehr.",
    "upload_hat_fehler": "Die Datei enthält Fehler; eine Übernahme ist nicht möglich.",
    "upload_vorschau_veraltet": (
        "Betroffene Felder wurden seit der Vorschau geändert. Bitte die aktualisierte Vorschau "
        "prüfen und erneut bestätigen."
    ),
    # Backups (Phase 7)
    "backup_intervall_ungueltig": "Ungültiges Backup-Intervall.",
    "backup_aufbewahrung_ungueltig": "Die Aufbewahrung muss zwischen 1 und 10000 liegen.",
    "backup_nicht_gefunden": "Backup nicht gefunden.",
    "backup_datei_fehlt": "Die Backup-Datei ist im Backup-Verzeichnis nicht (mehr) vorhanden.",
    "backup_ungueltig": "Die Backup-Datei ist beschädigt oder passt nicht zu diesem Projekt.",
    "restore_unbestaetigt": "Bitte die Wiederherstellung ausdrücklich bestätigen.",
    # Mailversand (Phase 8)
    "mail_betreff_fehlt": "Bitte einen Betreff angeben.",
    "mail_betreff_zu_lang": "Der Betreff ist zu lang (max. 200 Zeichen).",
    "mail_text_fehlt": "Bitte einen Mailtext eingeben.",
    "mail_text_zu_lang": "Der Mailtext ist zu lang.",
    "mail_selektor_ungueltig": "Bitte eine gültige Zielgruppe wählen.",
    "mail_filter_zu_lang": "Der eingefügte Filter ist zu lang.",
    "mail_kampagne_nicht_gefunden": "Kampagne nicht gefunden.",
    "mail_unbestaetigt": "Bitte den Versand ausdrücklich bestätigen.",
    "mail_bereits_gestartet": (
        "Dieser Versand wurde bereits gestartet (z. B. doppelt abgeschickt). Es wurde kein "
        "weiterer Versand ausgelöst – Status unten prüfen."
    ),
    "mail_versand_laeuft": "Es läuft noch ein Versand dieser Kampagne.",
    "mail_keine_empfaenger": "Die Zielgruppe enthält keine gültigen Empfänger.",
    "mail_empfaenger_geaendert": (
        "Die Zahl der Empfänger hat sich seit der Vorschau geändert. Bitte die aktualisierte "
        "Vorschau prüfen und erneut bestätigen."
    ),
    "mail_kein_lauf": "Es gibt noch keinen Versand, der wiederholt werden könnte.",
    "mail_modus_ungueltig": "Ungültige Versandart.",
    "mail_test_adresse_ungueltig": "Bitte eine gültige Adresse für die Testmail angeben.",
    "mail_test_fehlgeschlagen": (
        "Die Testmail konnte nicht versendet werden (Details im Audit-Log und im Server-Log)."
    ),
    "smtp_nicht_konfiguriert": "Der Mailversand ist nicht konfiguriert (SMTP_HOST fehlt).",
    "smtp_absender_fehlt": "Der Mailversand ist nicht konfiguriert (SMTP_FROM fehlt).",
    "smtp_absender_ungueltig": "SMTP_FROM bzw. SMTP_REPLY_TO ist keine gültige Adresse.",
}

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Zuschlag auf UPLOAD_MAX_MB fuer Multipart-Overhead und weitere Formularfelder.
UPLOAD_OVERHEAD_BYTES = 1024 * 1024
FORM_MAX_FIELDS = 100_000


class NotAdminError(Exception):
    """Gueltiges Zertifikat, aber keine (aktive) Admin-Berechtigung."""


class CsrfRejectedError(Exception):
    pass


class BodyTooLargeError(Exception):
    pass


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db(request: Request) -> Any:
    """Session je Request; die Factory ist austauschbar (Tests)."""
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


DbDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def get_principal(request: Request) -> CertIdentity:
    principal = request.scope.get("state", {}).get("principal")
    if not isinstance(principal, CertIdentity):  # Middleware fehlt -> niemals durchlassen
        raise StarletteHTTPException(status_code=403)
    return principal


PrincipalDep = Annotated[CertIdentity, Depends(get_principal)]


def csrf_scope_for(principal: CertIdentity) -> str:
    return csrf.admin_scope(principal.issuer_norm, principal.kind, principal.primary)


def expected_origin(request: Request, settings: Settings) -> str:
    if settings.ADMIN_BASE_URL:
        return settings.ADMIN_BASE_URL
    # Ohne Konfiguration: vom Gateway gesetzte (dort ueberschriebene) Forwarded-Header.
    proto = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"{proto}://{host}"


async def require_csrf(request: Request, principal: PrincipalDep, settings: SettingsDep) -> None:
    """CSRF fuer alle zustandsaendernden Requests (Router-weite Dependency)."""
    if request.method not in UNSAFE_METHODS:
        return
    token = request.headers.get("x-csrf-token")
    if token is None:
        ctype = request.headers.get("content-type", "")
        if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            # Groesse vor dem Parsen pruefen (Uploads landen sonst erst im tmpfs).
            length = request.headers.get("content-length", "")
            limit = settings.UPLOAD_MAX_MB * 1024 * 1024 + UPLOAD_OVERHEAD_BYTES
            if length.isdigit() and int(length) > limit:
                raise BodyTooLargeError
            # Viele Felder z. B. bei der Auswahl fehlender Systeme bzw. von Upload-
            # Konflikten (Checkbox je System/Feld).
            form = await request.form(max_fields=FORM_MAX_FIELDS)
            value = form.get("csrf_token")
            token = value if isinstance(value, str) else None
    try:
        csrf.verify_origin(request.headers.get("origin"), expected_origin(request, settings))
        csrf.verify_token(
            settings.CSRF_SECRET.get_secret_value().encode(), csrf_scope_for(principal), token
        )
    except csrf.CsrfError as exc:
        log.warning("CSRF abgelehnt", extra={"reason": str(exc), "path": request.url.path})
        raise CsrfRejectedError(str(exc)) from exc


def require_admin(principal: PrincipalDep, db: DbDep) -> AdminUser:
    admin = admins.find_active_admin(db, principal)
    if admin is None:
        raise NotAdminError
    return admin


AdminDep = Annotated[AdminUser, Depends(require_admin)]


def actor_of(request: Request, principal: CertIdentity) -> admins.Actor:
    # Client-IP: die vom Gateway gesetzte (dort ueberschriebene) X-Forwarded-For,
    # direkter Peer waere immer das Gateway.
    xff = request.headers.get("x-forwarded-for")
    ip = xff.split(",")[0].strip() if xff else direct_peer(request.scope)
    return admins.Actor.from_identity(principal, ip)


def redirect(path: str, **params: str) -> RedirectResponse:
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"{path}{query}", status_code=303)


@dataclass
class Renderer:
    templates: Jinja2Templates
    secret: bytes

    def __call__(
        self,
        request: Request,
        name: str,
        principal: CertIdentity,
        context: dict[str, Any] | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        ctx: dict[str, Any] = {
            "principal": principal,
            "csrf_token": csrf.issue_token(self.secret, csrf_scope_for(principal)),
            "id_kind_label": labels.ID_KIND[principal.kind],
            "msg_ok": MESSAGES_OK.get(request.query_params.get("ok", "")),
            "msg_err": MESSAGES_ERR.get(request.query_params.get("fehler", "")),
            "labels": labels,
        }
        ctx.update(context or {})
        return self.templates.TemplateResponse(request, name, ctx, status_code=status_code)
