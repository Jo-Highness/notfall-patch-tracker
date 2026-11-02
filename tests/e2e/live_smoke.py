"""E2E-Rauchtest gegen einen laufenden Stack: Live-Updates zwischen zwei Browser-Kontexten.

Kein Teil der pytest-Suite (Dateiname ohne ``test_``); QA baut die E2E-Suite aus.
Voraussetzung: Stammdaten sind importiert, Admin-Client-Zertifikat liegt vor.

    docker build -t npt-e2e tests/e2e
    docker run --rm --network host -v "$PWD:/w:ro" -w /w npt-e2e \\
        python -m tests.e2e.live_smoke --base http://127.0.0.1:8480 \\
        --admin https://localhost:8481 --pki pki-out/clients

Prueft: Aenderung in Kontext A erscheint in B <= 2 s (mit Hervorhebung), Fokus-Schutz mit
Hinweis „Behalten", Konfliktdialog bei gleichzeitiger Aenderung desselben Felds,
neues System erscheint sofort, Polling-Fallback bei blockiertem SSE,
Projektabschluss -> Schreibschutz in der UI, Token-Wechsel -> Hinweis „Link ungueltig".
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from typing import Any

from playwright.sync_api import Page, expect, sync_playwright

LIVE_MAX_MS = 2000


class Admin:
    """Minimaler Admin-Client ueber das mTLS-Gateway (Formular-POST mit CSRF + Origin)."""

    def __init__(self, base: str, pki: str) -> None:
        self.base = base.rstrip("/")
        ctx = ssl.create_default_context(cafile=f"{pki}/ca.crt")
        ctx.load_cert_chain(f"{pki}/admin.crt", f"{pki}/admin.key")
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    def get(self, path: str) -> tuple[str, str]:
        with self.opener.open(self.base + path, timeout=30) as r:
            return r.geturl(), r.read().decode()

    def csrf(self, html: str) -> str:
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
        assert m, "CSRF-Token fehlt"
        return m.group(1)

    def post(self, path: str, form: dict[str, str]) -> tuple[str, str]:
        _, html = self.get("/admin/")  # CSRF-Scope ist der Admin, nicht die Seite
        data = urllib.parse.urlencode({"csrf_token": self.csrf(html), **form}).encode()
        req = urllib.request.Request(  # noqa: S310 - feste https-URL
            self.base + path, data=data, headers={"Origin": self.base}
        )
        with self.opener.open(req, timeout=60) as r:
            return r.geturl(), r.read().decode()

    def create_project(self, name: str) -> tuple[int, str]:
        url, html = self.post("/admin/projekte", {"name": name, "description": "E2E"})
        pid = int(re.search(r"/admin/projekte/(\d+)", url).group(1))  # type: ignore[union-attr]
        m = re.search(r'id="project-url" value="([^"]+)"', html)
        assert m
        return pid, urllib.parse.urlsplit(m.group(1)).path

    def project_path(self, pid: int) -> str:
        _, html = self.get(f"/admin/projekte/{pid}")
        m = re.search(r'id="project-url" value="([^"]+)"', html)
        assert m
        return urllib.parse.urlsplit(m.group(1)).path

    def set_status(self, pid: int, status: str) -> None:
        self.post(f"/admin/projekte/{pid}/status", {"status": status})

    def new_token(self, pid: int) -> None:
        self.post(f"/admin/projekte/{pid}/token", {})


def cell(page: Page, sid: str, field: str) -> Any:
    return page.locator(f'[data-testid="row-{sid}"] .tabulator-cell[tabulator-field="{field}"]')


API_JS = """async ([method, path, body]) => {
  const t = document.querySelector('meta[name="csrf-token"]').content;
  const base = location.pathname.replace(/\\/$/, '');
  const r = await fetch(base + path, {method, headers: {'Content-Type': 'application/json',
    'X-CSRF-Token': t}, body: body === null ? undefined : JSON.stringify(body)});
  return [r.status, await r.json()];
}"""


def api(page: Page, method: str, path: str, body: Any = None) -> tuple[int, Any]:
    status, data = page.evaluate(API_JS, [method, path, body])
    return int(status), data


def open_list(page: Page, url: str) -> None:
    page.goto(url)
    page.locator("#counter", has_text="Systemen angezeigt").wait_for(timeout=15000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8480")
    ap.add_argument("--admin", default="https://localhost:8481")
    ap.add_argument("--pki", default="pki-out/clients")
    args = ap.parse_args()
    admin = Admin(args.admin, args.pki)
    pid, path = admin.create_project("E2E Live " + time.strftime("%H%M%S"))
    url = args.base + path
    results: dict[str, Any] = {"project_id": pid}
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()

        def new_page(name: str, block_sse: bool = False) -> Page:
            ctx = browser.new_context()
            ctx.add_init_script("localStorage.setItem('npt.clientName', 'E2E');")
            if block_sse:
                ctx.route("**/api/events*", lambda route: route.abort())
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(f"{name}: {e}"))
            return page

        a, b = new_page("A"), new_page("B")
        for page in (a, b):
            open_list(page, url)
            expect(page.locator("#live-status")).to_have_attribute("data-state", "live")
        rows = a.locator(".tabulator-row")
        sids = [str(rows.nth(i).get_attribute("data-testid") or "")[4:] for i in range(4)]

        # 1) Aenderung per UI in A erscheint in B (<= 2 s, hervorgehoben)
        cell(a, sids[0], "affected").click()
        a.locator(".tabulator-edit-list-item", has_text="Ja").first.click()
        t0 = time.monotonic()
        target = cell(b, sids[0], "affected")
        expect(target).to_have_text("Ja", timeout=LIVE_MAX_MS)
        results["live_ms"] = round((time.monotonic() - t0) * 1000)
        expect(target).to_have_class(re.compile("cell-flash"), timeout=500)
        expect(a.locator("#status-line")).to_contain_text("Gespeichert")

        # 2) Fokus-Schutz: B tippt in Bemerkung, A aendert dasselbe Feld -> Hinweis, B behaelt
        cell(b, sids[1], "remark").click()
        editor = cell(b, sids[1], "remark").locator("textarea")
        editor.fill("von B")
        status, _ = api(
            a,
            "PATCH",
            f"/api/systems/{sids[1]}",
            {"field": "remark", "value": "von A", "expected": None},
        )
        assert status == 200, status
        expect(b.locator("#live-hint")).to_be_visible(timeout=LIVE_MAX_MS)
        expect(b.locator("#live-hint-text")).to_contain_text("von A")
        assert editor.input_value() == "von B", "Feld unter dem Cursor wurde ueberschrieben"
        b.click('[data-testid="live-hint-keep"]')
        expect(b.locator("#live-hint")).to_be_hidden()
        b.locator("#counter").click()  # Editor verlassen -> speichern
        expect(b.locator("#status-line")).to_contain_text("Gespeichert", timeout=3000)
        assert not b.locator("#conflict-dialog[open]").count()
        expect(cell(a, sids[1], "remark")).to_have_text("von B", timeout=LIVE_MAX_MS)
        results["focus_keep"] = "ok"

        # 3) Konflikt: Hinweis ignorieren und speichern -> Dialog, ueberschreiben
        cell(b, sids[2], "installed_version").click()
        cell(b, sids[2], "installed_version").locator("input").fill("1.0")
        api(
            a,
            "PATCH",
            f"/api/systems/{sids[2]}",
            {"field": "installed_version", "value": "2.0", "expected": None},
        )
        expect(b.locator("#live-hint")).to_be_visible(timeout=LIVE_MAX_MS)
        b.locator("#counter").click()
        expect(b.locator("#conflict-dialog")).to_be_visible(timeout=3000)
        expect(b.locator('[data-testid="conflict-current"]')).to_have_text("2.0")
        b.click('[data-testid="conflict-overwrite"]')
        expect(cell(a, sids[2], "installed_version")).to_have_text("1.0", timeout=3000)
        results["conflict_overwrite"] = "ok"

        # 4) Neues System erscheint sofort beim anderen Kontext
        before = b.text_content("#counter") or ""
        total = int(before.split(" von ")[1].split()[0])
        status, created = api(
            a,
            "POST",
            "/api/systems",
            {
                "system_name": "E2E neu",
                "sbf": "SBF-E2E",
                "digital_access_zone": "DAZ-E2E",
                "system_description": "E2E",
                "app_owner": "e2e@firma.test",
                "app_type": "SaaS",
                "lifecycle": "Betrieb",
                "fqdn": "e2e.test",
                "cio_unit": "CIO-E2E",
                "foreign_key": "FK",
            },
        )
        assert status == 201, (status, created)
        expect(b.locator("#counter")).to_contain_text(f"von {total + 1} ", timeout=LIVE_MAX_MS)
        results["new_system"] = created["row"]["system_id"]

        # 5) Polling-Fallback: SSE blockiert -> Abgleich alle 5 s
        c = new_page("C", block_sse=True)
        open_list(c, url)
        expect(c.locator("#live-status")).to_have_attribute(
            "data-state", re.compile("polling|connecting"), timeout=5000
        )
        api(
            a,
            "PATCH",
            f"/api/systems/{sids[3]}",
            {"field": "affected", "value": "nein", "expected": None},
        )
        t0 = time.monotonic()
        expect(cell(c, sids[3], "affected")).to_have_text("Nein", timeout=8000)
        results["polling_ms"] = round((time.monotonic() - t0) * 1000)
        expect(c.locator("#live-status")).to_have_attribute("data-state", "polling")

        # 6) Projekt abschliessen -> Schreibschutz ohne Neuladen
        admin.set_status(pid, "abgeschlossen")
        expect(b.locator("#live-banner")).to_contain_text("schreibgeschützt", timeout=LIVE_MAX_MS)
        expect(b.locator("#new-system-open")).to_be_hidden()
        cell(b, sids[0], "remark").click()
        assert not cell(b, sids[0], "remark").locator("textarea").count(), (
            "Editor trotz Schreibschutz"
        )
        admin.set_status(pid, "aktiv")
        expect(b.locator("#live-banner")).to_be_hidden(timeout=LIVE_MAX_MS)
        results["readonly_toggle"] = "ok"

        # 7) Token neu -> alter Link ungueltig
        admin.new_token(pid)
        expect(b.locator("#live-banner")).to_contain_text("nicht mehr gültig", timeout=LIVE_MAX_MS)
        expect(b.locator("#live-status")).to_have_attribute("data-state", "gone")
        results["token_change"] = "ok"
        browser.close()
    results["page_errors"] = errors
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
