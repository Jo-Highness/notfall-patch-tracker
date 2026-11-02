"""E2E-Rauchtest Mailversand (Auftrag 6.3) gegen den Dev-Stack mit Test-SMTP Mailpit.

Es werden ausschliesslich Mails an Mailpit zugestellt (Overlay docker-compose.mailpit.yml);
die generierten Testadressen verlassen den Stack nie.

    docker build -t npt-e2e tests/e2e
    MP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \\
        npt-dev-mailpit-1)
    docker run --rm --network host -v "$PWD:/w:ro" -w /w npt-e2e \\
        python -m tests.e2e.mail_smoke --base http://127.0.0.1:8480 \\
        --admin https://localhost:8481 --pki pki-out/clients --mailpit http://$MP:8025

Ablauf: neues Projekt (alle Systeme „System betroffen" leer) -> Kampagne im Quill-Editor
(Text, fett, [LINK]) mit Selektor 1 -> Vorschau (Empfaenger/Systeme gegen eigene
Berechnung) -> Testmail -> Versand mit Bestaetigung -> Status je Empfaenger bis „gesendet"
(Auto-Refresh) -> Mailpit: Anzahl Mails = Anzahl gueltiger Empfaenger, je Adresse genau
eine, Inhalt einer Mail (Links, multipart) -> Links oeffnen die gefilterte Hauptliste.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from playwright.sync_api import expect, sync_playwright

from tests.e2e.live_smoke import Admin, open_list

sys.path.insert(0, "src")
from npt.domain.validation import parse_emails

SEND_TIMEOUT_S = 900
# Testmail laeuft ueber SMTP in Mailpit ein; nicht auf ein festes Zeitfenster verlassen.
TESTMAIL_TIMEOUT_S = 30.0
TESTMAIL_POLL_S = 0.5


def http_json(url: str, method: str = "GET") -> Any:
    req = urllib.request.Request(url, method=method)  # noqa: S310 - Test-URL
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        body = r.read()
    try:
        return json.loads(body) if body else None
    except ValueError:  # z. B. "ok" bei DELETE
        return body.decode()


def wait_for_test_mails(mp: str, timeout_s: float = TESTMAIL_TIMEOUT_S) -> list[Any]:
    """Pollt Mailpit, bis mindestens eine [TEST]-Mail da ist (oder das Zeitlimit ablaeuft)."""
    deadline = time.monotonic() + timeout_s
    while True:
        seen = [
            m
            for m in http_json(f"{mp}/api/v1/messages?limit=50")["messages"]
            if m["Subject"].startswith("[TEST] ")
        ]
        if seen or time.monotonic() >= deadline:
            return seen
        time.sleep(TESTMAIL_POLL_S)


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href, self._text = dict(attrs).get("href") or "", ""

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, self._text))
            self._href = None


def expected_recipients(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in rows:
        if row.get("affected") is not None:
            continue
        for email in parse_emails(row.get("app_owner")).valid:
            out.setdefault(email, set()).add(row["system_id"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8480")
    ap.add_argument("--admin", default="https://localhost:8481")
    ap.add_argument("--pki", default="pki-out/clients")
    ap.add_argument("--mailpit", required=True, help="Mailpit-API, z. B. http://172.18.0.5:8025")
    args = ap.parse_args()
    admin_base = args.admin.rstrip("/")
    mp = args.mailpit.rstrip("/")
    admin = Admin(admin_base, args.pki)
    pid, path = admin.create_project("E2E Mail " + time.strftime("%H%M%S"))
    token = path.rstrip("/").split("/")[-1]
    rows = http_json(f"{args.base}{path}/api/systems")["rows"]
    expected = expected_recipients(rows)
    results: dict[str, Any] = {
        "project_id": pid,
        "systems": len(rows),
        "expected_recipients": len(expected),
    }
    errors: list[str] = []
    http_json(f"{mp}/api/v1/messages", "DELETE")  # nur Test-SMTP

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
        page = actx.new_page()
        page.on("pageerror", lambda e: errors.append(f"admin: {e}"))
        page.on(
            "console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None
        )
        page.on(
            "dialog", lambda d: (results.setdefault("dialogs", []).append(d.message), d.accept())
        )

        # 1) Kampagne im Quill-Editor anlegen (Selektor 1 ist vorausgewaehlt)
        page.goto(f"{admin_base}/admin/projekte/{pid}/mail/neu")
        editor = page.locator("#mail-editor .ql-editor")
        expect(editor).to_be_visible()
        expect(page.locator("#mail-body")).to_be_hidden()
        page.fill('[data-testid="mail-subject"]', "E2E Notfall CVE-2026-0001: bitte prüfen")
        editor.click()
        page.keyboard.type("Hallo,")
        page.keyboard.press("Enter")
        page.keyboard.press("Control+b")
        page.keyboard.type("bitte heute prüfen")
        page.keyboard.press("Control+b")
        page.keyboard.type(":")
        page.keyboard.press("Enter")
        page.click('[data-testid="mail-insert-link"]')
        page.keyboard.press("Enter")
        page.keyboard.type("Danke")
        expect(page.locator('[data-testid="mail-selector-betroffen_leer"]')).to_be_checked()
        page.click('[data-testid="mail-save"]')
        expect(page.locator('[data-testid="mail-preview"]')).to_be_visible()
        cid = int(re.search(r"/mail/(\d+)", page.url).group(1))  # type: ignore[union-attr]
        results["campaign_id"] = cid
        n_rec = int(page.locator('[data-testid="mail-recipient-count"]').inner_text())
        n_sys = int(page.locator('[data-testid="mail-system-count"]').inner_text())
        results["preview"] = {"recipients": n_rec, "systems": n_sys}
        assert n_rec == len(expected), (n_rec, len(expected))
        assert n_sys == len(rows), (n_sys, len(rows))
        sample_body = page.locator('[data-testid="mail-sample-body"]')
        expect(sample_body.locator("strong")).to_have_text("bitte heute prüfen")
        expect(sample_body.locator("a", has_text="Alle meine Systeme")).to_have_count(1)
        assert "[LINK]" not in sample_body.inner_html()
        results["skipped_systems"] = int(
            page.locator('[data-testid="mail-skipped-count"]').inner_text()
        )

        # 2) Testmail an frei gewaehlte (Test-)Adresse
        page.fill('[data-testid="mail-test-to"]', "e2e-pruefer@npt.test")
        page.click('[data-testid="mail-test-send"]')
        expect(page.locator(".msg-ok")).to_contain_text("Testmail")
        tests_seen = wait_for_test_mails(mp)
        assert len(tests_seen) == 1, tests_seen
        assert tests_seen[0]["To"][0]["Address"] == "e2e-pruefer@npt.test"

        # 3) Versand mit Bestaetigung (Dialog mit Empfaengerzahl + Checkbox)
        page.check('[data-testid="mail-send-confirm"]')
        t0 = time.monotonic()
        page.click('[data-testid="mail-send-submit"]')
        page.wait_for_load_state()
        if page.locator(".msg-err").count():
            raise AssertionError(f"{page.url}: {page.locator('.msg-err').first.inner_text()}")
        expect(page.locator(".msg-ok")).to_contain_text("Versand wurde gestartet")
        assert f"Mail jetzt an {n_rec} Empfänger senden?" in results["dialogs"], results["dialogs"]
        expect(page.locator('[data-testid="mail-status"]')).to_be_visible()
        # Statusanzeige aktualisiert sich selbst; am Ende laedt die Seite neu.
        gesendet = page.locator('[data-testid="mail-count-gesendet"]')
        expect(gesendet).to_have_text(str(n_rec), timeout=SEND_TIMEOUT_S * 1000)
        results["send_s"] = round(time.monotonic() - t0, 1)
        results["rate_per_s"] = round(n_rec / results["send_s"], 2)
        expect(page.locator('[data-testid="mail-send-submit"]')).to_contain_text(
            "Erneut an alle senden", timeout=30_000
        )
        assert int(page.locator('[data-testid="mail-count-fehlgeschlagen"]').inner_text()) == 0

        # 4) Mailpit: genau eine Mail je gueltiger Adresse
        listing = http_json(f"{mp}/api/v1/messages?limit=10000")
        msgs = [m for m in listing["messages"] if not m["Subject"].startswith("[TEST]")]
        tos = [m["To"][0]["Address"].lower() for m in msgs]
        assert all(len(m["To"]) == 1 and not m.get("Cc") and not m.get("Bcc") for m in msgs)
        results["mailpit_mails"] = len(msgs)
        assert len(msgs) == n_rec, (len(msgs), n_rec)
        assert len(set(tos)) == len(tos)
        assert set(tos) == set(expected)

        # 5) Inhalt einer Mail mit mehreren Systemen
        target = max(expected, key=lambda e: (len(expected[e]), e))
        msg_id = next(m["ID"] for m in msgs if m["To"][0]["Address"].lower() == target)
        full = http_json(f"{mp}/api/v1/message/{msg_id}")
        parser = Links()
        parser.feed(full["HTML"])
        links = parser.links
        sys_links = links[:-1]
        collective = links[-1]
        assert collective[1] == "Alle meine Systeme", collective
        got_ids = set()
        for href, label in sys_links:
            q = urllib.parse.parse_qsl(urllib.parse.urlsplit(href).query)
            assert href.startswith(f"{args.base}/p/{token}/?"), href
            assert len(q) == 1, q
            assert q[0][0] == "x.system_id", q
            assert label.endswith(f"({q[0][1]})"), label
            got_ids.add(q[0][1])
        assert got_ids == expected[target], (got_ids, expected[target])
        assert full["Text"].count(f"{args.base}/p/{token}/?x.system_id=") >= len(got_ids)
        assert "[LINK]" not in full["HTML"] + full["Text"]
        raw = urllib.request.urlopen(f"{mp}/api/v1/message/{msg_id}/raw", timeout=30).read()  # noqa: S310
        for part in (b"multipart/alternative", b"text/plain", b"text/html"):
            assert part in raw, part
        assert b"Message-ID:" in raw or b"Message-Id:" in raw
        results["sample"] = {"to": target, "systems": len(got_ids)}

        # 6) Links oeffnen die gefilterte Hauptliste (Bearbeiter-Browser ohne Zertifikat)
        uctx = browser.new_context()
        user = uctx.new_page()
        user.on("pageerror", lambda e: errors.append(f"liste: {e}"))
        open_list(user, collective[0])
        n = len(expected[target])
        expect(user.locator("#counter")).to_have_text(f"{n} von {len(rows)} Systemen angezeigt")
        open_list(user, sys_links[0][0])
        expect(user.locator("#counter")).to_have_text(f"1 von {len(rows)} Systemen angezeigt")
        sid = urllib.parse.parse_qsl(urllib.parse.urlsplit(sys_links[0][0]).query)[0][1]
        expect(user.locator(f'[data-testid="row-{sid}"]')).to_be_visible()
        browser.close()

    results["errors"] = errors
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
