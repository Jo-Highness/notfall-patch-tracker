"""Zulassung je Worker (NPT-09): Semaphor mit Wartebudget vor DB-gebundenen Requests.

Ist der Semaphor nach ``SEMAPHORE_WAIT_S`` nicht frei, wird mit 503 und
``Retry-After`` abgewiesen, bevor der DB-Pool (``pool_timeout``) oder das
Rueckfallnetz von Uvicorn greift.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

from npt import limits


class OverloadedError(Exception):
    """Worker ausgelastet; Antwort 503 mit ``{reason}``."""


class Admission:
    def __init__(
        self, size: int = limits.REQUEST_SEMAPHORE, wait_s: float = limits.SEMAPHORE_WAIT_S
    ) -> None:
        self._sem = threading.BoundedSemaphore(size)
        self._wait_s = wait_s

    def __call__(self) -> Iterator[None]:
        """FastAPI-Dependency (sync, laeuft im Threadpool)."""
        if not self._sem.acquire(timeout=self._wait_s):
            raise OverloadedError
        try:
            yield
        finally:
            self._sem.release()
