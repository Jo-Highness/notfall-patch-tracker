from __future__ import annotations

import sys
import threading
from pathlib import Path

from npt.config import get_settings
from npt.jobs import JOBS, beat, run_forever
from npt.logging_setup import configure_logging

SENDER_JOIN_S = 10.0

if __name__ == "__main__":
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    from npt.db.session import new_session
    from npt.jobs import backups, housekeeping, mail, master_api

    JOBS.append(("backups", backups.make_job(settings, new_session, progress=beat)))
    JOBS.append(("health_probe_aufraeumen", housekeeping.make_job(new_session)))
    api_job = master_api.make_job(settings, new_session)
    if api_job is not None:
        JOBS.append(("stammdaten_api_vorschau", api_job))
    stop = threading.Event()
    sender = mail.start_sender(settings, stop)
    heartbeat = Path(settings.JOBS_HEARTBEAT_FILE) if settings.JOBS_HEARTBEAT_FILE else None
    rc = run_forever(stop, heartbeat)
    if sender is not None:
        sender.join(SENDER_JOIN_S)
    sys.exit(rc)
