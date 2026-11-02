"""Hilfen fuer Tests gegen die Admin-App (Zertifikats-Header, CSRF)."""

from __future__ import annotations

import re
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from npt import admins
from tests.certs import IssuedCert
from tests.conftest import gateway_headers

ADMIN_DN = "CN=Test Admin,OU=Admins,O=NPT Test,C=DE"


def csrf_of(html: str) -> str:
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    assert m, "csrf-Meta-Tag fehlt"
    return m.group(1)


def bootstrap(sf: sessionmaker[Session], *entries: str) -> None:
    with sf() as s:
        admins.bootstrap_admins(s, "subject_dn", list(entries or (ADMIN_DN,)))
        s.commit()


class AdminClient:
    """TestClient-Wrapper: setzt Gateway-Header, holt und sendet CSRF-Token."""

    def __init__(self, client: TestClient, cert: IssuedCert) -> None:
        self.c = client
        self.cert = cert
        self._token: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        return gateway_headers(self.cert.pem)

    def get(self, path: str, **kw: Any) -> httpx.Response:
        r: httpx.Response = self.c.get(path, headers=self.headers, **kw)
        if "text/html" in r.headers.get("content-type", "") and "csrf-token" in r.text:
            self._token = csrf_of(r.text)
        return r

    @property
    def token(self) -> str:
        if self._token is None:
            self.get("/admin/")
        assert self._token is not None
        return self._token

    def post(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        **kw: Any,
    ) -> httpx.Response:
        form = dict(data or {})
        form.setdefault("csrf_token", self.token)
        r: httpx.Response = self.c.post(path, headers=self.headers, data=form, files=files, **kw)
        return r
