"""Mail-Queue ``mail_deliveries`` und der Sender im jobs-Prozess (NPT-05).

- **Genau ein Sender**: Session-Advisory-Lock (``pg_try_advisory_lock``) auf einer
  eigenen Verbindung; weitere Instanzen warten, bis die Sperre frei wird (z. B. weil
  der Halter beendet wurde – dann gibt PostgreSQL die Sperre mit der Verbindung frei).
- **Abholung** einzeln mit ``FOR UPDATE SKIP LOCKED``: ``ausstehend`` und faellig ->
  ``in_versand`` (``sending_since``, ``attempts+1``) und sofort committen; erst danach
  wird gesendet. Bricht der Prozess dabei ab, bleibt die Sendung ``in_versand``.
- **Drosselung**: Token-Bucket mit ``MAIL_RATE_PER_SEC`` (ohne Burst: Abstand 1/Rate).
- **Ergebnis**: gesendet; voruebergehender Fehler (4xx, Timeout, Verbindungsfehler) ->
  wieder ``ausstehend`` mit Backoff, hoechstens 5 Versuche; 5xx -> endgueltig
  ``fehlgeschlagen``.
- **Haengende Sendungen**: ``in_versand`` laenger als 10 min -> ``fehlgeschlagen``
  („Status unklar"), **nicht** erneut gesendet (die Mail kann bereits zugestellt sein).
- Inhalt und Systeme sind beim Start des Laufs eingefroren (``mail_runs`` bzw.
  ``mail_deliveries.systems``); der Projekt-Token wird beim Senden gelesen (nach einer
  Token-Neugenerierung enthalten noch nicht versandte Mails die gueltige URL).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Connection, Engine, text

from npt.db.session import DB_UNAVAILABLE_ERRORS
from npt.mail.audience import SystemRef
from npt.mail.render import MailConfig, build_message, render_mail
from npt.mail.transport import SendError, SmtpTransport, Transport, classify

log = logging.getLogger("npt.mail")

SENDER_LOCK_KEY: Final = (0x4E50_544D << 32) | 1  # "NPTM"
MAX_ATTEMPTS: Final = 5
BACKOFF_S: Final = (30.0, 60.0, 120.0, 240.0)  # Wartezeit nach Versuch 1..4
STALE_AFTER_S: Final = 600
UNCLEAR_TEXT: Final = (
    "Status unklar: Versand wurde unterbrochen (länger als 10 min „in Versand“). "
    "Nicht erneut gesendet – ggf. „nur fehlgeschlagene erneut senden“."
)
CANCELLED_TEXT: Final = "Versand abgebrochen durch Admin."
LOCK_RETRY_S: Final = 5.0
IDLE_WAIT_S: Final = 1.0
LOCK_CHECK_S: Final = 30.0
STALE_CHECK_S: Final = 60.0


def backoff_for(attempts: int) -> float:
    return BACKOFF_S[min(max(attempts, 1), len(BACKOFF_S)) - 1]


class TokenBucket:
    """Token-Bucket (Kapazitaet 1): zwischen zwei Sendungen liegen mindestens 1/Rate s."""

    def __init__(
        self,
        rate: float,
        *,
        capacity: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.clock = clock
        self.sleep = sleep
        self.last = clock()

    def acquire(self) -> None:
        while True:
            now = self.clock()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
            self.last = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return
            self.sleep((1.0 - self.tokens) / self.rate)


@dataclass(frozen=True)
class Claimed:
    id: int
    campaign_id: int
    send_run: int
    email: str
    systems: list[SystemRef]
    attempts: int


_CLAIM_SQL: Final = text(
    """
    UPDATE mail_deliveries d
       SET status = 'in_versand', sending_since = now(), attempts = d.attempts + 1,
           updated_at = now()
     WHERE d.id = (
           SELECT id FROM mail_deliveries
            WHERE status = 'ausstehend' AND next_attempt_at <= now()
            ORDER BY next_attempt_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1)
    RETURNING d.id, d.campaign_id, d.send_run, d.email_norm, d.systems, d.attempts
    """
)
_CONTENT_SQL: Final = text(
    """
    SELECT r.subject, r.body_html, p.access_token
      FROM mail_runs r
      JOIN mail_campaigns c ON c.id = r.campaign_id
      JOIN projects p ON p.id = c.project_id
     WHERE r.campaign_id = :c AND r.send_run = :r
    """
)
_STALE_SQL: Final = text(
    """
    UPDATE mail_deliveries
       SET status = 'fehlgeschlagen', last_error = :t, sending_since = NULL, updated_at = now()
     WHERE status = 'in_versand' AND sending_since < now() - make_interval(secs => :s)
    """
)


def recover_stale(conn: Connection, stale_after_s: float = STALE_AFTER_S) -> int:
    """Haengende ``in_versand`` -> ``fehlgeschlagen`` (Status unklar); liefert die Anzahl."""
    result = conn.execute(_STALE_SQL, {"t": UNCLEAR_TEXT, "s": stale_after_s})
    return int(result.rowcount or 0)


def claim_next(conn: Connection) -> Claimed | None:
    row = conn.execute(_CLAIM_SQL).mappings().first()
    if row is None:
        return None
    systems = [SystemRef.from_json(s) for s in (row["systems"] or [])]
    return Claimed(
        int(row["id"]),
        int(row["campaign_id"]),
        int(row["send_run"]),
        str(row["email_norm"]),
        systems,
        int(row["attempts"]),
    )


_SENT_SQL: Final = text(
    """
    UPDATE mail_deliveries
       SET status = 'gesendet', last_error = NULL, message_id = :m, sent_at = now(),
           sending_since = NULL, updated_at = now()
     WHERE id = :id
    """
)
_FAILED_SQL: Final = text(
    """
    UPDATE mail_deliveries
       SET status = 'fehlgeschlagen', last_error = :e, sending_since = NULL, updated_at = now()
     WHERE id = :id
    """
)
_RETRY_SQL: Final = text(
    """
    UPDATE mail_deliveries
       SET status = 'ausstehend', last_error = :e, sending_since = NULL, updated_at = now(),
           next_attempt_at = now() + make_interval(secs => :d)
     WHERE id = :id
    """
)


class MailSender:
    """Ein Sender je Instanz; aktiv ist nur der Halter der Advisory-Sperre."""

    def __init__(
        self,
        engine: Engine,
        config: MailConfig,
        *,
        base_url: str,
        transport_factory: Callable[[MailConfig], Transport] = SmtpTransport,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        stale_after_s: float = STALE_AFTER_S,
    ) -> None:
        self.engine = engine
        self.config = config
        self.base_url = base_url
        self.transport_factory = transport_factory
        self.bucket = TokenBucket(config.rate_per_sec, clock=clock, sleep=sleep)
        self.clock = clock
        self.stale_after_s = stale_after_s
        self._lock_conn: Connection | None = None
        self._transport: Transport | None = None

    # ------------------------------------------------------------ Sperre
    @property
    def active(self) -> bool:
        return self._lock_conn is not None

    def acquire_lock(self) -> bool:
        if self._lock_conn is not None:
            return True
        conn = self.engine.connect()
        try:
            got = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": SENDER_LOCK_KEY}
                ).scalar()
            )
            conn.commit()
        except Exception:
            conn.close()
            raise
        if not got:
            conn.close()
            return False
        self._lock_conn = conn
        return True

    def lock_alive(self) -> bool:
        conn = self._lock_conn
        if conn is None:
            return False
        try:
            conn.execute(text("SELECT 1"))
            conn.commit()
        except Exception:
            log.warning("Verbindung mit der Sender-Sperre verloren")
            self._drop_lock()
            return False
        return True

    def _drop_lock(self) -> None:
        conn, self._lock_conn = self._lock_conn, None
        if conn is not None:
            try:
                conn.invalidate()
                conn.close()
            except Exception:  # pragma: no cover - nur Aufraeumen
                log.debug("Sperrverbindung nicht sauber geschlossen", exc_info=True)

    def release_lock(self) -> None:
        conn, self._lock_conn = self._lock_conn, None
        if conn is None:
            return
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SENDER_LOCK_KEY})
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------ Arbeit
    def recover_stale(self) -> int:
        with self.engine.begin() as conn:
            n = recover_stale(conn, self.stale_after_s)
        if n:
            log.warning("Haengende Sendungen als fehlgeschlagen markiert", extra={"anzahl": n})
        return n

    def _transport_get(self) -> Transport:
        if self._transport is None:
            self._transport = self.transport_factory(self.config)
        return self._transport

    def close_transport(self) -> None:
        transport, self._transport = self._transport, None
        if transport is not None:
            try:
                transport.close()
            except Exception:  # pragma: no cover
                log.debug("Transport nicht sauber geschlossen", exc_info=True)

    def run_once(self) -> bool:
        """Eine faellige Sendung abholen und senden; ``False`` = nichts zu tun/nicht aktiv."""
        if not self.active:
            return False
        with self.engine.begin() as conn:
            claimed = claim_next(conn)
        if claimed is None:
            return False
        self.bucket.acquire()
        self.deliver(claimed)
        return True

    def deliver(self, c: Claimed) -> str:
        try:
            with self.engine.connect() as conn:
                content = conn.execute(_CONTENT_SQL, {"c": c.campaign_id, "r": c.send_run}).first()
            if content is None:
                return self._fail(c, "Kampagne oder Projekt nicht mehr vorhanden.")
            subject, body, token = content
            mail = render_mail(str(subject), str(body), self.base_url, str(token), c.systems)
            msg = build_message(self.config, c.email, mail)
        except Exception as exc:
            log.exception("Mail konnte nicht erzeugt werden", extra={"delivery": c.id})
            return self._fail(c, f"Mail konnte nicht erzeugt werden: {type(exc).__name__}")
        try:
            self._transport_get().send(msg, c.email)
        except Exception as exc:
            self.close_transport()
            err = exc if isinstance(exc, SendError) else classify(exc)
            if err.temporary and c.attempts < MAX_ATTEMPTS:
                delay = backoff_for(c.attempts)
                with self.engine.begin() as conn:
                    conn.execute(
                        _RETRY_SQL, {"id": c.id, "e": f"Versuch {c.attempts}: {err}", "d": delay}
                    )
                log.info(
                    "Mail voruebergehend fehlgeschlagen, neuer Versuch",
                    extra={"delivery": c.id, "versuch": c.attempts, "warte_s": delay},
                )
                return "ausstehend"
            prefix = "" if not err.temporary else f"Nach {c.attempts} Versuchen: "
            return self._fail(c, f"{prefix}{err}")
        with self.engine.begin() as conn:
            conn.execute(_SENT_SQL, {"id": c.id, "m": str(msg["Message-ID"])})
        return "gesendet"

    def _fail(self, c: Claimed, error: str) -> str:
        with self.engine.begin() as conn:
            conn.execute(_FAILED_SQL, {"id": c.id, "e": error[:1000]})
        log.info("Mail fehlgeschlagen", extra={"delivery": c.id})
        return "fehlgeschlagen"

    def drain(self, limit: int = 100_000) -> int:
        """Alle faelligen Sendungen abarbeiten (Tests, Werkzeuge); liefert die Anzahl."""
        n = 0
        while n < limit and self.run_once():
            n += 1
        self.close_transport()
        return n

    # ------------------------------------------------------------ Schleife
    def run(self, stop: threading.Event) -> None:
        last_lock_check = last_stale = 0.0
        try:
            while not stop.is_set():
                try:
                    if not self.active:
                        if not self.acquire_lock():
                            stop.wait(LOCK_RETRY_S)
                            continue
                        log.info("Mail-Sender aktiv (Sperre erhalten)")
                        self.recover_stale()
                        last_lock_check = last_stale = self.clock()
                    now = self.clock()
                    if now - last_lock_check >= LOCK_CHECK_S:
                        last_lock_check = now
                        if not self.lock_alive():
                            continue
                    if now - last_stale >= STALE_CHECK_S:
                        last_stale = now
                        self.recover_stale()
                    if not self.run_once():
                        self.close_transport()
                        stop.wait(IDLE_WAIT_S)
                except DB_UNAVAILABLE_ERRORS as exc:
                    log.warning(
                        "Mail-Sender: Datenbank nicht erreichbar",
                        extra={"error": type(exc).__name__},
                    )
                    self.close_transport()
                    stop.wait(LOCK_RETRY_S)
                except Exception:
                    log.exception("Fehler im Mail-Sender")
                    self.close_transport()
                    stop.wait(LOCK_RETRY_S)
        finally:
            self.close_transport()
            try:
                self.release_lock()
            except Exception:  # pragma: no cover - Verbindung ggf. schon weg
                self._drop_lock()
