"""Kubernetes-Laufzeit ohne Datenbank (NPT-12): APP_ROLE, Start-Flags, DB-URL, Probe."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from npt import jobs, limits, probe, run
from npt.config import parse_roles
from npt.logging_setup import JsonFormatter
from tests.conftest import make_settings


# ------------------------------------------------------------------ C1 APP_ROLE
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("all", {"public", "admin", "jobs"}),
        ("public", {"public"}),
        ("admin,jobs", {"admin", "jobs"}),
        (" Admin , JOBS ", {"admin", "jobs"}),
        ("jobs,admin,jobs", {"admin", "jobs"}),
        ("public,admin,jobs", {"public", "admin", "jobs"}),
    ],
)
def test_parse_roles_valid(raw: str, expected: set[str]) -> None:
    assert parse_roles(raw) == expected


@pytest.mark.parametrize("raw", ["", "all,public", "public,", "web", "admin;jobs", "admin,,jobs"])
def test_parse_roles_invalid(raw: str) -> None:
    with pytest.raises(ValueError, match="APP_ROLE"):
        parse_roles(raw)


def test_settings_reject_invalid_role() -> None:
    with pytest.raises(ValidationError):
        make_settings(APP_ROLE="all,jobs")


def test_child_commands_for_role_list() -> None:
    cmds = run.child_commands(make_settings(APP_ROLE="admin,jobs", ADMIN_BIND_ADDR="127.0.0.1"))
    assert set(cmds) == {"admin", "jobs"}
    assert "127.0.0.1:8001" in cmds["admin"]
    assert set(run.child_commands(make_settings(APP_ROLE="public"))) == {"public"}


# ------------------------------------------------------- C2 NPT_SKIP_MIGRATE
class FakeSupervisor:
    started = False

    def __init__(self, commands: dict[str, list[str]]) -> None:
        self.commands = commands

    def start(self) -> None:
        FakeSupervisor.started = True

    def wait(self) -> int:
        return 0


@pytest.fixture
def fake_start(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"prepare": [], "ranking": 0}
    for name in ("NPT_SKIP_MIGRATE", "NPT_MIGRATE_ONLY", "NPT_SCHEMA_NEWER"):
        monkeypatch.delenv(name, raising=False)

    def ranking() -> None:
        calls["ranking"] += 1

    monkeypatch.setattr(limits, "check_ranking", ranking)
    monkeypatch.setattr(run, "configure_logging", lambda _level: None)
    monkeypatch.setattr(run, "Supervisor", FakeSupervisor)
    monkeypatch.setattr(
        run, "prepare_database", lambda _s, mode="fail": calls["prepare"].append(mode)
    )
    FakeSupervisor.started = False
    return calls


def _main(monkeypatch: pytest.MonkeyPatch, **settings: Any) -> int:
    monkeypatch.setattr(run, "get_settings", lambda: make_settings(**settings))
    with pytest.raises(SystemExit) as exc:
        run.main()
    return int(exc.value.code or 0)


def test_default_start_migrates_with_fail_mode(
    monkeypatch: pytest.MonkeyPatch, fake_start: dict[str, Any]
) -> None:
    assert _main(monkeypatch) == 0
    assert fake_start["prepare"] == ["fail"]
    assert FakeSupervisor.started


def test_skip_migrate_skips_prepare_but_checks_limits(
    monkeypatch: pytest.MonkeyPatch, fake_start: dict[str, Any]
) -> None:
    monkeypatch.setenv("NPT_SKIP_MIGRATE", "1")
    assert _main(monkeypatch) == 0
    assert fake_start["prepare"] == []
    assert fake_start["ranking"] == 1
    assert FakeSupervisor.started


def test_skip_migrate_still_requires_secrets(
    monkeypatch: pytest.MonkeyPatch, fake_start: dict[str, Any]
) -> None:
    monkeypatch.setenv("NPT_SKIP_MIGRATE", "1")
    assert _main(monkeypatch, CSRF_SECRET="kurz") == 2
    assert not FakeSupervisor.started


def test_migrate_only_and_skip_are_exclusive(
    monkeypatch: pytest.MonkeyPatch, fake_start: dict[str, Any]
) -> None:
    monkeypatch.setenv("NPT_SKIP_MIGRATE", "1")
    monkeypatch.setenv("NPT_MIGRATE_ONLY", "1")
    assert _main(monkeypatch) == 2
    assert fake_start["prepare"] == []


def test_migrate_only_returns_without_supervisor(
    monkeypatch: pytest.MonkeyPatch, fake_start: dict[str, Any]
) -> None:
    monkeypatch.setenv("NPT_MIGRATE_ONLY", "1")
    monkeypatch.setenv("NPT_SCHEMA_NEWER", "warn")
    monkeypatch.setattr(run, "get_settings", make_settings)
    run.main()
    assert fake_start["prepare"] == ["warn"]
    assert not FakeSupervisor.started


def test_schema_newer_mode_rejects_unknown(
    monkeypatch: pytest.MonkeyPatch, fake_start: dict[str, Any]
) -> None:
    monkeypatch.setenv("NPT_SCHEMA_NEWER", "ignore")
    assert _main(monkeypatch) == 2
    assert fake_start["prepare"] == []


# ------------------------------------------------------------ C4 Datenbank-URL
@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg"])
def test_database_url_driver_normalized(scheme: str) -> None:
    s = make_settings(DATABASE_URL=f"{scheme}://app:p%40ss%2Fw@db.example.org:5433/npt")
    assert s.sqlalchemy_url == "postgresql+psycopg://app:p%40ss%2Fw@db.example.org:5433/npt"


def test_database_url_ssl_params_added_and_override() -> None:
    s = make_settings(
        DATABASE_URL="postgresql://app:pw@db.example.org/npt?sslmode=prefer&application_name=x",
        POSTGRES_SSLMODE="verify-full",
        POSTGRES_SSLROOTCERT="/etc/npt-db/ca.crt",
    )
    url = s.sqlalchemy_url
    assert url.startswith("postgresql+psycopg://app:pw@db.example.org/npt?")
    assert "sslmode=verify-full" in url
    assert "sslmode=prefer" not in url
    assert "application_name=x" in url
    assert "sslrootcert=%2Fetc%2Fnpt-db%2Fca.crt" in url or "sslrootcert=/etc/npt-db/ca.crt" in url


def test_postgres_fields_with_ssl_params() -> None:
    s = make_settings(
        DATABASE_URL="",
        POSTGRES_HOST="db.example.org",
        POSTGRES_PASSWORD="geh eim",
        POSTGRES_SSLMODE="require",
        POSTGRES_SSLCERT="/c/tls.crt",
        POSTGRES_SSLKEY="/c/tls.key",
    )
    from sqlalchemy.engine import make_url

    url = make_url(s.sqlalchemy_url)
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "geh eim"
    assert url.query == {"sslmode": "require", "sslcert": "/c/tls.crt", "sslkey": "/c/tls.key"}


def test_postgres_fields_without_ssl_unchanged() -> None:
    s = make_settings(DATABASE_URL="", POSTGRES_PASSWORD="pw")
    assert s.sqlalchemy_url == "postgresql+psycopg://npt:pw@db:5432/npt"


def test_postgres_password_special_chars_roundtrip() -> None:
    from sqlalchemy.engine import make_url

    pw = "a b+c@d/e:f%g?h#i"
    url = make_url(make_settings(DATABASE_URL="", POSTGRES_PASSWORD=pw).sqlalchemy_url)
    assert url.password == pw
    assert url.host == "db"


def test_invalid_sslmode_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(POSTGRES_SSLMODE="verify")


def test_invalid_database_url_hides_value() -> None:
    with pytest.raises(ValidationError) as exc:
        make_settings(DATABASE_URL="kein-url-geheimnis123")
    assert "geheimnis123" not in str(exc.value)


# --------------------------------------------------------- C5 Heartbeat/Probe
def test_run_forever_touches_heartbeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hb = tmp_path / "hb"
    stop = threading.Event()
    seen: list[bool] = []

    def job() -> None:
        seen.append(hb.exists())
        stop.set()

    monkeypatch.setattr(jobs, "JOBS", [("probe", job)])
    assert jobs.run_forever(stop, hb) == 0
    assert seen == [True]
    assert time.time() - hb.stat().st_mtime < 5


def test_run_forever_without_heartbeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stop = threading.Event()
    monkeypatch.setattr(jobs, "JOBS", [("stop", stop.set)])
    assert jobs.run_forever(stop, None) == 0
    assert list(tmp_path.iterdir()) == []


def test_beat_touches_heartbeat_during_long_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """beat() aktualisiert den Heartbeat mitten im Job; ohne beat() bleibt er alt."""
    hb = tmp_path / "hb"
    stop = threading.Event()
    ages: dict[str, float] = {}

    def age() -> float:
        return time.time() - hb.stat().st_mtime

    def make_old() -> None:
        old = time.time() - 600
        os.utime(hb, (old, old))

    def long_job() -> None:
        make_old()  # Job laeuft schon "lange"
        jobs.beat()  # Fortschritt (z. B. naechstes Projekt)
        ages["nach_beat"] = age()

    def hanging_job() -> None:  # kein Fortschritt: nichts darf den Heartbeat auffrischen
        make_old()
        time.sleep(0.3)
        ages["haengt"] = age()
        stop.set()

    monkeypatch.setattr(jobs, "JOBS", [("lang", long_job), ("haengt", hanging_job)])
    assert jobs.run_forever(stop, hb) == 0
    assert ages["nach_beat"] < 5
    assert ages["haengt"] > 500


def test_beat_without_heartbeat_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stop = threading.Event()

    def job() -> None:
        jobs.beat()
        stop.set()

    monkeypatch.setattr(jobs, "JOBS", [("ohne", job)])
    assert jobs.run_forever(stop, None) == 0
    assert list(tmp_path.iterdir()) == []


def test_touch_heartbeat_unwritable_does_not_raise(tmp_path: Path) -> None:
    jobs.touch_heartbeat(tmp_path / "fehlt" / "hb")


def test_probe_heartbeat_age(tmp_path: Path) -> None:
    hb = tmp_path / "hb"
    assert probe.check_heartbeat(str(hb)) == "Jobs-Heartbeat fehlt"
    hb.touch()
    assert probe.check_heartbeat(str(hb)) is None
    old = time.time() - 300
    os.utime(hb, (old, old))
    assert "alt" in (probe.check_heartbeat(str(hb)) or "")


def test_probe_tcp_and_roles(tmp_path: Path) -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    hb = tmp_path / "hb"
    hb.touch()
    env = {
        "APP_ROLE": "admin,jobs",
        "ADMIN_BIND_ADDR": "127.0.0.1",
        "ADMIN_LISTEN_PORT": str(port),
        "JOBS_HEARTBEAT_FILE": str(hb),
    }
    try:
        assert probe.run(env) == []
    finally:
        srv.close()
    assert probe.run(env) == [f"Admin-Port {port} nicht erreichbar"]
    # Nur jobs: kein Port-Check; ohne Heartbeat-Datei keine Heartbeat-Pruefung.
    assert probe.run({**env, "APP_ROLE": "jobs"}) == []
    assert probe.run({"APP_ROLE": "public"}) == []


@pytest.mark.parametrize("raw", ["geheim99", "0", "70000", "80x", " "])
def test_probe_invalid_port_is_clean_failure(
    raw: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {"APP_ROLE": "admin", "ADMIN_LISTEN_PORT": raw}
    assert probe.run(env) == ["ADMIN_LISTEN_PORT ist keine gueltige Portnummer"]
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("JOBS_HEARTBEAT_FILE", raising=False)
    assert probe.main() == 1
    err = capsys.readouterr().err
    assert err == "probe: ADMIN_LISTEN_PORT ist keine gueltige Portnummer\n"


def test_probe_parse_port() -> None:
    assert probe.parse_port(" 8001 ") == 8001
    assert probe.parse_port("65535") == 65535
    assert probe.parse_port("65536") is None
    assert probe.parse_port("-1") is None


# ------------------------------------------------- Start: ungueltige Settings
def _last_run_error(caplog: pytest.LogCaptureFixture) -> tuple[dict[str, Any], str]:
    rec = [r for r in caplog.records if r.name == "npt.run" and r.levelno >= logging.ERROR][-1]
    line = JsonFormatter().format(rec)
    return json.loads(line), line


def test_main_invalid_settings_logs_fields_without_values(
    monkeypatch: pytest.MonkeyPatch, fake_start: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> Any:
        return make_settings(DATABASE_URL="kein-url-geheimnis123", APP_ROLE="geheimrolle")

    monkeypatch.setattr(run, "get_settings", broken)
    with caplog.at_level(logging.ERROR, logger="npt.run"), pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 2
    data, line = _last_run_error(caplog)
    assert data["msg"] == "Start abgebrochen: ungueltige Einstellungen"
    assert {f["feld"] for f in data["fehler"]} == {"DATABASE_URL", "APP_ROLE"}
    assert "exc" not in data
    assert "geheim" not in line
    assert fake_start["ranking"] == 0
    assert not FakeSupervisor.started


def test_main_settings_other_error_logs_only_type(
    monkeypatch: pytest.MonkeyPatch, fake_start: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> Any:
        raise RuntimeError("geheimer Wert")

    monkeypatch.setattr(run, "get_settings", broken)
    with caplog.at_level(logging.ERROR, logger="npt.run"), pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 2
    data, line = _last_run_error(caplog)
    assert data["error"] == "RuntimeError"
    assert "geheim" not in line


def test_main_invalid_log_level(
    monkeypatch: pytest.MonkeyPatch, fake_start: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    def configure(level: str) -> None:
        if level not in ("INFO", "DEBUG", "WARNING", "ERROR"):
            raise ValueError("Unknown level")

    monkeypatch.setattr(run, "configure_logging", configure)
    with caplog.at_level(logging.ERROR, logger="npt.run"):
        assert _main(monkeypatch, LOG_LEVEL="LAUT") == 2
    data, _line = _last_run_error(caplog)
    assert data["fehler"] == [{"feld": "LOG_LEVEL", "art": "value_error"}]
    assert not FakeSupervisor.started


def test_npt_run_process_invalid_settings_prints_json_line() -> None:
    """Echter Prozess: eine JSON-Zeile auf stdout, kein Traceback, kein Wert."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("POSTGRES_", "DATABASE_", "APP_", "NPT_"))
    }
    env.update(DATABASE_URL="kein-url-geheimnis123", APP_ROLE="geheimrolle")
    proc = subprocess.run(
        [sys.executable, "-m", "npt.run"],
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2
    assert "Traceback" not in proc.stdout + proc.stderr
    assert "geheim" not in proc.stdout + proc.stderr
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    errors = [x for x in lines if x["level"] == "ERROR"]
    assert len(errors) == 1
    assert errors[0]["msg"] == "Start abgebrochen: ungueltige Einstellungen"
    assert {"DATABASE_URL", "APP_ROLE"} <= {f["feld"] for f in errors[0]["fehler"]}
