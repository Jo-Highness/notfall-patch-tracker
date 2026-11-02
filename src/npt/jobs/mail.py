"""Mail-Sender im jobs-Prozess (NPT-05): eigener Thread neben der 15-s-Tick-Schleife.

Der Versand laeuft kontinuierlich (Drosselung je Mail), deshalb nicht als Tick-Job.
Aktiv ist bei mehreren Instanzen nur der Halter der Advisory-Sperre
(``npt.mail.queue.MailSender``). Ohne SMTP-Konfiguration startet kein Sender.
"""

from __future__ import annotations

import logging
import threading

from npt.config import Settings
from npt.mail.queue import MailSender
from npt.mail.render import MailConfig, MailConfigError

log = logging.getLogger("npt.jobs.mail")

SENDER_POOL_SIZE = 3  # Sperrverbindung + Abholung/Status + Reserve


def start_sender(settings: Settings, stop: threading.Event) -> threading.Thread | None:
    try:
        config = MailConfig.from_settings(settings)
    except MailConfigError as exc:
        log.warning("Mailversand deaktiviert", extra={"grund": exc.code})
        return None
    from npt.db.session import make_engine

    engine = make_engine(pool_size=SENDER_POOL_SIZE)
    sender = MailSender(engine, config, base_url=settings.PUBLIC_BASE_URL)
    thread = threading.Thread(target=sender.run, args=(stop,), name="mail-sender", daemon=True)
    thread.start()
    log.info(
        "Mail-Sender gestartet",
        extra={"rate_per_sec": config.rate_per_sec, "security": config.security},
    )
    return thread
