"""Gemeinsame Gunicorn-Konfiguration (``-c python:npt.gunicorn_conf``).

Bindung und Workerzahl setzt ``npt-run`` per Kommandozeile.
"""

from __future__ import annotations

import os

from npt import limits
from npt.logging_setup import logging_dict

worker_class = "npt.web.worker.NptUvicornWorker"
# SSE-Verbindungen sind langlebig; der Worker-Timeout betrifft bei Uvicorn-Workern nur
# den Herzschlag des Workers zum Arbiter, nicht die Request-Dauer (SSE-tauglich).
# Beim Herunterfahren bricht Uvicorn offene Streams nach SSE_SHUTDOWN_GRACE_S ab
# (Worker-Klasse), lange vor dem graceful_timeout.
timeout = limits.GUNICORN_TIMEOUT_S
graceful_timeout = limits.GUNICORN_GRACEFUL_TIMEOUT_S
keepalive = limits.UVICORN_KEEPALIVE_S
accesslog = None
errorlog = "-"
logconfig_dict = logging_dict(os.environ.get("LOG_LEVEL", "INFO"))
# Gunicorn >= 26: kein Control-Socket (wuerde in $HOME schreiben; Rootfs ist read_only).
control_socket_disable = True
forwarded_allow_ips = ""
proxy_allow_ips = ""
# Herzschlag-Dateien im RAM (read_only-Rootfs); /dev/shm ist container-privat.
worker_tmp_dir = "/dev/shm" if os.path.isdir("/dev/shm") else None  # noqa: S108
