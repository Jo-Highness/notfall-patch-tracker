"""Rate-Limits je Client-IP mit festen Fenstern in PostgreSQL (NPT-09).

UNLOGGED-Tabelle ``rate_limit_buckets``, ein UPSERT je Zugriff – funktioniert ueber
alle Worker hinweg ohne zusaetzlichen Dienst. Gezaehlt wird jeder Schreibversuch, der
den Endpunkt erreicht (nach CSRF-/Origin-Pruefung), auch fachlich abgelehnte
(Domain-Validierung, Konflikt, schreibgeschuetzt). Schema-Fehler, die FastAPI/Pydantic
vor dem Endpunkt mit 422 abweist, zaehlen nicht (DECISIONS D8).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

# Alte Fenster gelegentlich wegraeumen (Anteil der Zugriffe, die aufraeumen).
CLEANUP_PROBABILITY = 0.01
CLEANUP_AGE_S = 3600

# Uhr als Modulattribut, damit Tests das feste Fenster deterministisch setzen koennen.
clock = time.time


@dataclass(frozen=True)
class Limit:
    name: str
    limit: int
    window_s: int


class RateLimitedError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("rate_limit")
        self.retry_after = retry_after


_UPSERT = text(
    "INSERT INTO rate_limit_buckets (bucket_key, window_start, hits) VALUES (:k, :w, 1) "
    "ON CONFLICT (bucket_key, window_start) DO UPDATE "
    "SET hits = rate_limit_buckets.hits + 1 RETURNING hits"
)


def hit(session: Session, limit: Limit, client: str, now: float | None = None) -> None:
    """Zugriff zaehlen (eigene, sofort committete Transaktion); bei Ueberschreitung 429.

    Muss vor dem eigentlichen Schreibvorgang aufgerufen werden (committet).
    """
    current = now if now is not None else clock()
    window = int(current // limit.window_s) * limit.window_s
    key = f"{limit.name}:{client}"
    try:
        hits = int(session.execute(_UPSERT, {"k": key, "w": window}).scalar_one())
        if random.random() < CLEANUP_PROBABILITY:  # noqa: S311 - kein Sicherheitsbezug
            session.execute(
                text("DELETE FROM rate_limit_buckets WHERE window_start < :old"),
                {"old": int(current) - CLEANUP_AGE_S},
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    if hits > limit.limit:
        raise RateLimitedError(max(1, int(window + limit.window_s - current + 0.999)))
