"""Supervisor ``npt-run`` (NPT-02), nur Standardbibliothek + App-Module.

Ablauf: Grenzen pruefen -> Migration und Admin-Bootstrap unter Advisory Lock ->
Start der Prozesse je ``APP_ROLE`` (``all`` oder Komma-Liste, z. B. ``admin,jobs``):

- public: ``gunicorn npt.web.public:app`` (WEB_WORKERS, :8000)
- admin:  ``gunicorn npt.web.admin:app``  (ADMIN_WORKERS, nur ADMIN_BIND_ADDR:8001)
- jobs:   ``python -m npt.jobs``

Endet ein Kind, beendet der Supervisor alle anderen und den Container mit
Exitcode ungleich 0 (Docker-Restart-Policy uebernimmt). SIGTERM/SIGINT werden
an alle Kinder weitergereicht. PID 1 ist tini.

Umgebung (NPT-12 K2, nicht in ``Settings``, weil nur der Supervisor sie liest):

- ``NPT_MIGRATE_ONLY=1``: nur Migration + Bootstrap, dann Ende (initContainer).
- ``NPT_SKIP_MIGRATE=1``: Migration + Bootstrap ueberspringen (Hauptcontainer neben
  dem initContainer); Grenzen- und Secret-Pruefung laufen trotzdem.
- ``NPT_SCHEMA_NEWER=fail|warn``: Verhalten bei unbekannter Datenbank-Revision
  (Default ``fail``, ``npt.migrate``).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from types import FrameType
from typing import TYPE_CHECKING

from pydantic import ValidationError

from npt import limits
from npt.config import Settings, get_settings
from npt.logging_setup import configure_logging

if TYPE_CHECKING:
    from npt.migrate import SchemaNewer

log = logging.getLogger("npt.run")


def child_commands(settings: Settings) -> dict[str, list[str]]:
    py = sys.executable
    gunicorn = [py, "-m", "gunicorn", "-c", "python:npt.gunicorn_conf"]
    cmds: dict[str, list[str]] = {}
    roles = settings.roles
    if "public" in roles:
        cmds["public"] = [
            *gunicorn,
            "--name",
            "npt-public",
            "--bind",
            f"0.0.0.0:{settings.PUBLIC_LISTEN_PORT}",
            "--workers",
            str(settings.WEB_WORKERS),
            "npt.web.public:app",
        ]
    if "admin" in roles:
        cmds["admin"] = [
            *gunicorn,
            "--name",
            "npt-admin",
            "--bind",
            f"{settings.ADMIN_BIND_ADDR}:{settings.ADMIN_LISTEN_PORT}",
            "--workers",
            str(settings.ADMIN_WORKERS),
            "npt.web.admin:app",
        ]
    if "jobs" in roles:
        cmds["jobs"] = [py, "-m", "npt.jobs"]
    return cmds


def env_flag(name: str) -> bool:
    return os.environ.get(name) == "1"


def schema_newer_mode() -> SchemaNewer:
    mode = os.environ.get("NPT_SCHEMA_NEWER", "fail") or "fail"
    if mode == "fail":
        return "fail"
    if mode == "warn":
        return "warn"
    raise RuntimeError("NPT_SCHEMA_NEWER muss fail oder warn sein")


def prepare_database(settings: Settings, schema_newer: SchemaNewer = "fail") -> None:
    from sqlalchemy import Connection
    from sqlalchemy.orm import Session

    from npt import admins
    from npt.db.session import make_engine
    from npt.migrate import upgrade_head, wait_for_db

    def bootstrap(conn: Connection) -> None:
        # Im Migrations-Lock: gleichzeitig startende Pods legen Admins genau einmal an.
        with Session(bind=conn) as session:
            created = admins.bootstrap_admins(
                session, settings.ADMIN_CERT_ID_ATTRIBUTE, settings.bootstrap_ids
            )
            session.commit()
        log.info("Admin-Bootstrap abgeschlossen", extra={"neu": created})

    engine = make_engine(pool_size=1)
    try:
        wait_for_db(engine)
        upgrade_head(engine, bootstrap=bootstrap, schema_newer=schema_newer)
    finally:
        engine.dispose()


class Supervisor:
    def __init__(self, commands: dict[str, list[str]]) -> None:
        self.commands = commands
        self.procs: dict[str, subprocess.Popen[bytes]] = {}
        self.stopping = False

    def _on_signal(self, signum: int, _frame: FrameType | None) -> None:
        log.info("Signal empfangen, beende Kinder", extra={"signal": signum})
        self.stopping = True

    def start(self) -> None:
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        for name, cmd in self.commands.items():
            self.procs[name] = subprocess.Popen(cmd)  # noqa: S603 - feste Kommandos
            log.info("Prozess gestartet", extra={"proc": name, "pid": self.procs[name].pid})

    def wait(self) -> int:
        exit_code = 0
        while not self.stopping:
            for name, proc in self.procs.items():
                rc = proc.poll()
                if rc is not None:
                    log.error(
                        "Kindprozess beendet, Container stoppt", extra={"proc": name, "rc": rc}
                    )
                    exit_code = rc or 1
                    self.stopping = True
                    break
            time.sleep(0.5)
        self.terminate_all()
        return exit_code

    def terminate_all(self) -> None:
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
        deadline = time.monotonic() + limits.SUPERVISOR_STOP_TIMEOUT_S
        for name, proc in self.procs.items():
            remaining = max(0.1, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                log.warning("Prozess reagiert nicht, kill", extra={"proc": name})
                proc.kill()
                proc.wait()


def load_settings() -> Settings | None:
    """Settings laden; bei Fehler eine JSON-Zeile mit Feld und Fehlerart (ohne Werte)."""
    try:
        return get_settings()
    except ValidationError as exc:
        # Nur Feldname und Fehlerart: Meldungstexte eigener Validatoren koennen Werte
        # enthalten, Eingaben (DATABASE_URL, Secrets) gehoeren nie ins Log.
        fehler = [
            {"feld": ".".join(str(p) for p in err["loc"]) or "-", "art": err["type"]}
            for err in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
        log.error("Start abgebrochen: ungueltige Einstellungen", extra={"fehler": fehler})
    except Exception as exc:  # Ausnahmetext koennte Werte enthalten -> nur der Typ
        log.error(
            "Start abgebrochen: Einstellungen nicht lesbar", extra={"error": type(exc).__name__}
        )
    return None


def main() -> None:
    # Vorlaeufiges Logging, damit auch ein Settings-Fehler als JSON-Zeile erscheint.
    configure_logging("INFO")
    settings = load_settings()
    if settings is None:
        sys.exit(2)
    try:
        configure_logging(settings.LOG_LEVEL)
    except ValueError:  # unbekannte Stufe: dictConfig ist halb fertig -> neu aufsetzen
        configure_logging("INFO")
        log.error(
            "Start abgebrochen: ungueltige Einstellungen",
            extra={"fehler": [{"feld": "LOG_LEVEL", "art": "value_error"}]},
        )
        sys.exit(2)
    migrate_only = env_flag("NPT_MIGRATE_ONLY")
    try:
        limits.check_ranking()
        settings.require_runtime_secrets()
        if migrate_only and env_flag("NPT_SKIP_MIGRATE"):
            raise RuntimeError("NPT_MIGRATE_ONLY und NPT_SKIP_MIGRATE schliessen sich aus")
        if env_flag("NPT_SKIP_MIGRATE"):
            log.info("Migration uebersprungen (NPT_SKIP_MIGRATE=1)")
        else:
            prepare_database(settings, schema_newer_mode())
    except Exception:
        log.exception("Start abgebrochen")
        sys.exit(2)
    if migrate_only:
        return
    sup = Supervisor(child_commands(settings))
    sup.start()
    sys.exit(sup.wait())


if __name__ == "__main__":
    main()
