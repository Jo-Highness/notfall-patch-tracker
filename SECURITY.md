# Sicherheitsrichtlinie

## Schwachstellen melden

Bitte melde Sicherheitslücken **nicht** über öffentliche Issues, Pull Requests oder
Diskussionen. Nutze stattdessen die vertrauliche Meldefunktion von GitHub:

1. Im Repository auf **Security** → **Advisories** → **Report a vulnerability** gehen
   (GitHub *Private Vulnerability Reporting*).
2. Beschreibe die Schwachstelle möglichst reproduzierbar: betroffene Version bzw. Commit,
   Komponente (Bearbeiter-App, Admin-App, admin-gateway, TLS-Proxy `public-tls`,
   Compose-Konfiguration, Helm-Chart, `deploy.sh`),
   Voraussetzungen, Schritte, erwartete und tatsächliche Wirkung.
3. Bitte keine echten Projekt-Token, Zertifikate, Schlüssel oder personenbezogenen Daten
   mitschicken; Beispieldaten genügen (`scripts/gen-testdata.py`, `scripts/gen-test-pki.sh`).

Ablauf: Eingangsbestätigung angestrebt innerhalb von 7 Tagen, danach gemeinsame Bewertung.
Die Behebung erfolgt in einem privaten Security Advisory; veröffentlicht wird nach
Bereitstellung eines Fixes (koordinierte Offenlegung), auf Wunsch mit Nennung der meldenden
Person. Zugesicherte Reaktionszeiten gibt es nicht.

## Unterstützte Versionen

Sicherheitskorrekturen gibt es für den jeweils neuesten Stand des Default-Branches bzw. das
neueste Release.

## Geltungsbereich

Im Geltungsbereich sind der Code dieses Repositorys, die Dockerfiles, die mitgelieferten
Compose-Dateien, das Helm-Chart (`deploy/helm/notfall-patch-tracker`) und die
Ausroll-Skripte (`deploy.sh`, `deploy/helm/deploy.sh`). Schwachstellen in Abhängigkeiten oder Basisabbildern bitte zusätzlich beim
jeweiligen Projekt melden; ein Hinweis hier ist willkommen, wenn der Notfall-Patch-Tracker
dadurch konkret ausnutzbar ist.

Nicht im Geltungsbereich: Befunde, die nur mit der Test-PKI aus `scripts/gen-test-pki.sh`
oder mit bewusst unsicheren Testeinstellungen auftreten, sowie fehlende Härtung einer
eigenen Betriebsumgebung (Reverse Proxy, Host).

Das Sicherheitskonzept mit allen Maßnahmen und Nachweisen steht in
[docs/SECURITY.md](docs/SECURITY.md); die Zusammensetzung der Software in der SBOM
([docs/SBOM.md](docs/SBOM.md)).
