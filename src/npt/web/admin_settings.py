"""Admin: Einstellungen der Instanz (NPT-15).

- ``GET  /admin/einstellungen``               Uebersicht
- ``POST /admin/einstellungen/seitenname``    Seitenname aendern
- ``POST /admin/einstellungen/ca``            Web-CA ersetzen (Aussperrschutz)
- ``POST /admin/einstellungen/ca/entfernen``  Web-CA entfernen (nur mit Bestaetigung)
- ``POST /admin/einstellungen/crl``           Sperrliste ersetzen
- ``POST /admin/einstellungen/crl/entfernen`` Sperrliste entfernen

Alle Schreibzugriffe mit CSRF (Router-Dependency) und Audit-Eintrag in derselben
Transaktion. Der Jobs-Prozess schreibt geaenderte Vertrauensanker nach TRUST_DIR, das
Gateway laedt sie ohne Neustart.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from npt import instance, limits, pki
from npt.config import Settings
from npt.db.models import AdminUser
from npt.identity import CertIdentity
from npt.web.admin_deps import (
    AdminDep,
    DbDep,
    PrincipalDep,
    Renderer,
    actor_of,
    redirect,
    require_csrf,
)
from npt.web.public_setup import MESSAGES as SETUP_MESSAGES

BASE = "/admin/einstellungen"
OK = {
    "seitenname": "Seitenname gespeichert.",
    "ca": "CA-Bundle gespeichert – das Gateway übernimmt es innerhalb von etwa einer Minute.",
    "ca_entfernt": "Web-CA entfernt – das Gateway übernimmt das innerhalb von etwa einer Minute.",
    "crl": "Sperrliste gespeichert.",
    "crl_entfernt": "Sperrliste entfernt.",
}
ERRORS = {
    **SETUP_MESSAGES,
    "aussperrung_crl": (
        "Mit dieser Sperrliste würde das Gateway Ihr eigenes Admin-Zertifikat über die Web-CA "
        "nicht mehr akzeptieren (gesperrt oder CRL unvollständig). Speichern nur mit Bestätigung."
    ),
    "aussperrung": (
        "Ihr eigenes Admin-Zertifikat wird von dieser CA nicht abgedeckt. Speichern nur mit "
        "der Bestätigung, dass eine per Datei bereitgestellte CA (ADMIN_PKI_DIR) Ihr "
        "Zertifikat weiterhin abdeckt – sonst sperren Sie sich aus."
    ),
}


def build_router(
    render: Renderer, settings: Settings, site_name: instance.SiteNameCache
) -> APIRouter:
    router = APIRouter(prefix=BASE, dependencies=[Depends(require_csrf)])

    def page(
        request: Request,
        principal: CertIdentity,
        admin: AdminUser,
        db: Session,
        *,
        error: str | None = None,
        status_code: int = 200,
    ) -> Response:
        row = instance.get_row(db)
        ca = [pki.info(c) for c in pki.load_bundle_pem(row.admin_ca_pem)]
        crls = pki.load_crls_pem(row.admin_crl_pem)
        own = request.scope.get("state", {}).get("principal_cert")
        covered = instance.own_cert_accepted(
            own, pki.load_bundle_pem(row.admin_ca_pem), crls, settings.ADMIN_VERIFY_DEPTH
        )
        ctx: dict[str, Any] = {
            "admin": admin,
            "row": row,
            "ca": ca,
            "crls": crls,
            "own_covered": covered,
            "fp_display": pki.fingerprint_display,
            "ok_text": OK.get(request.query_params.get("ok", "")),
            "form_error": ERRORS.get(error or "", error) if error else None,
        }
        return render(request, "admin/einstellungen.html", principal, ctx, status_code)

    @router.get("")
    def show(request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep) -> Response:
        return page(request, principal, admin, db)

    def run(
        request: Request,
        principal: CertIdentity,
        admin: AdminUser,
        db: Session,
        ok: str,
        action: Any,
    ) -> Response:
        try:
            action(actor_of(request, principal))
            db.commit()
        except (instance.SetupError, pki.PkiError) as exc:
            db.rollback()
            return page(request, principal, admin, db, error=exc.code, status_code=422)
        except pki.CRYPTO_ERRORS:
            db.rollback()
            return page(request, principal, admin, db, error="zertifikat_defekt", status_code=422)
        site_name.invalidate()
        return redirect(BASE, ok=ok)

    @router.post("/seitenname")
    def save_name(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        site_name_value: Annotated[str, Form(alias="site_name", max_length=1000)] = "",
    ) -> Response:
        return run(
            request,
            principal,
            admin,
            db,
            "seitenname",
            lambda actor: instance.update_site_name(db, actor, site_name_value),
        )

    async def read(upload: UploadFile, max_bytes: int) -> bytes:
        data = await upload.read(max_bytes + 1)
        await upload.close()
        if len(data) > max_bytes:
            raise pki.PkiError("datei_zu_gross")
        return data

    @router.post("/ca")
    async def save_ca(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        datei: UploadFile = File(),  # noqa: B008 - FastAPI-Konvention
        bestaetigung: Annotated[str, Form(max_length=10)] = "",
    ) -> Response:
        try:
            data = await read(datei, limits.CA_BUNDLE_MAX_BYTES)
        except pki.PkiError as exc:
            return await run_in_threadpool(
                page, request, principal, admin, db, error=exc.code, status_code=413
            )
        if not data.strip():
            return await run_in_threadpool(
                page, request, principal, admin, db, error="datei_leer", status_code=422
            )
        own = request.scope.get("state", {}).get("principal_cert")
        return await run_in_threadpool(
            run,
            request,
            principal,
            admin,
            db,
            "ca",
            lambda actor: instance.replace_ca(
                db,
                actor,
                data,
                own,
                confirm_lockout=bestaetigung == "ja",
                max_depth=settings.ADMIN_VERIFY_DEPTH,
            ),
        )

    @router.post("/ca/entfernen")
    def remove_ca(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        bestaetigung: Annotated[str, Form(max_length=10)] = "",
    ) -> Response:
        return run(
            request,
            principal,
            admin,
            db,
            "ca_entfernt",
            lambda actor: instance.replace_ca(
                db,
                actor,
                None,
                None,
                confirm_lockout=bestaetigung == "ja",
                max_depth=settings.ADMIN_VERIFY_DEPTH,
            ),
        )

    @router.post("/crl")
    async def save_crl(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        datei: UploadFile = File(),  # noqa: B008 - FastAPI-Konvention
        bestaetigung: Annotated[str, Form(max_length=10)] = "",
    ) -> Response:
        try:
            data = await read(datei, limits.CRL_MAX_BYTES)
        except pki.PkiError as exc:
            return await run_in_threadpool(
                page, request, principal, admin, db, error=exc.code, status_code=413
            )
        if not data.strip():
            return await run_in_threadpool(
                page, request, principal, admin, db, error="datei_leer", status_code=422
            )
        return await run_in_threadpool(
            run,
            request,
            principal,
            admin,
            db,
            "crl",
            lambda actor: instance.replace_crl(
                db,
                actor,
                data,
                request.scope.get("state", {}).get("principal_cert"),
                confirm_lockout=bestaetigung == "ja",
                max_depth=settings.ADMIN_VERIFY_DEPTH,
            ),
        )

    @router.post("/crl/entfernen")
    def remove_crl(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep
    ) -> Response:
        return run(
            request,
            principal,
            admin,
            db,
            "crl_entfernt",
            lambda actor: instance.replace_crl(db, actor, None),
        )

    return router
