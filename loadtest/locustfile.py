"""Lasttest (Locust): 500 gleichzeitige Nutzer, alle mit offenem SSE-Stream, Schreibmix.

Ziel (Auftrag 5.5/10): Initialladen der Liste (2.000 Zeilen) < 2 s, Schreib-Latenz
p95 < 300 ms, Live-Aenderungen <= 2 s – bei 500 parallelen Verbindungen. Methodik und
Ergebnisse: docs/LOADTEST.md.

Nutzerarten (Gewichte ueber Umgebungsvariablen einstellbar):
- ``Viewer`` (Default 86 %): laedt Hauptliste (HTML + JSON), haelt den SSE-Stream offen und
  laedt nach einer zufaelligen Verweildauer (``NPT_RELOAD_MIN_S``–``NPT_RELOAD_MAX_S``) neu
  – so entsteht waehrend der ganzen Messung Initialladen **unter Last** (Pseudo-Request
  „Initialladen Liste (HTML+JSON)"). Jeder ``NPT_LIVE_SAMPLE_EVERY``-te Viewer misst die
  Live-Latenz: Schreiber setzen in „Bemerkung" ihren Zeitstempel, der Viewer meldet
  ``Empfang - Zeitstempel`` als „SSE live (Schreiben -> Empfang)" (gleiche Uhr, da Schreiber
  und Leser im selben Locust-Host laufen).
- ``PivotViewer`` (10 %): Uebersichtsseite + ``/api/pivot``, haelt den SSE-Stream offen und
  holt die Pivot-Zahlen wie der Browser hoechstens einmal je Sekunde neu.
- ``Editor`` (4 %): haelt zusaetzlich einen SSE-Stream (eigene Verbindung) und schreibt alle
  0,5–2 s ein *-Feld (Bemerkung 40 %, System betroffen 30 %, Aktion 15 %, Version 15 %) mit
  Konfliktbehandlung wie die UI (409 -> erneut mit aktuellem Wert); etwa jede Minute je
  Editor eine Neuanlage (``POST /api/systems``).

Jeder Nutzer sendet eine eigene ``X-Forwarded-For``-Adresse (Rate-Limit je IP greift
realistisch); dafuer muss die App der Absenderadresse vertrauen (``TRUSTED_PROXY_IPS``).

    docker build -t npt-loadtest loadtest
    docker run --rm --network host -e NPT_PROJECT_PATH=/p/<token> -v "$PWD/loadtest:/lt:ro" \\
        npt-loadtest -f /lt/locustfile.py --headless -u 500 -r 25 -t 5m --reset-stats \\
        --host http://127.0.0.1:8480 --csv /tmp/lt
"""

from __future__ import annotations

import itertools
import json
import os
import random
import re
import time
from typing import Any

import gevent
from locust import HttpUser, between, events, task
from locust.clients import HttpSession
from locust.exception import StopUser

PROJECT = os.environ.get("NPT_PROJECT_PATH", "").rstrip("/")
SAMPLE_EVERY = int(os.environ.get("NPT_LIVE_SAMPLE_EVERY", "10"))
RELOAD_MIN_S = float(os.environ.get("NPT_RELOAD_MIN_S", "60"))
RELOAD_MAX_S = float(os.environ.get("NPT_RELOAD_MAX_S", "240"))
CREATE_EVERY_S = float(os.environ.get("NPT_CREATE_EVERY_S", "60"))
W_VIEWER = int(os.environ.get("NPT_W_VIEWER", "86"))
W_PIVOT = int(os.environ.get("NPT_W_PIVOT", "10"))
W_EDITOR = int(os.environ.get("NPT_W_EDITOR", "4"))
# Eindeutige Nutzernummern auch bei --processes N (Prozess-Offset im oberen Bereich).
_ids = itertools.count(1 + (os.getpid() % 200) * 10_000)
_STREAM_TIMEOUT = (10, 60)  # Heartbeat alle 15 s -> 60 s Lesepause = Verbindung tot


def _fake_ip(n: int) -> str:
    return f"10.{(n >> 16) % 256}.{(n >> 8) & 255}.{(n & 255) or 1}"


@events.test_start.add_listener
def _check(environment: Any, **_kw: Any) -> None:
    if not PROJECT.startswith("/p/"):
        raise SystemExit("NPT_PROJECT_PATH=/p/<token> setzen")


def _fire(env: Any, name: str, ms: float, length: int = 0, exc: Exception | None = None) -> None:
    env.events.request.fire(
        request_type="SSE" if name.startswith("SSE") else "META",
        name=name,
        response_time=ms,
        response_length=length,
        exception=exc,
        context={},
    )


class Base(HttpUser):
    abstract = True
    csrf: str
    seq: int
    rows: dict[str, dict[str, Any]]

    def on_start(self) -> None:
        self.n = next(_ids)
        self.client.headers["X-Forwarded-For"] = _fake_ip(self.n)
        self.load_list()

    def load_list(self) -> None:
        """Hauptliste wie der Browser: HTML (CSRF), dann alle Zeilen als JSON."""
        t0 = time.perf_counter()
        page = self.client.get(PROJECT + "/", name="GET Hauptliste (HTML)")
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text)
        if not m:
            raise StopUser
        self.csrf = m.group(1)
        resp = self.client.get(f"{PROJECT}/api/systems", name="GET /api/systems (voll)")
        if resp.status_code != 200:
            raise StopUser
        data = resp.json()
        self.seq = int(data["seq"])
        self.rows = {r["system_id"]: r for r in data["rows"]}
        _fire(
            self.environment,
            "Initialladen Liste (HTML+JSON)",
            (time.perf_counter() - t0) * 1000,
            len(resp.content),
        )

    def stream(self, session: Any, until: float, on_data: Any = None) -> None:
        """SSE-Stream bis ``until`` (monotonic) offen halten; ``on_data(payload)`` je Frame."""
        url = f"{PROJECT}/api/events?since={self.seq}"
        started = time.perf_counter()
        with session.get(
            url, name="SSE /api/events", stream=True, catch_response=True, timeout=_STREAM_TIMEOUT
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"SSE {resp.status_code}")
                gevent.sleep(5)
                return
            resp.success()
            try:
                for line in resp.iter_lines(decode_unicode=True):
                    if line.startswith("id: "):
                        self.seq = int(line[4:])
                    elif on_data is not None and line.startswith("data: "):
                        on_data(line[6:])
                    if time.monotonic() >= until:
                        return
            except Exception as exc:  # Stream beendet (Ueberlauf, Neustart) -> neu verbinden
                elapsed = (time.perf_counter() - started) * 1000
                _fire(self.environment, "SSE Abbruch", elapsed, 0, exc)


class Viewer(Base):
    weight = W_VIEWER
    wait_time = between(0.5, 2)
    remarks: dict[str, Any]

    def on_start(self) -> None:
        super().on_start()
        self.sample = self.n % SAMPLE_EVERY == 0
        self.remarks = {sid: r.get("remark") for sid, r in self.rows.items()}
        self.reload_at = time.monotonic() + random.uniform(RELOAD_MIN_S, RELOAD_MAX_S)  # noqa: S311

    @task
    def watch(self) -> None:
        if time.monotonic() >= self.reload_at:
            self.load_list()  # Neuladen (F5 / neuer Nutzer) unter Last
            self.remarks = {sid: r.get("remark") for sid, r in self.rows.items()}
            self.reload_at = time.monotonic() + random.uniform(RELOAD_MIN_S, RELOAD_MAX_S)  # noqa: S311
        self.stream(self.client, self.reload_at, self._measure if self.sample else None)

    def _measure(self, payload: str) -> None:
        now = time.time()
        try:
            data = json.loads(payload)
        except ValueError:
            return
        for row in data.get("rows", ()):
            remark = row.get("remark") or ""
            # nur neu geschriebene Bemerkungen messen (eine Zeile kann wegen eines anderen
            # Felds erneut kommen und traegt dann einen alten Zeitstempel)
            if self.remarks.get(row["system_id"]) == remark:
                continue
            self.remarks[row["system_id"]] = remark
            if remark.startswith("lt "):
                try:
                    sent = float(remark.split()[1])
                except (IndexError, ValueError):
                    continue
                _fire(
                    self.environment,
                    "SSE live (Schreiben -> Empfang)",
                    max(0.0, (now - sent) * 1000),
                    len(payload),
                )


class PivotViewer(Base):
    """Uebersichtsseite: SSE + gebuendeltes Nachladen von /api/pivot (max. 1/s)."""

    weight = W_PIVOT
    wait_time = between(0.5, 2)

    def on_start(self) -> None:
        self.n = next(_ids)
        self.client.headers["X-Forwarded-For"] = _fake_ip(self.n)
        page = self.client.get(f"{PROJECT}/uebersicht", name="GET Uebersicht (HTML)")
        if page.status_code != 200:
            raise StopUser
        self.seq = 0
        self.last_pivot = 0.0
        self.pending = False
        self._pivot()

    def _pivot(self) -> None:
        resp = self.client.get(f"{PROJECT}/api/pivot", name="GET /api/pivot")
        if resp.status_code == 200:
            self.seq = max(self.seq, int(resp.json().get("seq", 0)))
        self.last_pivot = time.monotonic()
        self.pending = False

    def _on_delta(self, _payload: str) -> None:
        if time.monotonic() - self.last_pivot >= 1.0:
            self._pivot()
        else:
            self.pending = True

    @task
    def watch(self) -> None:
        self.stream(self.client, time.monotonic() + 120, self._on_delta)
        if self.pending:
            self._pivot()


class Editor(Base):
    weight = W_EDITOR
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        super().on_start()
        self.next_create = time.monotonic() + random.uniform(5, CREATE_EVERY_S)  # noqa: S311
        # Editor hat die Liste offen: eigener SSE-Stream auf eigener Verbindung.
        self.sse = HttpSession(
            base_url=self.host, request_event=self.environment.events.request, user=self
        )
        self.sse.headers["X-Forwarded-For"] = _fake_ip(self.n)
        self.sse_loop = gevent.spawn(self._hold_stream)

    def on_stop(self) -> None:
        self.sse_loop.kill(block=False)

    def _hold_stream(self) -> None:
        while True:
            self.stream(self.sse, time.monotonic() + 600)

    def _patch(self, sid: str, field: str, value: str | None, *, retry: bool = True) -> None:
        row = self.rows[sid]
        body = {"field": field, "value": value, "expected": row.get(field), "client_name": "last"}
        with self.client.patch(
            f"{PROJECT}/api/systems/{sid}",
            json=body,
            headers={"X-CSRF-Token": self.csrf},
            name="PATCH *-Feld",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                self.rows[sid] = resp.json()["row"]
                resp.success()
            elif resp.status_code == 409:
                resp.success()  # erwartbar bei vielen Schreibern; UI fragt nach
                _fire(self.environment, "PATCH Konflikt (409, erwartet)", 0.0)
                self.rows[sid] = resp.json()["row"]
                if retry:
                    self._patch(sid, field, value, retry=False)
            else:
                resp.failure(f"{resp.status_code} {resp.text[:80]}")

    def _sid(self) -> str:
        return random.choice(list(self.rows))  # noqa: S311 - Lastmuster

    @task(8)
    def remark(self) -> None:
        self._patch(self._sid(), "remark", f"lt {time.time():.3f} u{self.n}")

    @task(6)
    def affected(self) -> None:
        self._patch(self._sid(), "affected", random.choice(["ja", "nein", "in_pruefung", None]))  # noqa: S311

    @task(3)
    def action(self) -> None:
        value = random.choice(["update_installiert", "mitigiert", "isoliert", None])  # noqa: S311
        self._patch(self._sid(), "action_taken", value)

    @task(3)
    def version(self) -> None:
        self._patch(self._sid(), "installed_version", f"{random.randint(1, 9)}.{self.n % 50}")  # noqa: S311

    @task(1)
    def maybe_create(self) -> None:
        if time.monotonic() < self.next_create:
            return
        self.next_create = time.monotonic() + CREATE_EVERY_S * random.uniform(0.8, 1.2)  # noqa: S311
        body = {
            "system_name": f"Last {self.n}",
            "sbf": "SBF-LT",
            "digital_access_zone": "DAZ-LT",
            "system_description": "Lasttest-Neuanlage",
            "app_owner": f"lt{self.n}@firma.test",
            "app_type": "SaaS",
            "lifecycle": "Betrieb",
            "fqdn": f"lt{self.n}.firma.test",
            "cio_unit": "CIO-LT",
            "foreign_key": f"FK-{self.n}",
            "client_name": "last",
        }
        with self.client.post(
            f"{PROJECT}/api/systems",
            json=body,
            headers={"X-CSRF-Token": self.csrf},
            name="POST /api/systems (Neuanlage)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                row = resp.json()["row"]
                self.rows[row["system_id"]] = row
                resp.success()
            else:
                resp.failure(f"{resp.status_code} {resp.text[:80]}")
