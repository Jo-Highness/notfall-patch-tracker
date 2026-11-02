"""API-Stub der Stammdaten (deaktiviert, nur Vorschau) – Auftrag 4, ARCHITECTURE A3."""

from __future__ import annotations

import io
import threading
import urllib.request
from collections.abc import Iterator
from http.client import HTTPMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar, cast

import pytest

from npt.jobs import master_api
from npt.masterdata.api import (
    ApiMasterDataSource,
    ApiPullSchedule,
    SameOriginRedirectHandler,
    open_api,
)
from npt.masterdata.source import MasterDataError, MasterDataSource, MasterDataSourceDisabledError
from tests.conftest import make_settings


def test_api_source_is_disabled_by_default() -> None:
    src = ApiMasterDataSource(make_settings())
    assert isinstance(src, MasterDataSource)
    assert not src.enabled
    with pytest.raises(MasterDataSourceDisabledError):
        src.fetch()


def test_api_mapping_is_the_only_missing_piece() -> None:
    src = ApiMasterDataSource(make_settings(MASTER_API_ENABLED=True, MASTER_API_URL="https://x"))
    with pytest.raises(NotImplementedError):
        src.map_response({"items": []})


def test_schedule_and_job_registration() -> None:
    off = make_settings()
    assert master_api.make_job(off, lambda: None) is None  # type: ignore[arg-type,return-value]
    on = make_settings(
        MASTER_API_ENABLED=True, MASTER_API_URL="https://x", MASTER_API_SCHEDULE_MINUTES=5
    )
    assert master_api.make_job(on, lambda: None) is not None  # type: ignore[arg-type,return-value]
    sched = ApiPullSchedule(on)
    assert sched.due(now=1000.0)
    assert not sched.due(now=1100.0)
    assert sched.due(now=1000.0 + 301)


# --------------------------------------------------------- Weiterleitungen (K14)
def _redirect(
    handler: SameOriginRedirectHandler, start: str, target: str
) -> urllib.request.Request:
    req = urllib.request.Request(start, headers={"Accept": "application/json"})  # noqa: S310
    req.add_unredirected_header("Authorization", "Bearer tok")
    new = handler.redirect_request(req, io.BytesIO(b""), 302, "Found", HTTPMessage(), target)
    assert new is not None
    return new


def test_redirect_same_origin_keeps_token() -> None:
    start = "https://api.example/v1/systems"
    h = SameOriginRedirectHandler(start, "tok")
    for target in ("https://API.example/v2/systems", "https://api.example:443/x?page=2"):
        new = _redirect(h, start, target)
        assert new.full_url == target
        assert new.unredirected_hdrs["Authorization"] == "Bearer tok"
        assert "Authorization" not in new.headers  # nur als nicht weitergeleiteter Header
    # Ohne Token wird auch keiner gesetzt.
    new = _redirect(SameOriginRedirectHandler(start, ""), start, "https://api.example/y")
    assert "Authorization" not in new.unredirected_hdrs


@pytest.mark.parametrize(
    "target",
    [
        "http://api.example/v1/systems",  # Herabstufung auf http
        "https://other.example/v1/systems",  # anderer Host
        "https://api.example.evil.test/",  # anderer Host (Praefix)
        "https://api.example:8443/v1/systems",  # anderer Port
        "ftp://api.example/x",
        "https://[::1/",  # ungueltig
    ],
)
def test_redirect_elsewhere_is_refused(target: str) -> None:
    start = "https://api.example/v1/systems"
    h = SameOriginRedirectHandler(start, "tok")
    req = urllib.request.Request(start)
    with pytest.raises(MasterDataError, match="leitet weiter") as exc:
        h.redirect_request(req, io.BytesIO(b""), 301, "Moved", HTTPMessage(), target)
    assert "tok" not in str(exc.value)
    assert "example" not in str(exc.value)  # Ziel-URL nicht in der Meldung


class _Recorder(BaseHTTPRequestHandler):
    seen: ClassVar[list[tuple[str, str | None]]] = []

    def do_GET(self) -> None:
        type(self).seen.append((self.path, self.headers.get("Authorization")))
        if self.path == "/redir":
            self.send_response(302)
            port = cast(ThreadingHTTPServer, self.server).server_port
            self.send_header("Location", f"http://127.0.0.1:{port}/data")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def recorder() -> Iterator[str]:
    _Recorder.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_open_api_sends_token_and_stops_at_redirect(recorder: str) -> None:
    # Ohne Weiterleitung: Token kommt an (nicht weitergeleiteter Header wird gesendet).
    with open_api(f"{recorder}/data", "tok", timeout=5) as resp:
        assert resp.read() == b"[]"
    assert _Recorder.seen == [("/data", "Bearer tok")]
    # Weiterleitung auf http: Abbruch, keine zweite Anfrage (Token verlaesst nie den Prozess).
    _Recorder.seen = []
    with pytest.raises(MasterDataError, match="HTTP 302"):
        open_api(f"{recorder}/redir", "tok", timeout=5)
    assert _Recorder.seen == [("/redir", "Bearer tok")]


def test_fetch_uses_restricted_opener(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    class _Resp(io.BytesIO):
        def __enter__(self) -> _Resp:
            return self

    def fake_open(url: str, token: str) -> _Resp:
        calls.append((url, token))
        return _Resp(b"{}")

    monkeypatch.setattr("npt.masterdata.api.open_api", fake_open)
    src = ApiMasterDataSource(
        make_settings(
            MASTER_API_ENABLED=True, MASTER_API_URL="https://api.example/x", MASTER_API_TOKEN="t"
        )
    )
    with pytest.raises(NotImplementedError):
        src.fetch()
    assert calls == [("https://api.example/x", "t")]
