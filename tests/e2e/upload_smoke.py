"""E2E-Rauchtest Admin-Upload mit Konfliktbestaetigung (Auftrag 6.1, Pflichtfall aus 10).

Laeuft gegen einen Stack mit Stammdaten; der Admin-Browser nutzt das Admin-Client-
Zertifikat am mTLS-Gateway (Playwright ``client_certificates``):

    docker build -t npt-e2e tests/e2e
    docker run --rm --network host -v "$PWD:/w:ro" -w /w npt-e2e \\
        python -m tests.e2e.upload_smoke --base http://127.0.0.1:8480 \\
        --admin https://localhost:8481 --pki pki-out/clients

Prueft: Hinweis „leere Zellen loeschen nicht" am Upload-Dialog, Vorschau mit Konflikten /
Neubefuellungen / unbekannter ID / unveraendert, einzelne Konfliktbestaetigung (der
nicht ausgewaehlte Konflikt behaelt den Projektwert, leere Zellen loeschen nichts),
Live-Update in einer offenen Hauptliste ohne Neuladen, Backup „vor Import", erneute
Bestaetigung nach zwischenzeitlicher Bearbeitung, „alle Konflikte", Aenderungsprotokoll,
Restore des Backups „vor Import" mit Neuladen der offenen Liste.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from typing import Any

from openpyxl import Workbook
from playwright.sync_api import Page, expect, sync_playwright

from tests.e2e.live_smoke import LIVE_MAX_MS, Admin, api, cell, open_list

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def xlsx(rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(page: Page, admin_base: str, pid: int, data: bytes) -> None:
    page.goto(f"{admin_base}/admin/projekte/{pid}/upload")
    expect(page.locator('[data-testid="upload-empty-cells-hint"]')).to_contain_text(
        "Leere Zellen löschen keine vorhandenen Werte"
    )
    page.set_input_files(
        '[data-testid="project-upload-file"]',
        {"name": "e2e-upload.xlsx", "mimeType": XLSX_MIME, "buffer": data},
    )
    page.click('[data-testid="project-upload-submit"]')
    expect(page.locator('[data-testid="upload-summary"]')).to_be_visible()


def count(page: Page, testid: str) -> int:
    return int(page.locator(f'[data-testid="{testid}"]').inner_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8480")
    ap.add_argument("--admin", default="https://localhost:8481")
    ap.add_argument("--pki", default="pki-out/clients")
    args = ap.parse_args()
    admin_base = args.admin.rstrip("/")
    admin = Admin(admin_base, args.pki)
    pid, path = admin.create_project("E2E Upload " + time.strftime("%H%M%S"))
    list_url = args.base + path
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
        ap_page.on("dialog", lambda d: d.accept())  # data-confirm bestaetigen
        uctx = browser.new_context()
        uctx.add_init_script("localStorage.setItem('npt.clientName', 'E2E');")
        user = uctx.new_page()
        user.on("pageerror", lambda e: errors.append(f"liste: {e}"))
        open_list(user, list_url)
        expect(user.locator("#live-status")).to_have_attribute("data-state", "live")
        rows = user.locator(".tabulator-row")
        sids = [str(rows.nth(i).get_attribute("data-testid") or "")[4:] for i in range(3)]
        a, b, c = sids
        # Ausgangslage: A und B bereits bearbeitet (-> Konflikte), B mit Bemerkung
        for sid in (a, b):
            status, _ = api(
                user,
                "PATCH",
                f"/api/systems/{sid}",
                {"field": "affected", "value": "nein", "expected": None},
            )
            assert status == 200, status
        api(
            user,
            "PATCH",
            f"/api/systems/{b}",
            {"field": "remark", "value": "bleibt", "expected": None},
        )
        header = ["System-ID", "System betroffen*", "Bemerkung*", "Installierte Version*"]
        data = xlsx(
            [
                header,
                [a, "Ja", None, "2.0"],  # Konflikt affected (bestaetigt), Neubefuellung Version
                [b, "In Prüfung", None, None],  # Konflikt (nicht bestaetigt), Bemerkung leer
                [c, None, None, None],  # unveraendert
                ["E2E-UNBEKANNT", "Ja", None, None],
            ]
        )

        # 1) Vorschau
        upload(ap_page, admin_base, pid, data)
        results["preview"] = {
            k: count(ap_page, f"count-{k}")
            for k in ("conflicts", "fills", "unknown", "unchanged", "errors")
        }
        assert results["preview"] == {
            "conflicts": 2,
            "fills": 1,
            "unknown": 1,
            "unchanged": 1,
            "errors": 0,
        }, results["preview"]
        expect(ap_page.locator(f'[data-testid="conflict-{a}-affected"] td.old')).to_have_text(
            "Nein"
        )
        expect(ap_page.locator(f'[data-testid="conflict-{a}-affected"] td.new')).to_have_text("Ja")

        # 2) Einzelbestaetigung: nur A; Live-Update in der offenen Liste
        ap_page.check(f'[data-testid="conflict-take-{a}-affected"]')
        ap_page.click('[data-testid="upload-apply"]')
        expect(ap_page.locator(".msg-ok")).to_contain_text("Upload wurde übernommen")
        t0 = time.monotonic()
        expect(cell(user, a, "affected")).to_have_text("Ja", timeout=LIVE_MAX_MS)
        results["live_ms"] = round((time.monotonic() - t0) * 1000)
        expect(cell(user, a, "installed_version")).to_have_text("2.0", timeout=LIVE_MAX_MS)
        expect(cell(user, b, "affected")).to_have_text("Nein")
        expect(cell(user, b, "remark")).to_have_text("bleibt")
        assert user.locator('[data-testid="row-E2E-UNBEKANNT"]').count() == 0

        # 3) Backup vor Import sichtbar (Phase 7: Backup-Liste auf der Projektseite)
        ap_page.goto(f"{admin_base}/admin/projekte/{pid}")
        backups = ap_page.locator('[data-testid="backup-list"]')
        if backups.count():
            expect(backups).to_contain_text("vor Import")
            results["backup_vor_import"] = "ok"

        # 4) Zwischenzeitliche Bearbeitung -> aktualisierte Vorschau, erneut bestaetigen
        upload(ap_page, admin_base, pid, data)
        assert count(ap_page, "count-conflicts") == 1  # nur noch B
        ap_page.check(f'[data-testid="conflict-take-{b}-affected"]')
        status, _ = api(
            user,
            "PATCH",
            f"/api/systems/{b}",
            {"field": "affected", "value": "ja", "expected": "nein"},
        )
        assert status == 200, status
        ap_page.click('[data-testid="upload-apply"]')
        expect(ap_page.locator('[data-testid="upload-stale"]')).to_be_visible()
        expect(ap_page.locator(f'[data-testid="conflict-{b}-affected"] td.old')).to_have_text("Ja")
        assert not ap_page.is_checked(f'[data-testid="conflict-take-{b}-affected"]')
        ap_page.click('[data-testid="upload-apply-all"]')
        expect(ap_page.locator(".msg-ok")).to_contain_text("Upload wurde übernommen")
        expect(cell(user, b, "affected")).to_have_text("In Prüfung", timeout=LIVE_MAX_MS)
        results["stale_reconfirm"] = "ok"

        # 5) Aenderungsprotokoll
        ap_page.goto(f"{admin_base}/admin/projekte/{pid}/aenderungen?quelle=admin_upload")
        total = int(ap_page.locator('[data-testid="log-total"]').inner_text())
        assert total == 3, total  # A affected + A Version, spaeter B affected
        expect(ap_page.locator('[data-testid="log-table"]')).to_contain_text("Admin-Upload")
        results["changelog_entries"] = total

        # 6) Restore des ersten Backups „vor Import" -> offene Liste laedt neu (reload)
        ap_page.goto(f"{admin_base}/admin/projekte/{pid}")
        # Aeltestes „vor Import"-Backup (nicht einfach die letzte Zeile: der Scheduler kann
        # nach der ersten Bearbeitung schon ein zeitgesteuertes Backup angelegt haben, H2).
        first = (
            ap_page.locator('[data-testid="backup-list"] tbody tr')
            .filter(has_text="vor Import")
            .last
        )
        expect(first).to_contain_text("vor Import")
        first.locator('a[data-testid^="backup-restore-"]').click()
        expect(ap_page.locator('[data-testid="restore-dialog"]')).to_be_visible()
        ap_page.check('[data-testid="restore-confirm"]')
        ap_page.click('[data-testid="restore-submit"]')
        expect(ap_page.locator(".msg-ok")).to_contain_text("wiederhergestellt")
        t0 = time.monotonic()
        expect(cell(user, a, "affected")).to_have_text("Nein", timeout=4000)
        results["restore_reload_ms"] = round((time.monotonic() - t0) * 1000)
        expect(cell(user, a, "installed_version")).to_have_text("")
        expect(cell(user, b, "affected")).to_have_text("Nein")
        expect(ap_page.locator('[data-testid="backup-list"]')).to_contain_text(
            "vor Wiederherstellung"
        )
        browser.close()
    results["page_errors"] = errors
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
