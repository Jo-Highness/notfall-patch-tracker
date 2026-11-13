"""Notfall: web-verwaltete CA und Sperrliste leeren (NPT-15).

    docker compose exec app python -m npt.trust_reset

Fuer den Fall, dass sich alle Admins ueber eine falsche Web-CA/CRL ausgesperrt haben.
Danach gilt nur noch die Datei-CA (ADMIN_PKI_DIR); ohne Datei-CA laeuft das Gateway im
Einrichtungsmodus (503) – dann eine CA per Datei bereitstellen. Audit-Eintrag
``vertrauensanker_zurueckgesetzt``.
"""

from __future__ import annotations

import sys

from npt import instance
from npt.audit import Actor
from npt.config import get_settings


def main() -> int:
    from npt.db.session import new_session

    get_settings().require_runtime_secrets()
    with new_session() as session:
        changed = instance.reset_trust(session, Actor.system("trust_reset"))
        session.commit()
    print(
        "Web-CA und Web-CRL entfernt; das Gateway uebernimmt das innerhalb von etwa einer Minute."
        if changed
        else "Keine Web-CA/CRL hinterlegt - nichts zu tun."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
