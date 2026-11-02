"""Hilfen fuer Projekt-/Hauptlisten-Tests: Stammdaten anlegen, Projekt erzeugen, API-Aufrufe."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from npt import projects
from npt.admins import Actor
from npt.db.models import MasterSystem

ACTOR = Actor("issuer", "cn=test admin", "192.0.2.10")


def seed_master(sf: sessionmaker[Session], count: int = 5, prefix: str = "APP-") -> list[str]:
    ids = [f"{prefix}{i:05d}" for i in range(1, count + 1)]
    with sf() as s:
        s.add_all(
            MasterSystem(
                system_id=sid,
                system_name=f"System {i}",
                sbf=f"SBF-{i % 3}",
                digital_access_zone=f"DAZ-{i % 4}" if i % 7 else None,
                system_description=f"Beschreibung {i}",
                app_owner=f"owner{i % 5}@firma.test",
                app_type="SaaS",
                lifecycle="Betrieb",
                fqdn=f"h{i}.firma.test",
                cio_unit="CIO-1",
                foreign_key=f"FK-{i}",
            )
            for i, sid in enumerate(ids, start=1)
        )
        s.commit()
    return ids


@dataclass
class Proj:
    id: int
    token: str
    name: str

    @property
    def base(self) -> str:
        return f"/p/{self.token}"


def make_project(sf: sessionmaker[Session], name: str = "CVE-2026-0001") -> Proj:
    with sf() as s:
        p = projects.create_project(s, ACTOR, name, "Testprojekt")
        s.commit()
        return Proj(p.id, p.access_token, p.name)


def csrf_from(html: str) -> str:
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    assert m
    return m.group(1)


class Editor:
    """Bearbeiter-Client fuer ein Projekt (holt das CSRF-Token von der Hauptliste)."""

    def __init__(self, client: TestClient, proj: Proj) -> None:
        self.c = client
        self.p = proj
        page = client.get(proj.base)
        assert page.status_code == 200, page.text
        self.token = csrf_from(page.text)

    def headers(self, **extra: str) -> dict[str, str]:
        return {"X-CSRF-Token": self.token, "Content-Type": "application/json", **extra}

    def rows(self) -> dict[str, dict[str, Any]]:
        r = self.c.get(f"{self.p.base}/api/systems")
        assert r.status_code == 200
        return {row["system_id"]: row for row in r.json()["rows"]}

    def patch(
        self,
        sid: str,
        field: str,
        value: Any,
        expected: Any = None,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> httpx.Response:
        body = {"field": field, "value": value, "expected": expected, **extra}
        r: httpx.Response = self.c.patch(
            f"{self.p.base}/api/systems/{sid}", json=body, headers=headers or self.headers()
        )
        return r

    def create(self, headers: dict[str, str] | None = None, **values: Any) -> httpx.Response:
        r: httpx.Response = self.c.post(
            f"{self.p.base}/api/systems", json=values, headers=headers or self.headers()
        )
        return r


def new_system_body(**kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "system_name": "Neues System",
        "sbf": "SBF-1",
        "digital_access_zone": "DAZ-1",
        "system_description": "Beschreibung",
        "app_owner": "neu@firma.test",
        "app_type": "SaaS",
        "lifecycle": "Betrieb",
        "fqdn": "neu.firma.test",
        "cio_unit": "CIO-1",
        "foreign_key": "FK-neu",
    }
    body.update(kw)
    return body
