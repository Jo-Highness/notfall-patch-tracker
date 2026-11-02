"""public-App: keine Admin-Routen, Security-Header, Health (NPT-02/09, Auftrag 8)."""

from __future__ import annotations

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from npt.web.public import create_public_app
from tests.certs import CertFactory
from tests.conftest import GATEWAY_IP, gateway_headers, make_settings, with_peer
from tests.routes import iter_routes


@pytest.fixture
def client() -> TestClient:
    app = create_public_app(make_settings(), enable_monitor=False)
    return TestClient(with_peer(app, GATEWAY_IP))


def test_routing_table_has_no_admin_routes() -> None:
    app = create_public_app(make_settings(), enable_monitor=False)
    paths = [p for p, _ in iter_routes(app.routes)]
    # Walker funktioniert (findet auch Routen aus eingebundenen Routern) ...
    assert {"/healthz", "/readyz", "/robots.txt", "/static"} <= set(paths)
    # ... und es gibt keine einzige /admin-Route.
    assert not [p for p in paths if p.lower().startswith("/admin")]


@pytest.mark.parametrize(
    "path",
    [
        "/admin",
        "/admin/",
        "/admin/benutzer",
        "/admin/static/css/app.css",
        "/ADMIN/",
        "/admin/zugang",
    ],
)
def test_admin_paths_are_404(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404
    assert client.post(path).status_code in (404, 405)


def test_gateway_headers_have_no_effect(client: TestClient, certs: CertFactory) -> None:
    r = client.get("/admin/", headers=gateway_headers(certs.issue().pem))
    assert r.status_code == 404


def test_public_module_does_not_import_admin() -> None:
    code = (
        "import sys, npt.web.public\n"
        "bad = [m for m in sys.modules if m.startswith('npt.web.admin') or m == 'npt.admins']\n"
        "assert not bad, bad\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_robots_txt(client: TestClient) -> None:
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "Disallow: /" in r.text


def test_security_headers(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "noindex" in r.headers["x-robots-tag"]
    assert '<meta name="robots" content="noindex, nofollow">' in r.text


def test_static_assets_local(client: TestClient) -> None:
    r = client.get("/static/js/app.js")
    assert r.status_code == 200
    html = client.get("/").text
    assert "http://" not in html
    assert "https://" not in html


def test_health_without_monitor(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["reason"] == "db"
