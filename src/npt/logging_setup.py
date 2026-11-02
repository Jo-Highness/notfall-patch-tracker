"""Strukturierte JSON-Logs nach stdout mit Maskierung von Projekt-Token (Auftrag 8.2)."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

# Projekt-URLs /p/<token>: Token wird nie geloggt. Erfasst auch URL-kodierte Varianten.
_TOKEN_PATH_RE = re.compile(r"(/p/)[A-Za-z0-9_\-%]{8,}")
# Querystring-Parameter, die Geheimnisse tragen koennten.
_SECRET_PARAM_RE = re.compile(r"((?:token|access_token|csrf_token)=)[^&\s\"']+", re.IGNORECASE)
# Rueckfallnetz: freistehende Projekt-Token (token_urlsafe(32) = 43 Zeichen Base64url),
# z. B. in Ausnahmetexten oder repr()-Ausgaben ohne /p/-Praefix.
_BARE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{43}(?![A-Za-z0-9_\-])")

MASK = "***"

# Standardattribute eines LogRecord, die nicht als "extra" ausgegeben werden.
_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime", "color_message"}


def mask_secrets(text: str) -> str:
    """Ersetzt Projekt-Token in Pfaden, geheime Query-Parameter und freistehende Token."""
    text = _TOKEN_PATH_RE.sub(r"\1" + MASK, text)
    text = _SECRET_PARAM_RE.sub(r"\1" + MASK, text)
    return _BARE_TOKEN_RE.sub(MASK, text)


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_mask_value(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    """Eine JSON-Zeile je Ereignis; alle Strings werden maskiert."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        masked = _mask_value(payload)
        return json.dumps(masked, ensure_ascii=False, default=str)


def logging_dict(level: str = "INFO") -> dict[str, Any]:
    """dictConfig fuer App, Gunicorn und Uvicorn (auch in gunicorn_conf genutzt)."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": "npt.logging_setup.JsonFormatter"}},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"level": level, "handlers": ["stdout"]},
        "loggers": {
            # Eigenes Access-Log (npt.access) mit maskierten Pfaden statt der Server-Access-Logs.
            "uvicorn.access": {"level": "WARNING", "handlers": [], "propagate": False},
            "gunicorn.access": {"level": "WARNING", "handlers": [], "propagate": False},
            "uvicorn.error": {"level": level, "handlers": ["stdout"], "propagate": False},
            "gunicorn.error": {"level": level, "handlers": ["stdout"], "propagate": False},
            "uvicorn": {"level": level, "handlers": ["stdout"], "propagate": False},
        },
    }


def configure_logging(level: str = "INFO") -> None:
    import logging.config

    logging.config.dictConfig(logging_dict(level))
    logging.captureWarnings(True)
    sys.excepthook = _log_uncaught


def _log_uncaught(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
    logging.getLogger("npt").critical("unbehandelte Ausnahme", exc_info=(exc_type, exc, tb))
