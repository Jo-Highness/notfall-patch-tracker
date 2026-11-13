# Änderungen

## 0.5.0 – 2026-11-13

### Neu
- **Gemerkter Filter** (NPT-17): Suche, Spaltenfilter und Sortierung der Hauptliste gelten
  nach dem Neuladen und **projektübergreifend** weiter (z. B. einmal nach dem eigenen Namen
  filtern). Gespeichert im Browser-Cookie `npt_filter` (`Path=/p/`, `SameSite=Strict`,
  `Secure` bei HTTPS, ein Jahr); der Server wertet ihn nicht aus.
  - Filter in der URL (geteilte Links, Mail-Links) haben Vorrang. Link-Vorfilter (`x.*`)
    werden nie gemerkt.
  - Gespeichert wird nur, wenn der Nutzer den Filter ändert. „Filter zurücksetzen“ löscht
    ihn.
  - Beim Wiederherstellen erscheint ein Hinweis (auch für Screenreader).
- **Excel-Datenverbindung** (NPT-18): Die Hilfe beschreibt, wie man die Liste in Excel per
  Power Query („Daten → Aus dem Web“) als externe Datenquelle einbindet. Darauf aufbauende
  Pivot-Tabellen aktualisieren sich selbst. Der Export „alle“ wird dafür je Projekt und
  Änderungsstand zwischengespeichert, häufige Abrufe belasten den Server kaum.
- **MCP-Beispiele** in der Hilfe für GitHub Copilot (VS Code) und Kiro, jeweils mit dem
  Token im Header statt in der Datei.
- E2E-Suite `filter_cookie_smoke`, JS-Tests `tests/js/filter-persist.test.js`.

### Behoben
- Hilfe: Die MCP-Adresse enthielt einen doppelten Schrägstrich (`…/p/<token>//mcp`).

## 0.4.0 – 2026-11-13

### Neu
- **Erstinbetriebnahme im Browser** (`/einrichtung`, NPT-15): Eine neue Instanz ohne Admin
  lässt sich ohne Dateien einrichten. Man legt Seitennamen, CA-Bundle für die mTLS-Prüfung
  (Root + Intermediates), optional eine Sperrliste und den ersten Admin (Upload des
  Admin-Zertifikats, an den Aussteller gebunden) fest. Zusätzlich zeigt der Installer eine
  Prüfliste der `.env`-Werte. Geschützt durch einen Einrichtungscode aus dem App-Log
  (`python -m npt.setup_code` erzeugt einen neuen), Origin-Prüfung und Rate-Limit. Nach dem
  Abschluss ist die Seite dauerhaft weg.
- **Admin-Einstellungen** (`/admin/einstellungen`): Seitenname, CA-Bundle und Sperrliste
  (mehrere CRLs, eine je CA) ändern, mit Aussperrschutz (Kette, `ADMIN_VERIFY_DEPTH`,
  Sperrung des eigenen Zertifikats) und Audit. Notausgang `python -m npt.trust_reset`.
- **Seitenname** frei wählbar, erscheint in allen Seiten (Bearbeiter und Admin).
- **Gateway:** vertraut Datei-CA **und** Web-CA. Ohne CA läuft es im Einrichtungsmodus
  (503, nichts wird weitergeleitet). Ohne eigenes Serverzertifikat nutzt es ein
  selbstsigniertes, dessen Fingerprint der Installer anzeigt. Änderungen werden ohne
  Neustart übernommen.
- E2E der Erstinbetriebnahme auf frischem Stack: `scripts/run-setup-e2e.sh`.
- **Barrierefreiheit nach BITV 2.0 / WCAG 2.1 AA** (NPT-16, `docs/BARRIEREFREIHEIT.md`):
  - Navigation: Sprunglinks, Überschriften und Namen für Navigation, Dialoge, Filter,
    Zell-Editoren und Checkboxen.
  - Darstellung: sichtbarer Fokus, Kontraste (Feldrahmen, Warnfarben), Reflow bei 320 px.
  - Hauptliste: Sortieren per Tastatur, Feldfehler mit Fokus und Ansage, Alt+J/Alt+N für
    den Hinweis auf Änderungen anderer.
  - Editoren: Tab verlässt den Text-Editor, deutsche Werkzeugnamen.
  - Neue E2E-Suite `a11y_smoke` (axe-core) in `run-e2e.sh`.

### Geändert
- Migration `0005`: Tabelle `instance_settings`. Bestandsinstallationen mit Admins gelten
  als eingerichtet.
- `ADMIN_VERIFY_DEPTH` wird jetzt auch an die App übergeben (Compose, Helm-ConfigMap).
- Compose: neues Volume `admin_trust` (App schreibt, Gateway liest), `/run/nginx` des
  Gateways 4 MB. Helm: emptyDir `trust` im Backend-Pod, `TRUST_DIR` in der ConfigMap.
- Fehlt die Datei-CA, startet das Gateway nicht mehr mit Fehler, sondern im
  Einrichtungsmodus (503).
- CSRF-Token gelten 24 h statt 12 h (WCAG 2.2.1).
- VEX-Bewertung für vier neue Befunde des SBOM-Abgleichs: zlib (Debian: gz-Schreib-API wird
  nicht genutzt; Alpine: Eintrag betrifft GDAL), glibc wide-scanf (nicht importiert),
  CPython `getaddrinfo` (Hosts nur aus der Konfiguration). Prüfung per `nm` über alle
  ELF-Dateien. Damit keine offenen kritischen oder hohen Befunde.
- Audit-Hilfen von `npt.admins` nach `npt.audit` verschoben (Bearbeiter-App lädt das
  Admin-Modul weiterhin nicht).

### Behoben
- MCP: Tief verschachteltes JSON (unter 64 KB) führte zu HTTP 500. Jetzt kommt JSON-RPC
  -32700 (Sicherheitsreview 0.3.0).
- VEX-Begründung zu Quill (GHSA-v3m3-f69x-jf25) um die News-/FAQ-Editoren ergänzt.

## 0.3.0 – 2026-11-12

### Neu
- **News je Projekt** (`/p/<token>/news`, 20 Beiträge je Seite, Permalink je Beitrag):
  Admins schreiben Beiträge mit Rich-Text-Editor (Quill 2: Überschriften, fett, kursiv,
  Listen, Links), lesen kann jeder mit der Projekt-URL. Anlegen, Bearbeiten, Löschen im
  Admin-Bereich (`/admin/projekte/<id>/news`) mit Audit-Eintrag (NPT-13).
- **Durchsuchbare FAQ je Projekt** (`/p/<token>/faq`): Frage–Antwort-Einträge mit
  Reihenfolge; Suche serverseitig (`?q=`, jedes Wort in Frage oder Antwort) und als
  Live-Filter beim Tippen. Pflege wie die News (`/admin/projekte/<id>/faq`).
- **MCP-Server je Projekt zum Ausfüllen der Liste** (Model Context Protocol, Streamable
  HTTP, zustandslos): `POST /p/<token>/mcp` bzw. `POST /mcp` mit
  `Authorization: Bearer <token>`. Werkzeuge `get_project` (Felder, erlaubte Werte),
  `list_systems` (alle Einträge mit allen Details, Blättern, Sortierung),
  `search_systems` (Freitext, Auswahlfelder inkl. „leer“, „Feld enthält“), `get_system`,
  `update_field` (nur die vier *-Felder, Konfliktschutz über `expected`). Keine
  Administration, keine Neuanlage. Gleicher Schreibpfad wie die Liste:
  Validierung, Änderungsprotokoll, Live-Update aller offenen Listen, Rate-Limit,
  Schreibschutz abgeschlossener Projekte (NPT-14). Eigene Implementierung ohne neue
  Laufzeitabhängigkeit.
- Änderungsquelle **„MCP“** im Änderungsprotokoll.
- Navigation der Bearbeiter um „News“ und „FAQ“ ergänzt; Projektseite im Admin-Bereich mit
  Links zur Pflege und der MCP-Adresse.

### Geändert
- Rich-Text-Sanitisierung (nh3-Allowlist) aus `npt.mail.sanitize` in das neutrale Modul
  `npt.richtext` verschoben (Mailversand unverändert, `npt.mail.sanitize` reicht weiter).
- Migration `0004`: Tabellen `project_news`, `project_faq`; erweiterter CHECK-Constraint
  für `change_log.source` (Quelle `mcp`).

### Hinweise
- News und FAQ sind nicht Teil der Projekt-Backups (nur Systemdaten), sondern nur über die
  Datenbanksicherung geschützt.
- Freitexte (z. B. Bemerkungen) können Anweisungen an KI-Assistenten enthalten
  (Prompt-Injection); der MCP-Server weist Clients darauf hin, Änderungen vor dem
  Bestätigen prüfen.

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
