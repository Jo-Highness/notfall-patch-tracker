# Architektur – Notfall-Patch-Tracker

Verbindliche Vorgabe für die Entwicklung (Architektur-Review 02de6ec6, approved 2026-10-26),
fortgeschrieben auf den umgesetzten Stand (Nachbesserung nach QA-Gate, 2026-10-27).
Anforderungsquelle ist der ursprüngliche Auftrag des Betreibers (nicht Teil des öffentlichen Repositorys; „Auftrag n.m“ verweist auf dessen Abschnitte). Details und Begründungen stehen in den ADRs des Entwicklungsprozesses (Asset `notfall-patch-tracker`, nicht Teil des Repositorys)
und in `docs/DECISIONS.md` (Kennungen A1–K11 in Klammern). Wo der Ist-Stand bewusst vom
ursprünglichen Review abweicht, steht das hier im Text und zusammengefasst am Ende.

## Komponenten
| Komponente | Rolle | Landkarte |
|---|---|---|
| app | FastAPI, drei Prozessgruppen (public, admin, jobs) | 24d082d2 |
| db | PostgreSQL 16, nur internes Netz `db_net` | fd7f84ea |
| admin-gateway | nginx mit mTLS, einziger Weg zum Admin-Port | bf0b1864 |

Netze: `public_net` (Bearbeiter-Port), `admin_net` (internal, feste Subnetze, nur Gateway und App), `db_net` (internal). Das Gateway hängt zusätzlich in einem Netz mit veröffentlichtem Port (ein internal-Netz kann keine Ports veröffentlichen).

## 1. Frontend (NPT-01, b81c3ab0)
- Tabulator 6.x, mitgeliefert unter /static (kein CDN, kein Bundler).
- Alle Zeilen eines Projekts werden einmal als JSON geladen; Filtern, Sortieren und Suchen laufen im Browser; der Filterzustand steht in der URL.
- Die Filter-Grammatik ist einmal spezifiziert. Maßgeblich ist die Python-Implementierung (Export „gefilterte Sicht", Mail-Selektor „Benutzerdefiniert"). JS und Python laufen gegen gemeinsame JSON-Testvektoren (pytest und Playwright).
- Mail-Editor: Quill 2 (mitgeliefert); Sanitisierung serverseitig mit nh3.
- CSP `script-src 'self'`, kein Inline-Script. Kein Service Worker, kein Manifest mit standalone.

## 2. Prozessmodell (NPT-02, d89a1c5c)
- PID 1 ist tini, darunter der Supervisor `npt-run` (stdlib).
- Ablauf: Alembic-Migration und Admin-Bootstrap unter Advisory Lock (`pg_try_advisory_lock`-Schleife, bis 300 s Wartezeit; NPT-12 K2), dann Start der drei Prozesse:
  - `gunicorn npt.web.public:app` (WEB_WORKERS, Default 6, UvicornWorker, :8000; ursprünglich 4, J12)
  - `gunicorn npt.web.admin:app` (2 Worker, bindet nur an die admin_net-IP, :8001)
  - `python -m npt.jobs`
- Endet ein Kind, beendet der Supervisor alle und den Container (Exitcode ungleich 0).
- `APP_ROLE=all` oder eine Komma-Liste aus `public`, `admin`, `jobs` teilt die Prozesse auf mehrere Container auf (Kubernetes: `public` und `admin,jobs`, Abschnitt 12).
- Start-Schalter: `NPT_MIGRATE_ONLY=1` (nur Migration), `NPT_SKIP_MIGRATE=1` (ohne Migration), `NPT_SCHEMA_NEWER=fail|warn` (unbekannte, neuere DB-Revision: abbrechen bzw. ohne Migration starten).
- Die Public-Factory importiert keine Admin-Router. Ein Test prüft, dass die Routing-Tabelle kein `/admin*` enthält und der Bearbeiter-Port dafür 404 liefert.

## 3. Live-Updates (NPT-03, 2c18a904)
- Jede Schreibtransaktion beginnt mit `UPDATE projects SET change_seq=change_seq+1 … RETURNING`. Die geänderten Zeilen bekommen `row_seq`.
- `GET /p/<token>/api/systems?updated_since=<seq>`: Cursor ist die Sequenz, kein Zeitstempel. Liegt der Cursor unter `reset_seq`, antwortet der Endpunkt mit `full_reload`.
- NOTIFY auf Kanal `npt_changes` mit `{"p","s","k"}`, versendet wird erst beim Commit.
- Pro Worker eine LISTEN-Verbindung. Der Worker bündelt 150 ms, macht eine Delta-Abfrage pro Projekt und verteilt an alle lokalen Clients.
- Pro Client eine begrenzte Queue (64 Ereignisse); läuft sie über, wird die Verbindung geschlossen und der Client holt per Delta nach.
- SSE: `id` ist change_seq, Heartbeat alle 15 s, `X-Accel-Buffering: no`, keine GZip-Kompression für den Stream. Ohne SSE: Polling alle 5 s.
- Pivot: abonniert denselben Stream und holt das serverseitige GROUP-BY-Ergebnis gebündelt neu (1 s).

## 4. Schreibpfad und Konflikte (NPT-04, ea8aa426)
- `PATCH /p/<token>/api/systems/<id>` mit `{field, value, expected, client_name?}`.
  - Pydantic mit `extra=forbid`; `field` ist ein Literal der vier *-Felder.
- Eine Transaktion, `lock_timeout 2s`. Sperrreihenfolge in allen Pfaden: zuerst Projekt, dann Systemzeile.
- Vergleich mit `IS DISTINCT FROM expected`:
  - gleich: das Feld wird geschrieben, `version+1`, Eintrag in change_log, NOTIFY
  - ungleich: 409 mit dem aktuellen Wert
- Überschreiben bedeutet neu senden mit dem gezeigten aktuellen Wert; es gibt kein force-Flag.
- Lock-Timeout ergibt 503 `gesperrt` mit `Retry-After: 1`. Der Transaktionsabschluss steht immer im finally.
- Eine einzige Domain-Validierung für UI, Admin-Upload und Restore. Je nach Herkunft unterscheidet sich nur die Folge, nicht ob geprüft wird.
- Admin-Upload:
  - Die geparste Datei liegt in `staged_uploads`; die Vorschau trägt einen Fingerprint.
  - Bei Bestätigung wird der Diff unter Sperre neu berechnet; weicht er ab, kommt eine neue Vorschau.
  - Vorher Backup `vor_import`.
- Neue Systeme: Nummer aus `nextval('new_system_seq')` (SQL), Formatierung in Python als
  `NEW-{n:04d}` – mindestens vier Stellen, ab 10000 ohne Kürzung (`NEW-10000`). Die
  ursprüngliche Formel `'NEW-'||lpad(nextval(…)::text,4,'0')` ist falsch: `lpad` kürzt auf
  vier Zeichen (`NEW-1000` statt `NEW-10000`) und erzeugte Duplikate (A5, D5).

## 5. Hintergrundjobs (NPT-05, 3228345a)
- **Backups:** Tick alle 15 s. Pro Projekt `pg_try_advisory_xact_lock`. Gesichert wird nur bei einer **Datenänderung** seit dem letzten Backup: `GREATEST(max(row_seq), reset_seq) > last_backup_seq` (H2). `change_seq` allein reicht nicht, weil Status- und Token-Wechsel ihn ebenfalls erhöhen, ohne Daten zu ändern.
  - Format: JSON (ohne Token) und xlsx; Schreiben als tmp-Datei, fsync, rename. Danach Aufbewahrung n.
- **Restore:** eine Transaktion; vorher Backup `vor_restore`, danach `reset_seq` setzen und reload senden.
- **Mail:** genau ein Sender per `pg_try_advisory_lock`.
  - Abholung mit `FOR UPDATE SKIP LOCKED`, Token-Bucket MAIL_RATE_PER_SEC.
  - 4xx und Timeouts: Backoff, höchstens 5 Versuche. 5xx: endgültig fehlgeschlagen.
  - Hängt eine Mail im Status `in_versand` (mehr als 10 min), wird sie als fehlgeschlagen markiert („Status unklar"), nicht erneut gesendet.
  - Unique `(campaign_id, send_run, email_norm)`; erneuter Versand nur explizit mit `send_run+1`.
- Mails: ein Empfänger pro Mail, Header gegen CRLF gehärtet. `[LINK]` wird erst nach der Sanitisierung eingesetzt und escaped. Format multipart/alternative.

## 6. Admin-Vertrauensmodell (NPT-06, f7bc0861)
- **Gateway:**
  - `ssl_verify_client on`, CA-Bundle, Tiefe und optionale CRL aus Env und Mounts.
  - Prüft `$ssl_client_verify` selbst noch einmal.
  - Setzt `X-SSL-Client-*` (inklusive `$ssl_client_escaped_cert`) und `X-Forwarded-*` immer selbst und überschreibt Client-Header.
  - Leitet nur `/admin` weiter.
  - Prüft alle 60 s den Inhalt (sha256) von Serverzertifikat, Schlüssel, CA-Bundle und optionaler CRL und macht bei Änderung `nginx -t && nginx -s reload` (ursprünglich nur die CRL-mtime; NPT-12 K3).
- **App-Admin-Middleware:**
  1. Die direkte Absender-IP muss in `ADMIN_GATEWAY_CIDR` liegen (nicht XFF).
  2. `X-SSL-Client-Verify == SUCCESS`.
  3. Das PEM aus dem Header parsen; der SHA-1 des DER muss zum Fingerprint-Header passen. Subject und Issuer kommen aus dem geparsten Zertifikat.
  4. Identifier bilden (siehe 7), mit admin_users abgleichen; kein Admin führt zur Anfrageseite.
- Die Public-App wertet diese Header nirgends aus.
- Jede Admin-Aktion schreibt in `admin_audit_log`, in derselben Transaktion.

## 7. Identifier (NPT-07, fcb33dbb)
- Eine Funktion `normalize_identifier`.
- **DN:** Typen als kanonische Kurznamen in Großbuchstaben. Werte: NFKC, trimmen, Leerzeichenfolgen zusammenziehen, casefold. RDNs sortieren, dann RFC 4514. Akzeptierte Eingaben: RFC 4514/2253 und das OpenSSL-Slash-Format.
- **san_email:** alle SAN-Adressen, lowercase; ein Treffer genügt.
- **san_upn:** OID 1.3.6.1.4.1.311.20.2.3, casefold.
- **fingerprint_sha256:** 64 Hex-Zeichen lowercase.
- Schlüssel ist `(issuer_norm, id_kind, identifier_norm)`. Im Bootstrap ist `issuer::id` optional; ohne Issuer gilt `*`.

## 8. CSRF (NPT-08, 6f467b67)
- Token = `ts || HMAC-SHA256(CSRF_SECRET, scope||ts)`, gültig 12 h, Vergleich mit `compare_digest`.
  - Admin-Scope: Issuer und Identifier
  - Bearbeiter-Scope: Projekt und `token_generation`
- Transport: `<meta>`-Tag, als Header `X-CSRF-Token` bzw. als Hidden-Field. Keine Cookies.
- **Admin:** Zusätzlich muss der Origin-Header exakt der Admin-Basis-URL entsprechen (`ADMIN_BASE_URL`, sonst die vom Gateway gesetzten `X-Forwarded-*`, B2). Admin-Seiten senden `Referrer-Policy: same-origin`, sonst schickten Browser bei Formular-POSTs `Origin: null` (B1).
- **Bearbeiter (D1):** Seiten behalten `Referrer-Policy: no-referrer` (Auftrag 8.2); Browser senden bei `fetch()`-Schreibzugriffen deshalb `Origin: null`. Regel für `PATCH/POST /p/<token>/api/…`:
  - `Content-Type: application/json` Pflicht (sonst 415), keine CORS-Header (Cross-Site-JSON scheitert am Preflight);
  - gültiges CSRF-Token im Header `X-CSRF-Token` (sonst 403 `csrf`);
  - `Origin` fehlt oder `null` → zulässig (nur zusammen mit den beiden Punkten oben); ein anderer Origin muss exakt `PUBLIC_BASE_URL` entsprechen;
  - `Sec-Fetch-Site`, falls gesendet, muss `same-origin` oder `none` sein.

## 9. Grenzen und Gesundheit (NPT-09, 5ab5d5f0)
- **Rate-Limit:** UNLOGGED-Tabelle `rate_limit_buckets` mit festen Fenstern und UPSERT. Limits: 30 Schreibvorgänge pro 10 s und 10 Neuanlagen pro 60 s je IP; bei Überschreitung 429 mit Retry-After (Rest des Fensters). Gezählt wird jeder Schreibversuch, der den Endpunkt erreicht (nach CSRF/Origin), auch fachlich abgelehnte (Domain-Validierung, Konflikt); Schema-Fehler (422 aus Pydantic vor dem Endpunkt) zählen nicht (D8).
- Die Client-IP kommt nur dann aus XFF, wenn der direkte Absender in `TRUSTED_PROXY_IPS` steht.
- **Zulassung:** Alle Grenzwerte stehen zentral in `npt/limits.py`, die Rangfolge wird beim Start geprüft. SSE pro Worker begrenzt (300, J10), Semaphor mit 2 s Wartebudget, `pool_timeout 2s`. Abweisung: 503 mit Retry-After und `{reason}`. Die Uvicorn-Grenze ist nur ein höher liegendes Rückfallnetz. Kapazitätsgrenzen sind keine .env-Stellschrauben (die Worker-Zahl `WEB_WORKERS` schon).
- **Retry-After je Fall (D4):** 503 `gesperrt` (Lock-/Statement-Timeout beim Schreiben) 1 s; 503 `ueberlastet` (Semaphor oder DB-Pool erschöpft) 2 s; 503 `ueberlastet` am SSE-Endpunkt (Grenze je Worker) 5 s; 503 `datenbank` (Datenbank nicht erreichbar) 5 s; `/readyz` 503 5 s; 429 Rest des Rate-Limit-Fensters.
- **Datenbank nicht erreichbar:** Verbindungs- und Schnittstellenfehler (`OperationalError`/`InterfaceError` von SQLAlchemy und psycopg) ergeben in beiden Apps 503 mit Retry-After, Security-Headern und einer Warnzeile `npt.db` ohne Traceback – nie eine unbehandelte Ausnahme (500). Bearbeiter-API: JSON `{reason: "datenbank"}`, Bearbeiter-Seiten: HTML-Hinweisseite, Admin: Klartext.
- **Health:** `/healthz` und `/readyz` sind async und liefern nur den Schnappschuss eines Monitors.
  - Der Monitor schreibt alle 10 s eine Probe in `health_probe`.
  - Steht der Monitor länger als 120 s still, beendet sich der Worker selbst.

## 10. Datenmodell (NPT-10, 7782a8aa)
- **Grundlagen:** timestamptz in UTC (Anzeige Europe/Berlin). Enums als text mit CHECK und ASCII-Schlüsseln; „Leer" ist NULL. Ein zentrales Label-Mapping.
- **projects:** `access_token` (token_urlsafe(32)), `token_generation`, `change_seq`, `reset_seq`, Backup-Einstellungen.
- **project_systems:** `UNIQUE(project_id, system_id)`, `version`, `row_seq`, Index `(project_id, row_seq)`.
- **change_log:** ohne FK; enthält `seq`, `source`, `client_ip`, `client_name`, `admin_identifier`.
- **admin_users:** UNIQUE wie in Abschnitt 7. Die Regel „letzter aktiver Admin" wird mit FOR UPDATE geprüft. Offene Anfragen haben einen partiellen UNIQUE-Index.
- **mail_deliveries:** UNIQUE `(campaign_id, send_run, email_norm)`.
- Weitere Tabellen: `staged_uploads`, `rate_limit_buckets` (UNLOGGED), `health_probe`.
- Sequenz `new_system_seq`. `master_systems` hat CHECK `NOT LIKE 'NEW-%'` und `missing_since`.
- SQL nur über SQLAlchemy mit gebundenen Parametern.

## 11. Betrieb (NPT-11, 7da52b7c)
- `docker-compose.yml` ist portabel (wie im Auftrag) und bindet den Bearbeiter-Port standardmäßig an 127.0.0.1. Ein Betriebs-Overlay (z. B. `docker-compose.prod.yml` über `COMPOSE_FILE`) ist eine **künftige Option** für das Ausrollen im Betrieb und wird in diesem Repository nicht gebaut (B15, J9). Beschreibung des Zielbilds:
- **Betrieb (Zielbild des Overlays):**
  - Die App veröffentlicht keinen Port; Bearbeiter kommen über einen zentralen Reverse Proxy.
  - Einziger veröffentlichter Port ist der des admin-gateway (mTLS; Client-CA, Serverzertifikat und CRL aus der eigenen PKI).
  - Secrets beim Start aus einem eigenen Secret-Store (statt `.env`).
- `gen-test-pki.sh` ist nur für Tests und CI; seine Ausgaben stehen in .gitignore und werden nie in ein Deployment gemountet.
- **PII:** Der Public-Pfad liefert nur Spalten aus einer Allowlist (eigenes DTO). change_log, admin_*, mail_* und master_* sind über die Public-App nicht erreichbar.
- **Persistenz:** Volumes `db_data` und `backups`, erfasst von einem Host-/Volume-Backup, plus nächtlicher pg_dump.
- **Container:** `restart: unless-stopped`, mem_limit, Healthchecks, JSON-Logs nach stdout, `deploy.sh`. Laufzeit-Image non-root, read_only, ohne Build-Tools.
- **Abhängigkeiten (B12):** direkte Abhängigkeiten in `pyproject.toml` **exakt gepinnt** (`==`), alle transitiven Pakete mit Hashes in `requirements.lock`/`requirements-dev.lock`, Installation mit `pip --require-hashes`; Basis-Images mit Patch-Version, Hilfs-Images per Digest. (Ursprünglich: „Obergrenze unter der nächsten Major-Version“ – exakte Pins sind strenger und reproduzierbar.)

## 12. Kubernetes (NPT-12, 54aaf020)
- Helm-Chart `deploy/helm/notfall-patch-tracker`, Anleitung `docs/KUBERNETES.md`, Entscheidungen K1–K11 in `docs/DECISIONS.md`.
- **public**-Deployment (APP_ROLE=public, skalierbar, PDB, optional HPA) und **backend**-Deployment mit genau einem Pod (APP_ROLE=admin,jobs + Gateway-Sidecar + Backup-PVC, Recreate).
- Migration im initContainer jedes App-Pods unter Advisory Lock; Hauptcontainer mit `NPT_SKIP_MIGRATE=1`; Chart setzt `NPT_SCHEMA_NEWER=warn` (Rollback). Migrationen sind expand/contract (N-1-kompatibel).
- Admin-Vertrauen: App-Admin-Port nur auf 127.0.0.1, `ADMIN_GATEWAY_CIDR=127.0.0.1/32`; TLS endet nur im Gateway-Sidecar (L4-Service oder TLSRoute-Passthrough); keine Mesh-Injektion, keine weiteren Container im Pod.
- Externe PostgreSQL, Verbindungsbudget im Template geprüft; NetworkPolicies (Default-Deny), PodSecurity `restricted`, Secrets per `secretKeyRef`.
- Liveness des backend-Pods: `python -m npt.probe` (Admin-Port + Jobs-Heartbeat); der Jobs-Prozess löscht `health_probe`-Zeilen älter als 24 h.

## Abweichungen vom Auftrag (in DECISIONS.md übernommen)
- `updated_since` ist ein Sequenz-Cursor, kein Zeitstempel (Grund: Commit-Reihenfolge; A1).
- Die Bindeadresse des Bearbeiter-Ports ist per Env einstellbar, Default 127.0.0.1 (A2).
- Ein künftiger API-Abruf von Stammdaten erzeugt nur eine Vorschau; übernommen wird erst nach Admin-Bestätigung (A3).
- Phase 6 enthält bereits den Backup-Erzeugungskern (für `vor_import`); Phase 7 ergänzt Scheduler, Aufbewahrung, Download und Restore (A4).
- Admin-Seiten senden `Referrer-Policy: same-origin` statt `no-referrer` (Origin-Prüfung, B1).
- Bearbeiter-Schreibzugriffe akzeptieren `Origin: null`/fehlend nur zusammen mit JSON-Content-Type und CSRF-Token (D1).
- Der Mail-Betreff ist eine Klartextzeile, formatiert wird nur der Text (I1).

## Abweichungen vom ursprünglichen Architektur-Review (Fortschreibung)
| Stelle | Review 02de6ec6 | Ist-Stand | Kennung |
|---|---|---|---|
| §4 Neue Systeme | `'NEW-'\|\|lpad(nextval(…)::text,4,'0')` | `nextval` in SQL, `NEW-{n:04d}` in Python (kürzt nie) | A5, D5 |
| §5 Backup-Kriterium | `change_seq` > Stand beim letzten Backup | Datenänderung `GREATEST(max(row_seq), reset_seq) > last_backup_seq` | H2 |
| §8 Origin | Origin exakt = Basis-URL (alle Schreibzugriffe) | Admin exakt; Bearbeiter zusätzlich `null`/fehlend mit JSON + CSRF | B1, D1 |
| §9 SSE-Grenze | „pro Worker begrenzt“ (200) | 300 je Worker | E4, J10 |
| §9 Fehlerbild DB | nicht festgelegt | 503 `datenbank`, Retry-After 5 s, beide Apps | D4 |
| §2 `WEB_WORKERS` | Default 4 | Default 6 (1.800 SSE-Plätze) | J12 |
| §11 Betriebs-Overlay | per `COMPOSE_FILE` aktiviert | nicht gebaut, künftige Option für den Betrieb | B15, J9 |
| §11 Abhängigkeiten | Obergrenze unter nächster Major | exakt gepinnt, transitiv mit Hashes | B12 |
| §2 Migrations-Lock | `pg_advisory_lock`, Bootstrap danach | `pg_try_advisory_lock`-Schleife (300 s), Bootstrap im Lock | K2 |
| §6 Gateway-Reload | CRL-mtime | Inhalt von Zertifikat, Schlüssel, CA-Bundle und CRL | K3 |
| §11 Betrieb | nur Compose | zusätzlich Helm-Chart für Kubernetes | K1–K11 |
