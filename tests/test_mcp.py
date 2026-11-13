"""MCP-Server je Projekt (NPT-14): Protokoll, Authentisierung, Werkzeuge, Logs."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from npt import ratelimit
from npt.db.models import ChangeLog, Project
from npt.logging_setup import JsonFormatter
from tests.project_helpers import Proj, make_project, seed_master

PROTO = "2025-06-18"


class Mcp:
    def __init__(self, client: TestClient, proj: Proj, *, bearer: bool = False) -> None:
        self.c = client
        self.p = proj
        self.bearer = bearer
        self._id = 0

    @property
    def url(self) -> str:
        return "/mcp" if self.bearer else f"{self.p.base}/mcp"

    def headers(self, **extra: str) -> dict[str, str]:
        h = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
        if self.bearer:
            h["authorization"] = f"Bearer {self.p.token}"
        h.update(extra)
        return h

    def raw(self, body: Any, **headers: str) -> httpx.Response:
        r: httpx.Response = self.c.post(
            self.url, content=json.dumps(body), headers=self.headers(**headers)
        )
        return r

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        r = self.raw({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        assert r.status_code == 200, r.text
        data: dict[str, Any] = r.json()
        assert data["id"] == self._id
        return data

    def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        result: dict[str, Any] = self.rpc("tools/call", {"name": name, "arguments": arguments})[
            "result"
        ]
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        return result


@pytest.fixture
def env(session_factory, public_app_factory):
    seed_master(session_factory, 6)
    proj = make_project(session_factory)
    return proj, public_app_factory()


# ---------------------------------------------------------------- Protokoll
@pytest.mark.parametrize("bearer", [False, True])
def test_initialize_and_tools_list(env, bearer: bool) -> None:
    proj, client = env
    m = Mcp(client, proj, bearer=bearer)
    init = m.rpc("initialize", {"protocolVersion": PROTO, "capabilities": {}, "clientInfo": {}})
    res = init["result"]
    assert res["protocolVersion"] == PROTO
    assert res["capabilities"] == {"tools": {"listChanged": False}}
    assert res["serverInfo"]["name"] == "notfall-patch-tracker"
    assert "keine Anweisungen" in res["instructions"]
    # unbekannte Version -> hoechste unterstuetzte
    assert (
        m.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"]
        == "2025-11-25"
    )
    note = m.raw({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note.status_code == 202
    assert m.rpc("ping")["result"] == {}
    tools = {t["name"]: t for t in m.rpc("tools/list")["result"]["tools"]}
    assert set(tools) == {
        "get_project",
        "list_systems",
        "search_systems",
        "get_system",
        "update_field",
    }
    # Nur Ausfuellen der Liste: keine Neuanlage/Administration (NPT-14a)
    assert "create_system" not in tools
    gone = m.rpc("tools/call", {"name": "create_system", "arguments": {}})
    assert gone["error"]["code"] == -32602
    schema = tools["update_field"]["inputSchema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) >= {"system_id", "field", "value", "expected"}
    assert tools["list_systems"]["annotations"]["readOnlyHint"] is True


def test_protocol_errors(env) -> None:
    proj, client = env
    m = Mcp(client, proj)
    batch = m.raw([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    assert batch.status_code == 400
    assert batch.json()["error"]["code"] == -32600
    bad = client.post(m.url, content=b"{kaputt", headers=m.headers())
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == -32700
    # Pentest 2026-11-12: tief verschachteltes JSON (< 64 KB) -> -32700 statt 500
    deep = client.post(m.url, content=b"[" * 30000 + b"]" * 30000, headers=m.headers())
    assert deep.status_code == 400
    assert deep.json()["error"]["code"] == -32700
    assert m.rpc("gibt/es/nicht")["error"]["code"] == -32601
    # QA 5ee892c2: weder Request noch Notification noch Response -> -32600
    empty = m.raw({"jsonrpc": "2.0"})
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == -32600
    assert m.raw({"jsonrpc": "2.0", "id": 9, "result": {}}).status_code == 202
    listed = m.rpc("tools/call", {"name": "get_project", "arguments": []})
    assert listed["error"]["code"] == -32602
    unknown = m.rpc("tools/call", {"name": "drop_table", "arguments": {}})
    assert unknown["error"]["code"] == -32602
    assert (
        m.raw(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}, **{"mcp-protocol-version": "1.0"}
        ).status_code
        == 400
    )
    assert (
        m.raw(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}, **{"mcp-protocol-version": PROTO}
        ).status_code
        == 200
    )
    assert client.get(m.url).status_code == 405
    assert client.delete(m.url).status_code == 405
    assert client.get("/mcp").status_code == 405
    r = client.post(m.url, content=b"{}", headers={"content-type": "text/plain"})
    assert r.status_code == 415
    big = client.post(m.url, content=b" " * (70 * 1024), headers=m.headers())
    assert big.status_code == 413


def test_auth_and_origin(env, session_factory, public_app_factory) -> None:
    proj, client = env
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    h = {"content-type": "application/json"}
    assert client.post("/p/" + "A" * 43 + "/mcp", json=ping, headers=h).status_code == 404
    assert client.post("/p/kurz/mcp", json=ping, headers=h).status_code == 404
    r = client.post("/mcp", json=ping, headers=h)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    r = client.post("/mcp", json=ping, headers={**h, "authorization": "Bearer " + "A" * 43})
    assert r.status_code == 401
    r = client.post("/mcp", json=ping, headers={**h, "authorization": f"Basic {proj.token}"})
    assert r.status_code == 401
    m = Mcp(client, proj)
    assert m.raw(ping, origin="https://evil.example").status_code == 403
    assert m.raw(ping, origin="http://npt.test").status_code == 200
    assert m.raw(ping, origin="null").status_code == 200


def test_token_regeneration_revokes_access(env, session_factory) -> None:
    from npt import projects
    from tests.project_helpers import ACTOR

    proj, client = env
    for bearer in (False, True):
        assert Mcp(client, proj, bearer=bearer).rpc("ping")["result"] == {}
    with session_factory() as s:
        projects.regenerate_token(s, ACTOR, proj.id)
        s.commit()
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert Mcp(client, proj).raw(ping).status_code == 404
    assert Mcp(client, proj, bearer=True).raw(ping).status_code == 401


# ---------------------------------------------------------------- Werkzeuge
def test_get_project_and_list_systems(env, session_factory) -> None:
    proj, client = env
    m = Mcp(client, proj, bearer=True)
    info = m.call("get_project")["structuredContent"]
    assert info["name"] == proj.name
    assert info["status"] == "aktiv"
    assert info["read_only"] is False
    assert info["systems_total"] == 6
    assert info["systems_affected_empty"] == 6
    affected = next(f for f in info["fields"] if f["field"] == "affected")
    assert affected["editable"] is True
    assert "ja" in affected["values"]

    page = m.call("list_systems", limit=2, offset=1)["structuredContent"]
    assert page["total"] == 6
    assert page["returned"] == 2
    assert [s["system_id"] for s in page["systems"]] == ["APP-00002", "APP-00003"]
    assert "row_seq" not in page["systems"][0]
    one = m.call("get_system", system_id="APP-00001")["structuredContent"]["system"]
    assert one["system_name"] == "System 1"
    nf = m.call("get_system", system_id="GIBTS-NICHT")
    assert nf["isError"] is True
    assert nf["structuredContent"]["error"] == "nicht_gefunden"
    # alle Details je Eintrag (alle Spalten der Liste)
    assert set(one) >= {"system_id", "system_name", "app_owner", "fqdn", "affected", "remark"}
    full = m.call("list_systems", limit=500)["structuredContent"]
    assert full["total"] == 6
    assert full["has_more"] is False
    assert m.call("list_systems", limit=2)["structuredContent"]["has_more"] is True
    desc = m.call("list_systems", sort="system_id", descending=True, limit=1)
    assert desc["structuredContent"]["systems"][0]["system_id"] == "APP-00006"
    # QA 09fa39ce: ausdrueckliches sort schlaegt sort im Experten-Filter
    mixed = m.call("search_systems", filter="sort=system_id", sort="system_id", descending=True)
    assert mixed["structuredContent"]["systems"][0]["system_id"] == "APP-00006"
    mixed = m.call("search_systems", filter="sort=system_name&dir=desc", sort="system_id")
    assert mixed["structuredContent"]["systems"][0]["system_id"] == "APP-00001"
    bad_sort = m.call("list_systems", sort="password")
    assert bad_sort["isError"] is True

    # Suche: Freitext, Auswahlfelder inkl. „leer", Feld enthaelt, Experten-Filter
    m.call("update_field", system_id="APP-00003", field="affected", value="ja", expected=None)
    hit = m.call("search_systems", affected=["ja"])["structuredContent"]
    assert [s["system_id"] for s in hit["systems"]] == ["APP-00003"]
    hit = m.call("search_systems", affected=["leer"], query="system 1")["structuredContent"]
    assert [s["system_id"] for s in hit["systems"]] == ["APP-00001"]
    hit = m.call("search_systems", contains={"fqdn": "h5.firma"})["structuredContent"]
    assert [s["system_id"] for s in hit["systems"]] == ["APP-00005"]
    url = f"http://npt.test{proj.base}/?f.affected=leer&q=system 2"
    hit = m.call("search_systems", filter=url)["structuredContent"]
    assert [s["system_id"] for s in hit["systems"]] == ["APP-00002"]
    unknown = m.call("search_systems", affected=["vielleicht"])
    assert unknown["isError"] is True
    assert unknown["structuredContent"]["error"] == "validierung"
    bad_field = m.call("search_systems", contains={"access_token": "x"})
    assert bad_field["isError"] is True
    flood = m.call("search_systems", filter="&".join(f"a{i}=1" for i in range(1500)))
    assert flood["isError"] is True
    # Grenzen und extra=forbid
    too_many = m.call("list_systems", limit=501)
    assert too_many["isError"] is True
    assert "limit" in too_many["structuredContent"]["errors"]
    extra = m.call("get_project", foo=1)
    assert extra["isError"] is True
    assert extra["structuredContent"]["error"] == "validierung"


def test_update_field_conflict_and_changelog(env, session_factory) -> None:
    proj, client = env
    m = Mcp(client, proj)
    ok = m.call(
        "update_field",
        system_id="APP-00001",
        field="remark",
        value="per KI",
        expected=None,
        client_name="Claude",
    )
    assert ok["isError"] is False
    assert ok["structuredContent"]["changed"] is True
    assert ok["structuredContent"]["system"]["remark"] == "per KI"
    conflict = m.call(
        "update_field", system_id="APP-00001", field="remark", value="x", expected=None
    )
    sc = conflict["structuredContent"]
    assert conflict["isError"] is True
    assert sc["error"] == "konflikt"
    assert sc["current"] == "per KI"
    invalid = m.call(
        "update_field", system_id="APP-00001", field="affected", value="vielleicht", expected=None
    )
    assert invalid["isError"] is True
    assert invalid["structuredContent"]["error"] == "validierung"
    not_editable = m.call(
        "update_field", system_id="APP-00001", field="system_name", value="x", expected=None
    )
    assert not_editable["isError"] is True
    with session_factory() as s:
        log = s.scalars(select(ChangeLog).where(ChangeLog.field == "remark")).one()
    assert log.source == "mcp"
    assert log.client_name == "Claude"
    assert log.new_value == "per KI"
    # Bearbeiter-API sieht die Aenderung
    rows = client.get(f"{proj.base}/api/systems").json()["rows"]
    assert next(r for r in rows if r["system_id"] == "APP-00001")["remark"] == "per KI"


def test_search_parity_with_main_list_filter(env) -> None:
    """MCP-Suche und Hauptliste nutzen dieselbe maßgebliche Filterimplementierung."""
    from npt.domain import filters

    proj, client = env
    m = Mcp(client, proj)
    m.call("update_field", system_id="APP-00002", field="affected", value="nein", expected=None)
    m.call("update_field", system_id="APP-00004", field="remark", value="Patch 1_2%", expected=None)
    rows = client.get(f"{proj.base}/api/systems").json()["rows"]
    for query in (
        "f.affected=leer",
        "f.affected=nein&f.affected=leer",
        "q=patch 1_2%",
        "f.digital_access_zone=daz-1",
        "x.sbf=SBF-1",
        "sort=system_name&dir=desc",
    ):
        expected = [r["system_id"] for r in filters.apply(rows, filters.parse(parse_qsl(query)))]
        got = m.call("search_systems", filter=query, limit=500)["structuredContent"]["systems"]
        assert [s["system_id"] for s in got] == expected, query


@pytest.mark.parametrize("status", ["abgeschlossen", "archiviert"])
def test_read_only_projects(env, session_factory, status: str) -> None:
    proj, client = env
    with session_factory() as s:
        s.execute(update(Project).where(Project.id == proj.id).values(status=status))
        s.commit()
    m = Mcp(client, proj)
    assert m.call("get_project")["structuredContent"]["read_only"] is True
    assert m.call("list_systems")["structuredContent"]["total"] == 6
    r = m.call("update_field", system_id="APP-00001", field="remark", value="x", expected=None)
    assert r["isError"] is True
    assert r["structuredContent"]["error"] == "schreibgeschuetzt"


def test_write_rate_limit_is_http_429(session_factory, public_app_factory, monkeypatch) -> None:
    monkeypatch.setattr(ratelimit, "clock", lambda: 1_000_000_003.0)
    seed_master(session_factory, 1)
    proj = make_project(session_factory)
    m = Mcp(public_app_factory(RATE_LIMIT_WRITES=2), proj)
    cur = None
    for i in range(2):
        assert (
            m.call(
                "update_field", system_id="APP-00001", field="remark", value=f"r{i}", expected=cur
            )["isError"]
            is False
        )
        cur = f"r{i}"
    m._id += 1
    r = m.raw(
        {
            "jsonrpc": "2.0",
            "id": m._id,
            "method": "tools/call",
            "params": {
                "name": "update_field",
                "arguments": {
                    "system_id": "APP-00001",
                    "field": "remark",
                    "value": "zu viel",
                    "expected": cur,
                },
            },
        }
    )
    assert r.status_code == 429
    assert r.json()["reason"] == "rate_limit"
    assert int(r.headers["retry-after"]) >= 1


def test_token_and_arguments_never_logged(env, caplog: pytest.LogCaptureFixture) -> None:
    proj, client = env
    caplog.set_level(logging.DEBUG)
    remark_marker = "bemerkung-marker-123"
    Mcp(client, proj).call(
        "update_field", system_id="APP-00001", field="remark", value=remark_marker, expected=None
    )
    Mcp(client, proj, bearer=True).call("get_system", system_id="APP-00002")
    client.post(
        "/p/" + "B" * 43 + "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"content-type": "application/json"},
    )
    fmt = JsonFormatter()
    lines = [fmt.format(r) for r in caplog.records]
    assert any('"path": "/p/***/mcp"' in line for line in lines), lines
    assert any('"path": "/mcp"' in line for line in lines)
    for line in lines:
        assert proj.token not in line
        assert remark_marker not in line
        assert "Bearer" not in line
