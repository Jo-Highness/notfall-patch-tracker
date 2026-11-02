# Entscheidungen und Annahmen

Anforderungsquelle ist der ursprüngliche Auftrag des Betreibers (nicht Teil des öffentlichen
Repositorys; „Auftrag n.m“ verweist auf dessen Abschnitte), verbindliche Architektur `docs/ARCHITECTURE.md`
(Architektur-Review 02de6ec6). Hier stehen **alle** Abweichungen vom Auftrag, Annahmen und
eigenen Entscheidungen, nach Phasen geordnet (A–K; Nachbesserungen nach dem QA-Gate: J12, J13; Lizenz/SBOM/Veröffentlichung: J15; Kubernetes/Helm: K). Sicherheits-Checkliste:
`docs/SECURITY.md`, Lastmessung: `docs/LOADTEST.md`.

## 0. Übersicht

### ADRs (Entwicklungsprozess, Asset `notfall-patch-tracker`) und wo sie hier umgesetzt sind

| ADR | ID | Thema | Umsetzung / Detail-Entscheidungen |
|---|---|---|---|
| NPT-01 | b81c3ab0 | Frontend: Tabulator, Filter-Grammatik, Quill, CSP | D2, D3, F2, I1, J5 |
| NPT-02 | d89a1c5c | Prozessmodell: tini, `npt-run`, drei Prozessgruppen | B11, B12, E4 |
| NPT-03 | 2c18a904 | Live-Updates: Sequenz-Cursor, LISTEN/NOTIFY, SSE | A1, E1–E5, F3 |
| NPT-04 | ea8aa426 | Schreibpfad, Konflikte, Admin-Upload, `NEW-` | A5, D4, D5, G1–G4 |
| NPT-05 | 3228345a | Backups, Restore, Mail-Queue | A4, G5, H1–H6, I4–I6 |
| NPT-06 | f7bc0861 | Admin-Vertrauensmodell (Gateway, Middleware) | B3–B6, J2 |
| NPT-07 | fcb33dbb | Identifier-Normalisierung | B7, B8 |
| NPT-08 | 6f467b67 | CSRF und Origin | B1, B2, D1 |
| NPT-09 | 5ab5d5f0 | Grenzen, Rate-Limit, Zulassung, Health | D8, E4, B11, J3 |
| NPT-10 | 7782a8aa | Datenmodell | B13, D6, H6, I4 |
| NPT-11 | 7da52b7c | Betrieb, Betriebs-Overlay, PII, Container | A2, B12, B15, J2, J8 |
| NPT-12 | 54aaf020 | Kubernetes-Betrieb per Helm-Chart (Review ee62741e) | K1–K11 |

### Wichtigste Annahmen (Kurzfassung)

- Betrieb nur im internen Netz; Bearbeiter-Port hinter einem TLS-Reverse-Proxy, Default-
  Bindung loopback (A2). Wer die Projekt-URL kennt, darf lesen und die *-Felder schreiben
  (Auftrag 8) – keine weitere Anmeldung.
- `Leer` = `NULL`; Enums als ASCII-Schlüssel mit zentralem Label-Mapping (NPT-10).
- Im Stammdatenimport fehlende Systeme bleiben standardmäßig erhalten und werden markiert;
  sie gehören weiter zum Bestand und werden in neue Projekte kopiert (C3).
- Upload ins Projekt: leere Zellen und „Leer“ löschen nie; jeder ungültige Wert ist ein
  Fehler (kein stilles Kürzen) (G1).
- Betreff einer Mail ist Klartext (I1); `[LINK]` nur im Text (I2).
- Delta-Abruf mit Sequenz-Cursor statt Zeitstempel (A1).
- Zeitanzeige Europe/Berlin, Speicherung UTC (NPT-10).

## A. Abweichungen vom Auftrag (aus ARCHITECTURE.md übernommen)

| # | Auftrag | Umsetzung | Grund |
|---|---|---|---|
| A1 | Delta-Abruf `?updated_since=<ts>` (Zeitstempel) | `updated_since` ist ein **Sequenz-Cursor** (`projects.change_seq`/`project_systems.row_seq`); unter `reset_seq` → `full_reload` | Zeitstempel sind unter Nebenläufigkeit nicht monoton in Commit-Reihenfolge; ein Sequenz-Cursor verliert keine Änderungen (NPT-03). |
| A2 | „Veröffentlicht werden der Bearbeiter-Port und der mTLS-Port" | Bindeadresse des Bearbeiter-Ports per Env (`PUBLIC_BIND_ADDR`), **Default 127.0.0.1** | Bearbeiter-Zugriff gehört hinter den Reverse Proxy (TLS); versehentliches Exponieren ohne TLS wird verhindert (NPT-11). |
| A3 | API-Pull der Stammdaten (perspektivisch) | Ein künftiger API-Abruf erzeugt nur eine **Vorschau**; übernommen wird erst nach Admin-Bestätigung | Gleiches Sicherheitsniveau wie der Excel-Import; kein unbeaufsichtigtes Entfernen von Systemen. |
| A5 | NPT-04: `'NEW-'\|\|lpad(nextval(…)::text,4,'0')` | `nextval` in SQL, Formatierung `NEW-{n:04d}` in Python | `lpad` kürzt ab 10000 (Duplikate); Auftrag 5.6 verlangt Weiterzählen ohne Kürzung (D5). |
| A4 | Phasen 6/7 | Phase 6 enthält bereits den Backup-Erzeugungskern (für `vor_import`); Phase 7 ergänzt Scheduler, Aufbewahrung, Download, Restore | Abhängigkeit des Upload-Ablaufs vom Backup. |

## B. Entscheidungen Phase 1

### B1 Admin-Seiten: `Referrer-Policy: same-origin` statt `no-referrer`
Der Auftrag (8.2) fordert `no-referrer`, damit Projekt-Token nicht über den Referer
abfließen. Nach dem Fetch-Standard senden Browser bei `no-referrer` für POST-Requests
`Origin: null`; die geforderte exakte Origin-Prüfung (NPT-08) würde dann jedes
Admin-Formular ablehnen. Admin-URLs enthalten keine Projekt-Token im Pfad, daher
setzt die Admin-App `same-origin` (Referer nur innerhalb des Admin-Origins). Die
Bearbeiter-App bleibt bei `no-referrer`.
Bearbeiter-Schreibzugriffe per `fetch()` unterliegen derselben Regel (Origin kann `null`
sein) – entschieden in D1.

### B2 Ermittlung der erwarteten Origin (Admin)
`ADMIN_BASE_URL` gesetzt → exakter Vergleich damit. Ungesetzt → `X-Forwarded-Proto`/
`X-Forwarded-Host`, die das Gateway **immer selbst setzt** (Client-Werte werden
überschrieben). Empfohlen ist, `ADMIN_BASE_URL` zu setzen.

### B3 PKI-Einbindung des Gateways über ein Verzeichnis
Der Auftrag nennt `ADMIN_CA_BUNDLE_PATH`/`ADMIN_CRL_PATH` als gemountete Dateien. Umsetzung:
ein Host-Verzeichnis `ADMIN_PKI_DIR` wird read-only nach `/etc/npt-gateway/pki` gemountet;
`ADMIN_CA_BUNDLE_PATH`, `ADMIN_CRL_PATH`, `GATEWAY_TLS_CERT_PATH`, `GATEWAY_TLS_KEY_PATH`
sind Pfade **im Container**. Grund: Einzeldatei-Bind-Mounts zeigen ersetzte Dateien
(neue Inode) nicht an – der CRL-Reload ohne Neustart funktioniert nur mit Verzeichnis-Mount.
Eine konfigurierte, aber fehlende CRL verhindert den Start (fail closed).

### B4 Gateway-Antworten
Fehlendes/ungültiges/gesperrtes Client-Zertifikat → einheitlich **403** ohne Details
(nginx-intern 495/496). Das Gateway leitet nur `/admin` und `/admin/…` weiter, alles
andere → 404. `/admin` → 308 auf `/admin/`. Ein separater Listener `127.0.0.1:8080`
(nicht veröffentlicht) dient nur dem Docker-Healthcheck.

### B5 Admin-Middleware
Zusätzlich zu NPT-06: mehrfach gesetzte Vertrauens-Header werden abgelehnt; die
`X-SSL-Client-*`-Header werden nach erfolgreicher Prüfung aus dem Request entfernt,
damit nachgelagerter Code nur den geprüften Principal sieht. Die direkte Absender-IP
stammt aus dem Socket; Uvicorn läuft mit `proxy_headers=False` (eigene Worker-Klasse
`npt.web.worker.NptUvicornWorker`), damit kein Proxy-Header den Absender überschreiben kann.
Für das Audit wird als Client-IP das vom Gateway gesetzte `X-Forwarded-For` verwendet.

### B6 Bindung des Admin-Ports
Das App-Container-Interface in `admin_net` hat eine feste Adresse (`APP_ADMIN_IP`,
Default 172.31.250.10), das Gateway ebenfalls (`GATEWAY_ADMIN_IP`, .11) in einem festen
Subnetz (`ADMIN_NET_SUBNET`, 172.31.250.0/28). Gunicorn-Admin bindet nur an
`APP_ADMIN_IP:8001`; `ADMIN_GATEWAY_CIDR` ist `GATEWAY_ADMIN_IP/32`. Bei Kollision mit
vorhandenen Docker-Netzen sind die drei Werte per `.env` änderbar.

### B7 Identifier-Normalisierung (NPT-07) – Details
- RDN-Typen: Kurznamen in Großbuchstaben (CN, O, OU, C, L, ST, STREET, DC, UID,
  SERIALNUMBER, EMAILADDRESS, …); unbekannte Typen als dotted OID bzw. großgeschrieben.
- Mehrwertige RDNs (`+`) bleiben als RDN erhalten, ihre AVAs werden sortiert.
- RFC 2253: `;` als RDN-Trenner und Werte in Anführungszeichen werden akzeptiert;
  `#<hex>`-Werte (BER) werden als Hex verglichen.
- `san_email`/`san_upn`: alle Adressen eines Zertifikats sind Kandidaten, ein Treffer
  genügt; Anzeige/Anfrage nutzt den alphabetisch ersten.
- Gespeichert wird `(issuer_norm, id_kind, identifier_norm)`; `issuer_norm = '*'`
  bedeutet „beliebiger Aussteller der konfigurierten CA". Nur Einträge der aktuell
  konfigurierten Art (`ADMIN_CERT_ID_ATTRIBUTE`) sind wirksam; andere werden in der
  Verwaltung grau angezeigt.

### B8 `ADMIN_BOOTSTRAP_IDS`-Format
Trenner `;` oder Zeilenumbruch (DNs enthalten Kommas), alternativ eine JSON-Liste.
Optionaler Aussteller als `<Issuer-DN>::<Identifier>`. Bootstrap legt nur fehlende
Einträge an; ein **deaktivierter** Bootstrap-Admin bleibt deaktiviert, ein
**entfernter** wird beim nächsten Start wieder angelegt („falls noch nicht vorhanden") –
dauerhaftes Entfernen heißt, ihn aus der Env zu streichen.

### B9 Letzter-Admin-Schutz
Gilt für Deaktivieren **und** Entfernen und für jeden Akteur (praktisch kann nur der
letzte Admin sich selbst treffen). Gezählt werden aktive Admins der wirksamen
Identifier-Art; geprüft unter `SELECT … FOR UPDATE` aller aktiven Admins (gleiche
Sperrreihenfolge in allen Pfaden).

### B10 Meldungen per Redirect
Nach POST folgt 303 auf die Listenseite mit festem Meldungsschlüssel (`?ok=…`/`?fehler=…`);
es werden keine Nutzereingaben über die URL reflektiert.

### B11 Health
`/healthz`/`/readyz` liefern nur den Schnappschuss eines Monitors je Worker (Thread,
eigene Mini-Engine). `/readyz` ist bereit, wenn die letzte erfolgreiche Probe ≤ 30 s alt
ist; steht der Monitor > 120 s still, beendet sich der Worker (Gunicorn startet neu).
Die Admin-App hat keine eigenen Health-Routen (alles außer `/admin*` ist dort 404);
der Container-Healthcheck nutzt den Bearbeiter-Port.

### B12 Abhängigkeiten und Image
- Direkte Abhängigkeiten in `pyproject.toml` exakt gepinnt; vollständige transitive
  Pins **mit Hashes** in `requirements.lock`/`requirements-dev.lock` (erzeugt mit
  `uv pip compile --generate-hashes`), installiert mit `pip --require-hashes`.
- Laufzeitstufe: `python:3.12-slim`, nur `tini` nachinstalliert, `pip`/`setuptools`
  entfernt, UID 10001, `read_only` + tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges`.
- Gunicorn 26: Control-Socket deaktiviert (schreibt sonst nach `$HOME`).
- FastAPI ≥ 0.16x hält eingebundene Router als `_IncludedRouter`; der Test der
  Routing-Tabelle läuft deshalb rekursiv und prüft, dass bekannte Pfade gefunden werden.

### B13 Datenmodell – ergänzende Felder
Über Auftrag 3.2/NPT-10 hinaus: `change_log.action` (`geaendert`/`angelegt`) und
Quelle `admin` (Bearbeiten-Dialog im Admin-Bereich, Auftrag 5.6);
`master_imports.count_missing_kept`; `mail_deliveries.sending_since` (Erkennung
hängender Sendungen, NPT-05); `projects.last_backup_seq/last_backup_at`;
`admin_audit_log` als eigene Tabelle. Default-Backup-Intervall eines Projekts: 1 h.

### B14 Test-PKI
`scripts/gen-test-pki.sh` erzeugt EC-P-256-Schlüssel, eine Test-CA mit CRL (Zertifikat
`revoked` gesperrt), eine fremde CA (deren Client-Zertifikat hat **denselben Subject-DN**
wie der Admin – darf trotzdem nicht durchkommen), Server-Zertifikat (SAN localhost,
127.0.0.1, `admin-gateway`, erweiterbar per `GATEWAY_SANS`) und PKCS#12-Dateien.
Die Ausgabe ist für den Gateway-Container lesbar (0644) – ausschließlich Test-PKI.

### B15 Betriebs-Overlay (künftige Option, nicht gebaut)
Ein Overlay `docker-compose.prod.yml` (NPT-11: kein App-Port, Geheimnisse aus einem eigenen
Secret-Store; Zertifikate/CRL aus der eigenen PKI; Aktivierung über `COMPOSE_FILE`) wird in
diesem Repository **nicht** gebaut. Es ist eine künftige Option für das Ausrollen im
Betrieb (J9); bis dahin gilt allein die portable `docker-compose.yml`. Der
Kopfkommentar der Compose-Datei verwies früher auf das Overlay „(siehe README)“ – korrigiert
(QA 9a01489c).

## C. Entscheidungen Phase 2 (Stammdatenimport)

### C1 Kopfzeile und Spalten
- Gesucht wird die erste Zeile (innerhalb der ersten 10) mit der Spalte „System-ID";
  Titelzeilen davor sind erlaubt. Vergleich nach NFKC, Whitespace-Zusammenziehen,
  casefold; ein abschließendes `*` ist optional; interne Feldnamen (`system_id`) werden
  ebenfalls erkannt. Fehlt „System-ID" → Abbruch mit Meldung. Dieselbe Spalte doppelt →
  Abbruch (Zuordnung wäre mehrdeutig).
- Unbekannte Spalten und Projektfelder (*-Felder, Herkunft) werden ignoriert und als
  Hinweis gemeldet.
- **Fehlt eine Stammdatenspalte ganz**, bleibt das Feld bei bestehenden Systemen
  **unverändert** (statt alle Werte zu leeren); neue Systeme erhalten dort keinen Wert.
  Eine **leere Zelle** in einer vorhandenen Spalte leert dagegen den Wert (Stammdaten
  sind der vollständige Bestand der Quelle).
- Gelesen wird nur das erste Arbeitsblatt, `read_only` + `data_only` (Formeln werden
  nicht ausgewertet, es zählt der gespeicherte Ergebniswert), keine externen Links, keine
  Makros. Zellwerte werden zu Text normalisiert (ganzzahlige Zahlen ohne `.0`, Datum ISO),
  Steuerzeichen entfernt, getrimmt; leere Zeilen werden übersprungen.
- Grenzen: Dateigröße `UPLOAD_MAX_MB` (zusätzlich `client_max_body_size` im Gateway und
  Prüfung der Content-Length vor dem Parsen), nur `.xlsx` (Endung **und** ZIP-Signatur mit
  `xl/workbook.xml`), höchstens 100.000 Zeilen, 200 Spalten.

### C2 Validierungsbericht
- **Fehler** (Übernahme gesperrt): leere System-ID in einer sonst befüllten Zeile,
  doppelte System-ID, System-ID länger als 100 Zeichen.
- **Warnung** (Übernahme möglich): System-ID mit Präfix `NEW-` (Groß-/Kleinschreibung
  egal) – Zeile wird **nicht** übernommen; Anwendungsverantwortlicher ohne gültige
  Adresse oder mit mindestens einem ungültigen Teil; Werte über der Längengrenze
  (1.000 Zeichen, Beschreibung 4.000) werden gekürzt.
- E-Mail-Regel (gemeinsam für Import, Anlage, später Mailversand): Trenner `;` und `,`,
  `Name <adresse>` erlaubt, pragmatische Syntaxprüfung (Domain mit TLD), Normalisierung
  lowercase/getrimmt.
- Der Bericht listet je Art höchstens 500 Einzelmeldungen, zählt aber alle.

### C3 Vorschau, Übernahme, fehlende Systeme
- Die geparste Datei liegt in `staged_uploads` (2 h gültig). Die Vorschau berechnet den
  Diff gegen den aktuellen Bestand und trägt einen **Fingerprint** (SHA-256 über den
  vollständigen Diff). Bei „Übernehmen" wird unter `pg_advisory_xact_lock` und
  `SELECT … FOR UPDATE` neu gerechnet; weicht der Fingerprint ab, wird nichts geschrieben
  und die aktualisierte Vorschau mit Hinweis gezeigt.
- Im Upload fehlende Systeme: Default **behalten und markieren** (`missing_since`,
  bleibt bei weiteren Importen auf dem ersten Zeitpunkt). Entfernt werden nur explizit
  per Checkbox ausgewählte (Hilfsschalter „alle auswählen"). Taucht ein markiertes
  System wieder auf, wird die Markierung aufgehoben (zählt als „geändert").
- Markierte Systeme gehören weiter zum Bestand und werden in neue Projekte kopiert
  (konservativ: lieber ein System zu viel prüfen).
- `master_imports` erhält Zeitpunkt, Admin-Identifier, Quelle, Dateiname, SHA-256,
  Zähler (neu/geändert/entfernt/fehlend behalten) und in `details` die ID-Listen;
  zusätzlich Audit-Einträge (`stammdaten_hochgeladen`, `stammdaten_importiert`,
  `stammdaten_vorschau_verworfen`).

### C4 `MasterDataSource` und API-Stub
- `MasterDataSource.fetch() -> list[MasterRecord]`; `load()` = `fetch()` + gemeinsame
  Validierung (`validate_records`) → `SourceResult`. Excel und API durchlaufen danach
  denselben Weg (Staging → Diff/Fingerprint → Admin-Bestätigung).
- `ApiMasterDataSource` ist ein deaktivierter Stub (`MASTER_API_ENABLED`,
  `MASTER_API_URL` (nur https), `MASTER_API_TOKEN` als Bearer, Antwort ≤ 50 MB). Für die
  Anbindung ist nur `map_response()` zu implementieren.
- Zeitplan (`MASTER_API_SCHEDULE_MINUTES > 0`): Der Jobs-Prozess legt dann **nur eine
  Vorschau** an (Auflage A3); ohne Aktivierung wird der Job nicht registriert.

### C5 Testdaten-Generator
`scripts/gen-testdata.py` (deterministisch per `--seed`): 2.000 Systeme, 15 DAZ (wenige
Systeme ohne DAZ), 400 Verantwortliche mit Zipf-artiger Verteilung (einige mit vielen
Systemen), ca. 6 % mit zwei Adressen (`;`, `,`), 6 % im Format `Name <adresse>`, ca. 4 %
ungültig, Groß-/Leerzeichen-Varianten. `--with-errors` ergänzt doppelte/leere IDs und
`NEW-`-Zeilen. Ausgabe nach `testdata-out/` (gitignored), ausschließlich erfundene Daten.

## D. Entscheidungen Phase 3 (Projekte, Hauptliste, neue Systeme)

### D1 Bearbeiter-Schreibzugriffe und Origin (Auflösung von B1, Entscheidung Orchestrator)
Bearbeiterseiten behalten `Referrer-Policy: no-referrer` (Auftrag 8.2). Browser senden
deshalb bei `fetch()`-PATCH/POST `Origin: null`. Regel für `PATCH/POST /p/<token>/api/…`:
- `Content-Type` muss `application/json` sein (sonst 415). Cross-Site-JSON erfordert
  einen CORS-Preflight, den der Server nie beantwortet: es werden **keine CORS-Header**
  gesetzt.
- Das CSRF-Token (Header `X-CSRF-Token`, Scope Projekt + `token_generation`, NPT-08) muss
  gültig sein (sonst 403 `csrf`).
- `Origin` fehlt oder ist `null` → zulässig (nur in Verbindung mit den beiden Punkten oben).
  Ein vorhandener, nicht-`null` Origin muss exakt `PUBLIC_BASE_URL` entsprechen.
- Zusätzlich (Defense in Depth): `Sec-Fetch-Site` – falls gesendet – muss `same-origin`
  oder `none` sein. JSON-Bodys > 64 KB → 413. Die Prüfung läuft als Dependency vor der
  Body-Validierung.
- `fetch()` setzt zusätzlich `referrerPolicy: "no-referrer"`; der Token verlässt den
  Browser nie im Referer.

### D2 Grid: Tabulator 6.7.1
Gewählt wurde Tabulator (NPT-01) statt AG Grid Community: MIT-Lizenz ohne
Enterprise-Abgrenzung, eine Datei ohne Bundler, virtuelles Scrolling, Header-Filter,
Editoren (Liste/Input/Textarea) und eigene Sortier-/Filterfunktionen eingebaut, kein
`eval`/Inline-Style-Attribut (CSP-tauglich, geprüft: nur CSSOM). Mitgeliefert unter
`src/npt/static/vendor/tabulator/` mit Version, Registry-Integrity (sha512) und sha256 der
Dateien in `VERSION`; sourceMappingURL-Kommentare entfernt.

### D3 Filter-Grammatik
Spezifiziert im Docstring von `npt/domain/filters.py` (maßgeblich), Browser-Gegenstück
`static/js/npt-filter.js`, gemeinsame Vektoren `tests/vectors/filter-vectors.json`
(pytest + `tests/js/filter-vectors.test.js`, ausführbar mit `scripts/test-js.sh`).
Kurzfassung: `q` (Volltext über alle Spalten, Enums über Anzeigenamen), `f.<text>`
(enthält, case-insensitiv), `f.<enum>` wiederholt (Schlüssel, `leer` = „(leer)",
unbekannte Schlüssel treffen nichts), `x.<feld>` wiederholt (exakt, leer = leeres Feld;
für Pivot-Drilldown und Mail-Deep-Links), `sort`/`dir`. Alle Filter UND-verknüpft.
Sortierung: Anzeigetext lowercase nach Codepoints, leere Werte bei `asc` am Ende,
Gleichstand nach System-ID; `dir` wirkt nur mit gültigem `sort`. Die Grid-Spalten nutzen
für Header-Filter und Sortierung exakt diese Funktionen; Tabulators eigene Filterlogik
wird nicht verwendet. Der Export „Sicht" wertet dieselben Parameter in Python aus.

### D4 Schreib-API (NPT-04)
- `PATCH /p/<token>/api/systems/<id>` mit `{field, value, expected, client_name?}`,
  `extra=forbid`, `field` ∈ vier *-Felder (andere Felder, auch `origin`, `version`,
  `system_id` → **422**, Datenbank unverändert). Enum-Werte sind die ASCII-Schlüssel,
  `null` bzw. `""` = „Leer". Version: einzeilig, ≤ 255; Bemerkung: Plaintext, ≤ 4000,
  CRLF → LF, Steuerzeichen entfernt, NUL abgelehnt.
- `expected` ist der zuletzt bekannte Feldwert; Vergleich `IS DISTINCT FROM`. Abweichung →
  **409** `{reason: "konflikt", field, current, row}`; andere Felder derselben Zeile werden
  ohne Konflikt zusammengeführt. Überschreiben = erneut senden mit `expected = current`.
- Wert unverändert → 200 `changed: false`, `change_seq` wird nicht verbraucht.
- Abgeschlossen/archiviert → 403 `schreibgeschuetzt` (serverseitig über
  `UPDATE projects … WHERE status='aktiv'`). Lock-/Statement-Timeout → 503 `gesperrt`
  mit `Retry-After`. Semaphor/Pool erschöpft → 503 `ueberlastet`.
- **Retry-After je Fall** (Sekunden, zentral in `npt/limits.py`):

  | Antwort | Auslöser | Retry-After |
  |---|---|---|
  | 503 `gesperrt` | Lock-/Statement-Timeout beim Schreiben (`BUSY_RETRY_AFTER_S`) | 1 |
  | 503 `ueberlastet` | Zulassungs-Semaphor oder DB-Pool (`pool_timeout`) erschöpft (`OVERLOADED_RETRY_AFTER_S`) | 2 |
  | 503 `ueberlastet` | SSE-Grenze des Workers erreicht, Broker nicht bereit (`SSE_RETRY_AFTER_S`) | 5 |
  | 503 `datenbank` | Datenbank nicht erreichbar / Verbindung abgebrochen (`DB_UNAVAILABLE_RETRY_AFTER_S`, J13) | 5 |
  | 503 `/readyz` | letzte erfolgreiche Probe älter als 30 s (`READY_RETRY_AFTER_S`) | 5 |
  | 429 `rate_limit` | Rate-Limit (D8) | Rest des Fensters, mind. 1 |

  Die Admin-App antwortet bei erschöpftem Pool bzw. nicht erreichbarer Datenbank ebenso mit
  503 und 2 s bzw. 5 s (Klartext statt JSON).
- NOTIFY `npt_changes` wird bereits gesendet (`{"p","s","k"}`, Versand beim Commit);
  Empfang/SSE kam in Phase 4 (E1–E5); bis dahin sahen andere Nutzer Änderungen nach dem
  Neuladen; die Konflikterkennung greift bereits.
- Die UI zeigt bei 409 einen Dialog „Aktuellen Wert übernehmen / Trotzdem überschreiben".
  Speichern: Auswahlfelder sofort, Textfelder beim Verlassen, je Zelle 300 ms entprellt
  und serialisiert (keine überholenden Requests); 429/503 werden automatisch wiederholt.

### D5 Vergabe `NEW-xxxx`
`nextval('new_system_seq')` und Formatierung in Python (`NEW-{n:04d}`). Abweichung vom
Wortlaut in NPT-04 (`lpad(…, 4, '0')`): `lpad` **kürzt** ab 10000 auf vier Stellen
(`NEW-1000` statt `NEW-10000`) und würde Duplikate erzeugen; Auftrag 5.6 verlangt
„ab 10000 ohne Kürzung weiterzählen". Neuanlage nur in aktiven Projekten, unter
Projektsperre; `change_log` erhält `angelegt` (neuer Wert = System-Name) plus je
gesetztem *-Feld einen `geaendert`-Eintrag. Pflicht: alle zehn Stammdatenfelder;
„Anwendungsverantwortlicher" muss mindestens eine und darf keine ungültige Adresse
enthalten (gleiche Regel wie Import, C2).

### D6 Projekte
- Snapshot: alle `master_systems` (auch als „fehlend" markierte, C3) per
  `INSERT … SELECT`, `origin = stammdaten`, *-Felder leer, `version = 1`.
- Token: `token_urlsafe(32)` (256 Bit). „Token neu generieren" erhöht
  `token_generation` – alte URL → 404, alte CSRF-Token ungültig.
- Status: `aktiv ↔ abgeschlossen`, `aktiv/abgeschlossen → archiviert`,
  `archiviert → abgeschlossen` (Archivierung aufheben). Abgeschlossene und archivierte
  Projekte bleiben über die URL **lesbar** (Liste, Export), sind aber schreibgeschützt.
- Löschen nur mit exakter Eingabe des Projektnamens; `project_systems` werden per
  Cascade gelöscht, `change_log` bleibt (ohne FK, NPT-10).
- Fortschritt = Anteil `affected IS NOT NULL`.
- Bearbeiten-Dialog (Admin) für Stammdatenfelder eines Projektsystems: Optimistic Locking
  über `version`, nur in aktiven Projekten, `change_log` Quelle `admin` mit
  Admin-Identifier, Audit-Eintrag; die globalen Stammdaten bleiben unberührt.
- Export „manuell angelegte Systeme" je Projekt (alle Listenspalten).

### D7 Excel-Export und Formel-Injection
Jede Textzelle wird mit `data_type = "s"` geschrieben (openpyxl würde `=…` sonst als
Formel speichern); beginnt sie mit `=`, `+`, `-`, `@`, Tab oder CR, wird zusätzlich
`quotePrefix` gesetzt (entspricht dem Excel-Apostroph, ohne den gespeicherten Wert zu
verändern – verlustfreier Re-Import in Phase 6). In XML-ungültige Steuerzeichen werden
entfernt. Kopfzeile fixiert, Autofilter über den Datenbereich, Enum-Werte als
Anzeigenamen, ASCII-Dateiname.

### D8 Rate-Limits und Zulassung
Gezählt wird jeder Schreibversuch, der den Endpunkt erreicht – also nach bestandener
Content-Type-, Origin- und CSRF-Prüfung –, auch fachlich abgelehnte (Domain-Validierung
422, Konflikt 409, schreibgeschützt 403), je Client-IP (XFF nur von `TRUSTED_PROXY_IPS`).
**Nicht** gezählt werden Schema-Fehler, die FastAPI/Pydantic schon vor dem Endpunkt mit 422
abweist (unbekanntes/nicht schreibbares Feld, falscher Typ, Überlänge). Entscheidung
(Nachbesserung, Doku an den Code angepasst): Solche Requests erreichen den Schreibpfad nie
und kosten nur die Projekt-Auflösung und die CSRF-Prüfung – so viel wie ein Lesezugriff,
der ebenfalls nicht limitiert ist; sie zu zählen hieße, die Body-Validierung in den
Endpunkt zu verlegen. Test: `test_write_rate_limit_counts_domain_but_not_schema_errors`. Neuanlagen zählen
auf beide Limits (Schreiben und Anlegen). Alte Fenster werden bei ca. 1 % der Zugriffe
aufgeräumt. Alle `/p/<token>`-Routen laufen durch den Zulassungs-Semaphor (NPT-09).

### D9 Namensabfrage
Beim ersten Speichern bzw. Anlegen fragt ein Dialog optional nach Name/Kürzel
(`localStorage["npt.clientName"]`, „Überspringen" speichert einen leeren Wert und fragt
nicht erneut). Serverseitig einzeilig bereinigt, ≤ 100 Zeichen, in `change_log.client_name`.

### D10 Ladezeit und Messung
JSON-Endpunkt mit 2.000 Zeilen: im Test 45–48 ms (TestClient, Wegwerf-DB), auf dem
Dev-Stack über HTTP 50–130 ms für 1,06 MB; im Browser (Chromium, Playwright-Rauchtest)
ist die Liste nach 0,2–0,3 s vollständig bedienbar, virtuell gerendert werden ~20 Zeilen.
Die Antwort ist unkomprimiert (GZip würde den SSE-Stream betreffen); Kompression am
Reverse Proxy für `application/json` ist in J7 bewertet.

### D11 Platzhalter und Testbarkeit
`/p/<token>/hilfe` (seit Phase 9 das Handbuch, J5) und `/p/<token>/uebersicht`
(Pivot, Phase 5) sind verlinkt. Bedienelemente tragen `data-testid`s (Zeilen:
`row-<System-ID>`, Dialoge, Toolbar, Formularfelder `new-<feld>`, Admin-Projektseite);
Zellen sind über Tabulators `tabulator-field`-Attribut adressierbar.

## E. Entscheidungen Phase 4 (Live-Updates, Konflikte)

### E1 Broker je Worker (NPT-03)
`npt/live.py`: Ein `LiveBroker` je Gunicorn-Worker, gestartet im Lifespan der
Bearbeiter-App. Genau **eine LISTEN-Verbindung** je Worker (psycopg `AsyncConnection`,
TCP-Keepalive, alle 30 s `SELECT 1` als Lebenszeichen, Wiederaufbau mit Backoff bis 10 s;
nach jedem Aufbau werden alle abonnierten Projekte einmal geprüft, damit währenddessen
verlorene NOTIFYs nichts verschlucken). Benachrichtigungen werden **150 ms gebündelt**,
danach **eine Delta-Abfrage je Projekt** und Verteilung an alle lokalen Clients. Die
Abfragen laufen über SQLAlchemy Core (NPT-10) auf einer eigenen Engine mit **einer**
Verbindung in einem eigenen Thread-Limiter (Kapazität 1): sie konkurrieren weder mit dem
Request-Pool noch mit dem Standard-Threadpool. Pro Worker also 2 zusätzliche
DB-Verbindungen (LISTEN + Abfrage).

### E2 Cursor-Regel und Delta-Endpunkt
Weil jede Schreibtransaktion die Projektzeile bis zum Commit sperrt, sind alle
Sequenzen ≤ `change_seq` eines Schnappschusses committet. Deshalb wird **zuerst**
`change_seq` gelesen und **danach** die Zeilen mit `row_seq > since` – der gelieferte
Cursor verliert keine Änderung (jüngere Zeilen können doppelt kommen). Die Phase-3-Liste
las umgekehrt (erst Zeilen, dann `seq`); das ist korrigiert.
`GET /p/<token>/api/systems?updated_since=<seq>` liefert `{seq, full_reload, rows,
project}`; Cursor unter `reset_seq` oder über `change_seq` → `full_reload: true`
**mit allen Zeilen** (spart dem Client einen zweiten Abruf). Ungültiger Cursor → 422.
Der Client wendet eine Zeile nur an, wenn ihr `row_seq` neuer ist (Duplikate harmlos).

### E3 SSE-Protokoll
`GET /p/<token>/api/events[?since=<seq>]`, `Last-Event-ID` hat Vorrang. Ereignisse:
`delta` (`{seq, since, rows, project:{status, read_only}}`), `reload` (Cursor unter
`reset_seq`), `gone` (`{reason: token|geloescht}`, danach schließt der Server). `id` =
`change_seq`; erster Frame `retry: 3000`; Heartbeat als Kommentar `: hb` alle 15 s.
Header `X-Accel-Buffering: no`, `Cache-Control: no-store`, keine Kompression. Beim
Verbinden wird unter derselben Sperre wie das Verteilen angemeldet und nachgeholt –
jede Änderung kommt entweder im Nachhol-Frame oder in einem späteren Delta. Ein Frame
wird einmal serialisiert und von allen Client-Queues geteilt.
Token-Neugenerierung und Projektlöschung senden jetzt ebenfalls NOTIFY (`k=token`,
`k=deleted`); offene Streams mit alter Token-Generation bekommen `gone` und werden
geschlossen.

### E4 Zulassung, Überlauf, Timeouts
- Der SSE-Endpunkt hängt **nicht** am Zulassungs-Semaphor und hält keine Request-Session
  (eigener Router); Token-Auflösung und Nachholen laufen über die Broker-Verbindung.
- Grenze `SSE_MAX_PER_WORKER` (200, `limits.py`; seit Phase 9 300, J10), darüber **503**
  `ueberlastet` mit `Retry-After: 5`. Bei 4 Workern: 1.200 Streams.
- Client-Queue 64 Ereignisse; Überlauf → Queue leeren, Verbindung schließen; der Browser
  verbindet nach `retry` (3 s) neu und holt per `Last-Event-ID` nach.
- Gunicorn `timeout` 60 s betrifft bei Uvicorn-Workern nur den Herzschlag zum Arbiter
  (nicht die Request-Dauer), `keepalive` 75 s > Heartbeat 15 s, `graceful_timeout` 20 s;
  Uvicorn bricht offene Streams beim Herunterfahren nach 5 s ab
  (`timeout_graceful_shutdown`), damit Clients sofort auf einen anderen Worker wechseln.
  Die Rangfolge prüft `limits.check_ranking()`.
- Reverse Proxy (Beispiele: `docs/INSTALLATION.md` §5): `proxy_buffering off`, `proxy_read_timeout` deutlich
  über 15 s (z. B. 1 h), keine gzip-Kompression für `text/event-stream`, HTTP/1.1.

### E5 Browser (npt-live.js, hauptliste.js)
- `EventSource` mit `?since=<Cursor>`; der Browser verbindet bei Netzfehlern selbst neu
  (mit `Last-Event-ID`). Bei HTTP-Fehler (503/404) eigener Neuaufbau mit Backoff 5–60 s
  und Jitter. Solange der Stream nicht offen ist: **Polling alle 5 s** über
  `updated_since`. Status im Kopf: Live / Verbinde … / Abgleich alle 5 s / Keine
  Verbindung / Getrennt.
- Geänderte Zellen blinken kurz (CSS-Animation per Klasse, keine Inline-Styles;
  `prefers-reduced-motion` → Rahmen statt Animation), neue Zeilen ebenso.
- **Fokus-Schutz:** Ein Feld mit offenem Editor oder laufendem Speichern wird nie
  überschrieben. Weicht der Serverwert ab, wird die Zeile zurückgestellt; bei offenem
  Editor erscheint „Wert wurde von anderer Person geändert: …" mit **Übernehmen**
  (Editor schließen, fremden Wert zeigen) oder **Behalten** (fremder Wert gilt als
  gesehen; Speichern überschreibt ihn bewusst). Ohne Entscheidung führt Speichern zum
  409-Dialog (Übernehmen / Trotzdem überschreiben = erneut senden mit `expected =
  current`). Zurückgestellte Zeilen werden angewendet, sobald der Editor zu ist.
- **Filter/Sortierung** werden bei fremden Änderungen nicht automatisch neu angewendet
  (Zeilen springen nicht unter dem Cursor weg); betrifft eine Änderung eine gefilterte
  oder sortierte Spalte bzw. die Suche, erscheint „Ansicht aktualisieren". Neue Zeilen
  werden erst eingefügt, wenn niemand editiert (Tabulators `addRow` filtert neu und würde
  einen offenen Editor schließen).
- Projekt abgeschlossen/archiviert → Editoren aus, „Neues System" weg, Hinweis; wieder
  geöffnet → Bearbeitung wieder an (war die Seite schreibgeschützt geladen: Hinweis
  „Seite neu laden"). Token gewechselt/gelöscht → Hinweis, Live und Export aus.

### E6 Tests und Messungen
- Unit (Fake-Quelle): Bündelung (eine Abfrage je Projekt, geteilter Frame), Nachholen,
  `reset_seq`, Status ohne Zeilen, Token/Löschung, Überlauf, Kapazität, Heartbeat,
  LISTEN-Wiederaufbau, Wiederholung fehlgeschlagener Abfragen.
- Integration: zwei Uvicorn-Prozesse gegen dieselbe Postgres, vier Clients: Live-Latenz
  **median 156 ms, max 162 ms** (Bündelfenster 150 ms); Nachholen per `Last-Event-ID`
  und `?since=`; Neuanlage, Status, Token, Löschung; Header.
- E2E (`tests/e2e/live_smoke.py`, Playwright 1.46/Chromium 128, gegen den Dev-Stack):
  UI-Änderung in Kontext A sichtbar in B nach 0,87 s (inkl. 300 ms Speicher-Entprellung),
  Fokus-Schutz/Behalten, Konfliktdialog/Überschreiben, Neuanlage, Polling-Fallback bei
  blockiertem SSE (5,9 s), Abschluss/Wiederöffnen, Token-Wechsel.
- **Frühe Lastprobe** (`loadtest/locustfile.py`, Locust 2.47, Dev-Stack auf dem Testhost,
  4 Worker, ein Projekt mit 2.000 Systemen, Locust auf demselben Host): 500 Nutzer
  (480 mit offenem SSE-Stream, 20 Schreiber, ≈ 19 Schreibvorgänge/s, eigene
  XFF-Adresse je Nutzer), 2 min nach Hochlauf: **0 Fehler, PATCH p50 19 ms, p95 35 ms,
  p99 47 ms**; Live-Latenz Schreiben → Empfang (Stichprobe 20 Clients): p50 180 ms,
  p95 960 ms, max 1,4 s. App-CPU ≈ 53 %, RAM ≈ 700 MB. Vollständiger Lasttest: J6,
  `docs/LOADTEST.md`. Für die Probe war `TRUSTED_PROXY_IPS` auf das
  Docker-Gateway gesetzt (danach zurückgesetzt).
- Werkzeuge außerhalb des App-Images: `tests/e2e/Dockerfile` (Playwright-Image per
  Digest gepinnt), `loadtest/Dockerfile` + `requirements.txt`.

## F. Entscheidungen Phase 5 (Pivot-Übersicht)

### F1 Aggregation und Ordnung
`npt/pivot.py`: `GROUP BY nullif(digital_access_zone, ''), affected` – leere DAZ (NULL
und Leerstring) bilden **eine** Zeile „(ohne DAZ)", die **am Ende** steht; übrige DAZ
alphabetisch ohne Groß-/Kleinschreibung (Gleichstand nach Codepoints, z. B. `A-Zone` vor
`a-zone`). DAZ, die sich nur in der Schreibweise unterscheiden, bleiben getrennte Zeilen
(die Filter-Grammatik vergleicht `x.` exakt). Spalten: (leer), In Prüfung, Ja, Nein;
Zeilen-, Spalten- und Gesamtsumme.

### F2 Drilldown über die bestehende Grammatik
Jede Zahl ist ein Link `/p/<token>/?…` in kanonischer Form (`filters.to_params`):
Zelle = `x.digital_access_zone=<DAZ>` (leer = ohne DAZ) + `f.affected=<schlüssel|leer>`,
Zeilensumme = nur DAZ, Spaltensumme = nur Status, Gesamt = ohne Filter. Kennzahlen:
„bearbeitet" = `f.affected=in_pruefung&f.affected=ja&f.affected=nein`, „Ja ohne Aktion" =
`f.affected=ja&f.action_taken=leer` – beide klickbar. Ein Konsistenztest wertet jeden
Link über die maßgebliche Python-Filterlogik gegen zufällige Testdaten (inkl. DAZ mit
`&`, `=`, `?`, `<…>`, Umlauten, `''` und NULL) aus: Trefferzahl = Zellzahl, Zellen einer
Zeile disjunkt. Die gemeinsamen Filtervektoren haben sechs Pivot-Fälle mehr (JS = Python).

### F3 Live und Last
Die Übersicht hängt am selben SSE-Stream wie die Liste. Neu geladen wird `GET
/p/<token>/api/pivot` **höchstens einmal je Sekunde** (erste Änderung nach Ruhe sofort,
weitere gesammelt) plus 0–300 ms Streuung; ohne Stream Polling alle 5 s. Damit viele
offene Übersichten nach einer Änderung nicht je eine Zählung auslösen, cacht jeder Worker
das Ergebnis je Projekt und `change_seq` (Cursor vor der Zählung gelesen, wie beim Delta).
Die Seite ist serverseitig gerendert (funktioniert ohne JS); das Skript baut Tabelle und
Kennzahlen per DOM-API neu (nur `textContent`) und hebt geänderte Zahlen kurz hervor.

### F4 Navigation und Tests
Die Platzhalter-Seite aus Phase 3 ist ersetzt; die Kopfnavigation markiert die aktive
Seite (`aria-current`). Tests: Aggregation inkl. leerer DAZ und Reihenfolge, Summen,
Kennzahlen, Konsistenz der Links, Cache, Escaping der DAZ, schreibgeschützte Projekte.
E2E (`tests/e2e/pivot_smoke.py`, Dev-Stack): Übersicht aktualisiert sich nach einer
Änderung in einem anderen Kontext nach 0,9 s ohne Neuladen; zehn geklickte Zahlen
(Gesamt, Spaltensummen, Zeilensumme, Zellen, „(ohne DAZ)", beide Kennzahlen) ergeben in
der Hauptliste exakt die angezeigte Anzahl.

## G. Entscheidungen Phase 6 (Admin-Upload ins Projekt, Änderungsprotokoll)

### G1 Parsing und Validierung
`npt/project_upload/parse.py` nutzt dieselbe Spaltenlogik wie der Stammdatenimport
(`read_rows`/`map_header`, C1): Kopfzeile in den ersten 10 Zeilen, tolerant, nur erstes
Blatt, `read_only`/`data_only`. Zulässig sind alle 15 Felder aus 3.1; „Herkunft“ ist
nicht über den Upload änderbar und wird mit Hinweis ignoriert. Der Excel-Export der
Hauptliste lässt sich unverändert wieder hochladen (Enums als Anzeigename, `quotePrefix`
verändert den Wert nicht).
- Geprüft wird mit der einen Domain-Validierung (`clean_master_value`,
  `clean_editable_value`, `enum_key_from_label`). Anders als beim Stammdatenimport
  (Kürzen mit Warnung) ist im Upload **jeder** ungültige Wert ein **Fehler mit Zeilenangabe**
  (ungültiger Auswahlwert mit Nennung der erlaubten Werte, zu lange Texte, leere/doppelte
  System-ID); die Übernahme ist dann gesperrt. Begründung: der Upload überschreibt
  Projektdaten, stilles Kürzen wäre Datenverlust. Ungültige E-Mail-Adressen sind wie
  beim Import eine Warnung.
- **Leere Zellen löschen nie** vorhandene Werte. Auch die Auswahlwerte „Leer“/„(leer)“
  gelten als „kein Wert geliefert“ (konservativ: ein Upload kann nichts leeren; Leeren
  geht nur in der Hauptliste bzw. über „Stammdaten bearbeiten“). Hinweis am
  Upload-Dialog (Text + Tooltip), in der Vorschau und auf der Projektseite.
- System-IDs mit Präfix `NEW-` sind erlaubt, wenn sie im Projekt existieren (Korrektur
  manuell angelegter Systeme); unbekannte `NEW-`-IDs lassen sich nicht anlegen (die
  Vergabe gehört der Sequenz, 5.6).

### G2 Diff, Gruppen, Fingerprint
Je geliefertem Feld: gleich → unverändert; Ziel leer → **Neubefüllung**; Ziel gefüllt
und abweichend → **Konflikt**. Ein System kann in beiden Gruppen stehen; „Unverändert“
zählt Systeme ohne jede Änderung. Unbekannte IDs werden mit Zeile und Kerndaten
aufgelistet (Default: ignorieren). Der **Fingerprint** umfasst nur die betroffenen
Felder (alt/neu), die Unbekannten und die Zahl der unveränderten Systeme – Bearbeitungen
**anderer** Felder machen die Vorschau nicht ungültig, Bearbeitungen betroffener Felder
schon (auch ein Wechsel des alten Werts eines Konflikts).

### G3 Auswahl der Konflikte
Je Konfliktfeld eine Checkbox, je Zeile „ganze Zeile“, oben „alle Konflikte auswählen“
(JS, `data-check-group`/`data-check-all`); zusätzlich der Button „Übernehmen und alle
Konflikte überschreiben“ (`alle=1`, funktioniert ohne JS). Der Formularwert eines
Konflikts ist `<feld>:<hash(System-ID, Feld, alt, neu)>:<System-ID>`: eine Auswahl gilt
nur für genau diese alt/neu-Kombination. Admin-Formulare dürfen bis zu 100.000 Felder
haben (vorher 20.000; ein Upload mit 2.000 Zeilen × 14 Feldern bleibt darunter).

### G4 Bestätigung, Revalidierung, Transaktion
`service.apply`: zuerst Projekt sperren (`lock_project`, nur aktive Projekte), dann alle
Systemzeilen `FOR UPDATE`, Diff neu berechnen. Weicht der Fingerprint ab (oder ist eine
anzulegende ID nicht mehr unbekannt), wird zurückgerollt und die **aktualisierte
Vorschau** (HTTP 409, Hinweis) gezeigt; unveränderte Auswahlen bleiben angehakt, der
Admin bestätigt erneut. Sonst in **einer** Transaktion: Backup `vor_import` (Stand vor dem
Import, `change_seq` davor; G5), Updates (`version+1`, `row_seq`), Neuanlagen
(`origin=admin_upload`, `version 1`), `change_log` (Quelle `admin_upload`, Admin-
Identifier, Client-IP; Neuanlage = `angelegt` + je geliefertem Feld ein Eintrag), Audit,
`NOTIFY` (`k=upload`) → Live-Update über den Phase-4-Pfad. Gibt es nichts zu schreiben
(keine Neubefüllung, kein ausgewählter Konflikt, keine Neuanlage), wird weder ein Backup
erstellt noch `change_seq` verbraucht.

### G5 Backup-Erzeugungskern (A4)
`npt/backups.py`: Schnappschuss in **einer** SQL-Anweisung (Projekt LEFT JOIN Systeme;
unter READ COMMITTED sieht eine Anweisung genau einen Stand, ohne Schreiber zu
blockieren); JSON (Format `npt-projekt-backup` v1, alle Systemspalten inkl. `origin`,
`version`, Zeitstempel; Projekt ohne `access_token`/`token_generation`) und .xlsx (gleiche
Spalten und Formel-Schutz wie der Export). Ablage `BACKUP_DIR/<projekt-id>/<UTC-Zeit>-
<auslöser>-<zufall>.{json,xlsx}` atomar (tmp im selben Verzeichnis, `fsync`, `rename`,
`fsync` des Verzeichnisses), Rechte 0640. In `backups` stehen nur Pfade relativ zu
`BACKUP_DIR`. `vor_import` läuft in der Import-Transaktion unter der Projektsperre;
scheitert der Versuch vor dem Commit, werden die Dateien wieder gelöscht.

### G6 Änderungsprotokoll
`/admin/projekte/<id>/aenderungen`: Filter System-ID (enthält, ohne Groß-/Klein-
schreibung), Feld (inkl. „System angelegt“), zusätzlich Quelle, Zeitraum von/bis als
Kalendertage in Europe/Berlin (`bis` einschließlich, Grenzen in UTC umgerechnet);
neueste zuerst, 100 Einträge je Seite. Ungültige Filterwerte werden ignoriert. Export
`.xlsx` mit denselben Filtern (höchstens 200.000 Zeilen), Zeit als Text in Europe/Berlin,
Enum-Werte als Anzeigenamen, jede Zelle als Text mit `quotePrefix` bei `= + - @` (D7).

### G7 Tests
Unit: Parsing (Labels/Schlüssel/„Leer“, Enum-Fehler mit Zeile, Doppelte, Längen,
Herkunft ignoriert, Export-Reimport verlustfrei), Diff-Gruppierung, Fingerprint nur über
betroffene Felder. DB/Service: Neubefüllung vs. Konflikt, leere Zellen löschen nichts,
Konflikt einzeln/alle, Revalidierung (kein Schreiben, kein Backup, neue Vorschau),
Unbekannte ignorieren/anlegen (`NEW-` nicht anlegbar), Transaktionalität (Fehler nach dem
Schreiben → nichts geändert, kein Backup, Dateien entfernt), Schreibschutz, Live-Update
(NOTIFY + Delta). Web: Upload-Fehler, Vorschau, Bestätigung, 409 mit vorausgewählten
unveränderten Konflikten, Änderungsprotokoll (Filter, Zeitzone, Seiten, Export).
E2E `tests/e2e/upload_smoke.py` (Playwright `client_certificates` am mTLS-Gateway,
Dev-Stack): Vorschau 2 Konflikte / 1 Neubefüllung / 1 unbekannt / 1 unverändert,
Einzelbestätigung, Live-Update in der offenen Hauptliste nach 0,12 s, erneute Bestätigung
nach zwischenzeitlicher Bearbeitung, „alle Konflikte“, Protokoll. Das E2E-Image enthält
dafür zusätzlich `openpyxl`.

## H. Entscheidungen Phase 7 (Backups, Restore)

### H1 Einstellungen und Auslöser
Je Projekt Intervall `aus`/15/30 min/1/4/12/24 h (Default 1 h, B13) und Aufbewahrung
n (1–10.000, Default 48) auf der Projektseite. Auslöser in `backups.trigger`:
`zeitgesteuert`, `manuell` (Button), `vor_import` (G5), `vor_restore`. Jedes Backup =
JSON + .xlsx (G5); `last_backup_seq`/`last_backup_at` werden von jedem Auslöser
fortgeschrieben (der Zeitplan zählt ab dem letzten Backup gleich welcher Art).

### H2 „Nur bei Änderung“
Maßgeblich ist eine **Datenänderung**: `GREATEST(max(row_seq), reset_seq) >
last_backup_seq`. `change_seq` allein reicht nicht, weil Status- und Token-Wechsel ihn
ebenfalls erhöhen (NPT-03), aber keine Daten ändern. Ein frisch angelegtes Projekt ohne
Bearbeitung wird nicht zeitgesteuert gesichert (der Stand ist die Stammdatenkopie).
`vor_import`/`vor_restore` vermerken den Stand **vor** der Änderung, daher folgt nach dem
nächsten Intervall automatisch ein Backup des neuen Stands.

### H3 Scheduler (NPT-05)
`npt/jobs/backups.py` im jobs-Prozess, Tick 15 s (`npt.jobs.TICK_S`). Je fälligem
Projekt eine eigene Transaktion mit `pg_try_advisory_xact_lock(("NPTB" << 32) | id)`;
ist die Sperre belegt, wird das Projekt in diesem Tick übersprungen. Nach dem Erwerb wird
die Fälligkeit erneut geprüft (READ COMMITTED, eine Instanz kann gerade committet haben)
– so sichert bei mehreren Instanzen genau eine. Fehler eines Projekts werden protokolliert
und halten die anderen nicht auf. Manuelles Backup und Einstellungen nehmen dieselbe
Sperre wartend (`pg_advisory_xact_lock`).

### H4 Aufbewahrung
Nach zeitgesteuerten und manuellen Backups, nach einem Restore und beim Speichern der
Einstellungen (Verkleinern wirkt sofort) bleiben die neuesten n Backups; alle Auslöser
zählen mit. Beim Restore sind das wiederhergestellte und das `vor_restore`-Backup
geschützt. Zeilen werden in der Transaktion gelöscht (`DELETE … RETURNING`, parallele
Läufe löschen nichts doppelt), Dateien erst nach dem Commit. Beim Löschen eines Projekts
werden seine Backup-Dateien mit entfernt (die Zeilen gehen per Cascade).

### H5 Download
Nur über `/admin/projekte/<id>/backups/<backup-id>/datei.{json|xlsx}`: kein Pfad aus dem
Request; der gespeicherte relative Pfad muss nach `resolve()` im Verzeichnis
`BACKUP_DIR/<projekt-id>/` liegen (sonst „nicht gefunden“, auch bei manipulierter
Tabelle oder Symlink nach außen). ASCII-Dateiname, `Cache-Control: no-store`.

### H6 Restore
Bestätigungsseite (Zeitpunkt, Auslöser, Stand, Warntext, Pflicht-Checkbox +
`data-confirm`), nur in **aktiven** Projekten (wie Upload und Admin-Bearbeitung; ein
abgeschlossenes Projekt zuerst wieder öffnen). Eine Transaktion (`npt/restore.py`):
Projekt sperren → JSON lesen und mit der einen Domain-Validierung prüfen (Format,
Projekt-ID, Enums, Längen, Herkunft, doppelte IDs; ungültig → Abbruch ohne Änderung) →
Backup `vor_restore` → Systeme angleichen: nach dem Backup entstandene **löschen**,
seither fehlende mit gleicher System-ID wieder anlegen (auch `NEW-…`, die Sequenz wird
nicht zurückgesetzt), abweichende Felder inkl. Herkunft schreiben (`version+1`) →
`change_log` Quelle `restore` (Aktionen `geaendert`/`angelegt`/`geloescht`, Admin-
Identifier) → `reset_seq = change_seq` → `NOTIFY k=reload`. Clients bekommen über den
Phase-4-Pfad `reload` (SSE) bzw. `full_reload` (Polling) und laden die Liste neu.
Projektname, Status, Token und Backup-Einstellungen bleiben unverändert.
Migration `0002` erweitert den CHECK von `change_log.action` um `geloescht`.

### H7 Tests
Backup-Dateien (vollständig, ohne Token, keine tmp-Reste, Formel-Schutz im xlsx),
Pfad-Traversal, atomares Schreiben bei Fehler; Scheduler nur bei Datenänderung und nach
Ablauf des Intervalls (Status/Token zählen nicht, Intervall „aus“); fremd gehaltene
Sperre → überspringen; sechs parallele Ticks über drei Projekte → genau ein Backup je
Projekt; Aufbewahrung inkl. sofortiger Verkleinerung; Roundtrip Backup → Änderungen,
neues manuelles System, Upload-Neuanlage, gelöschtes manuelles System → Restore = exakt
der Backup-Stand, `vor_restore` mit dem Stand davor, Rück-Restore, `reset_seq`,
`full_reload`, NOTIFY `reload`; ungültige Datei/fremdes Projekt/abgeschlossenes Projekt.
Web: Einstellungen, Button, Liste, Downloads, manipulierter Pfad, Bestätigungsdialog,
Löschen entfernt Dateien. E2E (`upload_smoke`): Backup „vor Import“ in der Liste,
Restore über das Gateway → offene Hauptliste lädt neu (0,38 s).

## I. Entscheidungen Phase 8 (Mailversand)

### I1 Editor, Betreff, Sanitisierung
- Editor: **Quill 2.0.3** (NPT-01), mitgeliefert unter `static/vendor/quill/` (UMD-Bundle
  `dist/quill.js` als `quill.min.js` + `quill.snow.css`, ohne sourceMappingURL; Version,
  Registry-Integrity und sha256 in `VERSION`). Kein CDN, kein Inline-Script: `mail.js`
  initialisiert den Editor; Quill setzt Stile nur per CSSOM (CSP-tauglich, `style-src
  'self'` bleibt). Formate fest auf Überschrift 1–3, fett, kursiv, nummerierte/
  Aufzählungsliste, Link (`formats`-Allowlist, auch beim Einfügen). Übertragen wird
  `getSemanticHTML()` (echte `<ul>/<ol>`) über ein verstecktes Textfeld; ohne JS bleibt das
  Textfeld sichtbar (HTML-Eingabe, gleiche Sanitisierung).
- **Betreff ist eine Klartextzeile** (Abweichung vom Wortlaut „Editor mit Formatierung für
  Betreff und Text"): Der Mail-Header `Subject` kennt keine Formatierung; formatierter
  Betreff würde beim Versand ohnehin verworfen. Steuerzeichen (inkl. CR/LF, U+2028/2029)
  werden zu Leerzeichen, max. 200 Zeichen.
- Sanitisierung serverseitig mit **nh3 0.3.7** beim Speichern (Allowlist): `p, br, strong,
  b, em, i, ul, ol, li, a, h1–h3`; einziges Attribut `a href` (nur `http`, `https`,
  `mailto`; relative Ziele, `javascript:`, `data:` usw. verlieren das `href`), `rel="noopener
  noreferrer"` fest; `script`/`style` samt Inhalt entfernt; auch nh3-Standardattribute
  (`title`, `lang`) entfallen. Quill-`&nbsp;` wird zu normalem Leerzeichen. Ein `href`, das
  `[LINK]` enthält, wird verworfen (der Platzhalter darf nur im Text stehen, nie in einem
  Attribut). Gespeichert wird nur das sanitisierte HTML.

### I2 `[LINK]` und Deep-Links (NPT-05)
- Ersetzung **nach** der Sanitisierung beim Rendern je Empfänger, in **einem** Durchlauf
  (`<p>[LINK]</p>` → Block, sonst inline): je System `<li><a>System-Name (System-ID)</a>`
  (ohne Namen nur die ID), danach „Alle meine Systeme (n Systeme)". Name, ID und URL werden
  einzeln HTML-escaped; ein System-Name wie `<script>` oder `[LINK]` erzeugt weder Markup
  noch eine weitere Ersetzung.
- URL: `PUBLIC_BASE_URL/p/<token>/?x.system_id=<ID>` (Filter-Grammatik D3, exakter
  Vergleich), Sammel-Link mit allen IDs des Empfängers (wiederholtes `x.system_id`). Über
  6.000 Zeichen wird der Sammel-Link in Teile geteilt („Teil 1 von 2"), damit Proxies mit
  8-KB-Request-Zeile ihn nicht abweisen (betrifft erst > ~250 Systeme je Adresse).
- Der Projekt-Token wird **beim Senden** gelesen: Nach „Token neu generieren" enthalten
  noch ausstehende Mails die gültige URL. Bereits versandte Links werden ungültig (wie jede
  alte Projekt-URL).
- Plaintext-Teil wird aus dem fertigen HTML erzeugt (Absätze, `-`/`1.`-Listen,
  unterstrichene Überschriften, Links als `Text <URL>`).

### I3 Selektoren und Empfänger
- Registry `npt.mail.audience.SELECTORS` (`register(Selector(key, name, description,
  params))`); das Filterkriterium ist in der Filter-Grammatik der Hauptliste formuliert und
  wird mit der maßgeblichen Python-Implementierung ausgewertet: (1) `f.affected=leer`,
  (2) `f.affected=ja&f.action_taken=leer`, (3) „Benutzerdefiniert" = gespeicherte
  Filterparameter. Weitere Selektoren = ein `register`-Aufruf, ohne Umbau.
- „Benutzerdefiniert": eingefügt wird die **Adresse der gefilterten Hauptliste** (oder nur
  der Query-Teil). Übernommen werden `q`, `f.*`, `x.*` in kanonischer Form, nicht `sort`/
  `dir`, der Token in der URL wird ignoriert (das Projekt bestimmt die Kampagne). Ein Link
  „als Mail-Zielgruppe verwenden" **in** der Hauptliste entfällt bewusst: die Bearbeiter-
  Seite darf den Admin-Bereich (anderer Port/Host, nur mTLS) weder kennen noch verlinken.
  Stattdessen: Admin-Formular mit Link „Hauptliste öffnen", Adresse aus der Adresszeile
  kopieren; zusätzlich Vorbelegung per `/admin/projekte/<id>/mail/neu?selektor=
  benutzerdefiniert&filter=<query>`.
- Empfänger: getroffene Systeme nach normalisierter Adresse gruppiert (Regel C2:
  `;`/`,`, `Name <adresse>`, lowercase/getrimmt, dedupliziert). Jede gültige Adresse =
  eine Mail mit ihren Systemen. Leere Felder und ungültige Teile stehen im Bericht
  (Grund, Angabe, System-IDs) und werden übersprungen; ein Feld mit einer gültigen und
  einer ungültigen Adresse liefert die Mail an die gültige **und** einen Berichtseintrag.

### I4 Datenmodell (Migration 0003)
- `mail_runs` (PK `(campaign_id, send_run)`): je explizit gestartetem Lauf eingefrorener
  Betreff/Text/Selektor, Modus (`alle` | `nur_fehlgeschlagene`), Empfänger-/Systemzahl,
  Bericht der übersprungenen Angaben, Start/Abbruch mit Admin.
- `mail_deliveries.systems` (eingefrorene `[{id, name}]`), `message_id`; FK
  `(campaign_id, send_run)` → `mail_runs`. `mail_campaigns.updated_at/updated_by`.
- Die Kampagne bleibt nach einem Lauf bearbeitbar; Änderungen gelten erst für den nächsten
  Lauf (der laufende nutzt seinen eingefrorenen Stand).

### I5 Doppelversand-Schutz und erneuter Versand
- Start nur mit **Bestätigungsdialog** (`data-confirm` mit Empfängerzahl) **und**
  Pflicht-Checkbox (wirkt auch ohne JS). Das Formular trägt `expected_run` (zuletzt
  gesehene Laufnummer) und `expected_count`: Unter `SELECT … FOR UPDATE` der Kampagne muss
  `send_run == expected_run` gelten (ein doppelt abgeschicktes Formular legt keinen
  zweiten Lauf an → Meldung „bereits gestartet"), und die neu berechnete Empfängerzahl muss
  der bestätigten entsprechen (sonst aktualisierte Vorschau, erneut bestätigen).
- Neuer Lauf nur, wenn kein Lauf der Kampagne mehr offen ist (ausstehend/in Versand), und
  nur in aktiven Projekten (Links auf schreibgeschützte Listen wären sinnlos).
- Erneuter Versand nur explizit als `send_run + 1`: „Erneut an alle" (Zielgruppe neu
  berechnet) oder „Nur fehlgeschlagene erneut" (die im letzten Lauf fehlgeschlagenen
  Adressen mit ihren eingefrorenen Systemen, aktueller Text). UNIQUE `(campaign_id,
  send_run, email_norm)` verhindert Doppelzeilen in der Datenbank.
- „Ausstehende abbrechen": noch nicht abgeholte Sendungen → fehlgeschlagen („abgebrochen
  durch Admin"); eine gerade gesendete Mail läuft zu Ende.

### I6 Queue und Sender (NPT-05)
- Sender als **eigener Thread im jobs-Prozess** (nicht im 15-s-Tick: ein Lauf mit 400
  Mails dauert bei 5/s über eine Minute und würde Backups blockieren). Nur ein Sender aktiv:
  `pg_try_advisory_lock` (Session) auf einer eigenen Verbindung, Prüfung der Verbindung alle
  30 s; andere Instanzen versuchen es alle 5 s. Ohne `SMTP_HOST`/`SMTP_FROM` startet kein
  Sender (Warnung im Log), der Versand-Button ist dann gesperrt.
- Abholung einzeln: `UPDATE … WHERE id = (SELECT … WHERE status='ausstehend' AND
  next_attempt_at <= now() ORDER BY next_attempt_at, id FOR UPDATE SKIP LOCKED LIMIT 1)
  RETURNING …` → `in_versand`, `attempts+1`, Commit, **dann** senden.
- Token-Bucket mit Kapazität 1 (kein Burst): Abstand ≥ 1/`MAIL_RATE_PER_SEC`. Gemessen auf
  dem Dev-Stack: 386 Mails in 81,8 s (4,7/s inkl. Rendern und Statusschreiben).
- Fehlerklassen: SMTP-Antwort 4xx, Verbindungsabbruch, Timeout, Netz-/TLS-Fehler =
  vorübergehend → wieder `ausstehend` mit Backoff 30 s, 60 s, 120 s, 240 s; nach dem
  5. Versuch endgültig fehlgeschlagen. 5xx (auch 535 Anmeldung) = sofort endgültig – ein
  falsch konfiguriertes Konto lässt also den Lauf fehlschlagen; danach „Nur fehlgeschlagene
  erneut". Fehlertext (gekürzt) je Empfänger sichtbar.
- `in_versand` länger als 10 min (Absturz während des Sendens) → `fehlgeschlagen`
  „Status unklar … nicht erneut gesendet" (geprüft beim Erwerb der Sperre und jede
  Minute). Die Mail kann bereits zugestellt sein; ein erneuter Versand ist eine bewusste
  Admin-Entscheidung.
- SMTP: eine Verbindung wird über mehrere Mails wiederverwendet und bei jedem Fehler bzw.
  Leerlauf geschlossen; `starttls` erzwingt STARTTLS mit Zertifikatsprüfung (System-CAs),
  `tls` = implizites TLS, `none` nur für interne Relays/Test-SMTP. `SMTP_TIMEOUT_S`
  (Default 30) neu.

### I7 Nachricht
`multipart/alternative` (text/plain aus dem HTML, text/html als vollständiges Dokument),
genau ein `To` (Empfänger einzeln, kein Cc/Bcc), `From`/`Reply-To` aus der Env (geprüft:
gültige Adresse, keine Steuerzeichen, genau eine Adresse), `Message-ID` mit der
Absender-Domain, `Date`, `Auto-Submitted: auto-generated`. Header über die SMTP-Policy der
Standardbibliothek (CR/LF in Headerwerten → Fehler) und vorher bereinigt; Umlaute im
Betreff nach RFC 2047.

### I8 Testmail, Vorschau, Status, Audit
- Testmail **synchron** an eine frei eingegebene Adresse (gültig, normalisiert), Inhalt mit
  den Systemen des gewählten Beispiel-Empfängers, Betreff „[TEST] …"; zählt nicht als Lauf.
  Fehler → Meldung, Details im Audit-Log und Server-Log.
- Vorschau bei jedem Aufruf neu berechnet: Empfänger, Systeme der Zielgruppe, Systeme mit
  leerer/ungültiger Angabe, Beispielmail (Auswahl des Empfängers), Textfassung, Liste der
  übersprungenen Angaben, Link auf die Zielgruppe in der Hauptliste, Warnung ohne `[LINK]`.
  Die Beispielmail wird als sanitisiertes HTML direkt eingebettet.
- Status je Empfänger je Lauf (ausstehend / in Versand / gesendet / fehlgeschlagen,
  Versuche, Zeit, Fehler); bei offenem Lauf aktualisiert `mail.js` Zähler und Tabelle alle
  3 s über `…/status.json` (nur `textContent`) und lädt nach Abschluss neu.
- Audit: `mail_kampagne_angelegt/_geaendert/_geloescht`, `mail_testmail(_fehlgeschlagen)`,
  `mail_versand_gestartet` (Lauf, Modus, Zahlen), `mail_versand_abgebrochen`.

### I9 Test-SMTP und Tests
- Dev/Test: Overlay `docker-compose.mailpit.yml` (`axllent/mailpit:v1.32.0` per Digest
  gepinnt, ohne Host-Port, read-only, tmpfs-Datenbank) setzt `SMTP_HOST=mailpit`. Die
  portable `docker-compose.yml` enthält keinen Test-SMTP. pytest nutzt einen Fake-Transport
  bzw. einen Mini-SMTP-Server auf 127.0.0.1 (4xx/5xx je Empfänger steuerbar); es werden nie
  Mails an externe Adressen versendet.
- pytest (neu 107): Sanitisierung (script/onerror/javascript:/data:/style/relative Links),
  Plaintext, Selektoren 1 und 2 (Mehrfachadressen, ungültige/leere Angaben,
  Normalisierung), „Benutzerdefiniert" gegen alle 40 Filtervektoren, `[LINK]` (richtige
  Systeme je Empfänger, URLs zurückgeparst über die Grammatik, Escaping/Injection über
  System-Namen, Teilung), MIME-Struktur und Header-Injection, Läufe (Einfrieren,
  Doppel-Start, UNIQUE, geänderte Empfängerzahl, nur fehlgeschlagene, Abbruch), Sender
  (Drosselung, Retry 4xx mit Backoff, Aufgabe nach 5 Versuchen, 5xx endgültig, Status
  unklar, `SKIP LOCKED`, zwei Sender → einer aktiv, zwei laufende Sender → jede Mail genau
  einmal, echter SMTP-Weg), Web (Editor/CSP, Vorschau, Testmail, Versand, Status-JSON,
  Bearbeiter-App ohne Mail-Routen).
- E2E `tests/e2e/mail_smoke.py` (Dev-Stack + Mailpit): Kampagne im Quill-Editor mit
  Selektor 1 an das frische Projekt mit 2.000 Testsystemen → Vorschau 386 Empfänger
  (= eigene Berechnung), 2.000 Systeme, 126 Systeme mit leerer/ungültiger Angabe;
  Testmail; Versand mit Dialog „Mail jetzt an 386 Empfänger senden?"; **386 Mails in
  Mailpit = 386 gültige Empfänger**, je Adresse genau eine, kein Cc/Bcc; Mail eines
  Empfängers mit 53 Systemen inhaltlich geprüft (53 System-Links + Sammel-Link,
  multipart/alternative); Sammel-Link öffnet die Hauptliste mit „53 von 2000 Systemen
  angezeigt", ein System-Link mit „1 von 2000".

## J. Entscheidungen Phase 9 (Security-Härtung, E2E, Lasttest, Dokumentation)

### J1 Audit gegen §8
Vollständige Checkliste mit Nachweisen: `docs/SECURITY.md`. Gefundene und geschlossene
Lücken: (1) 500-Antworten aus Starlettes `ServerErrorMiddleware` hatten keine Security-
Header und keinen Access-Log-Eintrag (mit `add_middleware` liegt eigene Middleware *innerhalb*
davon); (2) Gateway-eigene Antworten (403/404/308) ohne `nosniff`/`X-Frame-Options`/CSP,
kein `robots.txt` am Gateway; (3) Body-Grenze auf dem Bearbeiter-Port nur über
`Content-Length` (chunked umging sie); (4) gebundene SQL-Parameter (Token-Lookup) konnten
in SQLAlchemy-Ausnahmetexten und im Postgres-Slow-Query-Log landen; (5) freistehende Token
(ohne `/p/`-Präfix) wurden im Log nicht maskiert; (6) Gateway-Upload-Grenze = genau
`UPLOAD_MAX_MB` ließ Dateien knapp unter der Grenze am Multipart-Overhead scheitern;
(7) db-Container ohne `read_only`/`cap_drop`.

### J2 Äußere Schichten: `NptFastAPI`
Beide App-Factories erzeugen `NptFastAPI(outer=[…])`: Access-Log, Security-Header und
(Bearbeiter-Port) Body-Grenze umschließen den **kompletten** Starlette-Stack inklusive
`ServerErrorMiddleware`. `AdminAuthMiddleware` bleibt innen (per `add_middleware`), damit
auch abgelehnte Admin-Requests Header und Log bekommen. Antworten, die Uvicorn selbst vor der
App erzeugt (400 ungültiges HTTP, 503 `limit_concurrency`), bleiben ohne Header – fester
Klartext ohne Nutzerdaten, akzeptiert.

### J3 Body-Grenze Bearbeiter-Port
`BodyLimitMiddleware(max_bytes = 2 × 64 KB)`: `Content-Length` darüber → sofort 413; ohne
Längenangabe wird mitgezählt und bei Überschreitung **selbst** 413 gesendet, der App ein
`http.disconnect` gemeldet und deren weitere Ausgabe verworfen (eine Ausnahme im `receive`
würde FastAPI als 400 „Body nicht lesbar" beantworten). Die fachliche JSON-Grenze 64 KB
(`check_write`, JSON-413 `zu_gross`) bleibt maßgeblich; die Middleware ist Rückfallnetz.

### J4 Logs ohne Token
Zusätzlich zu Phase 1: Maskierung freistehender 43-Zeichen-Base64url-Werte (Länge von
`token_urlsafe(32)`; SHA-256-Hex hat 64 Zeichen und bleibt lesbar), `hide_parameters=True`
an der SQLAlchemy-Engine, `log_parameter_max_length(_on_error)=0` und (Nachbesserung, J13)
`log_error_verbosity=terse` in PostgreSQL. Das Postgres-eigene Log läuft **nicht** durch den
JsonFormatter der App; was dort landen kann, steht in `docs/SECURITY.md` („PostgreSQL-Log“). Das
Gateway loggt nur `$uri` von `/admin…`. `scripts/run-e2e.sh` prüft nach der Suite alle
Container-Logs des Wegwerf-Stacks auf `/p/<20+ Zeichen>` (Befund: keiner).

### J5 Handbücher in der Anwendung
- Eine Quelle: `docs/handbuch/bearbeiter.md`, `docs/handbuch/admin.md`. `pyproject.toml`
  übernimmt das Verzeichnis per `force-include` als `npt/handbuch` ins Paket (das
  Dockerfile kopiert `docs/handbuch` in die Build-Stufe); im Quellbaum (Tests) liest
  `npt.handbook` direkt `docs/handbuch`.
- Rendern mit **markdown-it-py 4.2.0** (CommonMark + Tabellen, `html=False`: rohes HTML in
  der Quelle erscheint als Text; `javascript:`-Links rendert markdown-it nicht), danach
  **nh3-Allowlist** (Überschriften, Absätze, Listen, Tabellen, Code, Links `http(s)`/
  `mailto`/relativ mit `rel="noopener noreferrer"`, `id` an h2/h3). Kein `style`-Attribut
  (CSP `style-src 'self'`). Ergebnis je Prozess gecacht.
- Platzhalter `{{projekt}}`, `{{projekt_url}}` (Bearbeiter) bzw. `{{public_base_url}}`,
  `{{admin_base_url}}` (Admin) werden **nach** dem Sanitisieren HTML-escaped eingesetzt;
  unbekannte Platzhalter bleiben sichtbar. Links im Bearbeiter-Handbuch sind relativ
  (`uebersicht`, `./`) und funktionieren unter `/p/<token>/hilfe` ohne Platzhalter.
- `/p/<token>/hilfe` für jeden mit Projekt-URL (auch schreibgeschützte Projekte),
  `/admin/hilfe` nur auf dem Admin-Port und nur für Admins (Nicht-Admins sehen die
  Anfrage-Seite). Hilfe-Link im Kopf jeder Projektansicht farblich hervorgehoben, im
  Admin-Kopf ergänzt.
- Kurz-Tooltips: `labels.FIELD_HINTS` an den vier *-Feldern (Spaltenkopf der Hauptliste via
  Tabulator-`headerTooltip` als DOM-Element mit `textContent` – Tabulator setzt Strings per
  `innerHTML` – und `title` im Formular „Neues System“); Upload-Dialog: `title` an Datei-
  Feld und Button.

### J6 E2E-Suite
`scripts/run-e2e.sh` startet einen **frischen** Stack als eigenes Compose-Projekt
(`npt-e2e`, Ports 8490/8491 auf loopback, eigenes `admin_net` 172.31.250.32/28, Image-Tag
`e2e` – der Dev-Stack bleibt unberührt), erzeugt Test-PKI und `.env` in einem Temp-
Verzeichnis, importiert 2.000 Testsysteme über den Admin-Bereich (`tests.e2e.seed`) und
führt nacheinander aus: `inline_smoke` (neu: Inline-Bearbeitung Auswahl/Text/Bemerkung,
serverseitig geprüft, XSS-Probe, Tooltip, Hilfe), `live_smoke` (zwei Browser-Kontexte),
`pivot_smoke` (Drilldown), `upload_smoke` (Admin-Upload mit Konfliktbestätigung),
`mail_smoke` (nur an Mailpit). Danach `down -v` nur dieses Projekts (kein `prune`).
Exitcode 0 nur, wenn alle Suiten grün sind und kein Token in den Logs steht. Die Suiten
bleiben eigenständige Skripte (kein pytest-Plugin): sie brauchen einen laufenden Stack und
das Playwright-Image, nicht die Wegwerf-DB der Unit-Tests.

### J7 Lasttest
Methodik, Ergebnisse und Bewertung: `docs/LOADTEST.md`. Lastgenerator auf dem Testhost (wie
die App) mit CPU-Trennung (`--cpuset-cpus`), weil ein zweiter Host (3,9 GB RAM, Swap voll,
Load ≈ 7 auf 2 Kernen) das Ergebnis verfälscht und gefährdet hätte und andere Hosts den
Dev-Stack nur über einen neuen Host-Port bzw. SSH-Port-Forwarding erreicht hätten (auf dem
Testhost `AllowTcpForwarding no`; zusätzliche Host-Ports widersprechen der Betriebsvorgabe). Kompression
der Listen-Antwort am Reverse Proxy (`gzip` für `application/json`, 1,1 MB → ca. 0,15 MB)
ist empfohlen (`docs/INSTALLATION.md` §5), aber für das Ziel < 2 s nicht nötig.

### J8 Container-Härtung db
`read_only` mit tmpfs `/var/run/postgresql` und `/tmp`, `cap_drop: ALL` plus
`CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID` (Entrypoint: `chown` des Datenverzeichnisses,
Wechsel zu `postgres` per `su-exec`), `no-new-privileges`. Geprüft mit bestehendem
(Dev-Stack) und frischem Volume (E2E-Stack, `initdb`).

### J9 Offene Punkte / außerhalb der Entwicklungsrolle
- **Betriebs-Overlay** (B15) und Ausrollen in Produktion: Betrieb.
- **Quellcode-Verwaltung:** Entwicklung in einem privaten Repository des Betreibers; das
  öffentliche Repository wird daraus als Stand ohne Entwicklungshistorie gespiegelt
  (Zugangsdaten nur serverseitig, nicht im Repository).

### J10 SSE-Grenze je Worker 300 (statt 200)
Im Lasttest (520 Streams: 500 Nutzer + eigener Stream der 20 Schreiber, 4 Worker) erhielten
einzelne Verbindungen 503 `ueberlastet` (7 bzw. 29 je Lauf), obwohl die Gesamtkapazität 800
betrug: Gunicorn/Uvicorn verteilen neue Verbindungen nicht gleichmäßig (wer gerade wartet,
nimmt an). Der Client verbindet nach `Retry-After` 5 s neu und holt nach – fachlich
korrekt, aber mit 5 s Verzögerung für diesen Nutzer. `SSE_MAX_PER_WORKER` ist jetzt 300
(4 Worker = 1.200, Reserve für die Ungleichverteilung), `UVICORN_LIMIT_CONCURRENCY` 500
(Rangfolge `Semaphor + SSE < Uvicorn-Grenze` bleibt erfüllt, `check_ranking`). Danach
0 Abweisungen. Mehr Nutzer → `WEB_WORKERS` erhöhen (je Worker ca. 18 DB-Verbindungen).

### J11 Messung der Live-Latenz
Die Locust-Stichprobe („SSE live“) misst vom Zeitstempel des Schreibers bis zur Auswertung im
lesenden Greenlet; bei ca. 260 Greenlets je Locust-Prozess könnte sie Wartezeit des
Lastgenerators enthalten. Zur Kontrolle misst `loadtest/live_probe.py` (ein Prozess, zwei
Threads, kaum CPU) Schreibbeginn → Eingang des ersten Frames mit diesem **oder einem neueren**
Wert derselben Zeile. „Neuer“ zählt, weil Delta-Frames den aktuellen Zeilenstand tragen: Ist
ein Frame länger unterwegs als bis zur nächsten Änderung derselben Zeile, kommt sofort der
neuere Wert (der ältere war für den Nutzer nie sichtbar, der Zustand ist konsistent). Eine
erste Fassung der Probe wertete solche Fälle als „verloren“ und unterschätzte damit das p95
– kein Fehler der App. Mit der korrigierten Probe stimmen beide Messungen überein (p95 ≈ 1 s,
max < 1,3 s): die Werte sind serverseitig, Ursache und Spielraum in `docs/LOADTEST.md`.

### J12 Kapazität: `WEB_WORKERS` Default 6 (statt 4)
QA-Hinweis: 4 Worker × 300 = 1.200 SSE-Plätze; öffnen 500 Nutzer Liste **und** Übersicht in
je eigenem Tab, entstehen ≈ 1.000 Streams. J10 hat gezeigt, dass Gunicorn neue Verbindungen
ungleich auf die Worker verteilt (Abweisungen schon bei 65 % Gesamtfüllung mit 200 je
Worker) – 83 % Füllung hätte vorhersehbar 503 mit 5 s Verzögerung für einzelne Nutzer
erzeugt. Entscheidung: Default `WEB_WORKERS=6` (1.800 Plätze, 80 % Reserve über 1.000).
`SSE_MAX_PER_WORKER` bleibt 300 (gemessen, J10) – mehr Streams je Event-Loop wären
ungemessen. (Alternative verworfen: nur Doku – die Reserve wäre beim von der QA beschriebenen
Nutzungsbild vorhersehbar zu knapp gewesen.)
- **DB-Verbindungen:** je Bearbeiter-Worker höchstens 18 (Pool 10 + Overflow 5, LISTEN,
  Broker-Abfrage, Health-Monitor), je Admin-Worker 16, Jobs/Start ca. 5: bei 6/2 Workern
  ≈ 145 von `max_connections=200` (Reserve für `pg_dump`, `psql`, Wartung).
- **RAM:** je Bearbeiter-Worker ≈ 95 MB im Leerlauf. Gemessen (`docs/LOADTEST.md`, Lauf 5)
  mit 6 Workern und **1.000 Nutzern** (≈ 1.000 Streams, doppelte Schreiblast): 1.007–1.066 MB
  für den ganzen Container, also ≈ 70 % von `APP_MEM_LIMIT=1536m` – das Limit bleibt.
- **Nachweis:** Lauf 5 mit 0 Fehlern und 0 SSE-Abweisungen, PATCH p95 170 ms, Live p95
  ≈ 1,2 s. Lauf 4 (4 Worker, 500 Nutzer) bleibt der Nachweis für die Auftragsziele.
- Wer weniger Last erwartet, kann `WEB_WORKERS=4` setzen (spart ≈ 0,2 GB RAM und 36
  DB-Verbindungen). Sizing-Tabelle für Betreiber: `docs/INSTALLATION.md` §1 („Kapazität und
  Sizing“).

### J13 Nachbesserungen nach dem QA-Gate (2026-10-27)
- **Datenbank nicht erreichbar** (QA 3aeca611): Bisher endete z. B. `docker compose stop db`
  in unbehandelten Ausnahmen (500 text/plain, „Exception in ASGI application“ mit
  Traceback). Jetzt fangen beide Apps `OperationalError`/`InterfaceError` (SQLAlchemy und
  psycopg, auch aus der Broker-Verbindung des SSE-Endpunkts) per Exception-Handler
  (`npt.web.common.register_db_unavailable`): 503, `Retry-After: 5`, `Cache-Control:
  no-store`, Security-Header (der Handler liegt innerhalb der äußeren Schichten, J2), eine
  Warnzeile `npt.db` (`datenbank_nicht_erreichbar`, Fehlerklasse, SQLSTATE, maskierter Pfad)
  ohne Traceback. Bearbeiter-API → JSON `{reason: "datenbank"}` (die UI wiederholt 503 wie
  bisher automatisch), Bearbeiter-Seiten → Hinweisseite `public/unavailable.html`, Admin →
  Klartext. Auch die Anfrage-Seite für Nicht-Admins (Exception-Handler mit eigener Session)
  fängt den Fall selbst ab. Andere DB-Fehler (Integrität, Programmierfehler) bleiben 500.
  Die Admin-App beantwortet zusätzlich einen erschöpften Pool mit 503/2 s (vorher 500).
  Der Jobs-Prozess (Backup-Tick, Mail-Sender) protokolliert eine nicht erreichbare Datenbank
  ebenfalls als Warnung ohne Traceback und versucht es im nächsten Takt erneut; echte Fehler
  behalten den Traceback. Die Fehlerklassen stehen einmal in `npt.db.session.
  DB_UNAVAILABLE_ERRORS`. Tests: `tests/test_db_unavailable.py`; auf dem Dev-Stack
  nachgestellt mit `docker compose stop db`: Liste, Übersicht, `/api/systems`, PATCH,
  `/api/events`, Admin → 503 mit `Retry-After: 5` und Security-Headern, kein „Exception in
  ASGI application“, nach `start db` ohne Neustart wieder 200.
- **PostgreSQL-Log** (QA 9a01489c c): `log_error_verbosity=terse` – keine DETAIL-Zeilen mit
  Schlüsselwerten (z. B. E-Mail-Adresse bei einer Unique-Verletzung). `log_min_error_statement`
  bleibt bewusst auf dem Default `error`: psycopg 3 bindet Parameter serverseitig, das
  geloggte Statement enthält nur `$1…` (geprüft auf dem Dev-Stack mit einem fehlschlagenden
  Token-Lookup und einer Unique-Verletzung über die App-Engine: Wert in keiner Zeile, keine
  DETAIL-Zeile); die Statement-Zeile ist
  für die Fehlersuche (welche Abfrage lief in einen Lock-Timeout?) wertvoll. `panic` würde
  sie ohne Sicherheitsgewinn verwerfen.
- **`DATABASE_URL`** (Docs-Befund): wird von `docker-compose.yml` bewusst **nicht** an die
  App durchgereicht. Im Compose-Betrieb gilt immer der `db`-Dienst mit `POSTGRES_*`;
  `DATABASE_URL` ist nur für den Betrieb außerhalb dieser Compose-Datei (externe DB, eigener
  Orchestrator) und hat dann Vorrang vor `POSTGRES_*`. Konservativ: ein Durchreichen würde
  den gekapselten `db_net`-Aufbau aushebeln und eine zweite, leicht übersehene Quelle für
  das DB-Passwort schaffen.
- **Hilfe-Seite** (QA ea7c23a1): `<title>` nannte den Projektnamen doppelt (Handbuch-Titel
  enthält ihn schon) und escapte ihn doppelt (`&amp;lt;`). Titel jetzt „<Handbuch-Titel> –
  Notfall-Patch-Tracker“; `handbook.render` liefert den Titel als Klartext, escaped wird
  einmal im Template.
- **Retry-After-Werte** in D4 beziffert, **Rate-Limit-Zählung** in D8 an den Code angepasst.
- **E2E:** `inline_smoke` prüft den optionalen Namensdialog beim ersten Bearbeiten (ohne
  vorbelegtes `localStorage`): Dialog erscheint, „Übernehmen“ bzw. „Überspringen“, kein
  zweiter Dialog, der Name (bzw. leer) steht im Änderungsprotokoll (Admin über das Gateway).
- **ARCHITECTURE.md** auf den Ist-Stand fortgeschrieben (Abweichungsliste am Ende).
- **E2E `upload_smoke`** robuster: restauriert das älteste Backup „vor Import“ statt der
  letzten Listenzeile – der Backup-Scheduler kann nach der ersten Bearbeitung des frischen
  Projekts schon ein zeitgesteuertes Backup angelegt haben (H2; trat einmal auf).

### J14 HTTPS für den Bearbeiter-Bereich per Overlay (2026-10-27)
- **Anlass:** Betreiber-Wunsch, den Bearbeiter-Bereich im LAN per HTTPS zu erreichen; der
  Projekt-Token steht in der URL und darf nicht im Klartext über das Netz gehen.
- **Entscheidung:** optionales Overlay `docker-compose.tls.yml` mit nginx `public-tls`
  (gleiches gepinntes Basisabbild wie das admin-gateway, Konfiguration als Read-only-Mount).
  Seit K13 ein eigenes schlankes Abbild `notfall-patch-tracker-tls-proxy`
  (`tls-proxy/Dockerfile`: nur die ungenutzten Module und curl entfernt, Entrypoint und
  Mounts unverändert). Die portable Basis bleibt unverändert (Proxy davor, Abschnitt 5 der
  INSTALLATION); das Overlay ist die Variante ohne vorhandenen Proxy.
- **Netz:** eigenes internes Netz `tls_net` mit festen Adressen (App .18, Proxy .19);
  die App vertraut `X-Forwarded-For` nur von .19 (`TRUSTED_PROXY_IPS`). Der Proxy
  **überschreibt** `X-Forwarded-For` mit `$remote_addr` statt anzuhängen, damit kein
  Client eine Absender-IP für das Rate-Limit vortäuschen kann.
- **Logs:** Access-Log als JSON mit `$uri` (ohne Query, ohne Header) und maskiertem
  Projekt-Token; `error_log` nur `crit`, weil nginx bei Upstream-Fehlern die Request-Zeile
  samt Token protokolliert (Auftrag 8.2 geht vor Diagnosekomfort).
- **Kein HSTS:** HSTS gilt für den Hostnamen über alle Ports und würde andere
  Klartext-Dienste auf demselben Host brechen. Stattdessen sprechen alle Proxy-Ports TLS
  und beantworten Klartext-Anfragen per `error_page 497` mit 301 auf `https://`. Anlass:
  Firefox rief `https://` auf dem zunächst reinen Klartext-Umleitungsport auf
  (`SSL_ERROR_RX_RECORD_TOO_LONG`); so funktionieren `http://` und `https://` auf
  demselben Port, alte `http://`-Links ebenso. Health-Check auf eigenem Loopback-Port.
- **SSE:** eigene Location mit `proxy_buffering off`, `gzip off`, 1 h Timeouts; HTTP/2 zum
  Browser hebt nebenbei die 6-Verbindungen-Grenze je Host für mehrere Tabs auf.
- **Geprüft** (npt-dev auf dem Testhost, Zertifikat einer internen CA): Kette gültig, HTTP/2, 301 vom
  Klartext-Port, PATCH mit neuer Origin 200/422, alte `http://`-Origin 403, SSE-Heartbeat
  und Live-Ereignis durch den Proxy, App sieht die echte Client-IP, Token in keinem Log.


### J15 Lizenz, SBOM und Veröffentlichungsfähigkeit (2026-11-02)
Anlass: Das Repository soll öffentlich werden; gewünscht sind eine Open-Source-Lizenz und
eine SBOM. Veröffentlicht wird mit frischer Git-Historie (Entscheidung des Betreibers).

- **Lizenz MIT** (`LICENSE`, Copyright 2026 Johannes Schmidt). `pyproject.toml` nutzt den
  SPDX-Ausdruck nach PEP 639 (`license = "MIT"`, `license-files = ["LICENSE",
  "THIRD_PARTY_NOTICES.md"]`); der gepinnte Build-Backend hatchling 1.32.4 unterstützt das
  (ab 1.27). Geprüft im gebauten Abbild: `Metadata-Version: 2.5`, `License-Expression: MIT`,
  beide Dateien unter `.dist-info/licenses/`. Damit sie beim Paketbau vorhanden sind, kopiert
  die Builder-Stufe des `Dockerfile` zusätzlich `LICENSE` und `THIRD_PARTY_NOTICES.md` –
  sonst unverändertes Build-Verhalten.
- **Lizenzverträglichkeit automatisiert** (`scripts/check-licenses.sh`): installiert
  `requirements.lock` wie das Dockerfile (`--require-hashes --no-deps`, gleiches
  Python-Basisabbild) in ein Wegwerf-venv und liest es mit pip-licenses 5.5.5 aus einem
  zweiten venv aus (`--python`), damit das Werkzeug nicht selbst im Befund steht. Befund
  (Exitcode 1) bei GPL/AGPL oder fehlender Lizenzangabe; LGPL wird ausgewiesen, ist aber
  erlaubt. pip-licenses 5.0.0 kennt `License-Expression` (Metadaten 2.4) noch nicht und
  meldete 24 Pakete als UNKNOWN – deshalb 5.5.5. Ergebnis für 39 Laufzeitpakete: nur
  permissive Lizenzen (MIT, BSD-3-Clause, Apache-2.0, PSF-2.0, MIT-0) plus **psycopg /
  psycopg-binary unter LGPL-3.0-only**. LGPL ist mit MIT vereinbar, solange psycopg als
  eigenständiges, unverändertes Paket importiert wird (so ist es); Pflichten bei Weitergabe
  des Abbilds und die mit `psycopg-binary` gebündelten nativen Bibliotheken (libpq,
  OpenSSL, Kerberos, OpenLDAP, Cyrus SASL; libxcrypt/keyutils LGPL-2.1+) stehen in
  `THIRD_PARTY_NOTICES.md`. Die Basisabbilder enthalten GPL-Programme (bash, coreutils,
  BusyBox …) als bloße Zusammenstellung – betrifft die Lizenz des Projekts nicht, nur die
  Weitergabe von Abbildern; geprüft wird bewusst nur die Python-Laufzeit.
- **SBOM: CycloneDX 1.6 JSON**, drei Dateien (Quellbaum, App-Abbild, Gateway-Abbild),
  erzeugt mit `scripts/sbom.sh` (Syft v1.56.0, Schema-Prüfung mit cyclonedx-cli 0.33.1,
  optional grype v0.121.1), alle als Container mit gepinnter Version; Abbilder per
  `docker save` statt Docker-Socket. CycloneDX statt SPDX, weil grype, cosign
  (`--type cyclonedx`) und Dependency-Track es direkt verarbeiten und 1.6 Fundstellen
  (`evidence.occurrences`) für vendorte Dateien abbildet.
- **Vendorte JS-Bibliotheken** erkennt Syft nicht (minifiziert, ohne `package.json`). Sie
  stehen in `scripts/sbom-components.json` und werden von `scripts/sbom_finalize.py`
  ergänzt; das Skript prüft Version und SHA-256 gegen die `VERSION`-Datei und bricht bei
  Abweichung ab (eine Quelle bleibt das Vendor-Verzeichnis, die SBOM kann nicht still
  veralten). Die in Quill gebündelten Pakete haben im Build keine Versionskennung; als
  Version gilt die neueste zum deklarierten Bereich passende am Veröffentlichungstag von
  Quill 2.0.3 (30.11.2024, npm-Zeitstempel: parchment 3.0.0, quill-delta 5.1.0,
  eventemitter3 5.0.1, lodash-es 4.17.21), gekennzeichnet als `npt:version-basis =
  abgeleitet`. Den Bereich (`^4.17.21`) als Version einzutragen wurde verworfen: grype
  wertete ihn falsch aus und meldete bereits behobene Lücken.
- **Python-Lock im Quellbaum:** Syft erkennt `requirements.lock` am Namen nicht; gescannt
  wird eine Kopie als `requirements.txt`, die Fundstelle wird auf `/requirements.lock`
  zurückgesetzt. Lizenzen übernimmt die Quellbaum-SBOM aus der App-Abbild-SBOM; zwei
  Pakete mit reiner Klassifikator-Angabe (jinja2, markdown-it-py) sowie CPython erhalten
  ihre Lizenz aus `license_overrides`. Dev-Abhängigkeiten und Lasttest-Werkzeuge gehören
  nicht in die SBOM (nicht ausgeliefert).
- **Reproduzierbar:** Zeitstempel aus `SOURCE_DATE_EPOCH` (Default Commit-Zeit), stabile
  Sortierung, `serialNumber` = UUIDv5 über den Inhalt. Geprüft: zwei Läufe gegen dieselben
  Abbilder ergeben byteidentische Dateien (sha256).
- **SBOM nicht committet** (`sbom/` in `.gitignore`): Sie beschreibt ein konkretes Release
  bzw. Abbild und gehört als Release-Artefakt dorthin (`docs/SBOM.md`); im Baum wäre sie
  nach jedem Lock- oder Basisabbild-Update veraltet.
- **OCI-Labels** in beiden Dockerfiles am Ende der Laufzeitstufe (`licenses=MIT`,
  `version`, `revision`/`source` per Build-Arg `NPT_REVISION`/`NPT_SOURCE_URL`, in
  `docker-compose.yml` aus der Umgebung durchgereicht, Default leer). Am Ende, damit ein
  neuer Commit-Hash nur die Metadaten-Schicht ändert und der Cache erhalten bleibt.
- **Bezüge auf die Betreiber-Infrastruktur neutralisiert** (Hostnamen, Adressen, interne
  Secret-Store-/CA-Bezeichnungen, Werkzeugnamen) in `.env.example`, `docker-compose.yml`,
  `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/LOADTEST.md`, `docs/INSTALLATION.md`
  und `scripts/gen-test-pki.sh` („Testhost“, „eigener Secret-Store“, „Betriebs-Overlay“,
  Beispiel-Adressen nach RFC 2606/5737). Messwerte und Begründungen bleiben unverändert.
  Der Originalauftrag ist nicht Teil des öffentlichen Repositorys, und das öffentliche
  Repository startet mit einer frischen Git-Historie (Entscheidung des Betreibers).
- **Öffentliche Begleitdateien:** `SECURITY.md` (Meldeweg GitHub Private Vulnerability
  Reporting, keine Mailadresse), `CONTRIBUTING.md`, README mit Funktionsübersicht,
  Schnellstart, Sicherheit, Lizenz, SBOM und Platzhalter „Kubernetes“ (künftig
  `docs/KUBERNETES.md`).


## K. Kubernetes-Betrieb per Helm-Chart (NPT-12, 2026-11-02)

Umsetzung der ADR NPT-12 (architect 54aaf020, Review ee62741e). Chart:
`deploy/helm/notfall-patch-tracker`, Anleitung: `docs/KUBERNETES.md`, Test:
`tests/k8s/`. Docker Compose bleibt unverändert der Standardweg (alle
`docker-compose*.yml` byte-gleich bis auf das Basisabbild in `docker-compose.tls.yml`).

### K1 Rollen-Schnitt
Zwei Deployments: `public` (APP_ROLE=public, Default 3 × WEB_WORKERS=2, RollingUpdate
maxSurge 0/maxUnavailable 1, PDB, topologySpread ScheduleAnyway, optional HPA) und
`backend` (genau 1 Replika, Recreate; Container `app` mit APP_ROLE=admin,jobs plus
Gateway-Sidecar und Backup-PVC). Jobs bekommen kein eigenes Deployment (RWX nötig,
mehrere RWO-Volumes = Split-Brain der Backup-Dateien). `APP_ROLE` ist dafür eine
Komma-Liste aus public/admin/jobs oder exklusiv `all` (C1, `Settings.roles`).

### K2 Migration
initContainer `migrate` (gleiches Abbild, `NPT_MIGRATE_ONLY=1`) in jedem App-Pod,
Hauptcontainer mit `NPT_SKIP_MIGRATE=1` (C2; Grenzen- und Secret-Prüfung laufen trotzdem).
Der Lock ist jetzt eine `pg_try_advisory_lock`-Schleife mit Zeitbudget
`MIGRATION_LOCK_WAIT_S=300` (limits.py): `pg_advisory_lock` lief in den `lock_timeout` der
Verbindung (2 s) und schickte den zweiten Pod in einen Crash-Loop. Der Admin-Bootstrap
läuft im selben Lock. `NPT_SCHEMA_NEWER=fail|warn` (C3): kennt das Abbild die Revision der
Datenbank nicht, bricht `fail` ab (Default, Compose), `warn` startet ohne Migration **und
ohne Bootstrap** (das Chart setzt `warn` für `helm rollback`). Regel ab jetzt: Migrationen
expand/contract (N-1-kompatibel).

### K3 Admin-Vertrauen
Gateway als Sidecar; App-Admin-Port nur 127.0.0.1:8001, `ADMIN_GATEWAY_CIDR=127.0.0.1/32`,
`ADMIN_UPSTREAM=127.0.0.1:8001`; `admin_auth.py` unverändert. Keine weiteren Container im
backend-Pod, Mesh-Injektion aus, `shareProcessNamespace: false`. Service `admin`:
ClusterIP/LoadBalancer (externalTrafficPolicy Local)/NodePort (Test), optional TLSRoute
(Passthrough). Gateway läuft als UID 101 ohne root und ohne Capabilities (gleiches Abbild;
emptyDirs für conf.d, /run/nginx, Cache, /tmp); Compose behält root-Master + cap_add. Die
Reload-Schleife läuft jetzt immer und vergleicht den **Inhalt** (sha256) von Zertifikat,
Schlüssel, CA-Bundle und optional CRL (C7) – Kubernetes tauscht Secret-Inhalte per
Symlink-Wechsel, nur die CRL-mtime zu prüfen reichte nicht.

### K4 Bearbeiter-Ingress
Service public ClusterIP :8000, optional Ingress **oder** HTTPRoute (beide zugleich →
Abbruch). `public.trustedProxyCIDRs` → `TRUSTED_PROXY_IPS`, Pflicht mit Ingress/HTTPRoute.
NetworkPolicy lässt 8000 nur aus dem Controller-Namespace zu. Controller-Beispiele mit SSE-
und Log-Einstellungen in `docs/KUBERNETES.md` §5.

### K5 Live, Kapazität, Shutdown
LISTEN/NOTIFY ist datenbankweit → Pod-übergreifend ohne Codeänderung (gemessen 181 ms).
DB-Budget `(P_max + 1) × W × 18 + ADMIN_WORKERS × 16 + 6 + 2 + budgetReserve ≤
database.maxConnections` wird im Template geprüft (Default 194 ≤ 200, Nachtrag unten). preStop 5 s,
terminationGracePeriod 40 s. Probes public: startup `/readyz`, readiness/liveness
`/healthz`; backend: `python -m npt.probe` (C5: TCP 127.0.0.1:8001 + Alter der
Heartbeat-Datei `JOBS_HEARTBEAT_FILE` ≤ 120 s), Gateway `wget …/gw-health`.

**Nachtrag (QA-Befund):** Ursprünglich rechnete das Budget mit `P_max + 0`, weil maxSurge 0
ist. Ein auslaufender Pod hält seine Verbindungen aber bis zum Ende der Grace-Zeit (bis
~45 s), während sein Ersatz startet – beim Rollout sind also P_max + 1 Pods verbunden.
Ohne Gegenmaßnahme könnten sich bei schnell bereiten Pods sogar mehrere auslaufende Pods
überlagern (der Controller beendet den nächsten alten Pod, sobald der neue bereit ist).
Deshalb: `minReadySeconds` = `terminationGracePeriodSeconds` + 5 s an der public-Deployment
und Budget mit `max(1, maxSurge + maxUnavailable)` = 1 Zusatz-Pod (Strategiewerte und
Budget aus denselben Helfern in `_helpers.tpl`). Preis: Rollout ≈ 45 s länger je Pod.
Mit `+1` hätten die alten Defaults 204 > 200 ergeben; `budgetReserve` sinkt deshalb von 20
auf 10 (psql/pg_dump/Monitoring; die Reserve `superuser_reserved_connections` der
Datenbank kommt hinzu) – Default jetzt `(3 + 1) × 2 × 18 + 32 + 8 + 10 = 194 ≤ 200`.
Replikas, Worker und `maxConnections` bleiben, damit Kapazität (1.800 SSE-Plätze) und
das CloudNativePG-Beispiel unverändert gelten. Knoten-Drains beachtet `minReadySeconds`
nicht (nur das PDB); kurzzeitig mehrere auslaufende Pods deckt die Reserve, dokumentiert
in `docs/KUBERNETES.md` §8.

### K6 Datenbank
Extern, kein Subchart; `examples/cnpg-cluster.yaml`. `DATABASE_URL` mit `postgres://` bzw.
`postgresql://` (z. B. Operator-Secret `uri`) wird auf `postgresql+psycopg://` umgestellt;
`POSTGRES_SSLMODE/SSLROOTCERT/SSLCERT/SSLKEY` werden als libpq-Query-Parameter angehängt
(C4). Betreiberpflichten (Log-Parameter, max_connections, kein Superuser, PITR) in
`docs/KUBERNETES.md` §7.

### K7 Backups
PVC RWO (Default 5Gi, `existingClaim`, `helm.sh/resource-policy: keep`), nur im
backend-Pod; `fsGroup` 10001, `fsGroupChangePolicy: OnRootMismatch`.

### K8 Secrets und Sicherheit
Je Schlüssel ein `secretKeyRef` (CSRF_SECRET, POSTGRES_PASSWORD bzw. DATABASE_URL,
SMTP_PASSWORD und MASTER_API_TOKEN optional); `existingSecret` empfohlen, `secrets.create`
nur Dev/Test. PodSecurity `restricted` für alle Pods, `automountServiceAccountToken:
false`. NetworkPolicies: Default-Deny, Freigaben für Controller → 8000, adminSourceCIDRs →
8443, Egress DNS/DB (+ SMTP, Stammdaten-API beim backend). Ressourcen ohne CPU-Limits.
`health_probe`-Zeilen beendeter Pods werden nach 24 h gelöscht (C6, Job
`health_probe_aufraeumen`, stündlich).

### K9 Abbilder
Registry/Repository/Tag/Digest per Values, nie `latest` (Template-Abbruch und Schema);
`deploy/helm/deploy.sh` gleicht nach dem Rollout je Pod das laufende Abbild ab.
Basisabbilder per Digest (C8): `nginx:1.31.8-alpine@sha256:7858…0606` für Gateway und
`public-tls` (`tls-proxy/Dockerfile`), `python:3.12.16-slim-bookworm@sha256:e697…d337` für die App
(seit 0.2.1 `python:3.12.16-slim-trixie@sha256:b18b…1ab30`, K14).

### K10 Sicherheitsbefund SBOM-Scan: Basisabbilder
grype (SBOM-Abgleich `scripts/sbom.sh --scan`) meldete für das Gateway-Abbild (nginx
1.29.8-alpine) 42 Critical / 135 High / 74 Medium / 13 Low / 5 Unknown, darunter eine
KEV-gelistete Lücke in `brotli-libs`; das App-Abbild (Python 3.12.15) 4 Critical / 59 High.

- **Gateway:** nginx 1.31.8 (aktuelle Mainline, Alpine 3.24) per Digest. Das offizielle
  Abbild enthält auch dann noch 15 Critical / 34 High (u. a. `brotli-libs` mit KEV, `curl`,
  `libexpat`) – alle aus Paketen, die das Gateway nicht nutzt (Module acme, geoip,
  image-filter, njs, xslt und curl). Die Gateway-Stufe entfernt sie per
  `apk del --no-network` (offline, reproduzierbar, kein `apk upgrade`). Ergebnis:
  **0 Critical / 0 High / 3 Medium** (BusyBox CVE-2025-60876, kein Fix verfügbar).
  Die stabile Linie 1.30.6 hatte dieselben Befunde.
- **App:** Python 3.12.16 per Digest: **3 Critical / 57 High / 106 Medium / 15 Low / 87
  Negligible**; die 3 Critical sind Debian-12-Pakete mit „won't fix“ (glibc CVE-2026-5450,
  SQLite CVE-2025-7458), behebbar nur per Wechsel auf Debian 13 (trixie) – bewusst nicht
  in diesem Schritt (umgesetzt in K14). Verbleibende behebbare Python-Befunde sind nur in 3.13+ behoben.
- **Quellbaum:** unverändert 1 High / 2 Medium / 1 Low (lodash-es in Quill, Quill selbst).
  Bewertung: Quill 2.0.3 bündelt aus lodash-es nur `cloneDeep`, `isEqual` und `merge`
  (die Bundle-Datei enthält weder `_.template` noch `_.unset`/`_.omit`; geprüft per Suche
  nach den Kennungen `templateSettings`, `sourceURL`, `baseUnset`). GHSA-r5fr-rjxr-66jc
  (`_.template`, Code-Injection über `imports`-Schlüssel) sowie GHSA-xxjr-mmjv-4gpg und
  GHSA-f23m-r3pf-42rh (Prototype-Pollution in `_.unset`/`_.omit`) sind damit im Frontend
  nicht erreichbar. GHSA-v3m3-f69x-jf25 (Quill, XSS im HTML-Export `getSemanticHTML`):
  nur im Admin-Mail-Editor genutzt, das Ergebnis landet in einem Formularfeld (nicht im
  DOM) und wird serverseitig mit nh3 bereinigt; CSP ohne Inline-Skripte. Kein
  Quill-Upgrade. (Seit K14: Quill mit lodash-es 4.18.1 neu gebaut, Bewertungen als VEX.)
- **Restrisiko `public-tls`:** Das optionale TLS-Overlay (J14) nutzte das offizielle
  nginx-Abbild unverändert (nur Konfiguration gemountet) und behielt die 15 Critical /
  34 High der ungenutzten Pakete. Behoben in K13 (eigenes Abbild, 0 Critical / 0 High).

### K11 Abweichungen und Ergänzungen gegenüber der Spezifikation
- **Zusätzliche Werte:** `backend.terminationGracePeriodSeconds`,
  `backend.gateway.workerProcesses` (neue Gateway-Variable `GATEWAY_WORKER_PROCESSES`,
  Default `auto` wie bisher; `nginx.conf` bindet dafür eine gerenderte Datei ein – ohne
  CPU-Limit hätte `auto` auf großen Knoten so viele Worker gestartet, dass das 128-Mi-Limit
  reißt), `networkPolicy.dns` (Selektor des Cluster-DNS), `networkPolicy.adminIngressFrom`
  (z. B. Gateway-API-Controller bei TLSRoute), `networkPolicy.masterApi`,
  `admin.tlsRoute.apiVersion` (TLSRoute ist noch Experimental), `tests.enabled`.
- **ingress-nginx-Defaults:** `ingress.sseTimeoutSeconds` wirkt über Annotationen, die das
  Chart nur bei `className: nginx` setzt (Timeouts, Body 1m, Access-Log aus); für andere
  Controller gibt es kein neutrales Mittel, dort gelten die Beispiele in KUBERNETES.md.
- **HTTPRoute:** eine Regel (`PathPrefix /`) mit `timeouts.request = sseTimeout` statt einer
  eigenen SSE-Regel – Regex-Pfade sind in der Gateway API implementierungsabhängig.
- **`NPT_SCHEMA_NEWER=warn` überspringt auch den Bootstrap** (Schema unbekannt; Admins
  existieren bei einem Rollback ohnehin).
- **Heartbeat** wird vor dem ersten und nach jedem Job berührt (nicht nur je Tick), damit
  ein langer Job die Liveness nicht unnötig früh reißt; lange Jobs melden zusätzlich
  Fortschritt (K13).
- **POSTGRES_SSL\*** überschreiben gleichnamige Parameter einer `DATABASE_URL`
  (explizite Einstellung hat Vorrang).
- **Nebenbei behoben:** Die aus `POSTGRES_*` gebaute URL nutzte `quote_plus`; ein
  Leerzeichen im Passwort wurde zu `+` und nicht zurückgewandelt. Jetzt `URL.create`.
  `Settings` geben in Validierungsfehlern keine Eingabewerte mehr aus
  (`hide_input_in_errors`), damit eine fehlerhafte `DATABASE_URL` kein Passwort ins Log bringt.
- **Gateway-Abbild verschlankt** (K10) – über „Basisabbild per Digest“ hinaus.
- **helm test** nutzt das App-Abbild (Python-Standardbibliothek) statt eines zusätzlichen
  curl-Abbilds.
- **Test-Edge Traefik statt ingress-nginx:** ingress-nginx ist eingestellt; Traefik 3.7.16
  (Chart 42.2.0, per sha256 geprüft) prüft Ingress, SSE durch den Controller, XFF-Verhalten
  und das Access-Log ohne Pfad. Envoy-Gateway-Beispiele sind nicht auf kind getestet.
- **Digest-Abgleich in `deploy.sh`** ist auf kind nur ohne Digest (Gleichheit je Container
  über alle Pods) prüfbar: per `kind load` importierte Abbilder lassen sich mit
  `pullPolicy: Never` nicht per Digest-Referenz starten (geprüft: `ErrImageNeverPull`).

### K12 Testergebnis (kind, 2026-11-02)
Lauf `tests/k8s/kind-test.sh` auf einem frischen Cluster (kind v0.33.0, Kubernetes 1.37.0,
1 Control-Plane + 2 Worker, kindnet, API-Server nur 127.0.0.1, keine Host-Ports; helm
v4.3.1, kubectl v1.37.2, kubeconform v0.8.0), danach Cluster gelöscht. Alle Punkte bestanden:

| Punkt | Ergebnis |
|---|---|
| 1 Lint/Schema/Budget | `helm lint --strict` (Helm 4.3.1 und 3.22.1), `kubeconform -strict` 13 bzw. 15 Ressourcen gültig (inkl. HTTPRoute/TLSRoute); Abbruch bei Budget 276/240 > 200, `latest`, fehlender URL, fehlenden trustedProxyCIDRs, doppelter Client-CA |
| 2 PodSecurity | Namespace `enforce=restricted`: keine abgewiesenen Pods, server-seitiger Dry-Run ohne Warnung |
| 3 Erstmigration | 3 public + backend gleichzeitig: genau ein Pod migriert, 2 warten auf den Lock, 0 Neustarts, genau 1 Bootstrap-Admin, keine Unique-Verletzung; `helm install --wait` 12 s, `helm test` grün |
| 4 Bearbeiter | Seite 200, `api/systems` 60 Zeilen, PATCH 200, Konflikt 409 mit aktuellem Wert, ohne CSRF 403 – direkt und über Traefik |
| 5 SSE | Stream an Pod A, PATCH über Pod B: 187 ms (über Traefik 190 ms); Pod A gelöscht → Änderung aus der Trennung nach Neuverbindung mit `Last-Event-ID` geliefert (0,13 s) |
| 6 Admin mTLS | gültig 200; ohne Zertifikat, fremde CA mit gleichem DN, gesperrt → 403; CRL-Secret getauscht → wirkt nach 52 s, Serverzertifikat getauscht → ausgeliefert nach 58 s (Intervall 10 s), ohne Pod-/Container-Neustart |
| 7 Admin-Port | Pod-IP:8001 aus fremden Namespaces: Timeout (NetworkPolicy); im Pod: Pod-IP refused, nur 127.0.0.1 offen; `X-SSL-*` an public `/admin/` → 404 |
| 8 Backups | manuell, Download JSON/xlsx, Restore, zeitgesteuert; backend-Pod gelöscht → alle Dateien vorhanden, Download vom neuen Pod 200 |
| 9 XFF | aus fremdem Namespace blockiert (NetworkPolicy, Negativtest für kindnet); aus dem Controller-Namespace gilt XFF; über Traefik gefälschtes XFF wirkungslos |
| 10 Rollback | Upgrade mit Test-Migration 0004, `helm rollback`: alte Pods starten mit Warnung „Datenbank-Revision unbekannt“, Bearbeiter-Funktion ok |
| 11 Logs | 1.616 Logzeilen aller Pods (App, Gateway, DB, Traefik, kube-system): kein `/p/<20+ Zeichen>` |
| 12 Compose | pytest 533 bestanden, ruff/mypy sauber, JS 40/40, `run-e2e.sh` 5/5 Suiten, `mtls-integration-test.sh` 33/33 |

### K13 Nacharbeit QA-Befunde (2026-11-02)
- **DB-Budget mit auslaufendem Pod:** siehe Nachtrag in K5 (`P_max + 1`, `minReadySeconds`,
  `budgetReserve` 10).
- **Jobs-Heartbeat bei langen Läufen:** Bisher wurde die Heartbeat-Datei nur zwischen Jobs
  berührt; ein Backup-Lauf über viele Projekte (> 120 s, Probe-Grenze) hätte die
  Liveness-Probe ausgelöst und den backend-Pod mitten im Lauf neu gestartet. Jetzt meldet
  der Backup-Lauf vor jedem Projekt Fortschritt (`npt.jobs.beat()`, durchgereicht als
  `progress` an `backups.run_due`). Verworfen: ein Hintergrund-Thread, der tickt, solange
  der Prozess lebt – er hielte den Heartbeat auch bei einem hängenden Job (z. B. blockierte
  DB-Sperre, hängender Dateizugriff) frisch und machte die Probe wirkungslos. Grenze der
  Lösung: Ein einzelnes Projekt-Backup (JSON + xlsx eines Projekts) muss unter 120 s bleiben;
  der Mailversand läuft als eigener Thread und berührt den Heartbeat nicht.
- **Start mit ungültigen Einstellungen:** `npt-run` richtet das JSON-Logging zuerst
  vorläufig ein und lädt die Settings im `try`; ein Validierungsfehler ergibt eine
  JSON-Zeile „Start abgebrochen: ungueltige Einstellungen“ mit Feldname und Fehlerart je
  Fehler, ohne Werte, ohne Traceback, Exitcode 2. Bewusst ohne die Meldungstexte: eigene
  Validatoren nennen teils den Wert (`APP_ROLE`, Basis-URL, Netze). Ungültiges `LOG_LEVEL`
  ebenso. `python -m npt.probe` meldet einen nicht-numerischen `ADMIN_LISTEN_PORT` als
  „probe: ADMIN_LISTEN_PORT ist keine gueltige Portnummer“ (Exitcode 1) statt Traceback.
- **E2E mail_smoke:** Die Testmail wird bis zu 30 s in Mailpit abgefragt statt einmalig.
- **`public-tls` verschlankt:** eigenes Abbild aus `tls-proxy/Dockerfile` statt des
  offiziellen nginx-Abbilds. Das Gateway-Abbild wurde bewusst nicht wiederverwendet: es
  ersetzt den Standard-Entrypoint (eigene envsubst-Liste, Reload-Schleife, eigene Pfade),
  `public-tls` braucht aber genau das Verhalten des offiziellen Entrypoints (envsubst der
  Templates aus `/etc/nginx/templates`). Das neue Abbild entfernt deshalb nur dieselben
  ungenutzten Pakete wie das Gateway (`apk del --no-network` von curl und den Modulen
  acme/geoip/image-filter/njs/xslt) und lässt Entrypoint, Skripte unter
  `/docker-entrypoint.d` und envsubst unverändert. Mounts (`/etc/nginx/nginx.conf`,
  `/etc/nginx/templates/default.conf.template`, `/etc/npt-tls`), Umgebung, Ports,
  Healthcheck und Härtung im Overlay sind gleich geblieben; neu sind nur `build`/`image`.
  `deploy.sh` baut und prüft `public-tls` mit, wenn das Overlay aktiv ist;
  `scripts/sbom.sh` erzeugt eine vierte SBOM für das Abbild, falls es gebaut ist.
  Geprüft (Wegwerf-Stack, Ports nur 127.0.0.1, eigene Test-PKI und selbstsigniertes
  Zertifikat): `nginx -T` mit denselben Mounts identisch zum offiziellen Abbild; HTTPS 200
  über HTTP/2, Klartext an den TLS-Port → 301 auf `https://`, Zusatzport (Klartext und TLS)
  → 301 auf den Haupt-Port, Neuanlage per POST 201 und fremde Origin 403, SSE-Ereignis und
  Heartbeat durch den Proxy, Token in keinem Log (Access-Log maskiert `/p/***`),
  gefälschtes `X-Forwarded-For` wirkungslos, Healthcheck „healthy“, Zertifikatstausch mit
  `nginx -s reload` – dieselben 13 Prüfungen mit dem offiziellen Abbild ebenfalls grün.
  grype v0.121.1 (SBOM-Weg wie `scripts/sbom.sh`): offizielles Abbild 15 Critical / 34 High
  / 3 Medium (1 KEV, `brotli-libs`), neues Abbild **0 Critical / 0 High / 3 Medium**
  (BusyBox CVE-2025-60876, kein Fix verfügbar – wie beim Gateway).

### K14 Schwachstellen-Befunde des SBOM-Scans beseitigt (0.2.1, 2026-11-02)
Anlass: Auftrag des Betreibers, die Befunde aus `scripts/sbom.sh --scan` (Stand 0.2.0, K10)
möglichst zu beheben statt nur zu dokumentieren und den Rest maschinenlesbar zu bewerten.
Alle Zahlen: grype v0.121.1 über die SBOMs von `scripts/sbom.sh`, Datenbank vom 2026-11-02,
Reihenfolge Critical / High / Medium / Low (Negligible getrennt).

| SBOM | 0.2.0 | 0.2.1 ohne VEX | 0.2.1 mit VEX (Bericht) | per VEX bewertet |
|---|---|---|---|---|
| App-Abbild | 3 / 57 / 106 / 15, 87 Negl. | 0 / 14 / 35 / 8, 26 Negl. | 0 / 0 / 2 / 0, 26 Negl. | 55 |
| Gateway-Abbild | 0 / 0 / 3 / 0 | 0 / 0 / 3 / 0 | 0 / 0 / 0 / 0 | 3 |
| TLS-Proxy-Abbild | 0 / 0 / 3 / 0 | 0 / 0 / 3 / 0 | 0 / 0 / 0 / 0 | 3 |
| Quellbaum | 0 / 1 / 2 / 1 | 0 / 0 / 0 / 1 | 0 / 0 / 0 / 0 | 1 |

**App: Debian 13.** Basis `python:3.12.16-slim-trixie@sha256:b18b261e…1ab30` (neueste
3.12-Patchversion, Docker Hub 2026-11-02) für App, Test-Stufe, Lasttest-Abbild und die
Werkzeug-Container in `scripts/sbom.sh`/`scripts/check-licenses.sh` (`scripts/audit-deps.sh`
nutzt unverändert das uv-Abbild, dort spielt die Distribution keine Rolle). Der Wechsel allein
ergab 0 / 60 / 68 / 12 (47 Negl.): die Critical (glibc, SQLite) sind weg, die High-Zahl stieg
aber, weil util-linux 2.41 in neun Binärpaketen je vier neue „won't fix“-Befunde hat. Kein
verbleibender Befund hatte eine Fix-Version; `apt-get upgrade` hätte nur `tzdata` (2026e →
2026f) aktualisiert und bleibt deshalb weg (Bau reproduzierbar wie bisher).

**Paket-Entfernung in der Laufzeitstufe** (`Dockerfile`): Die Laufzeit führt nur CPython und
tini aus; gebraucht werden daneben tzdata (`zoneinfo`), ca-certificates (SMTP/HTTPS) und die
von CPython bzw. den Wheels gelinkten Bibliotheken. Entfernt wird, was weder ausgeführt noch
gelinkt wird: util-linux, mount, login, bsdutils samt libmount1/libblkid1/libsmartcols1/
liblastlog2-2/libuuid1, passwd/login.defs/PAM (nach dem Anlegen des Benutzers),
ncurses-bin/ncurses-base/libncursesw6, libsqlite3-0, libstdc++6 (nur von apt genutzt),
apt/libapt-pkg7.0/libudev1, adduser, debconf, perl-base, tar; seit der QA-Nacharbeit (unten)
außerdem die apt-/PAM-Reste sqv, libnettle8t64, libhogweed6t64, debian-archive-keyring,
libseccomp2, liblz4-1, libxxhash0, libsemanage2/-common, libsepol2, libaudit1/-common,
libcap-ng0, libbsd0 und `/etc/security`.
- `dpkg --purge --force-remove-essential --force-remove-protected --force-depends` statt
  `apt-get purge`: der apt-Resolver verweigert das Entfernen essentieller Pakete
  („generated breaks“). Zwei Aufrufe, weil dpkg die Reihenfolge innerhalb eines Aufrufs
  selbst wählt: die PAM-`postrm` brauchen noch debconf, dpkg selbst braucht tar.
- Folge: **kein Paketmanager mehr im Abbild** (die dpkg-Datenbank bleibt für SBOM und Scanner,
  dpkg ist ohne tar nicht nutzbar). Wer Pakete ergänzen will, setzt auf dem Basisabbild auf.
  Für die Fehlersuche bleiben dash, bash, coreutils, grep, sed, findutils.
- Ohne Bibliothek (bewusst): Python-Module `_sqlite3` (Anwendung nutzt PostgreSQL),
  `_curses`/`_curses_panel` und `_uuid` (`uuid` fällt auf die reine Python-Umsetzung zurück,
  geprüft: `uuid4()`/`uuid1()` funktionieren); `_tkinter` war im slim-Abbild schon ohne Tk.
- **Prüfung beim Bau:** `scripts/check-runtime-libs.sh` läuft per Bind-Mount (landet nicht im
  Abbild) und prüft mit `ldd` jede dynamisch gelinkte Datei unter `/usr/bin`, `/usr/sbin`,
  `/usr/lib`, `/usr/local` und danach `/opt/venv`; fehlt eine Bibliothek außerhalb der
  Ausnahmen, bricht der Bau ab (Gegenprobe: ohne Ausnahme für `_sqlite3` meldet es den
  Fehler). Zusätzlich eine Import-Probe (ssl, zlib, bz2, lzma, ctypes, hashlib, uuid,
  zoneinfo `Europe/Berlin`, App-Module).
- Benutzer-Shell `/bin/false` statt `/usr/sbin/nologin` (gehörte zum entfernten Paket login).
- Ergebnis: 0 / 14 / 35 / 8 (26 Negl.). Verworfen: libc-bin entfernen (ldconfig/ldd;
  `ctypes.util.find_library` nutzt `ldconfig -p`; hätte nur die doppelt gezählten
  glibc-Zeilen halbiert, libc6 bleibt), bash/libtinfo6 (Fehlersuche, readline),
  coreutils/libacl1/libattr1 (Grundwerkzeuge), ein distroless-Abbild (bringt Debians
  Python 3.13 statt der gepinnten 3.12-Linie).

**Verhaltensänderungen durch Debian 13** (für den Betrieb relevant):
- Das Python-Modul `ssl` linkt jetzt **OpenSSL 3.5.7 statt 3.0.22** (glibc 2.41 statt 2.36).
  Betroffen sind die TLS-Verbindungen zum SMTP-Server und zur optionalen Stammdaten-API:
  sehr alte Gegenstellen (z. B. Zertifikate mit schwachen Schlüsseln/Signaturen, nur
  Altprotokolle) können abgewiesen werden. Die Datenbank-Verbindung ist nicht betroffen
  (psycopg-binary bringt eigenes libssl mit). Mindest-TLS-Version unverändert (TLS 1.2).
- `import sqlite3` schlägt im Laufzeitabbild fehl; kein `apt-get`/`perl`/`tar` mehr
  (`docker exec … sh` funktioniert weiter).
- Python, tini (0.19.0) und alle Python-Pakete sind unverändert.

**Python-Pakete:** pip-audit 2.10.1 (`scripts/audit-deps.sh`) meldet für beide Locks keine
Befunde, grype ebenfalls keine PyPI-Befunde – `requirements.lock` bleibt unverändert. Die
Befunde zu CPython 3.12.16 selbst sind nur in 3.13+ behoben.

**Quill/lodash-es: Neubau.** In der npm-Registry gibt es kein Quill nach 2.0.3 (Stand
2026-11-02). `scripts/vendor-quill/build.sh` bündelt die ESM-Quellen des npm-Pakets quill
2.0.3 (dieselbe Registry-Integrity wie bisher) mit esbuild 0.28.2 in
`node:22.23.4-alpine` (per Digest) neu: `npm ci --ignore-scripts` aus
`scripts/vendor-quill/package-lock.json` (Integrity je Paket), Versionen exakt in
`package.json`, lodash-es per `overrides` auf **4.18.1**; die übrigen gebündelten Pakete in
den bisher abgeleiteten Versionen (parchment 3.0.0, quill-delta 5.1.0, eventemitter3 5.0.1)
plus die bisher in der SBOM fehlenden quill-delta-Abhängigkeiten fast-diff 1.3.0
(Apache-2.0), lodash.clonedeep 4.5.0, lodash.isequal 4.5.0. Ausgabe: IIFE mit
`window.Quill`, Ziel ES2020, minifiziert (205 statt 209 KB); `quill.snow.css` und `LICENSE`
byte-gleich übernommen; `quill.js.LICENSE.txt` enthält jetzt die Lizenztexte aller
gebündelten Pakete (vorher nur den Quill-Banner).
- Zwischen lodash-es 4.17.21 und 4.18.1 unterscheiden sich in den von Quill genutzten
  Funktionen (`cloneDeep`, `isEqual`, `merge` samt Hilfsmodulen) nur Kommentare; geändert
  wurden u. a. `template`, `_baseUnset`, `_baseOrderBy`, `compact`, `fromPairs`, `random`.
- Reproduzierbar: zwei Läufe byteidentisch (`CHECK=1` vergleicht mit dem Baum).
- Verhaltensvergleich Upstream-Bundle vs. Neubau in Chromium (Playwright, Wegwerf-Skript):
  drei Editor-Konfigurationen (Mail-Editor wie `mail.js`, voller snow-Editor, bubble), sechs
  HTML-Eingaben inkl. XSS-Proben → Delta, `getSemanticHTML`, DOM und Text; Formatieren,
  Einfügen, Undo/Redo, Tastatureingabe (Fett, Listen-Autoformat), Toolbar-HTML und
  `Quill.imports` – **identisch**, keine Konsolenfehler. Die E2E-Suite (`mail_smoke`) läuft
  mit dem Neubau.
- SBOM: gebündelte Pakete exakt (`npt:version-basis = lockfile`, SHA-512 aus dem Lockfile),
  Quill mit `pedigree.notes` und `npt:rebuilt-recipe`; `scripts/sbom_finalize.py` prüft die
  Versionen gegen das Lockfile.
- Preis: `quill.min.js` ist jetzt ein eigenes Build-Artefakt statt der unveränderten
  Upstream-Datei (Rezept und Lockfile im Repository). Verworfen: nur per VEX bewerten – der
  verwundbare Code fehlte zwar schon im Upstream-Bundle (K10), der Scanner hätte aber weiter
  eine verwundbare Version gesehen.

**BusyBox CVE-2025-60876 (Gateway, TLS-Proxy):** nginx 1.31.8-alpine ist die neueste
Version (`mainline-alpine`/`1-alpine` = gleicher Digest; `stable-alpine` 1.30.6 hat dieselbe
Alpine-Basis). Alpine 3.24 liefert busybox 1.37.0-r31, die Alpine-secdb (v3.24 und edge)
kennt keinen Fix; nur edge hat 1.38.0 – Pakete aus edge in ein stabiles Abbild zu mischen
wurde verworfen. Bewertung per VEX: die Lücke betrifft BusyBox `wget` mit Steuerzeichen im
Request-Target; `wget` läuft nur im Healthcheck bzw. in der Kubernetes-Probe mit fester URL
auf 127.0.0.1, keine Komponente gibt externe Eingaben an `wget`/`ssl_client` weiter.

**VEX** (`vex/notfall-patch-tracker.openvex.json`, 39 Aussagen):
- **OpenVEX 0.2.0 statt CycloneDX-VEX**, weil grype `--vex` OpenVEX liest (CycloneDX-VEX
  nicht als Eingabe). Produkte als purl **mit Version, ohne Qualifier** – so gleicht grype
  ab (geprüft; ohne Version würde die Aussage auch künftige Versionen abdecken, mit
  Qualifiern trifft sie nicht alle Binärpakete). Nach einem Paket-Update greift eine
  Aussage nicht mehr: der Befund erscheint wieder im Bericht und ist neu zu bewerten;
  `scripts/sbom.sh --scan` listet Aussagen ohne Treffer.
- Status `not_affected` nur mit belegter Begründung: importierte Symbole aller ELF-Dateien
  des App-Abbilds (`nm -D --undefined-only`, z. B. ruft niemand `strfmon`, `wordexp`,
  `ns_printrr*`, `crc32_combine*` auf; `iconv_open` nur bash/diffutils/printf, `tdelete` nur
  libtinfo), keine setuid/setgid-Dateien im Abbild (ld.so-Lücken mit AT_SECURE), Programm in
  einem nicht installierten Paket (nscd, infocmp, getfattr, bzip2recover, systemd-Dienste,
  libstdc++ entfernt), Versionsbereich (zlib CVE-2026-85091 erst ab 1.3.1.2), Suche im venv
  (imaplib, poplib, `Morsel.js_output` ungenutzt), von außen nicht steuerbare Eingaben
  (Umgebung, `resolv.conf`, fester Healthcheck-URL). Quill GHSA-v3m3-f69x-jf25:
  `inline_mitigations_already_exist` (Ergebnis nur im Formularfeld, nh3-Allowlist
  serverseitig, CSP).
- **Offen bleiben** (Status `affected`, im Bericht sichtbar): CPython CVE-2025-12781 und
  CVE-2026-3446 (base64-Dekodierung, in Abhängigkeiten erreichbar, Fix erst in 3.13+) und
  die 26 Negligible-Befunde. Der Wechsel auf Python 3.13 ist ein eigener Schritt.
- `scripts/sbom.sh --scan` schreibt je SBOM `*.grype.txt` (nur offene Befunde) und
  `*.grype.json` (bewertete unter `ignoredMatches`), fasst je Bericht zusammen;
  `GRYPE_FAIL_ON` wirkt nur auf offene Befunde. `NO_VEX=1` zeigt den Rohzustand.

**Geprüft** (Testhost, Wegwerf-Abbilder und -Stacks): `ruff`/`ruff format`/`mypy` sauber,
pytest 546 bestanden (1 übersprungen: Node-Vektoren laufen separat), JS 40/40,
`scripts/run-e2e.sh` 5/5 Suiten, `scripts/mtls-integration-test.sh` 33/33 (gegen einen
Wegwerf-Stack), `helm lint --strict` und `helm template | kubeconform -strict`,
`scripts/check-licenses.sh` ok, Lasttest-Abbild baut (locust 2.47.0), `scripts/sbom.sh
--scan` vorher/nachher (Tabelle oben).

**Nacharbeit QA-Befunde (0.2.1, 2026-11-02):**
- **Paketliste gegen stillen Rückfall gesichert** (66ba3c8e): `dpkg --purge` übergeht nicht
  installierte Pakete ohne Fehler; nach einem neuen Basis-Digest mit umbenanntem Paket
  (z. B. `libapt-pkg7.0`, `liblastlog2-2`) bliebe das neue Paket im Abbild, der Bau wäre
  grün. Jetzt läuft die Entfernung über `scripts/purge-runtime-packages.sh` (Bind-Mount,
  landet nicht im Abbild): (1) jedes Paket aus `NPT_PURGE_PACKAGES`/`NPT_PURGE_PACKAGES_LATE`
  muss laut `dpkg-query` installiert sein, (2) danach muss der Paketstand exakt
  `scripts/runtime-packages.txt` entsprechen (47 Pakete). Jede Abweichung bricht den Bau mit
  den betroffenen Paketnamen und einem Hinweis auf die drei Listen ab. Gegenproben:
  `libapt-pkg8.0` statt `libapt-pkg7.0` → „zu entfernende Pakete sind nicht installiert
  (umbenannt oder entfallen?): libapt-pkg8.0“; `libseccomp2` aus der Liste genommen → „im
  Abbild, aber nicht erwartet: libseccomp2“. Die Erwartungsliste fängt auch Pakete, die ein
  neues Basisabbild zusätzlich mitbringt; sie ist bei jedem Digest-Wechsel zu bewerten.
- **Weitere apt-/PAM-Reste entfernt** (42c0fb9d), geprüft per `dpkg-query` (Rückwärts-
  abhängigkeiten unter den verbleibenden Paketen) und `ldd` über alle ELF-Dateien:
  `sqv` (nur von apt aufgerufen) samt `libnettle8t64`/`libhogweed6t64` (nur von sqv
  gelinkt), `debian-archive-keyring` (nur für apt), `libseccomp2` (nur apt, von nichts
  gelinkt), `liblz4-1`/`libxxhash0` (nur libapt-pkg), `libsemanage2`/`libsemanage-common`/
  `libsepol2`/`libaudit1`/`libaudit-common`/`libcap-ng0` (nur von passwd bzw. PAM
  gebraucht, untereinander gelinkt), `libbsd0` (von nichts gelinkt). `/etc/security`
  enthielt nur die leere, keinem Paket gehörende `opasswd` (PAM-Rest) und entfällt.
  **Bleiben:** `libgmp10` (coreutils `expr`/`factor` linken es), `libdebconfclient0`
  (von `update-passwd` aus base-passwd gelinkt; base-passwd ist essentiell und liefert die
  Grundlage von `/etc/passwd`/`/etc/group`, Entfernen hätte ein Programm mit fehlender
  Bibliothek hinterlassen, das `check-runtime-libs.sh` zu Recht beanstandet),
  `libsystemd0`/`libcap2` (coreutils `who`/`users`/`pinky` bzw. `ls`), `libdb5.3t64`/
  `libgdbm6t64` (Python-Module `_dbm`/`_gdbm`). Keine der entfernten Pakete hatte einen
  grype-Befund; die Zahlen bleiben 0 / 14 / 35 / 8 (26 Negl.) ohne VEX und 0 / 0 / 2 / 0 mit
  VEX, alle 39 VEX-Aussagen haben weiter Treffer (keine gegenstandslos). Außerdem doppelte
  Verneinung in den VEX-Begründungen zu CVE-2026-5435/CVE-2026-6238 korrigiert.
- **Weiterleitungen beim Stammdaten-Abruf** (8614a039): `urlopen` folgte Weiterleitungen
  auch auf `http://` und zu fremden Hosts und reichte dabei den Bearer-Token weiter.
  `npt.masterdata.api.open_api` setzt den Token jetzt als nicht weitergeleiteten Header
  (`add_unredirected_header`) und folgt Weiterleitungen nur auf `https://` mit demselben
  Host und Port (`SameOriginRedirectHandler`, Token dann neu gesetzt); jede andere
  Weiterleitung bricht mit `MasterDataError` ab, bevor eine Anfrage an das neue Ziel geht
  (Ziel-URL nicht in der Meldung). Verworfen: Weiterleitungen ganz abschalten – ein
  Pfadwechsel auf demselben Server (z. B. API-Versionen) wäre ohne Sicherheitsgewinn
  gescheitert. VEX-Begründung zu CVE-2026-103995 entsprechend präzisiert.
- **Docker-HEALTHCHECK rollenabhängig:** Bisher prüfte das Abbild immer
  `:8000/healthz`; mit `APP_ROLE` ohne `public` (z. B. `admin,jobs`) blieb der Container
  „starting“/„unhealthy“. Jetzt `python -m npt.probe --docker`: mit `public` (auch `all`,
  Compose-Default) unverändert nur `GET 127.0.0.1:PUBLIC_LISTEN_PORT/healthz` = 200 (ohne
  Proxy-Umgebung), sonst die Prüfungen der Kubernetes-Probe (Admin-Port per TCP,
  Jobs-Heartbeat falls `JOBS_HEARTBEAT_FILE` gesetzt). `python -m npt.probe` ohne Option
  (Kubernetes) ist unverändert.
- **Geprüft** (Testhost): ruff/ruff format/mypy sauber, pytest 557 bestanden
  (1 übersprungen), `scripts/run-e2e.sh` 5/5 Suiten, Healthcheck im E2E-Stack mit
  `APP_ROLE=all`, `admin,jobs` und `public` jeweils „healthy“, `docker build --no-cache` der
  Laufzeitstufe grün inkl. der beiden Gegenproben oben, `scripts/sbom.sh --scan` mit und
  ohne VEX (Zahlen wie in der Tabelle, keine VEX-Aussage ohne Treffer).
