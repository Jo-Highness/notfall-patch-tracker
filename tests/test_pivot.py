"""Pivot-Uebersicht (Auftrag 5.4): Aggregation inkl. leerer DAZ, Sortierung, Summen,
Kennzahlen und Konsistenz der Drilldown-Links mit der Python-Filterlogik."""

from __future__ import annotations

import random
import re
from collections import Counter
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from sqlalchemy import update

from npt import editing, pivot
from npt.db.models import ProjectSystem
from npt.domain import filters
from tests.project_helpers import Editor, Proj, make_project, seed_master

TRICKY_DAZ = ["DAZ & <Intern>", "ä-Zone", "b-zone", "B-Zone", "Zone 10", "Zone 9", "DAZ=1?x"]


def set_rows(sf, proj: Proj, values: dict[str, dict[str, Any]]) -> None:
    with sf() as s:
        for sid, vals in values.items():
            s.execute(
                update(ProjectSystem)
                .where(ProjectSystem.project_id == proj.id, ProjectSystem.system_id == sid)
                .values(**vals)
            )
        s.commit()


def all_rows(sf, proj: Proj) -> list[dict[str, Any]]:
    with sf() as s:
        return editing.list_rows(s, proj.id)


def hrefs(data: dict[str, Any]) -> list[tuple[str, int]]:
    out = [(c["href"], c["count"]) for r in data["rows"] for c in r["cells"]]
    out += [(r["href"], r["total"]) for r in data["rows"]]
    out += [(c["href"], c["count"]) for c in data["columns"]]
    out.append((data["total_href"], data["total"]))
    k = data["kpi"]
    out += [
        (k["processed_href"], k["processed"]),
        (k["yes_without_action_href"], k["yes_without_action"]),
    ]
    return out


def drill(rows: list[dict[str, Any]], href: str, base: str) -> list[dict[str, Any]]:
    parts = urlsplit(href)
    assert parts.path == base + "/"
    return [
        dict(r)
        for r in filters.apply(rows, filters.parse(parse_qsl(parts.query, keep_blank_values=True)))
    ]


@pytest.fixture
def ed(session_factory, public_app_factory) -> Editor:
    seed_master(session_factory, 40)
    proj = make_project(session_factory)
    return Editor(public_app_factory(), proj)


def get_pivot(ed: Editor) -> dict[str, Any]:
    r = ed.c.get(f"{ed.p.base}/api/pivot")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    data: dict[str, Any] = r.json()
    return data


def test_aggregation_with_empty_daz_and_order(ed, session_factory) -> None:
    rows = all_rows(session_factory, ed.p)
    sids = [r["system_id"] for r in rows]
    # leere DAZ als NULL und als '' -> beide „(ohne DAZ)"
    set_rows(
        session_factory,
        ed.p,
        {
            sids[0]: {"digital_access_zone": "", "affected": "ja"},
            sids[1]: {"digital_access_zone": None, "affected": "nein"},
            sids[2]: {"digital_access_zone": "b-zone", "affected": "in_pruefung"},
            sids[3]: {"digital_access_zone": "A-Zone", "affected": None},
            sids[4]: {
                "digital_access_zone": "a-zone",
                "affected": "ja",
                "action_taken": "isoliert",
            },
        },
    )
    data = get_pivot(ed)
    rows = all_rows(session_factory, ed.p)
    labels_ = [r["label"] for r in data["rows"]]
    assert labels_[-1] == "(ohne DAZ)"
    assert labels_.count("(ohne DAZ)") == 1
    # alphabetisch ohne Gross-/Kleinschreibung, Gleichstand stabil nach Codepoint
    named = labels_[:-1]
    assert named == sorted(named, key=lambda x: (x.casefold(), x))
    assert named.index("A-Zone") < named.index("a-zone") < named.index("b-zone")
    assert [c["label"] for c in data["columns"]] == ["(leer)", "In Prüfung", "Ja", "Nein"]
    # Zaehlung gegen Python
    expected = Counter(((r["digital_access_zone"] or None), r["affected"]) for r in rows)
    for prow in data["rows"]:
        for cell in prow["cells"]:
            assert cell["count"] == expected[(prow["daz"], cell["key"])], (prow["label"], cell)
        assert prow["total"] == sum(c["count"] for c in prow["cells"])
    assert data["total"] == len(rows) == 40
    assert sum(c["count"] for c in data["columns"]) == data["total"]
    no_daz = next(r for r in data["rows"] if r["daz"] is None)
    assert no_daz["total"] == sum(1 for r in rows if not r["digital_access_zone"])
    assert no_daz["cells"][2]["count"] >= 1  # '' + ja
    assert no_daz["cells"][3]["count"] >= 1  # NULL + nein


def test_kpis(ed, session_factory) -> None:
    sids = [r["system_id"] for r in all_rows(session_factory, ed.p)]
    set_rows(
        session_factory,
        ed.p,
        {
            sids[0]: {"affected": "ja"},
            sids[1]: {"affected": "ja", "action_taken": "update_installiert"},
            sids[2]: {"affected": "nein"},
            sids[3]: {"affected": "in_pruefung"},
        },
    )
    k = get_pivot(ed)["kpi"]
    assert (k["total"], k["processed"], k["yes_without_action"]) == (40, 4, 1)
    assert k["processed_pct"] == 10.0
    assert "f.affected=ja" in k["yes_without_action_href"]
    assert "f.action_taken=leer" in k["yes_without_action_href"]


def test_drilldown_links_match_python_filter_exactly(ed, session_factory) -> None:
    """Konsistenz ueber zufaellige Testdaten: jeder Link liefert exakt die Zellzahl."""
    rnd = random.Random(4711)  # noqa: S311 - deterministische Testdaten
    rows = all_rows(session_factory, ed.p)
    zones = [*TRICKY_DAZ, "", None]
    set_rows(
        session_factory,
        ed.p,
        {
            r["system_id"]: {
                "digital_access_zone": rnd.choice(zones),
                "affected": rnd.choice([None, "ja", "nein", "in_pruefung"]),
                "action_taken": rnd.choice([None, None, "isoliert", "abgeschaltet"]),
            }
            for r in rows
        },
    )
    data = get_pivot(ed)
    rows = all_rows(session_factory, ed.p)
    links = hrefs(data)
    assert len(links) == len(data["rows"]) * 5 + 5 + 2
    for href, count in links:
        hit = drill(rows, href, ed.p.base)
        assert len(hit) == count, (href, count, len(hit))
    # Zellen einer DAZ-Zeile sind disjunkt und decken die Zeilensumme
    for prow in data["rows"]:
        ids = [r["system_id"] for c in prow["cells"] for r in drill(rows, c["href"], ed.p.base)]
        assert len(ids) == len(set(ids)) == prow["total"]
    assert {r["label"] for r in data["rows"]} >= {"DAZ & <Intern>", "DAZ=1?x", "(ohne DAZ)"}


def test_drilldown_params_canonical() -> None:
    base = "/p/TOKEN"
    assert pivot.list_href(base, pivot.drilldown_params(None, any_daz=False, affected=[None])) == (
        "/p/TOKEN/?f.affected=leer&x.digital_access_zone="
    )
    assert pivot.list_href(base, pivot.drilldown_params("A & B", any_daz=False)) == (
        "/p/TOKEN/?x.digital_access_zone=A+%26+B"
    )
    assert (
        pivot.list_href(base, pivot.drilldown_params(affected=["ja"])) == "/p/TOKEN/?f.affected=ja"
    )
    assert pivot.list_href(base, []) == "/p/TOKEN/"


def test_pivot_follows_writes_and_cache(ed, session_factory) -> None:
    before = get_pivot(ed)
    sid = all_rows(session_factory, ed.p)[0]["system_id"]
    assert ed.patch(sid, "affected", "ja").status_code == 200
    after = get_pivot(ed)
    assert after["seq"] == before["seq"] + 1
    assert after["columns"][2]["count"] == before["columns"][2]["count"] + 1
    assert after["columns"][0]["count"] == before["columns"][0]["count"] - 1
    # unveraendert -> gleiches Ergebnis aus dem Cache
    assert get_pivot(ed) == after


def test_cache_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake(session: Any, project_id: int) -> tuple[pivot.Counts, int]:
        calls.append(project_id)
        return {(None, None): project_id}, 0

    monkeypatch.setattr(pivot, "count_rows", fake)
    cache = pivot.PivotCache(max_projects=2)
    s: Any = object()
    assert cache.get(s, 1, 5, "/p/a").total == 1
    cache.get(s, 1, 5, "/p/a")
    assert calls == [1]
    cache.get(s, 1, 6, "/p/a")  # neuer Stand
    cache.get(s, 2, 1, "/p/b")
    cache.get(s, 3, 1, "/p/c")  # verdraengt Projekt 1
    cache.get(s, 1, 6, "/p/a")
    assert calls == [1, 1, 2, 3, 1]


def test_overview_page(ed, session_factory) -> None:
    r = ed.c.get(f"{ed.p.base}/uebersicht")
    assert r.status_code == 200
    html = r.text
    assert "<script>" not in html
    assert 'data-testid="pivot"' in html
    assert 'data-testid="total"' in html
    assert 'data-testid="kpi-processed"' in html
    assert 'aria-current="page"' in html
    assert "/static/js/uebersicht.js" in html
    assert "Platzhalter" not in html
    assert "folgt in der n" not in html
    data = get_pivot(ed)
    # jede Zahl ist ein Link (Zellen, Summen, Gesamt)
    for href, _ in hrefs(data):
        assert f'href="{href.replace("&", "&amp;")}"' in html, href
    m = re.search(r'data-testid="total">(\d+)<', html)
    assert m
    assert int(m.group(1)) == 40


def test_overview_escapes_daz(ed, session_factory) -> None:
    sid = all_rows(session_factory, ed.p)[0]["system_id"]
    set_rows(session_factory, ed.p, {sid: {"digital_access_zone": "<script>alert(1)</script>"}})
    html = ed.c.get(f"{ed.p.base}/uebersicht").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_pivot_unknown_token_and_readonly(ed, session_factory) -> None:
    r = ed.c.get("/p/" + "Z" * 43 + "/api/pivot")
    assert r.status_code == 404
    assert r.json()["reason"] == "nicht_gefunden"
    from npt import projects
    from tests.project_helpers import ACTOR

    with session_factory() as s:
        projects.set_status(s, ACTOR, ed.p.id, "archiviert")
        s.commit()
    page = ed.c.get(f"{ed.p.base}/uebersicht")
    assert page.status_code == 200
    assert 'data-testid="readonly-banner"' in page.text
    assert get_pivot(ed)["total"] == 40
