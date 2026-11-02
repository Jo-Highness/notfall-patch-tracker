"""E2E-Rauchtest Pivot-Uebersicht: Live-Aktualisierung und Drilldown in die Hauptliste.

Aufruf wie ``live_smoke.py`` (siehe dort):
``python -m tests.e2e.pivot_smoke --base … --admin … --pki …``.
Prueft: Uebersicht laedt mit Summen, Aenderung in einem anderen Kontext aktualisiert die
Uebersicht ohne Neuladen (gebuendelt 1 s), jede geklickte Zahl (Zelle inkl. „(ohne DAZ)",
Zeilen-/Spaltensumme, Gesamt, Kennzahl) oeffnet die Hauptliste mit genau so vielen Zeilen.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from typing import Any

from playwright.sync_api import Page, expect, sync_playwright

from tests.e2e.live_smoke import Admin, api, open_list


def count_of(page: Page, testid: str) -> int:
    return int(page.locator(f'[data-testid="{testid}"]').first.inner_text())


def drill(page: Page, overview: str, testid: str, value_sel: str = "") -> tuple[int, int, str]:
    """Klickt eine Zahl der Uebersicht, liefert (Zahl, angezeigte Zeilen, Query)."""
    page.goto(overview)
    link = page.locator(f'[data-testid="{testid}"]').first
    value = page.locator(value_sel) if value_sel else link
    expected = int(re.sub(r"\D", "", value.inner_text()) or "0")
    link.click()
    page.locator("#counter", has_text="Systemen angezeigt").wait_for(timeout=15000)
    time.sleep(0.3)
    shown = int((page.text_content("#counter") or "0").split(" von ")[0])
    return expected, shown, page.url.split("?", 1)[1] if "?" in page.url else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8480")
    ap.add_argument("--admin", default="https://localhost:8481")
    ap.add_argument("--pki", default="pki-out/clients")
    args = ap.parse_args()
    admin = Admin(args.admin, args.pki)
    pid, path = admin.create_project("E2E Pivot " + time.strftime("%H%M%S"))
    list_url = args.base + path
    overview = list_url.rstrip("/") + "/uebersicht"
    results: dict[str, Any] = {"project_id": pid}
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pages = []
        for name in ("A", "B"):
            ctx = browser.new_context()
            ctx.add_init_script("localStorage.setItem('npt.clientName', 'E2E');")
            page = ctx.new_page()
            page.on("pageerror", lambda e, n=name: errors.append(f"{n}: {e}"))
            pages.append(page)
        a, b = pages
        a.goto(overview)
        expect(a.locator('[data-testid="pivot"]')).to_be_visible()
        expect(a.locator("#live-status")).to_have_attribute("data-state", "live")
        total = count_of(a, "total")
        results["total"] = total
        ja_before = count_of(a, "colsum-ja")

        # Live: Aenderung in B -> Uebersicht in A ohne Neuladen
        open_list(b, list_url)
        sid = str(b.locator(".tabulator-row").first.get_attribute("data-testid") or "")[4:]
        status, _ = api(
            b,
            "PATCH",
            f"/api/systems/{sid}",
            {"field": "affected", "value": "ja", "expected": None},
        )
        assert status == 200, status
        t0 = time.monotonic()
        expect(a.locator('[data-testid="colsum-ja"]')).to_have_text(
            str(ja_before + 1), timeout=4000
        )
        results["pivot_live_ms"] = round((time.monotonic() - t0) * 1000)
        expect(a.locator("#kpi-processed-count")).to_have_text("1")
        assert a.locator("td.changed").count() >= 1, "keine Hervorhebung"

        # Drilldown: Zellen, Summen, Gesamt, Kennzahlen
        rows = a.locator("#pivot-body tr")
        n_rows = rows.count()
        no_daz = a.locator('#pivot-body tr[data-daz=""]')
        targets = [
            ("total", ""),
            ("colsum-leer", ""),
            ("colsum-ja", ""),
            ("rowsum-0", ""),
            ("cell-0-leer", ""),
            ("kpi-yes-without-action", "#kpi-yes-count"),
            ("kpi-processed", "#kpi-processed-count"),
        ]
        if no_daz.count():
            ri = int(str(no_daz.get_attribute("data-testid")).rsplit("-", 1)[1])
            targets += [(f"rowsum-{ri}", ""), (f"cell-{ri}-leer", ""), (f"cell-{ri}-ja", "")]
        checked = []
        for testid, value_sel in targets:
            expected, shown, query = drill(a, overview, testid, value_sel)
            checked.append({"zahl": testid, "erwartet": expected, "liste": shown, "query": query})
            assert expected == shown, checked[-1]
        results["drilldowns"] = checked
        results["pivot_rows"] = n_rows
        # Vorfilter-Chip bei DAZ-Drilldown sichtbar
        a.goto(overview)
        a.locator('[data-testid="cell-0-ja"]').click()
        a.locator("#counter", has_text="Systemen angezeigt").wait_for(timeout=15000)
        expect(a.locator("#prefilters")).to_contain_text("Digital Access Zone")
        browser.close()
    results["page_errors"] = errors
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
