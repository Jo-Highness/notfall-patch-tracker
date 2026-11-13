"""E2E: Erstinbetriebnahme im Browser auf einem frischen Stack ohne CA/Admin (NPT-15).

    python -m tests.e2e.setup_smoke --base http://127.0.0.1:8492 \\
        --admin https://localhost:8493 --pki /pki --code XXXXX-XXXXX-XXXXX-XXXXX

Prueft: Gateway im Einrichtungsmodus (503), Installer mit Code (Seitenname, CA-Bundle,
Sperrliste, Admin-Zertifikat), Fingerprint des selbstsignierten Gateway-Zertifikats,
Uebernahme durch das Gateway OHNE Neustart (Admin per mTLS -> 200, gesperrtes und
fremdes Zertifikat abgewiesen), Installer danach 404, Seitenname im Admin-Bereich.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from tests.e2e.a11y_smoke import axe

SITE = "Lagezentrum E2E"


def admin_get(url: str, pki: str, client: str | None) -> tuple[int, str]:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # selbstsigniert; Fingerprint wird separat geprueft
    if client:
        ctx.load_cert_chain(f"{pki}/clients/{client}.crt", f"{pki}/clients/{client}.key")
    try:
        with urllib.request.urlopen(url, context=ctx, timeout=10) as r:  # noqa: S310
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (ssl.SSLError, ConnectionError, urllib.error.URLError) as exc:
        return 0, type(exc).__name__


def server_fingerprint(admin: str) -> str:
    parts = urlsplit(admin)
    pem = ssl.get_server_certificate((parts.hostname or "localhost", parts.port or 443))
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--admin", required=True)
    ap.add_argument("--pki", required=True)
    ap.add_argument("--code", required=True)
    args = ap.parse_args()
    admin = args.admin.rstrip("/")
    results: dict[str, Any] = {}
    errors: list[str] = []
    a11y: dict[str, Any] = {"seiten": [], "violations": {}, "manuell_pruefen": {}}

    status, body = admin_get(f"{admin}/admin/", args.pki, None)
    assert status == 503, (status, body)
    assert "noch nicht eingerichtet" in body, body
    results["gateway_einrichtungsmodus"] = "503"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(bypass_csp=True).new_page()  # CSP: axe-core per Script-Tag
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{args.base}/einrichtung")
        axe(page, "Einrichtung Anmeldung", a11y)
        page.fill('[data-testid="setup-code"]', "falsch")
        page.click('[data-testid="setup-login"]')
        expect(page.locator(".msg-err")).to_contain_text("stimmt nicht")
        axe(page, "Einrichtung Fehler", a11y)
        page.fill('[data-testid="setup-code"]', args.code.lower())
        page.click('[data-testid="setup-login"]')
        expect(page.locator('[data-testid="setup-site-name"]')).to_be_visible()
        expect(page.locator('[data-testid="setup-finish"]')).to_be_disabled()
        axe(page, "Einrichtung Schritte", a11y)

        page.fill('[data-testid="setup-site-name"]', SITE)
        page.click('[data-testid="setup-site-save"]')
        expect(page.locator(".msg-ok")).to_contain_text("Seitenname")
        expect(page.locator(".brand")).to_have_text("Notfall-Patch-Tracker")  # erst nach Abschluss

        page.set_input_files('[data-testid="setup-ca-file"]', f"{args.pki}/clients/ca.crt")
        page.click('[data-testid="setup-ca-upload"]')
        expect(page.locator('[data-testid="setup-ca-list"]')).to_contain_text("Root")

        page.set_input_files('input[name="crl_datei"]', f"{args.pki}/gateway/crl.pem")
        page.locator('input[name="crl_datei"]').locator("xpath=ancestor::form").locator(
            "button"
        ).click()
        expect(page.locator('[data-testid="setup-crl-ok"]')).to_be_visible()

        # Privater Schluessel wird abgewiesen
        page.set_input_files('[data-testid="setup-admin-file"]', f"{args.pki}/clients/admin.key")
        page.click('[data-testid="setup-admin-upload"]')
        expect(page.locator(".msg-err")).to_contain_text("privaten Schlüssel")

        page.set_input_files('[data-testid="setup-admin-file"]', f"{args.pki}/clients/admin.crt")
        page.fill('[data-testid="setup-admin-name"]', "E2E Admin")
        page.click('[data-testid="setup-admin-upload"]')
        expect(page.locator('[data-testid="setup-admin"]')).to_contain_text("E2E Admin")
        checks = page.locator('[data-testid="setup-checks"]').inner_text()
        page.click('[data-testid="setup-finish"]')
        expect(page.locator('[data-testid="setup-done"]')).to_be_visible()
        shown = page.locator('[data-testid="setup-fingerprint"]').inner_text()
        axe(page, "Einrichtung abgeschlossen", a11y)
        results["installer"] = "ok"

        page.goto(f"{args.base}/einrichtung")
        assert "Nicht gefunden" in page.content()
        # Seitenname je Prozess bis zu 30 s zwischengespeichert
        deadline = time.monotonic() + 45
        while True:
            page.goto(f"{args.base}/")
            if page.locator(".brand").inner_text() == SITE:
                break
            assert time.monotonic() < deadline, "Seitenname im Bearbeiter-Bereich fehlt"
            time.sleep(3)
        browser.close()

    actual = server_fingerprint(admin)
    assert shown.replace(":", "").lower() == actual, (shown, actual)
    assert "Gateway-Serverzertifikat" in checks
    results["fingerprint"] = "stimmt"

    t0 = time.monotonic()
    while True:
        status, body = admin_get(f"{admin}/admin/", args.pki, "admin")
        if status == 200:
            break
        if time.monotonic() - t0 > 120:
            raise AssertionError(f"Gateway hat die CA nicht uebernommen: {status} {body[:200]}")
        time.sleep(2)
    results["gateway_uebernahme_s"] = round(time.monotonic() - t0, 1)
    assert SITE in body, "Seitenname fehlt im Admin-Bereich"
    for client in ("revoked", "foreign"):
        status, _ = admin_get(f"{admin}/admin/", args.pki, client)
        assert status in (0, 400, 403), (client, status)
    results["gesperrt_und_fremd"] = "abgewiesen"
    status, body = admin_get(f"{admin}/admin/einstellungen", args.pki, "admin")
    assert status == 200
    assert "Ihr Admin-Zertifikat wird von dieser CA abgedeckt" in body
    results["einstellungen"] = "ok"

    results["barrierefreiheit"] = {"seiten": len(a11y["seiten"]), "verstoesse": a11y["violations"]}
    if a11y["violations"]:
        errors.append("axe: Verstoesse auf " + ", ".join(a11y["violations"]))
    if errors:
        results["js_errors"] = errors
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
