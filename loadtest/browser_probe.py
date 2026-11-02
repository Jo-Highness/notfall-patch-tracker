"""Browser-Messung waehrend des Lasttests: Initialladen der Hauptliste in Chromium.

Locust misst nur HTTP (HTML + JSON). Diese Probe misst, was ein Nutzer erlebt: Zeit vom
Aufruf der Projekt-URL bis die Liste mit allen Zeilen bedienbar ist (Zaehler „x von y
Systemen angezeigt" sichtbar, erste Zeilen gerendert). Laeuft mit dem E2E-Image:

    docker run --rm --network host -v "$PWD:/w:ro" -w /w npt-e2e \\
        python loadtest/browser_probe.py --url http://127.0.0.1:8480/p/<token>/ --runs 10

Ausgabe: JSON mit Einzelwerten, Median und Maximum (ms).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--pause", type=float, default=3.0, help="Sekunden zwischen den Laeufen")
    args = ap.parse_args()
    ready_ms: list[float] = []
    dom_ms: list[float] = []
    total = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for _ in range(args.runs):
            ctx = browser.new_context()  # kalter Cache je Lauf
            page = ctx.new_page()
            t0 = time.perf_counter()
            page.goto(args.url, wait_until="domcontentloaded")
            dom_ms.append((time.perf_counter() - t0) * 1000)
            page.locator("#counter", has_text="Systemen angezeigt").wait_for(timeout=30000)
            page.locator(".tabulator-row").first.wait_for(timeout=30000)
            ready_ms.append((time.perf_counter() - t0) * 1000)
            text = page.text_content("#counter") or ""
            total = int(text.split(" von ")[1].split()[0])
            ctx.close()
            time.sleep(args.pause)
        browser.close()
    print(
        json.dumps(
            {
                "rows": total,
                "runs": args.runs,
                "ready_ms": [round(v) for v in ready_ms],
                "ready_median_ms": round(statistics.median(ready_ms)),
                "ready_max_ms": round(max(ready_ms)),
                "domcontentloaded_median_ms": round(statistics.median(dom_ms)),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
