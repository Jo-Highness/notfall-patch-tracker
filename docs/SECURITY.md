# Sicherheits-Checkliste (Audit gegen Auftrag §8, Phase 9)

Stand: Phase 9 (2026-10-27). Jede Zeile nennt Umsetzung und Nachweis (Test/Skript).
Entscheidungen und Abweichungen: `docs/DECISIONS.md` (B1–B15, D1, D7, J1–J9, J13).

Legende: ✅ umgesetzt und geprüft · ⚠️ umgesetzt mit dokumentierter Einschränkung

## 8.1 Admin-Zugang über mTLS

| Anforderung | Stand | Umsetzung / Nachweis |
|---|---|---|
| Eigener Listener, nur per mTLS; Admin-Routen auf dem Bearbeiter-Port = 404 | ✅ | Public-Factory ohne Admin-Router (NPT-02); `test_public.py::test_routing_table_has_no_admin_routes`, `test_admin_paths_are_404`; `mtls-integration-test.sh` Abschnitt 10 |
| `admin-gateway` (nginx) mit `ssl_verify_client on`, reicht `$ssl_client_*` als Header weiter, überschreibt Client-Header | ✅ | `gateway/admin.conf.template` (`proxy_set_header` immer); mTLS-Test Abschnitte 1–3, 9 |
| App akzeptiert Header nur auf dem internen Port und nur von der Gateway-Adresse; ohne `SUCCESS` abgelehnt | ✅ | `AdminAuthMiddleware` (Socket-Absender ∈ `ADMIN_GATEWAY_CIDR`, Verify, PEM↔Fingerprint, doppelte Header abgelehnt); `test_admin_middleware.py`; mTLS-Test 9 (gespoofte Header aus fremden Containern) |
| CA-Bundle, Verifikationstiefe, optionale CRL mit Reload ohne Neustart, Server-Zertifikat/Key als Mounts | ✅ | `ADMIN_PKI_DIR` (Verzeichnis-Mount, B3), `crl-reload.sh` (Inhalt von Zertifikat, Schlüssel, CA-Bundle und CRL alle 60 s, `nginx -t && reload`); fehlende konfigurierte CRL → Start verweigert (fail closed); mTLS-Test (gesperrtes Zertifikat) |
| Identifier per `ADMIN_CERT_ID_ATTRIBUTE` (`subject_dn`/`san_email`/`san_upn`/`fingerprint_sha256`), normalisiert, mit Issuer gespeichert | ✅ | `npt/identity.py` (NPT-07, B7); `test_identity.py` |
| Admins ernennen/deaktivieren/entfernen, Anfrage-Seite, Bestätigen/Ablehnen | ✅ | `test_admin_flow.py`; mTLS-Test 8 |
| Bootstrap `ADMIN_BOOTSTRAP_IDS`; letzter aktiver Admin geschützt | ✅ | B8/B9, `FOR UPDATE`; `test_admin_flow.py` |
| Audit-Log aller Admin-Aktionen mit Identifier | ✅ | `admin_audit_log` in derselben Transaktion; `/admin/audit` |
| CSRF für alle zustandsändernden Admin-Requests | ✅ | HMAC-Token (NPT-08) + exakte Origin-Prüfung; `test_csrf.py`; mTLS-Test 7 |
| Test-PKI-Skript (Admin / Nicht-Admin / fremde CA) | ✅ | `scripts/gen-test-pki.sh` (+ gesperrtes Zertifikat, fremde CA mit gleichem DN) |

## 8.2 Allgemein

### Projekt-Token nie in Logs

| Logquelle | Stand | Umsetzung / Nachweis |
|---|---|---|
| App-Access-Log | ✅ | Eigenes Access-Log `npt.access` maskiert Pfad + Query (`/p/***`) bereits beim Schreiben; Gunicorn-/Uvicorn-Access-Logs sind abgeschaltet (`accesslog=None`, Logger `uvicorn.access`/`gunicorn.access` ohne Handler) |
| App-Fehlerlogs (Gunicorn, Uvicorn, eigene) | ✅ | Ein `JsonFormatter` für alle Logger maskiert **jede** Zeichenkette inkl. Traceback: `/p/<token>`, `token=`/`access_token=`/`csrf_token=`-Parameter und – neu in Phase 9 – freistehende 43-Zeichen-Token (z. B. in Ausnahmetexten) |
| SQL in Ausnahmetexten | ✅ | `create_engine(hide_parameters=True)` – gebundene Parameter (Token-Lookup) erscheinen nie in SQLAlchemy-Ausnahmetexten und damit nicht in App-Logs. Der JsonFormatter maskiert nur, was die **App** loggt; das Log des PostgreSQL-Servers (stdout des `db`-Containers) läuft nicht durch ihn – siehe nächste Zeile |
| PostgreSQL-Log | ✅ | Eigenes Log des db-Containers, **nicht** durch den Formatter maskiert. Konfiguriert (`docker-compose.yml`): `log_parameter_max_length=0` und `log_parameter_max_length_on_error=0` – Parameterwerte erscheinen weder bei langsamen Statements (`log_min_duration_statement=1000`) noch bei Fehlern; `log_error_verbosity=terse` – keine DETAIL/HINT/CONTEXT-Zeilen (DETAIL einer Unique-Verletzung nennt sonst Schlüsselwerte im Klartext). `log_min_error_statement` bleibt `error`: fehlerhafte Statements werden mit Text geloggt, enthalten aber nur Platzhalter (`$1`), weil psycopg 3 Parameter serverseitig bindet und die App SQL nur mit gebundenen Parametern baut (NPT-10); geprüft auf dem Dev-Stack über die App-Engine mit einem fehlschlagenden Token-Lookup (Log zeigt `access_token = $1`, keinen Wert) und einer Unique-Verletzung (keine DETAIL-Zeile, kein Schlüsselwert). Grenze: Statements, die jemand manuell per `psql` mit Literalen absetzt, stehen mit Werten im Log (`J13`) |
| Gateway-Log | ✅ | Nur `/admin…` (`$uri` ohne Query), Admin-URLs enthalten keine Projekt-Token |
| Reverse Proxy | ✅ | `docs/INSTALLATION.md` §5: nginx-`map` maskiert `/p/<token>`, Traefik verwirft `RequestPath` |
| Nachweis | ✅ | `test_security_hardening.py::test_project_token_never_in_access_or_error_logs`, `test_mask_secrets_variants`, `test_sql_errors_hide_bound_parameters`; `scripts/run-e2e.sh` prüft am Ende alle Container-Logs des E2E-Stacks auf unmaskierte `/p/<token>` (Ergebnis: keine) |
| Referer | ✅ | `Referrer-Policy: no-referrer` auf Bearbeiter-Seiten, `fetch(…, {referrerPolicy: "no-referrer"})`; Mail-Links/Handbuch-Links `rel="noopener noreferrer"` |

### Rate-Limit, Validierung, X-Forwarded-*

| Anforderung | Stand | Umsetzung / Nachweis |
|---|---|---|
| Rate-Limit Schreiben 30/10 s je IP, Anlegen 10/min | ✅ | UNLOGGED-Tabelle, UPSERT (NPT-09); `test_write_rate_limit` (jetzt deterministisch: injizierbare Uhr `ratelimit.clock`), `test_create_rate_limit` |
| Serverseitige Validierung (Enums, Längen), nur *-Felder schreibbar | ✅ | Pydantic `extra=forbid`, Literal der vier Felder, eine Domain-Validierung (NPT-04); `test_public_api.py` |
| `X-Forwarded-For` nur von vertrauten Proxies | ✅ | `TRUSTED_PROXY_IPS`, rechtester nicht vertrauter Eintrag; Uvicorn `proxy_headers=False`; `test_trusted_proxy_client_ip` |
| `X-Forwarded-Proto/Host` | ✅ | Bearbeiter-Port: Links/Origin aus `PUBLIC_BASE_URL` (keine Header-Auswertung – nicht fälschbar); Admin: vom Gateway gesetzte Werte nur, wenn `ADMIN_BASE_URL` leer (B2) |
| Zeit: Anzeige Europe/Berlin, Speicherung UTC | ✅ | `timestamptz`, Verbindungs-`timezone=UTC`, Filter `dt` (`DISPLAY_TZ`); Änderungsprotokoll-Tests mit Zeitzonengrenzen |

### XSS – Bemerkung/Version strikt als Plaintext

| Stelle | Stand | Umsetzung / Nachweis |
|---|---|---|
| Serverseitige Templates | ✅ | Jinja2 Autoescape; einzige `|safe`: Mail-Beispiel (nh3-sanitisiert + escapte Ersetzung, I1/I2) und Handbuch (markdown-it `html=False` + nh3, J5); Konfiguration per `tojson` (escaped `<`, `>`, `&`, `'`) – `test_main_list_escapes_project_name_and_config` |
| Grid-Zellen (Tabulator-Formatter) | ✅ | `textFormatter` erzeugt `<span>` mit `textContent`; keine Zell-`tooltip`-Option (Tabulator setzt Tooltip-Strings per `innerHTML`); Kopf-Tooltips als Element mit `textContent` |
| Editoren, Header-Filter | ✅ | Tabulator-`input`/`textarea` (Wert als Property), Listen nur mit festen Enum-Labels |
| Konfliktdialog, Live-Hinweis, Statuszeile, Vorfilter-Chips | ✅ | ausschließlich `textContent` |
| Pivot | ✅ | serverseitig escaped, Live-Neuaufbau per DOM-API/`textContent`; DAZ mit `<…>` getestet (`test_pivot.py`) |
| Mail-Vorschau/Mail | ✅ | Text nh3-Allowlist; System-Name/-ID/URL einzeln escaped (`test_mail_render.py`, `test_mail_sanitize.py`) |
| Nachweis Browser | ✅ | `tests/e2e/inline_smoke.py`: `<img onerror>`/`<script>` in Bemerkung und Version erscheinen in Zelle, nach Neuladen, im Live-Hinweis und im Konfliktdialog als Text; kein Script läuft, kein Element entsteht |

### Excel – Formel-Injection

| Export | Stand | Nachweis |
|---|---|---|
| Hauptliste (Sicht/alle) | ✅ | `export.write_text`: `data_type="s"` + `quotePrefix` bei `= + - @`, Tab, CR (D7); `test_export.py`, `test_formula_injection_all_exports` |
| Manuell angelegte Systeme | ✅ | gleicher Schreibpfad; `test_formula_injection_all_exports` |
| Änderungsprotokoll | ✅ | `changelog.workbook` → `export.write_sheet`; `test_formula_injection_all_exports` |
| Backup-.xlsx | ✅ | `export.systems_workbook`; `test_backups.py` |
| Excel-Vorlage | ✅ | nur feste Spaltenköpfe |

### Uploads

| Anforderung | Stand | Umsetzung |
|---|---|---|
| Größenlimit | ✅ | Gateway `client_max_body_size = UPLOAD_MAX_MB + 1 MB` (neu: aus `UPLOAD_MAX_MB` abgeleitet, eigene 413-Seite mit Headern); App prüft `Content-Length` vor dem Parsen; Bearbeiter-Port: JSON ≤ 64 KB, neu zusätzlich `BodyLimitMiddleware` (auch chunked, 128 KB Rückfallnetz) – `test_body_limit_also_for_chunked_requests`; Proxy-Beispiel `client_max_body_size 1m` |
| Nur .xlsx | ✅ | Endung **und** ZIP-Signatur mit `xl/workbook.xml` (C1) |
| Parsing `read_only`, keine Makros | ✅ | `load_workbook(read_only=True, data_only=True, keep_links=False)`; openpyxl führt nie Makros aus, VBA wird nicht gelesen; ≤ 100.000 Zeilen, 200 Spalten |

### Security-Header

| Antwortart | Bearbeiter-App | Admin-App | Gateway-eigene Antworten |
|---|---|---|---|
| CSP | `default-src 'self'; script-src 'self'; style-src 'self'; … object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'` (kein `unsafe-inline`) | gleich | `default-src 'none'; frame-ancestors 'none' …` |
| `X-Content-Type-Options` | `nosniff` | `nosniff` | `nosniff` |
| `X-Frame-Options` | `DENY` | `DENY` | `DENY` |
| `Referrer-Policy` | `no-referrer` | `same-origin` (B1: Origin-Header für CSRF nötig) | `no-referrer` |
| `X-Robots-Tag` | `noindex, nofollow` | `noindex, nofollow` | `noindex, nofollow` |
| weitere | COOP/CORP `same-origin`, Permissions-Policy | + `Cache-Control: no-store` | HSTS, `Cache-Control: no-store` |

Abgedeckt sind **alle** Antworten: Seiten, JSON, Static, 404/405/415/422/413, 503 (auch
bei nicht erreichbarer Datenbank, J13) und – neu in Phase 9 – auch 500-Antworten aus
Starlettes `ServerErrorMiddleware` (die Header-Middleware
umschließt jetzt den gesamten Stack, `NptFastAPI`). Das Gateway setzt die Header auf
seinen eigenen 403/404/413/308-Antworten und `robots.txt` (`gateway/security-headers.inc`).
Nachweis: `test_public_headers_on_all_kinds_of_responses`, `test_public_500_has_security_headers`,
`tests/test_db_unavailable.py` (503 bei gestoppter Datenbank, beide Apps),
`test_admin_404_and_500_have_security_headers`, mTLS-Test („CSP ohne unsafe-inline“), curl
gegen das Gateway (Dev-Stack).

⚠️ Antworten, die Uvicorn selbst vor der App erzeugt (400 bei kaputtem HTTP, 503 bei
erreichter `limit_concurrency`), tragen keine Security-Header. Sie enthalten nur festen
Klartext ohne Nutzerdaten; HSTS setzt der vorgelagerte Reverse Proxy.

### robots.txt / noindex

✅ `GET /robots.txt` → `User-agent: * / Disallow: /` auf Bearbeiter-Port und Gateway;
`<meta name="robots" content="noindex, nofollow">` in allen Seiten plus `X-Robots-Tag`
(`test_robots_and_noindex`).

### Abhängigkeiten und Image

| Anforderung | Stand | Umsetzung / Nachweis |
|---|---|---|
| Gepinnt | ✅ | direkte Pins in `pyproject.toml`, transitive Pins **mit Hashes** (`requirements*.lock`, `pip --require-hashes`); Basis-Images mit Patch-Version, Mailpit/Playwright per Digest; Tabulator/Quill mit sha256 in `VERSION` |
| Schwachstellen-Scan | ✅ | `scripts/audit-deps.sh` (pip-audit 2.10.1 gegen PyPI/OSV) am 2026-10-27: **keine bekannten Schwachstellen** in `requirements.lock` und `requirements-dev.lock` |
| SBOM und Lizenzen (J15) | ✅ | `scripts/sbom.sh` (CycloneDX 1.6 für Quellbaum, App- und Gateway-Abbild, optional grype-Abgleich `--scan` mit VEX-Bewertung `vex/notfall-patch-tracker.openvex.json`, Stand 0.2.1: offen nur 2 Medium in CPython 3.12 + Negligible, K14; `docs/SBOM.md`); `scripts/check-licenses.sh` (kein GPL/AGPL in Laufzeit-Abhängigkeiten, Stand 2026-11-02: ok, psycopg LGPL-3.0 ausgewiesen) |
| Laufzeit-Image ohne Build-Tools | ✅ | Multi-Stage; Laufzeitstufe ohne Compiler, `make`, `git`, `curl`, `pip`/`setuptools` (geprüft per `command -v` im Image). Seit 0.2.1 (DECISIONS K14) auch ohne `apt`, `debconf`, `perl`, `tar` und weitere ungenutzte Debian-Pakete; die dpkg-Datenbank bleibt für SBOM/Scanner, `dpkg` selbst ist ohne `tar` nicht nutzbar |
| Container-Härtung | ✅ | app: UID 10001, `read_only`, tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges`; admin-gateway: `read_only`, `cap_drop: ALL` + `CHOWN/SETUID/SETGID/DAC_OVERRIDE` (nginx-Master wechselt zum Worker-Benutzer), `no-new-privileges`; db (neu): `read_only` + tmpfs `/var/run/postgresql`,`/tmp`, `cap_drop: ALL` + `CHOWN/DAC_OVERRIDE/FOWNER/SETGID/SETUID` (Entrypoint/`su-exec`), `no-new-privileges` – geprüft mit bestehendem und frischem Volume |
| Netze | ✅ | `admin_net`, `db_net` `internal`; nur Bearbeiter-Port (Default loopback) und mTLS-Port veröffentlicht |

### Sonstiges

- Keine Cookies, keine Sessions, keine CORS-Header (Cross-Site-JSON scheitert am Preflight).
- Projekt-Token 256 Bit (`token_urlsafe(32)`), Neugenerierung invalidiert alte URL und
  CSRF-Token (D6).
- Bekannte, bewusst akzeptierte Eigenschaft: Wer die Projekt-URL hat, kann schreiben
  (Auftrag 8). Der Token steht in Browser-Verlauf und Lesezeichen der Bearbeiter.
