"""Uvicorn-Worker fuer Gunicorn mit festen, sicherheitsrelevanten Einstellungen.

``proxy_headers`` ist AUS: Uvicorn darf den Socket-Absender nie aus
X-Forwarded-For ueberschreiben. Die Admin-Middleware verlaesst sich auf den
echten Absender (NPT-06); die public-App wertet XFF selbst und nur fuer
vertraute Proxies aus (NPT-09).
"""

from __future__ import annotations

from typing import Any, ClassVar

from uvicorn_worker import UvicornWorker

from npt import limits


class NptUvicornWorker(UvicornWorker):  # type: ignore[misc]
    CONFIG_KWARGS: ClassVar[dict[str, Any]] = {
        "loop": "auto",
        "http": "auto",
        "proxy_headers": False,
        "server_header": False,
        "date_header": True,
        "limit_concurrency": limits.UVICORN_LIMIT_CONCURRENCY,
        "timeout_keep_alive": limits.UVICORN_KEEPALIVE_S,
        # Offene SSE-Streams beim Herunterfahren nicht bis zum Kill halten (NPT-03).
        "timeout_graceful_shutdown": limits.SSE_SHUTDOWN_GRACE_S,
    }
