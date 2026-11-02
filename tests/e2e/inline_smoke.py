"""E2E: Inline-Bearbeitung in der Hauptliste (Pflichtfall aus Auftrag 10) inkl. XSS-Probe.

    python -m tests.e2e.inline_smoke --base http://127.0.0.1:8480 \\
        --admin https://localhost:8481 --pki pki-out/clients

Prueft: Auswahlfeld (System betroffen, Ausgefuehrte Aktion) speichert sofort, Textfeld
(Installierte Version) beim Verlassen, Bemerkung mehrzeilig; gruener Rahmen „gespeichert";
Werte kommen serverseitig an (API) und bleiben nach Neuladen erhalten; Plaintext-Escaping:
HTML/Script in Bemerkung/Version wird in Zelle, Konfliktdialog und Live-Hinweis als Text
gezeigt (kein Element, kein ausgefuehrtes Script); Tooltip am *-Feld; Hilfe-Link im Kopf
fuehrt zum Handbuch; Filter per URL und Export-Link; optionaler Namensdialog beim ersten
Bearbeiten (ohne vorbelegten Namen): erscheint einmal, uebernehmen oder ueberspringen, der
Name steht im Aenderungsprotokoll (Admin, ueber das mTLS-Gateway).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from typing import Any

from playwright.sync_api import Page, expect, sync_playwright

from tests.e2e.live_smoke import LIVE_MAX_MS, Admin, api, cell, open_list

XSS = '<img src=x onerror="window.__npt_xss=1"><script>window.__npt_xss=2</script>'


def server_row(page: Page, sid: str) -> dict[str, Any]:
    status, data = api(page, "GET", "/api/systems", None)
    assert status == 200
    return next(r for r in data["rows"] if r["system_id"] == sid)


def wait_server(page: Page, sid: str, field: str, value: Any, timeout_s: float = 3.0) -> None:
    """Wartet, bis der Server den Wert hat (Speichern ist 300 ms entprellt)."""
    deadline = time.monotonic() + timeout_s
    while True:
        current = server_row(page, sid)[field]
        if current == value:
            return
        assert time.monotonic() < deadline, (sid, field, current, value)
        time.sleep(0.2)


def no_xss(page: Page) -> None:
    assert page.evaluate("window.__npt_xss === undefined"), "Script aus Nutzereingabe lief!"
    assert not page.locator("#grid img").count(), "HTML aus Nutzereingabe wurde gerendert"


def name_dialog(page: Page, url: str, sid: str, name: str | None) -> str:
    """Erste Aenderung oeffnet den Dialog; danach (gemerkt) nicht mehr."""
    open_list(page, url)
    assert page.evaluate("localStorage.getItem('npt.clientName')") is None
    cell(page, sid, "affected").click()
    page.locator(".tabulator-edit-list-item", has_text="Nein").first.click()
    expect(page.locator("#name-dialog")).to_be_visible(timeout=3000)
    if name is None:
        page.click('[data-testid="name-skip"]')
    else:
        page.fill("#name-input", name)
        page.click('[data-testid="name-save"]')
    expect(page.locator("#name-dialog")).to_be_hidden(timeout=3000)
    wait_server(page, sid, "affected", "nein")  # Speichern lief erst nach dem Dialog
    assert page.evaluate("localStorage.getItem('npt.clientName')") == (name or "")
    # Zweite Aenderung: kein Dialog mehr
    cell(page, sid, "action_taken").click()
    page.locator(".tabulator-edit-list-item", has_text="Isoliert").first.click()
    wait_server(page, sid, "action_taken", "isoliert")
    expect(page.locator("#name-dialog")).to_be_hidden()
    return "Dialog einmal, danach gemerkt"


def changelog_who(admin: Admin, pid: int, sid: str) -> set[str]:
    """Menge der „Wer"-Eintraege eines Systems im Aenderungsprotokoll (Quelle Bearbeiter)."""
    query = urllib.parse.urlencode({"quelle": "ui", "sid": sid})
    _, html = admin.get(f"/admin/projekte/{pid}/aenderungen?{query}")
    pattern = r'<td data-testid="log-sid">([^<]*)</td>.*?<td data-testid="log-who">([^<]*)</td>'
    return {name.strip() for s, name in re.findall(pattern, html, flags=re.S) if s == sid}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8480")
    ap.add_argument("--admin", default="https://localhost:8481")
    ap.add_argument("--pki", default="pki-out/clients")
    args = ap.parse_args()
    admin = Admin(args.admin, args.pki)
    pid, path = admin.create_project("E2E Inline " + time.strftime("%H%M%S"))
    url = args.base + path
    results: dict[str, Any] = {"project_id": pid}
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()

        def new_page(name: str, preset_name: bool = True) -> Page:
            ctx = browser.new_context()
            if preset_name:
                ctx.add_init_script("localStorage.setItem('npt.clientName', 'E2E');")
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(f"{name}: {e}"))
            return page

        a = new_page("A")
        t0 = time.monotonic()
        open_list(a, url)
        results["list_ready_ms"] = round((time.monotonic() - t0) * 1000)
        rows = a.locator(".tabulator-row")
        sids = [str(rows.nth(i).get_attribute("data-testid") or "")[4:] for i in range(5)]

        # 1) Auswahlfeld: speichert sofort, gruener Rahmen, serverseitig angekommen
        cell(a, sids[0], "affected").click()
        a.locator(".tabulator-edit-list-item", has_text="Ja").first.click()
        expect(a.locator("#status-line")).to_contain_text("Gespeichert", timeout=3000)
        expect(cell(a, sids[0], "affected")).to_have_class(re.compile("cell-saved"), timeout=3000)
        cell(a, sids[0], "action_taken").click()
        a.locator(".tabulator-edit-list-item", has_text="Isoliert").first.click()
        expect(cell(a, sids[0], "action_taken")).to_have_text("Isoliert", timeout=3000)
        wait_server(a, sids[0], "affected", "ja")
        wait_server(a, sids[0], "action_taken", "isoliert")

        # 2) Textfeld: speichert beim Verlassen (Version), Enter beendet die Eingabe
        cell(a, sids[1], "installed_version").click()
        a.keyboard.type("2.17.1")
        a.keyboard.press("Enter")
        expect(cell(a, sids[1], "installed_version")).to_have_text("2.17.1")
        expect(a.locator("#status-line")).to_contain_text("Gespeichert", timeout=3000)
        wait_server(a, sids[1], "installed_version", "2.17.1")

        # 3) Bemerkung mehrzeilig + XSS-Probe in Bemerkung und Version
        cell(a, sids[1], "remark").click()
        editor = cell(a, sids[1], "remark").locator("textarea")
        editor.fill("Zeile 1\nZeile 2 " + XSS)
        a.locator("#counter").click()
        expect(a.locator("#status-line")).to_contain_text("Gespeichert", timeout=3000)
        expect(cell(a, sids[1], "remark")).to_contain_text("<script>window.__npt_xss=2")
        cell(a, sids[2], "installed_version").click()
        cell(a, sids[2], "installed_version").locator("input").fill(XSS[:120])
        a.locator("#counter").click()
        expect(cell(a, sids[2], "installed_version")).to_contain_text("<img src=x", timeout=3000)
        no_xss(a)
        wait_server(a, sids[1], "remark", "Zeile 1\nZeile 2 " + XSS)
        wait_server(a, sids[2], "installed_version", XSS[:120])

        # 4) Neuladen: Werte bleiben, werden weiter als Text gezeigt (Formatter)
        open_list(a, url)
        expect(cell(a, sids[0], "affected")).to_have_text("Ja")
        expect(cell(a, sids[1], "remark")).to_contain_text("Zeile 2 <img")
        no_xss(a)

        # 5) Zweiter Kontext: Live-Empfang der XSS-Werte + Konfliktdialog mit XSS-Wert
        b = new_page("B")
        open_list(b, url)
        expect(b.locator("#live-status")).to_have_attribute("data-state", "live")
        cell(b, sids[2], "remark").click()
        cell(b, sids[2], "remark").locator("textarea").fill("Wert von B")
        status, _ = api(
            a,
            "PATCH",
            f"/api/systems/{sids[2]}",
            {"field": "remark", "value": XSS, "expected": None},
        )
        assert status == 200
        expect(b.locator("#live-hint")).to_be_visible(timeout=LIVE_MAX_MS)
        expect(b.locator("#live-hint-text")).to_contain_text("<script>")
        b.locator("#counter").click()  # speichern ohne Entscheidung -> 409-Dialog
        expect(b.locator("#conflict-dialog")).to_be_visible(timeout=3000)
        expect(b.locator('[data-testid="conflict-current"]')).to_have_text(XSS)
        b.click('[data-testid="conflict-take"]')
        expect(cell(b, sids[2], "remark")).to_have_text(XSS, timeout=3000)
        no_xss(b)
        results["xss"] = "als Text angezeigt (Zelle, Live-Hinweis, Konfliktdialog)"

        # 6) Tooltip am *-Feld, Filter in der URL, Export-Link, Hilfe-Link
        a.hover('.tabulator-col[tabulator-field="affected"] .tabulator-col-title')
        expect(a.locator(".tabulator-tooltip")).to_contain_text("betroffen", timeout=3000)
        open_list(a, url + "?f.affected=ja")
        shown = int((a.text_content("#counter") or "0").split(" von ")[0])
        assert shown == 1, a.text_content("#counter")
        href = a.get_attribute("#export-view", "href") or ""
        assert "f.affected=ja" in href, href
        a.click('[data-testid="nav-hilfe"]')
        expect(a.locator('[data-testid="handbook"]')).to_contain_text("Die vier Felder mit *")
        expect(a.locator('[data-testid="handbook"]')).to_contain_text("FAQ")
        a.click('[data-testid="help-back"]')
        a.locator("#counter", has_text="Systemen angezeigt").wait_for(timeout=15000)
        results["help"] = "ok"

        # 7) Namensdialog beim ersten Bearbeiten (alle anderen Kontexte setzen den Namen vorab)
        results["name_dialog"] = {
            "uebernehmen": name_dialog(new_page("C", preset_name=False), url, sids[3], "Erika E2E"),
            "ueberspringen": name_dialog(new_page("D", preset_name=False), url, sids[4], None),
        }
        who_c, who_d = changelog_who(admin, pid, sids[3]), changelog_who(admin, pid, sids[4])
        assert who_c == {"Erika E2E"}, who_c  # beide Aenderungen mit Namen
        assert who_d == {""}, who_d  # uebersprungen: ohne Namen
        results["name_dialog"]["aenderungsprotokoll"] = "Name bzw. leer protokolliert"
        browser.close()
    results["page_errors"] = errors
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
