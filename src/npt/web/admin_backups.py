"""Admin: Backups eines Projekts (Auftrag 6.2) – Einstellungen, manuell, Download, Restore."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

from npt import backups, editing, export, projects, restore
from npt.config import Settings
from npt.web.admin_deps import (
    AdminDep,
    DbDep,
    PrincipalDep,
    Renderer,
    actor_of,
    redirect,
    require_csrf,
)
from npt.web.admin_projects import write_error_code

log = logging.getLogger("npt.admin.backups")


def build_router(render: Renderer, settings: Settings) -> APIRouter:  # noqa: PLR0915
    router = APIRouter(prefix="/admin/projekte", dependencies=[Depends(require_csrf)])
    backup_dir = Path(settings.BACKUP_DIR)

    def detail(pid: int) -> str:
        return f"/admin/projekte/{pid}"

    @router.post("/{pid}/backups/einstellungen")
    def save_settings(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        interval: Annotated[int, Form()],
        retention: Annotated[int, Form()],
    ) -> Response:
        try:
            obsolete = backups.update_settings(
                db, actor_of(request, principal), pid, interval, retention, backup_dir=backup_dir
            )
            db.commit()
        except backups.BackupError as exc:
            db.rollback()
            target = "/admin/" if exc.code == "projekt_nicht_gefunden" else detail(pid)
            return redirect(target, fehler=exc.code)
        backups.remove_files(obsolete)
        return redirect(detail(pid), ok="backup_einstellungen")

    @router.post("/{pid}/backups/erstellen")
    async def create(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int
    ) -> Response:
        def work() -> Response:
            try:
                _, obsolete = backups.manual_backup(
                    db, actor_of(request, principal), pid, backup_dir=backup_dir
                )
                db.commit()
            except backups.BackupError as exc:
                db.rollback()
                return redirect("/admin/", fehler=exc.code)
            finally:
                if db.in_transaction():
                    db.rollback()
            backups.remove_files(obsolete)
            return redirect(detail(pid), ok="backup_erstellt")

        return await run_in_threadpool(work)

    @router.get("/{pid}/backups/{bid}/datei.{fmt}")
    def download(
        admin: AdminDep, db: DbDep, pid: int, bid: int, fmt: Literal["json", "xlsx"]
    ) -> Response:
        try:
            project = projects.get_project(db, pid)
            backup = backups.get_backup(db, pid, bid)
            path = backups.file_of(backup_dir, backup, fmt)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)
        except backups.BackupError as exc:
            return redirect(detail(pid), fehler=exc.code)
        name = export.download_name(
            project.name, f"backup-{backup.id}-{backup.trigger}", backup.created_at, fmt
        )
        media = "application/json" if fmt == "json" else export.XLSX_MIME
        return FileResponse(path, media_type=media, headers=export.attachment_headers(name))

    @router.get("/{pid}/backups/{bid}/wiederherstellen", response_class=HTMLResponse)
    def restore_confirm(
        request: Request, principal: PrincipalDep, admin: AdminDep, db: DbDep, pid: int, bid: int
    ) -> Response:
        try:
            summary = projects.get_summary(db, pid)
            backup = backups.get_backup(db, pid, bid)
        except projects.ProjectError as exc:
            return redirect("/admin/", fehler=exc.code)
        except backups.BackupError as exc:
            return redirect(detail(pid), fehler=exc.code)
        return render(
            request,
            "admin/backup_restore.html",
            principal,
            {"admin": admin, "p": summary.project, "s": summary, "b": backup},
        )

    @router.post("/{pid}/backups/{bid}/wiederherstellen")
    def restore_apply(
        request: Request,
        principal: PrincipalDep,
        admin: AdminDep,
        db: DbDep,
        pid: int,
        bid: int,
        confirm: Annotated[str, Form(max_length=10)] = "",
    ) -> Response:
        if confirm != "ja":
            return redirect(
                f"{detail(pid)}/backups/{bid}/wiederherstellen", fehler="restore_unbestaetigt"
            )
        try:
            result = restore.restore(
                db, actor_of(request, principal), pid, bid, backup_dir=backup_dir
            )
            db.commit()
        except backups.BackupError as exc:
            db.rollback()
            return redirect(detail(pid), fehler=exc.code)
        except editing.WriteError as exc:
            db.rollback()
            return redirect(detail(pid), fehler=write_error_code(exc))
        finally:
            if db.in_transaction():
                db.rollback()
        backups.remove_files(result.obsolete_files)
        log.info(
            "Backup wiederhergestellt",
            extra={"project_id": pid, "backup_id": bid, "seq": result.seq},
        )
        return redirect(detail(pid), ok="restore_ok")

    return router
