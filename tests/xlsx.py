"""Hilfen zum Erzeugen/Lesen von .xlsx in Tests."""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook, load_workbook

from npt.domain import fields


def xlsx_bytes(rows: list[list[Any]], *, title: str = "Tabelle1") -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = title
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


MASTER_HEADER = [fields.DISPLAY_NAMES[f] for f in fields.MASTER_FIELDS]


def master_row(sid: str, name: str = "Sys", owner: str = "a@firma.test", **kw: Any) -> list[Any]:
    values = {
        "system_id": sid,
        "system_name": name,
        "sbf": "SBF-1",
        "digital_access_zone": "DAZ-A",
        "system_description": "Beschreibung",
        "app_owner": owner,
        "app_type": "SaaS",
        "lifecycle": "Betrieb",
        "fqdn": f"{sid.lower()}.firma.test",
        "cio_unit": "CIO-1",
        "foreign_key": f"FK-{sid}",
    }
    values.update(kw)
    return [values[f] for f in fields.MASTER_FIELDS]


def master_xlsx(*rows: list[Any], header: list[Any] | None = None) -> bytes:
    return xlsx_bytes([header or MASTER_HEADER, *rows])


def read_xlsx(data: bytes) -> list[tuple[Any, ...]]:
    wb = load_workbook(io.BytesIO(data))
    ws = wb.active
    assert ws is not None
    return [tuple(r) for r in ws.iter_rows(values_only=True)]
