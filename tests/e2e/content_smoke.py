"""E2E: News und FAQ (NPT-13) sowie MCP-Endpunkt (NPT-14) im echten Stack.

    python -m tests.e2e.content_smoke --base http://127.0.0.1:8480 \\
        --admin https://localhost:8481 --pki pki-out/clients

Prueft im Browser: Admin legt per Quill-Editor (fett, Link) einen News-Beitrag an
(mTLS-Client-Zertifikat am Gateway); Bearbeiter sieht ihn mit Formatierung, ohne
ausgefuehrtes Script; FAQ mit Live-Filter beim Tippen und serverseitiger Suche ohne JS;
Navigation News/FAQ. Danach MCP per HTTP: initialize, tools/list, update_field
(Pfad- und Bearer-Variante); die Aenderung erscheint live in der geoeffneten Hauptliste.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from typing import Any

from playwright.sync_api import expect, sync_playwright

from tests.e2e.live_smoke import Admin, cell, open_list

XSS = '<p>Text</p><img src=x onerror="window.__npt_xss=1"><script>window.__npt_xss=2</script>'


def mcp(base: str, path: str, body: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(  # noqa: S310 - feste http-URL des Teststacks
        base + path, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        data: dict[str, Any] = json.loads(r.read())
        return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8480")
    ap.add_argument("--admin", default="https://localhost:8481")
    ap.add_argument("--pki", default="pki-out/clients")
    args = ap.parse_args()
    admin_base = args.admin.rstrip("/")
    admin = Admin(admin_base, args.pki)
    pid, path = admin.create_project("E2E Inhalte " + time.strftime("%H%M%S"))
    url = args.base + path
    token = path.rstrip("/").split("/")[-1]
    results: dict[str, Any] = {"project_id": pid}
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        actx = browser.new_context(
            ignore_https_errors=True,
            client_certificates=[
                {
                    "origin": admin_base,
                    "certPath": f"{args.pki}/admin.crt",
                    "keyPath": f"{args.pki}/admin.key",
                }
            ],
        )
        ap_page = actx.new_page()
        ap_page.on("pageerror", lambda e: errors.append(f"admin: {e}"))
        ap_page.on("dialog", lambda d: d.accept())

        # 1) News per Editor anlegen: Titel, fetter Text, Link
        ap_page.goto(f"{admin_base}/admin/projekte/{pid}/news/neu")
        editor = ap_page.locator('[data-testid="content-editor"] .ql-editor')
        expect(editor).to_be_visible(timeout=5000)
        expect(ap_page.locator('[data-testid="content-body"]')).to_be_hidden()
        ap_page.fill('[data-testid="content-title"]', "Patch verfügbar")
        editor.click()
        ap_page.keyboard.press("Control+b")
        ap_page.keyboard.type("Wichtig:")
        ap_page.keyboard.press("Control+b")
        ap_page.keyboard.type(" Hersteller-Hinweis ")
        ap_page.evaluate(
            "() => { const q = window.nptRichtextEditor; const i = q.getLength() - 1;"
            " q.insertText(i, 'hier', 'link', 'https://example.test/advisory', 'user'); }"
        )
        ap_page.click('[data-testid="content-save"]')
        expect(ap_page.locator(".msg-ok")).to_contain_text("gespeichert", timeout=5000)
        results["news_editor"] = "ok"

        # 2) Bearbeiter liest News (Formatierung, Link mit rel, kein XSS)
        admin.post(f"/admin/projekte/{pid}/news", {"title": "Zweiter", "body_html": XSS})
        uctx = browser.new_context()
        uctx.add_init_script("localStorage.setItem('npt.clientName', 'E2E');")
        user = uctx.new_page()
        user.on("pageerror", lambda e: errors.append(f"bearbeiter: {e}"))
        user.goto(url + "/news")
        first = user.locator(".news-item").filter(has_text="Patch verfügbar")
        expect(first.locator("strong")).to_have_text("Wichtig:")
        link = first.locator('a[href="https://example.test/advisory"]')
        expect(link).to_have_attribute("rel", "noopener noreferrer")
        assert user.evaluate("window.__npt_xss === undefined"), "Script aus News lief!"
        assert user.locator(".news-item img").count() == 0
        expect(user.locator('[data-testid="nav-news"]')).to_have_attribute("aria-current", "page")
        results["news_public"] = "ok"

        # 3) FAQ: Live-Filter beim Tippen und Server-Suche
        for q, a, order in (
            (
                "Wie melde ich ein betroffenes System?",
                "<p>Spalte <b>System betroffen</b></p>",
                "10",
            ),
            (
                "Wo finde ich den Patch?",
                "<p>Beim <a href='https://example.test'>Hersteller</a></p>",
                "20",
            ),
        ):
            admin.post(
                f"/admin/projekte/{pid}/faq",
                {"question": q, "answer_html": a, "sort_order": order},
            )
        user.click('[data-testid="nav-faq"]')
        items = user.locator(".faq-item")
        expect(items).to_have_count(2)
        user.fill('[data-testid="faq-search"]', "hersteller")
        expect(user.locator(".faq-item:visible")).to_have_count(1)
        expect(user.locator(".faq-item:visible summary")).to_have_text("Wo finde ich den Patch?")
        expect(user.locator('[data-testid="faq-count"]')).to_have_text("1 von 2 Einträgen")
        user.fill('[data-testid="faq-search"]', "gibtsnicht")
        expect(user.locator('[data-testid="faq-empty"]')).to_be_visible()
        nojs = browser.new_context(java_script_enabled=False).new_page()
        nojs.goto(url + "/faq?q=betroffen")
        expect(nojs.locator(".faq-item")).to_have_count(1)
        results["faq"] = "ok"

        # 4) MCP: initialize/tools/list, Schreiben erscheint live in der Liste
        open_list(user, url)
        expect(user.locator("#live-status")).to_have_attribute("data-state", "live")
        sid = str(user.locator(".tabulator-row").first.get_attribute("data-testid") or "")[4:]
        init = mcp(
            args.base,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            token,
        )
        assert init["result"]["protocolVersion"] == "2025-06-18", init
        tools = mcp(args.base, path + "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert len(tools["result"]["tools"]) == 5, tools
        upd = mcp(
            args.base,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "update_field",
                    "arguments": {
                        "system_id": sid,
                        "field": "remark",
                        "value": "via MCP",
                        "expected": None,
                        "client_name": "E2E-KI",
                    },
                },
            },
            token,
        )
        assert upd["result"]["isError"] is False, upd
        t0 = time.monotonic()
        expect(cell(user, sid, "remark")).to_have_text("via MCP", timeout=5000)
        results["mcp_live_ms"] = round((time.monotonic() - t0) * 1000)
        browser.close()
    if errors:
        results["js_errors"] = errors
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
