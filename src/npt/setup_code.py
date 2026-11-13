"""Neuen Einrichtungscode erzeugen und ausgeben (NPT-15).

    docker compose exec app python -m npt.setup_code

Der bisherige Code wird ungueltig. Ist die Einrichtung abgeschlossen (oder existiert
bereits ein Admin), passiert nichts (Exitcode 1).
"""

from __future__ import annotations

import sys

from npt import instance
from npt.config import get_settings


def main() -> int:
    from npt.db.session import new_session

    get_settings().require_runtime_secrets()
    with new_session() as session:
        code = instance.ensure_setup_code(session, rotate=True)
        session.commit()
    if code is None:
        print("Einrichtung ist bereits abgeschlossen - kein Code erzeugt.", file=sys.stderr)
        return 1
    url = get_settings().PUBLIC_BASE_URL.rstrip("/") + "/einrichtung"
    print(f"Einrichtungscode: {code}\nAufruf: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
