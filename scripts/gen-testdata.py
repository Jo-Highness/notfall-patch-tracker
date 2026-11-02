#!/usr/bin/env python3
"""Testdaten-Generator: Stammdaten-Excel mit realistischen, frei erfundenen Systemen.

Auftrag 10: 2.000 Systeme, ca. 15 Digital Access Zones, ca. 400
Anwendungsverantwortliche – einige mit mehreren Systemen, einige mit mehreren
Adressen (getrennt durch ``;`` oder ``,``), einige mit ungueltigen Adressen.
Ausgabe nach ``testdata-out/`` (gitignored). Keine echten Unternehmensdaten.

    python scripts/gen-testdata.py                       # testdata-out/stammdaten.xlsx
    python scripts/gen-testdata.py --systems 500 --seed 7 --out /tmp/x.xlsx
    python scripts/gen-testdata.py --with-errors         # + doppelte/leere IDs, NEW-Praefix
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from npt.domain import fields

ZONES = [
    "DAZ-Internet",
    "DAZ-Extranet",
    "DAZ-DMZ-Web",
    "DAZ-DMZ-Mail",
    "DAZ-Intranet",
    "DAZ-Backoffice",
    "DAZ-Finanzen",
    "DAZ-Personal",
    "DAZ-Produktion",
    "DAZ-Labor",
    "DAZ-OT",
    "DAZ-Management",
    "DAZ-Partner",
    "DAZ-Cloud",
    "DAZ-Legacy",
]
SBF = [
    "SBF-01 Vertrieb",
    "SBF-02 Finanzen",
    "SBF-03 Personal",
    "SBF-04 IT-Betrieb",
    "SBF-05 Produktion",
    "SBF-06 Einkauf",
    "SBF-07 Recht",
    "SBF-08 Kommunikation",
]
APP_TYPES = [
    "Standardsoftware",
    "Individualsoftware",
    "SaaS",
    "Middleware",
    "Datenbank",
    "Webanwendung",
    "Fachverfahren",
    "Infrastrukturdienst",
]
LIFECYCLE = ["Planung", "Einführung", "Betrieb", "Auslauf", "Abschaltung geplant"]
CIO_UNITS = [
    "CIO-Zentral",
    "CIO-Region Nord",
    "CIO-Region Süd",
    "CIO-Region West",
    "CIO-Shared Services",
    "CIO-Digital",
]
PREFIX = [
    "Kunden",
    "Rechnungs",
    "Personal",
    "Lager",
    "Bestell",
    "Termin",
    "Dokumenten",
    "Vertrags",
    "Zeit",
    "Reise",
    "Kassen",
    "Mess",
    "Labor",
    "Wartungs",
    "Portal",
    "Melde",
    "Archiv",
    "Planungs",
    "Berichts",
    "Zugangs",
]
SUFFIX = [
    "portal",
    "verwaltung",
    "dienst",
    "manager",
    "system",
    "plattform",
    "auskunft",
    "schnittstelle",
    "datenbank",
    "server",
    "app",
    "cockpit",
]
FIRST = [
    "Anna",
    "Ben",
    "Clara",
    "David",
    "Eva",
    "Felix",
    "Greta",
    "Hannes",
    "Ida",
    "Jonas",
    "Katrin",
    "Lukas",
    "Mia",
    "Noah",
    "Olga",
    "Paul",
    "Rita",
    "Stefan",
    "Tina",
    "Uwe",
    "Vera",
    "Wolfgang",
    "Yvonne",
    "Zoe",
    "Jörg",
    "Gülsen",
    "Søren",
    "Renée",
]
LAST = [
    "Albers",
    "Becker",
    "Conrad",
    "Dietrich",
    "Engel",
    "Fischer",
    "Graf",
    "Hoffmann",
    "Iske",
    "Jansen",
    "Krüger",
    "Lorenz",
    "Möller",
    "Neumann",
    "Otto",
    "Peters",
    "Quast",
    "Richter",
    "Schulz",
    "Thiel",
    "Ullrich",
    "Vogel",
    "Wagner",
    "Zimmermann",
]
DOMAIN = "beispiel-firma.test"


def ascii_name(text: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "é": "e", "ø": "o"})
    return text.lower().translate(table)


def make_owners(rng: random.Random, count: int) -> list[str]:
    owners: list[str] = []
    used: set[str] = set()
    while len(owners) < count:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        local = f"{ascii_name(first)}.{ascii_name(last)}"
        n = 2
        base = local
        while local in used:
            local = f"{base}{n}"
            n += 1
        used.add(local)
        addr = f"{local}@{DOMAIN}"
        roll = rng.random()
        if roll < 0.04:  # ungueltig: fehlendes @ bzw. Leerzeichen
            value = rng.choice([f"{local} at {DOMAIN}", f"{local}@", f"{first} {last}"])
        elif roll < 0.10:  # mehrere Adressen
            sep = rng.choice(["; ", ", ", ";"])
            second = f"team-{ascii_name(last)}@{DOMAIN}"
            value = f"{addr}{sep}{second}"
        elif roll < 0.16:  # Anzeigename mit Adresse
            value = f"{first} {last} <{addr}>"
        elif roll < 0.18:  # Grossschreibung/Leerzeichen (Normalisierung)
            value = f"  {addr.upper()} "
        else:
            value = addr
        owners.append(value)
    return owners


def generate(systems: int, seed: int, with_errors: bool) -> list[list[object]]:
    rng = random.Random(seed)
    owners = make_owners(rng, max(1, round(systems / 5)))
    # Verteilung: einige Verantwortliche mit vielen Systemen (Zipf-artig).
    weights = [1.0 / (i + 1) ** 0.6 for i in range(len(owners))]
    rows: list[list[object]] = []
    for n in range(1, systems + 1):
        name = f"{rng.choice(PREFIX)}{rng.choice(SUFFIX)}"
        if rng.random() < 0.3:
            name += f" {rng.randint(1, 9)}"
        zone = rng.choice(ZONES) if rng.random() > 0.01 else None  # wenige ohne DAZ
        host = ascii_name(name).replace(" ", "-")
        rows.append(
            [
                f"APP-{n:05d}",
                name,
                rng.choice(SBF),
                zone,
                f"{name} für den Bereich {rng.choice(SBF)[7:]}; erfundene Testdaten.",
                rng.choices(owners, weights)[0],
                rng.choice(APP_TYPES),
                rng.choice(LIFECYCLE),
                f"{host}-{n:05d}.{DOMAIN}" if rng.random() > 0.05 else None,
                rng.choice(CIO_UNITS),
                f"FK-{rng.randint(100000, 999999)}",
            ]
        )
    if with_errors:
        rows.append(list(rows[0]))  # doppelte ID
        rows.append([None, "Ohne ID", *rows[1][2:]])  # leere ID
        rows.append(["NEW-0001", "Reserviert", *rows[2][2:]])  # reserviertes Praefix
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Testdaten-Generator (Stammdaten-Excel)")
    parser.add_argument("--systems", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("testdata-out/stammdaten.xlsx"))
    parser.add_argument("--with-errors", action="store_true")
    args = parser.parse_args(argv)

    rows = generate(args.systems, args.seed, args.with_errors)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Stammdaten"
    ws.append([fields.DISPLAY_NAMES[f] for f in fields.MASTER_FIELDS])
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(args.out)
    owners = {str(r[5]) for r in rows}
    zones = {r[3] for r in rows if r[3]}
    print(f"{args.out}: {len(rows)} Zeilen, {len(zones)} DAZ, {len(owners)} Verantwortliche")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
