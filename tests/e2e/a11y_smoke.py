"""E2E: Barrierefreiheit (NPT-16, BITV 2.0 / EN 301 549 / WCAG 2.1 AA) im echten Stack.

    python -m tests.e2e.a11y_smoke --base http://127.0.0.1:8490 \\
        --admin https://localhost:8491 --pki /pki

Automatischer Teil: axe-core (Regelsaetze wcag2a, wcag2aa, wcag21a, wcag21aa, best-practice
nur informativ) auf allen Seiten der Bearbeiter- und Admin-Oberflaeche, jeweils in hellem
und dunklem Farbschema, mit geoeffneten Zustaenden (Fehlermeldung, Filter, Editor).
Zusaetzlich Tastatur-Stichproben: Sprunglink als erstes fokussierbares Element, sichtbarer
Fokus, Reflow bei 320 CSS-Pixeln ohne waagerechtes Scrollen der Seite.
axe-core liegt nur im E2E-Testabbild (/opt/axe/axe.min.js), nie in der Auslieferung.
Ein manueller Test mit Screenreader (NVDA/VoiceOver) ersetzt das nicht (docs/BARRIEREFREIHEIT.md).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from playwright.sync_api import BrowserContext, Page, sync_playwright

from tests.e2e.live_smoke import Admin

AXE_PATH = os.environ.get("AXE_PATH", "/opt/axe/axe.min.js")
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
RUN_AXE = """async (tags) => {
  const r = await axe.run(document, {runOnly: {type: 'tag', values: tags},
                                     resultTypes: ['violations', 'incomplete']});
  const slim = (v) => ({id: v.id, impact: v.impact, help: v.help,
    nodes: v.nodes.slice(0, 5).map((n) => ({target: n.target.join(' '),
      summary: (n.failureSummary || '').slice(0, 300)}))});
  return {violations: r.violations.map(slim), incomplete: r.incomplete.map((v) => v.id),
          version: axe.version};
}"""


def axe(page: Page, label: str, out: dict[str, Any]) -> None:
    # kein "networkidle": die Hauptliste haelt eine SSE-Verbindung offen
    page.wait_for_load_state("load")
    page.wait_for_timeout(400)
    if not page.evaluate("typeof window.axe !== 'undefined'"):
        page.add_script_tag(path=AXE_PATH)
    res = page.evaluate(RUN_AXE, TAGS)
    out["axe_version"] = res["version"]
    if res["violations"]:
        out["violations"][label] = res["violations"]
    if res["incomplete"]:
        out["manuell_pruefen"][label] = res["incomplete"]
    out["seiten"].append(label)


def keyboard_checks(page: Page, label: str, out: dict[str, Any]) -> None:
    """Sprunglink zuerst, sichtbarer Fokus (outline oder box-shadow)."""
    page.keyboard.press("Tab")
    first = page.evaluate(
        """() => { const a = document.activeElement; const s = getComputedStyle(a);
          return {cls: a.className, href: a.getAttribute('href') || '',
                  text: a.textContent.trim(), outline: s.outlineStyle + ' ' + s.outlineWidth,
                  shadow: s.boxShadow}; }"""
    )
    if first["href"] != "#inhalt":
        out["tastatur"][label] = f"erstes Tab-Ziel ist nicht der Sprunglink: {first}"
        return
    if first["outline"].startswith("none") and first["shadow"] in ("none", ""):
        out["tastatur"][label] = f"Fokus nicht sichtbar: {first}"
        return
    page.keyboard.press("Enter")
    target = page.evaluate("() => document.activeElement.id || document.activeElement.tagName")
    if target not in ("inhalt", "MAIN"):
        out["tastatur"][label] = f"Sprunglink setzt den Fokus nicht auf den Inhalt: {target}"


def reflow(page: Page, label: str, out: dict[str, Any]) -> None:
    page.set_viewport_size({"width": 320, "height": 640})
    page.wait_for_timeout(300)
    over = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    # Datentabellen duerfen in ihrem eigenen Container scrollen (WCAG 1.4.10 Ausnahme)
    if over > 1:
        wide = page.evaluate(
            """() => { const path = []; let e = document.body;
              while (e) { const kids = [...e.children]
                  .filter((c) => c.getBoundingClientRect().right > 322);
                if (!kids.length) { break; }
                e = kids[0];
                path.push(e.tagName + '.' + e.className + '#' + e.id + ' '
                  + Math.round(e.getBoundingClientRect().right));
                if (getComputedStyle(e).overflowX !== 'visible') { break; } }
              return path.slice(-4); }"""
        )
        out["reflow"][label] = f"Seite {over}px breiter als 320px: {wide}"
    page.set_viewport_size({"width": 1280, "height": 900})


def scan(page: Page, url: str, label: str, out: dict[str, Any], *, extra: bool = True) -> None:
    page.goto(url)
    axe(page, label, out)
    if extra:
        keyboard_checks(page, label, out)
        reflow(page, label, out)


def contexts(browser: Any, admin_base: str, pki: str, scheme: str) -> tuple[BrowserContext, ...]:
    common: dict[str, Any] = {"color_scheme": scheme, "bypass_csp": True, "locale": "de-DE"}
    pub = browser.new_context(**common)
    adm = browser.new_context(
        ignore_https_errors=True,
        client_certificates=[
            {"origin": admin_base, "certPath": f"{pki}/admin.crt", "keyPath": f"{pki}/admin.key"}
        ],
        **common,
    )
    return pub, adm


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8490")
    ap.add_argument("--admin", default="https://localhost:8491")
    ap.add_argument("--pki", default="/pki")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    admin_base = args.admin.rstrip("/")
    admin = Admin(admin_base, args.pki)
    pid, path = admin.create_project("E2E Barrierefreiheit " + time.strftime("%H%M%S"))
    purl = base + path.rstrip("/")
    out: dict[str, Any] = {
        "seiten": [],
        "violations": {},
        "manuell_pruefen": {},
        "tastatur": {},
        "reflow": {},
        "js_errors": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scheme in ("light", "dark"):
            pub, adm = contexts(browser, admin_base, args.pki, scheme)
            extra = scheme == "light"
            pp = pub.new_page()
            pp.on("pageerror", lambda e: out["js_errors"].append(str(e)))
            sfx = f" [{scheme}]"
            # Bearbeiter
            scan(pp, purl + "/", "Hauptliste" + sfx, out, extra=extra)
            pp.locator("#counter", has_text="Systemen angezeigt").wait_for(timeout=15000)
            axe(pp, "Hauptliste geladen" + sfx, out)
            scan(pp, purl + "/uebersicht", "Übersicht" + sfx, out, extra=extra)
            scan(pp, purl + "/news", "News" + sfx, out, extra=extra)
            scan(pp, purl + "/faq", "FAQ" + sfx, out, extra=extra)
            scan(pp, purl + "/faq?q=zzzkeintreffer", "FAQ ohne Treffer" + sfx, out, extra=False)
            scan(pp, purl + "/hilfe", "Hilfe" + sfx, out, extra=extra)
            scan(
                pp,
                base + "/p/ungueltig-ungueltig-ungueltig",
                "Nicht gefunden" + sfx,
                out,
                extra=extra,
            )
            # Admin
            pa = adm.new_page()
            pa.on("pageerror", lambda e: out["js_errors"].append(f"admin: {e}"))
            pa.on("dialog", lambda d: d.dismiss())
            for rel, label in (
                ("/admin/", "Admin Start"),
                (f"/admin/projekte/{pid}", "Admin Projekt"),
                (f"/admin/projekte/{pid}/aenderungen", "Admin Änderungen"),
                (f"/admin/projekte/{pid}/upload", "Admin Upload"),
                (f"/admin/projekte/{pid}/news", "Admin News"),
                (f"/admin/projekte/{pid}/news/neu", "Admin News-Editor"),
                (f"/admin/projekte/{pid}/faq/neu", "Admin FAQ-Editor"),
                (f"/admin/projekte/{pid}/mail", "Admin Mail"),
                (f"/admin/projekte/{pid}/mail/neu", "Admin Mail neu"),
                ("/admin/stammdaten", "Admin Stammdaten"),
                ("/admin/benutzer", "Admin Benutzer"),
                ("/admin/zugang", "Admin Zugang"),
                ("/admin/audit", "Admin Audit"),
                ("/admin/einstellungen", "Admin Einstellungen"),
                ("/admin/hilfe", "Admin Hilfe"),
            ):
                scan(pa, admin_base + rel, label + sfx, out, extra=extra)
            # Fehlerzustand: Formular mit ungueltiger Eingabe (Seitenname leer)
            pa.goto(admin_base + "/admin/einstellungen")
            form = pa.locator('form[action$="/einstellungen/seitenname"]')
            form.locator('input[name="site_name"]').fill(" ")
            form.locator('button[type="submit"]').click()
            axe(pa, "Admin Einstellungen Fehler" + sfx, out)
            pub.close()
            adm.close()
        browser.close()

    print(json.dumps(out, ensure_ascii=False, indent=2))
    failed = bool(out["violations"] or out["tastatur"] or out["reflow"] or out["js_errors"])
    print(
        f"Seiten: {len(out['seiten'])}, Verstöße: {len(out['violations'])} Seiten, "
        f"Tastatur: {len(out['tastatur'])}, Reflow: {len(out['reflow'])}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
