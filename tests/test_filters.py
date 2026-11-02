"""Filter-Grammatik: gemeinsame Testvektoren fuer Python (maßgeblich) und JS (NPT-01)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

import pytest

from npt.domain import filters

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads((ROOT / "tests" / "vectors" / "filter-vectors.json").read_text("utf-8"))
CASES: list[dict[str, Any]] = VECTORS["cases"]


def run(query: str) -> tuple[list[str], str]:
    state = filters.parse(parse_qsl(query, keep_blank_values=True))
    ids = [str(r["system_id"]) for r in filters.apply(VECTORS["rows"], state)]
    return ids, urlencode(filters.to_params(state))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_filter_vectors_python(case: dict[str, Any]) -> None:
    ids, canonical = run(case["query"])
    assert ids == case["expect"]
    if "canonical" in case:
        assert canonical == case["canonical"]
    # Rundlauf: die kanonische Darstellung beschreibt dieselbe Sicht
    assert run(canonical)[0] == ids


def test_filter_vectors_js() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node nicht installiert (JS-Vektoren laufen separat, siehe README)")
    res = subprocess.run(
        [node, str(ROOT / "tests" / "js" / "filter-vectors.test.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_limits_on_values() -> None:
    params = [("x.system_id", str(i)) for i in range(filters.MAX_VALUES_PER_FIELD + 10)]
    state = filters.parse(params)
    assert len(state.exact["system_id"]) == filters.MAX_VALUES_PER_FIELD
    state = filters.parse([("q", "x" * 10_000)])
    assert state.q is not None
    assert len(state.q) == filters.MAX_TEXT_LEN


def test_js_and_python_share_field_definitions() -> None:
    js = (ROOT / "src" / "npt" / "static" / "js" / "npt-filter.js").read_text("utf-8")
    from npt.domain import fields

    for name in fields.LIST_FIELDS:
        assert f'"{name}"' in js
    for mapping in fields.ENUM_FIELDS.values():
        for key, label in mapping.items():
            assert f'{key}: "{label}"' in js
