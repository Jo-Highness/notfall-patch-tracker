"""Liveness-Probe des backend-Pods: ``python -m npt.probe`` (NPT-12 K5).

Nur Standardbibliothek (kein Settings-Import, schneller Start alle paar Sekunden):

- Rolle ``admin``: TCP-Verbindung zu ``ADMIN_BIND_ADDR:ADMIN_LISTEN_PORT``.
- Rolle ``jobs`` mit ``JOBS_HEARTBEAT_FILE``: Datei hoechstens
  ``JOBS_HEARTBEAT_MAX_AGE_S`` (120 s) alt.

Exitcode 0 = gesund, 1 = krank (Grund auf stderr, ohne Interna). Eine ungueltige
Umgebung (z. B. nicht-numerischer ``ADMIN_LISTEN_PORT``) zaehlt als krank und wird
ohne Traceback und ohne den Wert gemeldet.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from collections.abc import Mapping

from npt.limits import JOBS_HEARTBEAT_MAX_AGE_S

TCP_TIMEOUT_S = 2.0
ALL_ROLES = frozenset({"public", "admin", "jobs"})


def roles_from_env(raw: str) -> frozenset[str]:
    parts = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return ALL_ROLES if not parts or "all" in parts else frozenset(parts)


def parse_port(raw: str) -> int | None:
    """Portnummer 1-65535 oder ``None`` bei ungueltiger Angabe."""
    try:
        port = int(raw.strip())
    except ValueError:
        return None
    return port if 0 < port < 65536 else None


def check_tcp(host: str, port: int, timeout_s: float = TCP_TIMEOUT_S) -> str | None:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return None
    except OSError:
        return f"Admin-Port {port} nicht erreichbar"


def check_heartbeat(path: str, max_age_s: float = JOBS_HEARTBEAT_MAX_AGE_S) -> str | None:
    try:
        age = time.time() - os.stat(path).st_mtime
    except OSError:
        return "Jobs-Heartbeat fehlt"
    if age > max_age_s:
        return f"Jobs-Heartbeat {age:.0f} s alt (Grenze {max_age_s:.0f} s)"
    return None


def run(env: Mapping[str, str]) -> list[str]:
    roles = roles_from_env(env.get("APP_ROLE", "all"))
    problems: list[str] = []
    if "admin" in roles:
        host = env.get("ADMIN_BIND_ADDR", "127.0.0.1") or "127.0.0.1"
        port = parse_port(env.get("ADMIN_LISTEN_PORT", "8001") or "8001")
        if port is None:
            problems.append("ADMIN_LISTEN_PORT ist keine gueltige Portnummer")
        elif (p := check_tcp(host, port)) is not None:
            problems.append(p)
    heartbeat = env.get("JOBS_HEARTBEAT_FILE", "")
    if "jobs" in roles and heartbeat and (p := check_heartbeat(heartbeat)) is not None:
        problems.append(p)
    return problems


def main() -> int:
    problems = run(os.environ)
    for p in problems:
        print(f"probe: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
