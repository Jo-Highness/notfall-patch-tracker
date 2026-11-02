"""Konfiguration, Grenzen, Logging-Maskierung, Supervisor-Kommandos."""

from __future__ import annotations

import json
import logging
import secrets

import pytest

from npt import limits
from npt.config import normalize_origin, parse_bootstrap_ids
from npt.logging_setup import JsonFormatter, mask_secrets
from npt.run import child_commands
from tests.conftest import make_settings


def test_limits_ranking_consistent() -> None:
    limits.check_ranking()


def test_bootstrap_ids_parsing() -> None:
    assert parse_bootstrap_ids("") == []
    assert parse_bootstrap_ids("CN=a,O=b; CN=c,O=d\nCN=e") == ["CN=a,O=b", "CN=c,O=d", "CN=e"]
    assert parse_bootstrap_ids('["CN=a;x,O=b"]') == ["CN=a;x,O=b"]
    with pytest.raises(ValueError, match="Liste"):
        parse_bootstrap_ids('["CN=a", 1]')


def test_normalize_origin() -> None:
    assert normalize_origin("HTTPS://Example.org:443/pfad") == "https://example.org"
    assert normalize_origin("http://x:8080") == "http://x:8080"
    with pytest.raises(ValueError, match="Basis-URL"):
        normalize_origin("ftp://x")


def test_settings_validation() -> None:
    with pytest.raises(ValueError, match="ADMIN_GATEWAY_CIDR"):
        make_settings(ADMIN_GATEWAY_CIDR="kein-netz")
    s = make_settings(CSRF_SECRET="kurz")
    with pytest.raises(RuntimeError):
        s.require_runtime_secrets()


def test_token_masking() -> None:
    token = secrets.token_urlsafe(32)  # zur Laufzeit, kein Literal im Repo
    assert token not in mask_secrets(f"GET /p/{token}/api/systems?x=1")
    assert mask_secrets(f"/p/{token}") == "/p/***"
    assert "geheim" not in mask_secrets("/x?access_token=geheim&y=1")


def test_json_formatter_masks_extra_fields() -> None:
    token = secrets.token_urlsafe(32)  # zur Laufzeit, kein Literal im Repo
    record = logging.makeLogRecord(
        {
            "msg": "zugriff auf /p/%s",
            "args": (token,),
            "levelno": 20,
            "levelname": "INFO",
            "path": f"/p/{token}/hilfe",
        }
    )
    out = JsonFormatter().format(record)
    assert token not in out
    data = json.loads(out)
    assert data["path"] == "/p/***/hilfe"
    assert data["level"] == "INFO"


def test_supervisor_commands_per_role() -> None:
    s = make_settings(APP_ROLE="all", ADMIN_BIND_ADDR="172.31.250.10", WEB_WORKERS=3)
    cmds = child_commands(s)
    assert set(cmds) == {"public", "admin", "jobs"}
    assert "172.31.250.10:8001" in cmds["admin"]
    assert "npt.web.admin:app" in cmds["admin"]
    assert "npt.web.public:app" in cmds["public"]
    assert cmds["public"][cmds["public"].index("--workers") + 1] == "3"
    assert set(child_commands(make_settings(APP_ROLE="jobs"))) == {"jobs"}
