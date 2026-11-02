# Änderungen

## 0.2.0 – 2026-11-02

Erste öffentliche Fassung.

### Neu
- **Kubernetes-Betrieb per Helm-Chart** (`deploy/helm/notfall-patch-tracker`, Anleitung
  `docs/KUBERNETES.md`): getrennte Deployments für den Bearbeiter-Bereich (mehrere Replikas)
  und das Backend (Admin, Jobs, Backups) mit mTLS-Gateway als Sidecar; Datenbank-Migration
  als initContainer unter einer Datenbank-Sperre, sodass gleichzeitig startende Pods nicht
  kollidieren; NetworkPolicies, PodSecurity `restricted`, Ingress oder Gateway-API,
  Prüfung des Datenbank-Verbindungsbudgets beim Rendern; `deploy/helm/deploy.sh` mit
  Abbild-Abgleich nach dem Rollout.
- **Optionales HTTPS-Overlay** für den Bearbeiter-Bereich (`docker-compose.tls.yml`):
  nginx terminiert TLS, Klartext-Aufrufe werden auf HTTPS umgeleitet, Projekt-Links werden
  im Zugriffsprotokoll maskiert.
- **Lizenz MIT**, Übersicht der Drittlizenzen (`THIRD_PARTY_NOTICES.md`) und
  Lizenzprüfung (`scripts/check-licenses.sh`).
- **SBOM** im Format CycloneDX 1.6 für Quellbaum und Abbilder (`scripts/sbom.sh`,
  `docs/SBOM.md`); die SBOMs hängen am Release.
- Hauptliste: bearbeitbare Spalten sind gelb hinterlegt und mit ✎ markiert, leere
  Pflichtfelder zeigen „bitte ausfüllen“, Zeilen sind abwechselnd eingefärbt.
- `SECURITY.md` (Meldeweg für Sicherheitslücken) und `CONTRIBUTING.md`.

### Geändert
- Basisabbilder per Digest gepinnt: Python 3.12.16, nginx 1.31.8. Gateway- und
  TLS-Proxy-Abbild ohne ungenutzte nginx-Module und ohne curl; damit keine kritischen
  oder hohen Befunde mehr in diesen Abbildern.
- Das Gateway lädt Serverzertifikat, Client-CA und Sperrliste bei Änderung selbst neu.
- Ungültige Einstellungen führen zu einer kurzen Fehlerzeile ohne Werte statt eines
  Tracebacks; Passwörter erscheinen auch bei fehlerhafter `DATABASE_URL` nicht im Log.
- Der Jobs-Prozess meldet sich auch während langer Backup-Läufe (Heartbeat für die
  Liveness-Probe).

### Behoben
- Ein Leerzeichen im Datenbank-Passwort wurde in der Verbindungs-URL falsch kodiert.

## 0.1.0 – 2026-10-27

Erste Version (nicht öffentlich).
