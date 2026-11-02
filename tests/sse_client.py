"""Minimaler SSE-Client fuer Integrationstests (Thread + httpx-Stream)."""

from __future__ import annotations

import contextlib
import json
import queue
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class Event:
    received: float  # time.monotonic()
    event: str | None
    id: int | None
    data: Any
    retry: int | None = None


class SseClient:
    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = headers or {}
        self.status: int | None = None
        self.response_headers: httpx.Headers | None = None
        self.events: queue.Queue[Event | None] = queue.Queue()
        self.opened = threading.Event()
        self.heartbeats = 0
        self._sock: socket.socket | None = None
        self._client = httpx.Client(timeout=httpx.Timeout(5.0, read=60.0))
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            with self._client.stream("GET", self.url, headers=self.headers) as resp:
                self.status = resp.status_code
                self.response_headers = resp.headers
                stream = resp.extensions.get("network_stream")
                if stream is not None:
                    self._sock = stream.get_extra_info("socket")
                self.opened.set()
                if resp.status_code != 200:
                    return
                fields: dict[str, str] = {}
                for line in resp.iter_lines():
                    if line == "":
                        if fields:
                            self.events.put(self._event(fields))
                        fields = {}
                        continue
                    if line.startswith(":"):
                        if line.strip() == ": hb":
                            self.heartbeats += 1
                        continue
                    key, _, value = line.partition(":")
                    value = value.removeprefix(" ")
                    fields[key] = fields[key] + "\n" + value if key in fields else value
        except Exception:  # noqa: S110 - Verbindungsende/Close aus dem Test
            pass
        finally:
            self.opened.set()
            self.events.put(None)

    @staticmethod
    def _event(fields: dict[str, str]) -> Event:
        data = json.loads(fields["data"]) if "data" in fields else None
        return Event(
            time.monotonic(),
            fields.get("event"),
            int(fields["id"]) if "id" in fields else None,
            data,
            int(fields["retry"]) if "retry" in fields else None,
        )

    def wait_open(self, timeout: float = 10.0) -> int | None:
        self.opened.wait(timeout)
        return self.status

    def next(self, timeout: float = 5.0, *, kind: str | None = None) -> Event | None:
        """Naechstes Ereignis (optional nur eines Typs); ``None`` = Stream beendet."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"kein Ereignis {kind or ''} binnen {timeout} s")
            try:
                ev = self.events.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(f"kein Ereignis {kind or ''} binnen {timeout} s") from exc
            if ev is None:
                return None
            if kind is None or ev.event == kind:
                return ev

    def close(self) -> None:
        """Verbindung hart trennen (wie ein geschlossener Browser-Tab)."""
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(Exception):
            self._client.close()
        self._thread.join(timeout=5)
