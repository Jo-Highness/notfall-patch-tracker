# Notfall-Patch-Tracker

Webanwendung, mit der bei einer kritischen Schwachstelle (z. B. einer neuen CVE) alle
Anwendungen einer Organisation schnell auf Betroffenheit geprüft werden: Aus einem
gepflegten Stammdatenbestand erzeugt ein Admin ein Projekt, das viele Personen parallel
über eine nicht erratbare Projekt-URL bearbeiten. Der Admin-Bereich ist nur per mTLS
(Client-Zertifikat) erreichbar.

Lizenz: [MIT](LICENSE) · Drittkomponenten: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) ·
SBOM: [docs/SBOM.md](docs/SBOM.md) · Schwachstellen melden: [SECURITY.md](SECURITY.md)

## Funktionen

- **Stammdaten** per Excel-Import mit Vorschau, Validierungsbericht und Import-Historie
- **Projekte** als Kopie der Stammdaten, erreichbar über eine geheime, neu generierbare
  Projekt-URL; Status aktiv / abgeschlossen / archiviert
- **Hauptliste** mit Inline-Bearbeitung, Filtern und Sortierung je Spalte, Volltextsuche,
  teilbaren Sichten (Filter in der URL), Excel-Export und Neuanlage von Systemen
- **Live-Updates** (Server-Sent Events, Polling-Fallback) mit Konflikterkennung: niemand
  überschreibt unbemerkt die Eingabe eines anderen
- **Übersicht** (Pivot je Zone und Status), jede Zahl als Drilldown in die Liste
- **Admin-Upload** von Excel-Korrekturen mit feldgenauer Konfliktbestätigung und
  vollständigem Änderungsprotokoll
- **Backups** zeitgesteuert und vor jedem Import/Restore, Download als JSON/Excel,
  Wiederherstellung in einer Transaktion
- **Mailversand** an Anwendungsverantwortliche mit personalisierten Links, Testmail,
  gedrosseltem Hintergrundversand und Status je Empfänger
- **Sicherheit:** mTLS-Admin-Bereich auf eigenem Listener, CSRF/Origin-Prüfung,
  Rate-Limits, strikte Security-Header, Token nie in Logs, Formel-Injection-Schutz beim
  Excel-Export, non-root/read-only-Container
- **Handbücher** für Bearbeiter und Admins direkt in der Anwendung

<!-- Screenshots (Platzhalter): docs/img/hauptliste.png, docs/img/uebersicht.png, docs/img/admin-projekt.png -->

Technik: Python 3.12, FastAPI, SQLAlchemy, PostgreSQL 16, nginx (mTLS-Gateway),
Tabulator und Quill im Browser; Betrieb mit Docker Compose oder auf Kubernetes per
Helm-Chart (Abschnitt [Kubernetes](#kubernetes)).

**Dokumentation:** [Installation](docs/INSTALLATION.md) ·
[Bearbeiter-Handbuch](docs/handbuch/bearbeiter.md) (in der App unter `/p/<token>/hilfe`) ·
[Admin-Handbuch](docs/handbuch/admin.md) (`/admin/hilfe`) ·
[Sicherheitskonzept](docs/SECURITY.md) · [Lasttest](docs/LOADTEST.md) ·
[Architektur](docs/ARCHITECTURE.md) · [Entscheidungen](docs/DECISIONS.md) ·
[Kubernetes](docs/KUBERNETES.md) · [SBOM](docs/SBOM.md) · [Mitwirken](CONTRIBUTING.md)

## Ablauf „Notfall tritt ein“ in 5 Schritten

1. **Stammdaten aktuell?** Admin-Bereich → *Stammdaten*: letzten Import prüfen, ggf.
   aktuelle Excel-Liste hochladen → Vorschau → *Übernehmen*.
2. **Projekt anlegen:** *Projekte* → *Neues Projekt* (Name, z. B. CVE-Nummer, Beschreibung)
   – erzeugt eine Kopie der Stammdaten.
3. **Link verteilen:** Projektseite → Projekt-URL *Kopieren* und an die Prüfenden geben,
   zusätzlich *Mailversand* an alle Anwendungsverantwortlichen (Zielgruppe „System
   betroffen noch leer“, Text mit `[LINK]` = Links auf ihre Systeme).
4. **Bearbeiten lassen und verfolgen:** Bearbeiter füllen die vier *-Felder direkt in der
   Liste; Fortschritt in der Projektliste und der *Übersicht* (Pivot, jede Zahl klickbar);
   Erinnerungsmails an „noch leer“ bzw. „Ja ohne Aktion“; Korrekturen per Excel-Upload.
5. **Abschließen:** *Status* → *Abschließen* (schreibgeschützt, weiter lesbar), Excel
   exportieren, manuell angelegte Systeme exportieren und im Quellsystem nachpflegen,
   später archivieren.

## Aufbau

| Dienst | Aufgabe | Netz/Port |
|---|---|---|
| `app` | FastAPI; Supervisor `npt-run` startet Bearbeiter-App (:8000), Admin-App (:8001, nur `admin_net`) und Jobs | Bearbeiter-Port veröffentlicht (Default `127.0.0.1:8080`) |
| `db` | PostgreSQL 16 | nur `db_net` (intern) |
| `admin-gateway` | nginx, terminiert mTLS, reicht geprüfte Zertifikatsdaten an den Admin-Port | mTLS-Port veröffentlicht (Default `0.0.0.0:8443`) |

Architektur und Begründungen: `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`.

## Schnellstart mit Docker Compose (Test-PKI)

Voraussetzungen: Docker mit Compose-Plugin, `openssl`, `bash`.

```bash
scripts/gen-test-pki.sh                    # erzeugt ./pki-out (nur für Tests!)
cp .env.example .env
# in .env setzen:
#   POSTGRES_PASSWORD=$(openssl rand -hex 24)
#   CSRF_SECRET=$(openssl rand -hex 32)
#   ADMIN_BOOTSTRAP_IDS=<Inhalt von pki-out/admin-bootstrap-id.txt>
#   ADMIN_CRL_PATH=/etc/npt-gateway/pki/crl.pem      (optional)
./deploy.sh                                # oder: docker compose up -d --build
```

- Bearbeiter-Port: `http://127.0.0.1:8080/healthz`, `/readyz`
- Admin-Bereich: `https://localhost:8443/admin/` mit Client-Zertifikat
  (`pki-out/clients/admin.p12`, Passwort `npt-test`, in den Browser importieren) bzw.
  `curl --cacert pki-out/clients/ca.crt --cert pki-out/clients/admin.crt --key pki-out/clients/admin.key https://localhost:8443/admin/`

Migrationen laufen beim Start automatisch (Advisory Lock), danach werden die Admins
aus `ADMIN_BOOTSTRAP_IDS` angelegt.

Die Test-PKI ist nur zum Ausprobieren gedacht. Für den Betrieb (eigene CA, Reverse Proxy
vor dem Bearbeiter-Port, Backups, Updates) siehe [docs/INSTALLATION.md](docs/INSTALLATION.md);
ein optionales HTTPS-Overlay für den Bearbeiter-Bereich liegt in `docker-compose.tls.yml`.

## Admin-Zugang

- Identifikation über `ADMIN_CERT_ID_ATTRIBUTE`: `subject_dn` (Default), `san_email`,
  `san_upn` oder `fingerprint_sha256`. Identifier werden normalisiert (Reihenfolge,
  Leerzeichen, Groß-/Kleinschreibung im DN egal; RFC 4514 und OpenSSL-Slash-Format).
- Identifier eines Zertifikats ermitteln:
  `openssl x509 -in cert.pem -noout -subject -nameopt RFC2253`
- Wer ein gültiges Zertifikat der konfigurierten CA, aber kein Admin-Recht hat, sieht
  seinen Identifier und kann eine Freischaltung anfragen; Admins bestätigen oder lehnen
  unter **Admin-Benutzer** ab. Dort lassen sich Admins auch direkt per Identifier
  hinzufügen, deaktivieren und entfernen. Der letzte aktive Admin ist geschützt.
- Alle Admin-Aktionen stehen im Audit-Log (`/admin/audit`).
- Admin-Handbuch mit dem Ablauf „Notfall tritt ein“ Schritt für Schritt: Link **Hilfe** im
  Kopf des Admin-Bereichs (`/admin/hilfe`, nur über das Gateway).

## Stammdaten (Admin)

Unter **Stammdaten** (`/admin/stammdaten`):

1. Excel-Datei (.xlsx) hochladen – Spalten wie in der **Vorlage** (Download auf der
   Seite); Kopfzeile tolerant gegenüber Groß-/Kleinschreibung und Leerzeichen, Pflicht ist
   „System-ID".
2. **Vorschau** prüfen: Anzahl und Detaillisten neuer, geänderter und im Upload fehlender
   Systeme; Validierungsbericht (doppelte/leere IDs sperren die Übernahme; ungültige
   E-Mail-Adressen und IDs mit dem reservierten Präfix `NEW-` sind Warnungen).
3. **Übernehmen**: Fehlende Systeme bleiben standardmäßig erhalten und werden markiert;
   entfernt werden nur ausdrücklich ausgewählte. Hat sich der Bestand seit der Vorschau
   geändert, erscheint eine aktualisierte Vorschau zur erneuten Bestätigung.

Jeder Import steht mit Datei-Hash, Admin und Zählern in der Import-Historie.
Testdaten: `python scripts/gen-testdata.py` → `testdata-out/stammdaten.xlsx`
(2.000 erfundene Systeme). Ein API-Abruf der Stammdaten ist als deaktivierter Stub
vorbereitet (`MASTER_API_*`, legt nur Vorschauen an).

## Projekte (Admin)

Auf der Startseite des Admin-Bereichs (`/admin/`) Projekt anlegen (Name, Beschreibung,
z. B. CVE-Nummer): Dabei entsteht eine Kopie des aktuellen Stammdatenbestands. Die
Projektseite zeigt die **Projekt-URL** (`PUBLIC_BASE_URL/p/<token>`, Kopieren-Button),
den Fortschritt und erlaubt: Token neu generieren (alte URL wird ungültig), abschließen /
wieder öffnen / archivieren (abgeschlossen und archiviert = schreibgeschützt), löschen
(Projektnamen eingeben), Stammdatenfelder einzelner Systeme korrigieren und den Export
„manuell angelegte Systeme" (.xlsx).

## Daten-Upload ins Projekt (Admin)

Projektseite → **Excel-Datei in das Projekt hochladen** (`/admin/projekte/<id>/upload`):

1. .xlsx hochladen – Schlüssel ist die **System-ID**, Spalten wie in der Vorlage bzw. im
   Excel-Export der Hauptliste. Der Upload darf **alle** Felder überschreiben (auch
   Stammdatenfelder); „Herkunft“ wird ignoriert. Ungültige Auswahlwerte, zu lange Texte,
   leere/doppelte System-IDs sind Fehler mit Zeilenangabe (Übernahme gesperrt).
2. **Vorschau**: *Konflikte* (Feld im Projekt gefüllt und abweichend, alt/neu je Feld –
   nur ausgewählte werden übernommen: einzeln, je Zeile oder alle), *Neubefüllungen*
   (Feld leer – werden übernommen), *unbekannte System-IDs* (Standard: ignorieren;
   ausgewählte werden mit Herkunft „Admin-Upload“ angelegt), Anzahl *unverändert*.
   **Leere Zellen löschen keine vorhandenen Werte.**
3. **Übernehmen**: Haben Bearbeiter betroffene Felder inzwischen geändert, erscheint die
   aktualisierte Vorschau zur erneuten Bestätigung. Sonst wird in einer Transaktion
   geschrieben – vorher automatisch ein Backup „vor Import“, jede Änderung im
   Änderungsprotokoll (Quelle Admin-Upload), alle offenen Listen aktualisieren sich live.

**Änderungsprotokoll** (`/admin/projekte/<id>/aenderungen`): filterbar nach System-ID,
Feld, Quelle und Zeitraum (Europe/Berlin), seitenweise, Export als .xlsx.

## Backups (Admin)

Projektseite → **Backups**:

- **Intervall** (aus, 15 min, 30 min, 1 h, 4 h, 12 h, 24 h; Default 1 h) und
  **Aufbewahrung** (letzte n Backups, Default 48). Zeitgesteuert wird nur gesichert, wenn
  sich seit dem letzten Backup Daten geändert haben; bei mehreren Instanzen genau einmal
  (PostgreSQL Advisory Lock im Jobs-Prozess, Prüfung alle 15 s).
- **Backup jetzt erstellen**; automatisch zusätzlich vor jedem Upload („vor Import“) und
  vor jeder Wiederherstellung („vor Wiederherstellung“).
- Jedes Backup als **JSON** (vollständig, ohne Projekt-Token) und **Excel** herunterladbar;
  abgelegt im Volume `backups` unter `/data/backups/<projekt-id>/`.
- **Wiederherstellen …** öffnet einen Bestätigungsdialog. Die Wiederherstellung setzt alle
  Systeme in einer Transaktion auf den Stand des Backups (auch manuell angelegte Systeme),
  protokolliert jede Änderung (Quelle Wiederherstellung) und lässt alle offenen Listen neu
  laden. Nur in aktiven Projekten.

## Mailversand (Admin)

Projektseite → **Mailversand / Kampagnen** (`/admin/projekte/<id>/mail`):

1. **Neue Kampagne**: Betreff (Klartextzeile) und Text im Editor (Überschriften, fett,
   kursiv, Listen, Links). Der Platzhalter `[LINK]` (Button „[LINK] einfügen", am besten
   als eigener Absatz) wird je Empfänger durch Links auf seine Systeme ersetzt – je System
   „System-Name (System-ID)" auf die gefilterte Hauptliste plus „Alle meine Systeme".
   Der Text wird beim Speichern serverseitig bereinigt (nur diese Formate, Links nur
   http/https/mailto).
2. **Zielgruppe**: „System betroffen noch leer", „Betroffen = Ja, Aktion noch leer" oder
   **Benutzerdefiniert** – Adresse einer gefilterten Hauptlisten-Sicht einfügen (Filter und
   Suche werden übernommen).
3. **Vorschau**: Anzahl Empfänger (je normalisierter Adresse genau eine Mail; mehrere
   Adressen im Feld mit `;`/`,` = mehrere Mails), Anzahl Systeme, Beispielmail für einen
   wählbaren Empfänger (HTML und Textfassung), übersprungene leere/ungültige Angaben.
4. **Testmail** an eine beliebige Adresse (Betreff „[TEST] …").
5. **Versand starten**: Bestätigung mit Empfängerzahl. Die Empfängerliste wird dabei
   eingefroren; versendet wird im Hintergrund (ein Sender, `MAIL_RATE_PER_SEC`, Default 5/s,
   vorübergehende Fehler bis zu 5 Versuche mit Backoff, 5xx endgültig). Der Status je
   Empfänger aktualisiert sich automatisch; „Ausstehende abbrechen" ist möglich.
   Erneut senden nur ausdrücklich als neuer Lauf: an alle oder nur an die fehlgeschlagenen.

SMTP über `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` (`starttls`/`tls`/`none`),
`SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_REPLY_TO`, `SMTP_TIMEOUT_S`,
`MAIL_RATE_PER_SEC` (`.env.example`). Ohne `SMTP_HOST` ist der Versand deaktiviert.
Für Entwicklung und Tests stellt das Overlay `docker-compose.mailpit.yml` einen lokalen
Test-SMTP (Mailpit) bereit, der nichts zustellt
(`COMPOSE_FILE=docker-compose.yml:docker-compose.mailpit.yml`).

## Hauptliste (Bearbeiter)

Die Projekt-URL öffnet direkt die Liste aller Systeme. Der gelbe Link **Hilfe** im Kopf
jeder Projektansicht öffnet das Bearbeiter-Handbuch (`/p/<token>/hilfe`); die *-Felder
haben Kurz-Tooltips am Spaltenkopf.

- Die vier Felder mit * direkt in der Zeile bearbeiten: Auswahlfelder speichern sofort,
  Textfelder beim Verlassen; ein farbiger Rahmen zeigt „speichert / gespeichert / Fehler".
  Hat jemand das Feld zwischenzeitlich geändert, erscheint eine Meldung mit dem aktuellen
  Wert („übernehmen" oder „trotzdem überschreiben").
- Jede Spalte ist filterbar (Auswahlfelder per Mehrfachauswahl inkl. „(leer)", Text per
  „enthält") und sortierbar; die Suche durchsucht alle Spalten. Filter, Suche und
  Sortierung stehen in der Adresszeile – der Link gibt genau diese Sicht wieder.
- Export als .xlsx: aktuelle Sicht oder alle Systeme.
- „Neues System anlegen": alle Stammdatenfelder sind Pflicht, die ID `NEW-0001` … wird
  automatisch vergeben.
- Beim ersten Bearbeiten kann ein Name/Kürzel angegeben werden (optional, wird im
  Browser gemerkt und im Änderungsprotokoll vermerkt).
- **Live:** Änderungen anderer erscheinen ohne Neuladen (kurz hervorgehoben), neue
  Systeme sofort. Ein Feld, in dem man gerade schreibt, wird nie überschrieben – statt
  dessen erscheint ein Hinweis mit „Übernehmen" / „Behalten". Der Verbindungsstatus steht
  im Kopf; ohne Live-Verbindung gleicht die Liste alle 5 s ab. Betrifft eine fremde
  Änderung gefilterte/sortierte Spalten, erscheint „Ansicht aktualisieren".

## Übersicht (Bearbeiter)

`/p/<token>/uebersicht` (Link „Übersicht" im Kopf): Systeme je Digital Access Zone
(Zeilen, alphabetisch, „(ohne DAZ)" zuletzt) und „System betroffen" (Spalten: (leer),
In Prüfung, Ja, Nein) mit Zeilen-, Spalten- und Gesamtsummen. Darüber: Anteil
bearbeiteter Systeme und Anzahl „Ja" ohne ausgeführte Aktion. **Jede Zahl** öffnet die
Hauptliste mit dem passenden Filter (DAZ und/oder Status). Die Übersicht aktualisiert
sich live (höchstens einmal je Sekunde). JSON: `GET /p/<token>/api/pivot`.

### Live-Updates (Technik)

- `GET /p/<token>/api/events` (SSE): `delta`/`reload`/`gone`, `id` = Änderungssequenz,
  Heartbeat 15 s, Wiederverbinden mit `Last-Event-ID`; höchstens 300 Streams je Worker
  (darüber 503 + `Retry-After`).
- `GET /p/<token>/api/systems?updated_since=<seq>`: Delta seit Cursor (Polling-Fallback),
  `full_reload` nach einem Restore.
- Reverse Proxy vor dem Bearbeiter-Port: Buffering aus, Lese-Timeout deutlich über 15 s,
  keine Kompression für `text/event-stream` (Beispiele für nginx und Traefik:
  `docs/INSTALLATION.md` §5).

## Konfiguration (Umgebungsvariablen)

Ausschließlich über Umgebungsvariablen (`.env`, Vorlage `.env.example`). Ausführliche
Beschreibung, Wertebereiche und Hinweise: [docs/INSTALLATION.md](docs/INSTALLATION.md) §2.

| Variable | Default | Kurz |
|---|---|---|
| `POSTGRES_PASSWORD` | – Pflicht | DB-Passwort |
| `CSRF_SECRET` | – Pflicht (≥ 32 Zeichen) | HMAC-Schlüssel CSRF |
| `POSTGRES_DB` / `POSTGRES_USER` | `npt` / `npt` | DB-Name/-Benutzer |
| `DATABASE_URL` | leer | nur **außerhalb** dieser Compose-Datei: externe DB, hat dann Vorrang vor `POSTGRES_*`; `docker-compose.yml` reicht sie nicht durch |
| `POSTGRES_MAX_CONNECTIONS` | `200` | `max_connections` der DB |
| `PUBLIC_BIND_ADDR` / `PUBLIC_PORT` | `127.0.0.1` / `8080` | Bearbeiter-Port auf dem Host |
| `GATEWAY_BIND_ADDR` / `GATEWAY_PORT` | `0.0.0.0` / `8443` | mTLS-Port des admin-gateway |
| `ADMIN_NET_SUBNET`, `APP_ADMIN_IP`, `GATEWAY_ADMIN_IP` | `172.31.250.0/28`, `.10`, `.11` | internes Admin-Netz (feste Adressen) |
| `TRUSTED_PROXY_IPS` | leer | Reverse Proxies, deren `X-Forwarded-For` gilt |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | öffentliche Bearbeiter-URL (Links, Mails, Origin) |
| `ADMIN_BASE_URL` | leer (`.env.example`: `https://localhost:8443`) | Admin-URL (CSRF-Origin) |
| `ADMIN_PKI_DIR` | `./pki-out/gateway` | Host-Verzeichnis mit CA-Bundle, Server-Zertifikat/-Key, CRL |
| `ADMIN_CA_BUNDLE_PATH`, `GATEWAY_TLS_CERT_PATH`, `GATEWAY_TLS_KEY_PATH` | `/etc/npt-gateway/pki/{ca-bundle.pem,server.crt,server.key}` | Pfade im Gateway-Container |
| `ADMIN_CRL_PATH` / `CRL_RELOAD_INTERVAL` | leer / `60` | optionale CRL, Reload-Intervall (s) |
| `ADMIN_VERIFY_DEPTH` | `2` | Kettentiefe Client-Zertifikate |
| `ADMIN_CERT_ID_ATTRIBUTE` | `subject_dn` | `subject_dn` \| `san_email` \| `san_upn` \| `fingerprint_sha256` |
| `ADMIN_BOOTSTRAP_IDS` | leer | initiale Admins (`;`-getrennt oder JSON) |
| `APP_ROLE` | `all` | `all` oder Komma-Liste aus `public`, `admin`, `jobs` (z. B. `admin,jobs`) |
| `WEB_WORKERS` / `ADMIN_WORKERS` | `6` / `2` | Gunicorn-Worker (je Bearbeiter-Worker 300 SSE-Streams; Sizing: INSTALLATION §1) |
| `LOG_LEVEL` / `DISPLAY_TZ` | `INFO` / `Europe/Berlin` | Logs (JSON), Anzeige-Zeitzone |
| `APP_MEM_LIMIT` / `DB_MEM_LIMIT` / `GATEWAY_MEM_LIMIT` | `1536m` / `1g` / `128m` | Speichergrenzen |
| `NPT_IMAGE_TAG` | `latest` | Image-Tag (Update/Rollback) |
| `RATE_LIMIT_WRITES` / `RATE_LIMIT_WRITES_WINDOW_S` | `30` / `10` | Schreib-Limit je IP |
| `RATE_LIMIT_CREATE` / `RATE_LIMIT_CREATE_WINDOW_S` | `10` / `60` | Neuanlagen je IP |
| `UPLOAD_MAX_MB` | `20` | max. Upload (.xlsx) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` | leer, `587`, `starttls` | SMTP (leer = Versand aus) |
| `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_REPLY_TO` | leer | Anmeldung, Absender (Pflicht für Versand), Antwortadresse |
| `SMTP_TIMEOUT_S` / `MAIL_RATE_PER_SEC` | `30` / `5` | Zeitlimit, Drosselung |
| `MASTER_API_ENABLED`, `MASTER_API_URL`, `MASTER_API_TOKEN`, `MASTER_API_SCHEDULE_MINUTES` | `false`, leer, leer, `0` | Stammdaten-API (Stub) |

## Sicherheit

- Der Admin-Bereich läuft auf einem eigenen, nur intern erreichbaren Listener; davor
  terminiert das `admin-gateway` mTLS (optional mit CRL). Der Bearbeiter-Port kennt keine
  Admin-Routen.
- Wer die Projekt-URL kennt, kann das Projekt bearbeiten – sie ist wie ein Passwort zu
  behandeln (bei Bedarf unter *Token neu generieren* ungültig machen).
- Geheimnisse ausschließlich über Umgebungsvariablen; `.env` nie committen, im Betrieb
  besser aus einem eigenen Secret-Store einspielen.
- Checkliste aller Maßnahmen mit Nachweisen: [docs/SECURITY.md](docs/SECURITY.md).
  Schwachstellen bitte **nicht** öffentlich melden, sondern wie in
  [SECURITY.md](SECURITY.md) beschrieben.

## Kubernetes

Für Kubernetes gibt es ein Helm-Chart: `deploy/helm/notfall-patch-tracker`
(Kubernetes ≥ 1.29, Helm 3 oder 4, externe PostgreSQL-Datenbank).

- **public**-Deployment für die Bearbeiter (skalierbar, Default 3 Pods × 2 Worker),
  **backend**-Pod mit Admin-App, Hintergrundjobs und dem mTLS-Gateway als Sidecar,
  Backups auf einem PVC.
- Migration im initContainer unter Advisory Lock; `helm rollback` funktioniert dank
  expand/contract-Migrationen.
- PodSecurity `restricted`, NetworkPolicies, Secrets per `existingSecret`,
  Verbindungsbudget der Datenbank wird beim Rendern geprüft.
- Ausrollen mit Prüfungen und Abbild-Abgleich je Pod: `deploy/helm/deploy.sh`.

```bash
deploy/helm/deploy.sh -n npt -f values-npt.yaml
```

Installation, Werte, Controller-Beispiele (ingress-nginx, Traefik, Envoy Gateway),
Admin-Zugang, Datenbank-Pflichten, Sizing, Betrieb und Risiken:
[docs/KUBERNETES.md](docs/KUBERNETES.md). Docker Compose bleibt unverändert unterstützt.

## Entwicklung und Tests

```bash
# Lint, Format, Typen, pytest in Wegwerf-Umgebung (eigenes Compose-Projekt, DB im tmpfs)
docker compose -p npt-test -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
docker compose -p npt-test -f docker-compose.test.yml down -v

# JS: Syntaxprüfung + gemeinsame Filter-Testvektoren (Node im Container)
scripts/test-js.sh

# E2E-Suite (Playwright) gegen einen FRISCHEN Wegwerf-Stack (eigenes Projekt npt-e2e,
# Ports 8490/8491, Test-PKI, Mailpit, 2.000 Testsysteme); Exitcode = Ergebnis.
# Suiten: inline_smoke (Inline-Bearbeitung + XSS), live_smoke (zwei Browser-Kontexte),
# pivot_smoke (Drilldown), upload_smoke (Admin-Upload mit Konfliktbestätigung), mail_smoke
scripts/run-e2e.sh                      # KEEP=1 laesst den Stack stehen

# mTLS-Integrationstest gegen einen laufenden Stack
GATEWAY_URL=https://localhost:8443 PUBLIC_URL=http://127.0.0.1:8080 \
  COMPOSE_PROJECT_NAME=<projekt> scripts/mtls-integration-test.sh ./pki-out

# Lasttest (Locust, 500 Nutzer mit SSE; Methodik und Ergebnisse: docs/LOADTEST.md)
LT_CPUSET=4,5 loadtest/run-loadtest.sh /p/<token>

# Abhängigkeiten auf bekannte Schwachstellen prüfen (pip-audit)
scripts/audit-deps.sh
```

Abhängigkeiten: direkte Pins in `pyproject.toml`, vollständige Hash-Pins in
`requirements.lock` / `requirements-dev.lock` (neu erzeugen mit
`uv pip compile pyproject.toml [--extra dev] --generate-hashes --python-version 3.12 --python-platform x86_64-manylinux_2_28 -o <lock>`).
Handbücher: Quelle `docs/handbuch/*.md`, beim Paketbau als `npt/handbuch` ins Image
übernommen und serverseitig gerendert (eine Quelle für Repo und Anwendung).

## SBOM und Lizenzprüfung

```bash
scripts/sbom.sh [--scan]       # CycloneDX-1.6-SBOMs (Quellbaum, App-, Gateway-Abbild) nach ./sbom
scripts/check-licenses.sh      # Lizenzen der Python-Laufzeitabhängigkeiten (Befund bei GPL/AGPL)
```

Details, Release-Artefakte und optionale cosign-Attestierung: [docs/SBOM.md](docs/SBOM.md).

## Mitwirken

Beiträge sind willkommen – Ablauf, Prüfungen und Konventionen: [CONTRIBUTING.md](CONTRIBUTING.md).

## Lizenz

[MIT](LICENSE) – Copyright (c) 2026 Johannes Schmidt. Mitgelieferte und genutzte
Drittkomponenten stehen unter eigenen Lizenzen, siehe
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
