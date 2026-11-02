# Mitwirken

Danke für dein Interesse! Fehlerberichte und Verbesserungsvorschläge bitte als Issue,
Änderungen als Pull Request gegen `main`. Sicherheitslücken **nicht** öffentlich melden,
sondern wie in [SECURITY.md](SECURITY.md) beschrieben.

## Prüfungen vor einem Pull Request

Alle Prüfungen laufen in Containern; lokal genügt Docker.

```bash
# Lint (ruff), Format, Typen (mypy --strict) und pytest gegen eine Wegwerf-Postgres
docker compose -p npt-test -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
docker compose -p npt-test -f docker-compose.test.yml down -v

# JavaScript: Syntax + gemeinsame Filter-Testvektoren
scripts/test-js.sh

# Secret-Scan (vor jedem Push)
docker run --rm -v "$PWD:/repo:ro" zricethezav/gitleaks:v8.28.0 git /repo --no-banner --redact -v
```

Bei Änderungen an Abhängigkeiten zusätzlich:

```bash
scripts/audit-deps.sh        # bekannte Schwachstellen (pip-audit)
scripts/check-licenses.sh    # Lizenzen der Laufzeitabhängigkeiten (kein GPL/AGPL)
```

Größere Änderungen an Oberfläche oder Live-Updates bitte mit der E2E-Suite prüfen
(`scripts/run-e2e.sh`, startet einen eigenen Wegwerf-Stack).

## Konventionen

- **Sprache:** Oberfläche, Dokumentation und Kommentare auf Deutsch. In Markdown-Dateien
  mit Umlauten, in Code-Kommentaren ASCII-Umschreibungen (ae, oe, ue, ss) wie im
  bestehenden Code. Bezeichner im Code englisch.
- **Stil:** `ruff` (Konfiguration in `pyproject.toml`, Zeilenlänge 100) und `mypy --strict`
  müssen fehlerfrei sein; neue Funktionen mit Tests.
- **Abhängigkeiten:** direkte Pins in `pyproject.toml`, vollständige Hash-Pins in
  `requirements.lock` / `requirements-dev.lock` (Befehl siehe README, *Entwicklung und Tests*).
  Neue Laufzeitabhängigkeiten in `THIRD_PARTY_NOTICES.md` eintragen.
- **Vendorte Browser-Bibliotheken** (`src/npt/static/vendor`): `VERSION`-Datei mit Quelle
  und Prüfsummen, `LICENSE` daneben, Eintrag in `scripts/sbom-components.json` und
  `THIRD_PARTY_NOTICES.md` anpassen (`scripts/sbom.sh` prüft die Übereinstimmung).
- **Keine Geheimnisse** in Code, Tests, Logs oder Commits – auch keine echten Projekt-Token
  oder Zertifikate. `.env`, `pki-out/` und Schlüsseldateien stehen in `.gitignore`.
- **Entscheidungen** mit Begründung in `docs/DECISIONS.md` festhalten, Betriebsdoku in
  `docs/INSTALLATION.md` und die Handbücher (`docs/handbuch/`) mitpflegen.
- **Commits:** kurze, sachliche Betreffzeile (gern mit Bereich, z. B. `fix(mail): …`),
  im Text das Warum.

## Lizenz der Beiträge

Mit einem Beitrag erklärst du dich einverstanden, dass er unter der
[MIT-Lizenz](LICENSE) dieses Projekts veröffentlicht wird.
