"""E2E: gemerkter Filter projektuebergreifend (NPT-17, Cookie npt_filter).

    python -m tests.e2e.filter_cookie_smoke --base http://127.0.0.1:8490 \\
        --admin https://localhost:8491 --pki /pki

Prueft im Browser: Suche setzen -> Neuladen -> Suche wieder aktiv (mit Hinweis); anderes
Projekt -> gleiche Suche aktiv; Link mit Vorfilter hat Vorrang und ueberschreibt den
gemerkten Filter nicht; Cookie-Attribute (Path=/p/, SameSite=Strict, ~1 Jahr);
"Filter zuruecksetzen" loescht ihn; der Admin-Bereich bekommt den Cookie nicht.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from playwright.sync_api import Page, expect, sync_playwright

from tests.e2e.live_smoke import Admin, open_list

COOKIE = "npt_filter"


def filter_cookie(page: Page) -> dict[str, Any] | None:
    for c in page.context.cookies():
        if c["name"] == COOKIE:
            return dict(c)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8490")
    ap.add_argument("--admin", default="https://localhost:8491")
    ap.add_argument("--pki", default="/pki")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    admin = Admin(args.admin, args.pki)
    stamp = time.strftime("%H%M%S")
    _, path_a = admin.create_project("E2E Filter A " + stamp)
    _, path_b = admin.create_project("E2E Filter B " + stamp)
    url_a, url_b = base + path_a, base + path_b
    results: dict[str, Any] = {}
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))

        # 1) Suche setzen (System-ID der ersten Zeile, damit genau ein Treffer bleibt)
        open_list(page, url_a)
        assert filter_cookie(page) is None, "Cookie ohne Nutzeraktion gesetzt"
        sid = page.locator('[data-testid^="row-"]').first.get_attribute("data-testid")
        assert sid
        term = sid.removeprefix("row-")
        page.fill('[data-testid="global-search"]', term)
        expect(page.locator("#counter")).to_contain_text("1 von", timeout=5000)
        page.wait_for_timeout(500)
        c = filter_cookie(page)
        assert c, "Cookie fehlt nach Suche"
        assert c["path"] == "/p/", c
        assert c["sameSite"] == "Strict", c
        assert c["expires"] > time.time() + 300 * 86400, c
        results["cookie"] = {"path": c["path"], "sameSite": c["sameSite"]}

        # 2) Neuladen ohne Parameter -> gemerkter Filter aktiv, mit Hinweis
        open_list(page, url_a)
        expect(page.locator('[data-testid="global-search"]')).to_have_value(term)
        expect(page.locator("#counter")).to_contain_text("1 von")
        expect(page.locator("#status-line")).to_contain_text("Gemerkter Filter angewendet")
        assert f"q={term}" in page.url, page.url
        results["neuladen"] = "ok"

        # 3) Anderes Projekt -> gleiche Suche
        open_list(page, url_b)
        expect(page.locator('[data-testid="global-search"]')).to_have_value(term)
        expect(page.locator("#counter")).to_contain_text("1 von")
        results["projektwechsel"] = "ok"

        # 4) Link mit Vorfilter (wie aus einer Mail) hat Vorrang, ueberschreibt nichts
        open_list(page, url_b + "/?x.system_id=GIBT-ES-NICHT")
        expect(page.locator('[data-testid="global-search"]')).to_have_value("")
        expect(page.locator('[data-testid="prefilters"]')).to_be_visible()
        page.wait_for_timeout(500)
        c = filter_cookie(page)
        assert c, "Vorfilter-Link hat den gemerkten Filter geloescht"
        assert term in c["value"], ("Vorfilter-Link hat den gemerkten Filter veraendert", c)
        open_list(page, url_b)
        expect(page.locator('[data-testid="global-search"]')).to_have_value(term)
        results["url_vorrang"] = "ok"

        # 5) Admin-Bereich (anderer Pfad) bekommt den Cookie nicht
        assert all(ck["path"] == "/p/" for ck in ctx.cookies() if ck["name"] == COOKIE), (
            ctx.cookies()
        )

        # 6) Zuruecksetzen loescht den gemerkten Filter
        page.click('[data-testid="reset-filters"]')
        expect(page.locator('[data-testid="global-search"]')).to_have_value("")
        page.wait_for_timeout(500)
        c = filter_cookie(page)
        assert c is None or "q=" not in c["value"], c
        open_list(page, url_a)
        expect(page.locator('[data-testid="global-search"]')).to_have_value("")
        results["zuruecksetzen"] = "ok"
        browser.close()

    if errors:
        results["js_errors"] = errors
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
