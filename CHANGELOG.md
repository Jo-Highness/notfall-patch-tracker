# Änderungen

## 0.2.1 – 2026-11-02

Sicherheitsnachtrag: Befunde aus dem SBOM-Schwachstellenabgleich beseitigt
(`docs/DECISIONS.md` K14).

### Geändert
- **App-Abbild auf Debian 13** (`python:3.12.16-slim-trixie`, per Digest). Die
  Laufzeitstufe enthält keine ungenutzten Debian-Pakete mehr (u. a. util-linux, PAM,
  ncurses-Programme, SQLite, apt, debconf, perl, tar); ein Bauschritt prüft, dass alle
  verbleibenden Programme und Module ihre Bibliotheken finden. Damit keine kritischen und
  hohen Befunde mehr im App-Abbild (vorher 3 kritische, 57 hohe).
- **Quill-Editor mit aktueller lodash-es-Version neu gebündelt** (4.18.1 statt 4.17.21,
  reproduzierbar mit `scripts/vendor-quill/build.sh`); Verhalten unverändert. Die SBOM
  nennt die gebündelten Pakete jetzt exakt, einschließlich der bisher fehlenden
  fast-diff, lodash.clonedeep und lodash.isequal.
- Weitere ungenutzte Pakete aus apt und PAM entfernt (u. a. sqv, nettle, seccomp,
  SELinux-Verwaltung, audit). Der Bau bricht ab, wenn ein zu entfernendes Paket fehlt
  (z. B. nach einem neuen Basisabbild umbenannt) oder der verbleibende Paketstand von
  `scripts/runtime-packages.txt` abweicht.
- **Stammdaten-API-Abruf folgt Weiterleitungen nur noch auf `https://` mit demselben Host
  und Port**; andere Weiterleitungen brechen ab, der Token wird nie an ein anderes Ziel
  gesendet.
- **Docker-Healthcheck rollenabhängig** (`python -m npt.probe --docker`): Container mit
  `APP_ROLE` ohne `public` (z. B. `admin,jobs`) werden jetzt „healthy“; mit `public` bzw.
  `all` wie bisher `/healthz`.
- **VEX-Bewertung** nicht behebbarer Befunde (`vex/notfall-patch-tracker.openvex.json`,
  OpenVEX) mit Begründung je Befund; `scripts/sbom.sh --scan` berücksichtigt sie, der
  Bericht zeigt nur noch offene Befunde und meldet veraltete Bewertungen.

### Hinweise zum Update
- Das Python-Modul `ssl` nutzt jetzt OpenSSL 3.5 (vorher 3.0). Sehr alte SMTP-Server bzw.
  Gegenstellen mit schwachen Zertifikaten oder Altprotokollen können abgewiesen werden;
  nach dem Update eine Testmail senden. Die Datenbank-Verbindung ist nicht betroffen.
- Im App-Abbild gibt es keinen Paketmanager mehr (`apt-get`, `dpkg`-Installation). Eigene
  Erweiterungen setzen auf dem Basisabbild auf.

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
