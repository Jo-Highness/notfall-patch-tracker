# Installationsanleitung – Notfall-Patch-Tracker

Zielgruppe: Betrieb/IT. Architektur und Begründungen: `docs/ARCHITECTURE.md`,
`docs/DECISIONS.md`; Sicherheits-Checkliste: `docs/SECURITY.md`; Lastmessung:
`docs/LOADTEST.md`. Diese Anleitung beschreibt Docker Compose; Kubernetes (Helm-Chart):
`docs/KUBERNETES.md`.

## 1. Voraussetzungen

| Punkt | Anforderung |
|---|---|
| Betriebssystem | Linux-Host (x86_64) mit Docker |
| Docker | Docker Engine ≥ 24, Docker Compose v2 ≥ 2.20 (`docker compose version`) |
| Netz ausgehend | beim Bauen: Docker Hub (`python:3.12.16-slim-bookworm`, `postgres:16.15-alpine`, `nginx:1.31.8-alpine`, Basisabbilder per Digest gepinnt) und PyPI; im Betrieb: nur der SMTP-Server |
| Reverse Proxy | vorhandener nginx/Traefik o. ä. mit TLS vor dem Bearbeiter-Port (Abschnitt 5) |
| PKI | CA, die Client-Zertifikate für Admins ausstellt; Server-Zertifikat für das admin-gateway |

**Ressourcen** für ein Projekt mit 2.000 Systemen und 500 gleichzeitigen Nutzern
(gemessen, siehe `docs/LOADTEST.md`):

| Dienst | CPU | RAM (gemessen / Limit) |
|---|---|---|
| app (4 Bearbeiter-Worker, 2 Admin-Worker, Jobs; 500 Nutzer) | Ø 0,8 Kern, Spitze 1,7 Kerne | 0,83–0,85 GB / `APP_MEM_LIMIT` 1536m |
| app (6 Bearbeiter-Worker = Default, 1.000 Nutzer) | Ø 1,35 Kerne, Spitze 1,8 | 1,01–1,07 GB / `APP_MEM_LIMIT` 1536m |
| db (PostgreSQL 16) | Ø 0,2 Kern (1.000 Nutzer: 0,3), Spitze 0,5 | 0,18–0,23 GB / `DB_MEM_LIMIT` 1g |
| admin-gateway (nginx) | vernachlässigbar | < 20 MB / `GATEWAY_MEM_LIMIT` 128m |
| Platte | – | DB < 1 GB je Jahr bei normaler Nutzung; Backups je Projekt und Backup ca. 1–2 MB (JSON + xlsx), × Aufbewahrung (Default 48) |

Empfehlung: 4 vCPU (Reserve für Lastspitzen und mehrere aktive Projekte), 4 GB RAM,
20 GB Platte.

**Kapazität und Sizing.** Jeder offene Browser-Tab mit Liste **oder** Übersicht hält einen
Live-Stream (SSE). Je Bearbeiter-Worker sind 300 Streams zugelassen; Gunicorn verteilt neue
Verbindungen nicht gleichmäßig, deshalb mit mindestens 30 % Reserve planen. Darüber
antwortet ein Worker mit 503 und der Browser verbindet nach 5 s neu (in der Zwischenzeit
Abgleich alle 5 s) – funktional korrekt, nur verzögert.

| Gleichzeitige Tabs (Streams) | `WEB_WORKERS` | SSE-Plätze | DB-Verbindungen (höchstens) | RAM app unter Last |
|---|---|---|---|---|
| bis ≈ 800 (z. B. 500 Nutzer, ein Tab) | 4 | 1.200 | ≈ 110 | ≈ 0,85 GB |
| bis ≈ 1.300 (z. B. 500 Nutzer mit Liste + Übersicht) – **Default** | 6 | 1.800 | ≈ 145 | ≈ 1,1 GB (gemessen bei 1.000) |
| bis ≈ 1.800 | 8 | 2.400 | ≈ 180 | ≈ 1,4 GB → `APP_MEM_LIMIT=2g` |
| darüber | 10+ | 3.000+ | > 200 → `POSTGRES_MAX_CONNECTIONS` erhöhen (je Worker 18) | `APP_MEM_LIMIT` ≈ 0,5 GB + 0,15 GB je Worker |

DB-Verbindungen = 18 je Bearbeiter-Worker + 16 je Admin-Worker (Default 2) + ≈ 5; sie
müssen unter `POSTGRES_MAX_CONNECTIONS` (Default 200) bleiben, mit Reserve für Wartung
(`pg_dump`, `psql`). Gemessen wurden 4 und 6 Worker (`docs/LOADTEST.md`, Läufe 4 und 5);
die Zeilen für 8 und mehr Worker sind hochgerechnet. Mehr Worker brauchen auch CPU:
≈ 1 Kern je 500 aktive Nutzer.

**Ports** (nur diese zwei werden veröffentlicht):

| Port | Dienst | Default | Erreichbar für |
|---|---|---|---|
| Bearbeiter-Port | app :8000 | `127.0.0.1:8080` (`PUBLIC_BIND_ADDR`/`PUBLIC_PORT`) | nur den Reverse Proxy (TLS dort) |
| mTLS-Port | admin-gateway :8443 | `0.0.0.0:8443` (`GATEWAY_BIND_ADDR`/`GATEWAY_PORT`) | Admins (Client-Zertifikat Pflicht) |

Der Admin-Port der App (8001) ist nur im internen Docker-Netz `admin_net` erreichbar;
die Datenbank nur in `db_net`. Beide Netze sind `internal`.

## 2. Repository und `.env`

```bash
git clone <repository-url>/notfall-patch-tracker.git /opt/notfall-patch-tracker
cd /opt/notfall-patch-tracker
cp .env.example .env
chmod 600 .env
openssl rand -hex 24   # -> POSTGRES_PASSWORD
openssl rand -hex 32   # -> CSRF_SECRET
```

`.env` nie committen (steht in `.gitignore`). Alle Variablen mit Bedeutung und Default
(identisch mit `.env.example`):

### Geheimnisse (Pflicht)

| Variable | Default | Bedeutung |
|---|---|---|
| `POSTGRES_PASSWORD` | – (Pflicht) | Passwort des DB-Benutzers; ohne Wert startet Compose nicht |
| `CSRF_SECRET` | – (Pflicht) | HMAC-Schlüssel für CSRF-Token, ≥ 32 Zeichen. Ändern macht offene Formulare ungültig (sonst folgenlos) |

### Datenbank

| Variable | Default | Bedeutung |
|---|---|---|
| `POSTGRES_DB` | `npt` | Datenbankname |
| `POSTGRES_USER` | `npt` | DB-Benutzer |
| `DATABASE_URL` | leer | **Nur beim Betrieb außerhalb dieser Compose-Datei** (externe DB, eigener Orchestrator): vollständige URL (`postgresql+psycopg://user:pw@host:5432/db`; `postgres://` und `postgresql://` werden auf den Treiber psycopg umgestellt), hat dann Vorrang vor `POSTGRES_*`. TLS zur DB: `POSTGRES_SSLMODE`, `POSTGRES_SSLROOTCERT`, `POSTGRES_SSLCERT`, `POSTGRES_SSLKEY` (libpq-Parameter, gelten für beide Varianten). `docker-compose.yml` reicht die Variable bewusst nicht an die App durch – im Compose-Betrieb wirkt ein Wert in `.env` nicht, es gilt immer der `db`-Dienst (DECISIONS J13) |
| `POSTGRES_MAX_CONNECTIONS` | `200` | `max_connections` des DB-Containers. Bedarf ca. 18 je Bearbeiter-Worker + 16 je Admin-Worker + 5 (Jobs/Start) – bei 6/2 Workern ≈ 145 (Tabelle „Kapazität und Sizing“) |

### Ports und Netz

| Variable | Default | Bedeutung |
|---|---|---|
| `PUBLIC_BIND_ADDR` | `127.0.0.1` | Host-Adresse des Bearbeiter-Ports. Nur auf eine andere Adresse setzen, wenn der Reverse Proxy auf einem anderen Host läuft (dann per Firewall auf den Proxy beschränken) |
| `PUBLIC_PORT` | `8080` | Host-Port des Bearbeiter-Ports |
| `GATEWAY_BIND_ADDR` | `0.0.0.0` | Host-Adresse des mTLS-Ports |
| `GATEWAY_PORT` | `8443` | Host-Port des mTLS-Ports |
| `ADMIN_NET_SUBNET` | `172.31.250.0/28` | Subnetz des internen Admin-Netzes (feste Adressen für die Absenderprüfung) |
| `APP_ADMIN_IP` | `172.31.250.10` | Adresse der App in `admin_net`; der Admin-Port bindet nur hier |
| `GATEWAY_ADMIN_IP` | `172.31.250.11` | Adresse des Gateways in `admin_net`; nur von hier akzeptiert die App Zertifikats-Header |
| `TRUSTED_PROXY_IPS` | leer | Kommagetrennte IPs/CIDRs der Reverse Proxies, deren `X-Forwarded-For` für Rate-Limit und Änderungsprotokoll ausgewertet wird. Leer = Socket-Absender (dann zählt das Rate-Limit alle Nutzer als eine IP!). Typisch: Gateway-IP des Docker-Netzes `public_net` (`docker network inspect <projekt>_public_net`) bzw. die Proxy-IP |

Die Netze `ADMIN_NET_SUBNET`/`APP_ADMIN_IP`/`GATEWAY_ADMIN_IP` nur ändern, wenn sie mit
vorhandenen Docker-Netzen kollidieren (dann alle drei passend).

### URLs

| Variable | Default | Bedeutung |
|---|---|---|
| `PUBLIC_BASE_URL` | `http://localhost:8080` | Öffentliche Basis-URL der Bearbeiter-Seite **wie der Browser sie sieht** (z. B. `https://npt.firma.de`). Wird für Projekt-Links, Mail-Deep-Links und die Origin-Prüfung der Schreib-API verwendet – muss exakt stimmen (Schema, Host, Port) |
| `ADMIN_BASE_URL` | `https://localhost:8443` (`.env.example`), leer im Code | Basis-URL des Admin-Bereichs für die CSRF-Origin-Prüfung, z. B. `https://npt-admin.firma.de:8443`. Leer = aus dem vom Gateway gesetzten Host-Header. Empfohlen: setzen |

### Admin-Zugang (mTLS)

| Variable | Default | Bedeutung |
|---|---|---|
| `ADMIN_PKI_DIR` | `./pki-out/gateway` | **Host-Verzeichnis** (absoluter Pfad in Produktion), wird read-only nach `/etc/npt-gateway/pki` gemountet; enthält CA-Bundle, Server-Zertifikat/-Key, optional CRL |
| `ADMIN_CA_BUNDLE_PATH` | `/etc/npt-gateway/pki/ca-bundle.pem` | CA-Bundle (Root + Intermediates) für die Prüfung der Client-Zertifikate – Pfad **im Container** |
| `GATEWAY_TLS_CERT_PATH` | `/etc/npt-gateway/pki/server.crt` | Server-Zertifikat (mit Kette) des Gateways |
| `GATEWAY_TLS_KEY_PATH` | `/etc/npt-gateway/pki/server.key` | Server-Schlüssel |
| `ADMIN_CRL_PATH` | leer | Optional CRL (PEM). Gesetzt, aber Datei fehlt → Gateway startet nicht (fail closed) |
| `CRL_RELOAD_INTERVAL` | `60` | Sekunden zwischen den Prüfungen von Serverzertifikat, Schlüssel, CA-Bundle und CRL (Reload ohne Neustart bei geändertem Inhalt) |
| `ADMIN_VERIFY_DEPTH` | `2` | Maximale Kettentiefe der Client-Zertifikatsprüfung (1 = direkt von der Root, 2 = über eine Intermediate) |
| `ADMIN_CERT_ID_ATTRIBUTE` | `subject_dn` | Woran Admins erkannt werden: `subject_dn`, `san_email`, `san_upn`, `fingerprint_sha256` (Abschnitt 3) |
| `ADMIN_BOOTSTRAP_IDS` | leer | Initiale Admins, getrennt mit `;` oder Zeilenumbruch, oder JSON-Liste; optional mit Aussteller `<Issuer-DN>::<Identifier>`. Werden beim Start angelegt, falls nicht vorhanden |

### Prozesse und Betrieb

| Variable | Default | Bedeutung |
|---|---|---|
| `APP_ROLE` | `all` | `all` = Bearbeiter-App, Admin-App und Jobs in einem Container; sonst Komma-Liste aus `public`, `admin`, `jobs` (Kubernetes: `public` bzw. `admin,jobs`, docs/KUBERNETES.md) |
| `WEB_WORKERS` | `6` | Gunicorn-Worker der Bearbeiter-App (je Worker bis 300 SSE-Streams); Tabelle „Kapazität und Sizing“ in §1 |
| `ADMIN_WORKERS` | `2` | Worker der Admin-App |
| `LOG_LEVEL` | `INFO` | Log-Level (JSON-Logs nach stdout) |
| `DISPLAY_TZ` | `Europe/Berlin` | Zeitzone der Anzeige (gespeichert wird UTC) |
| `APP_MEM_LIMIT` | `1536m` | Speichergrenze app |
| `DB_MEM_LIMIT` | `1g` | Speichergrenze db |
| `GATEWAY_MEM_LIMIT` | `128m` | Speichergrenze admin-gateway |
| `NPT_IMAGE_TAG` | `latest` | Tag der gebauten Images (`notfall-patch-tracker:<tag>`, `…-gateway:<tag>`) – für Update/Rollback (Abschnitt 9) |

### Grenzen

| Variable | Default | Bedeutung |
|---|---|---|
| `RATE_LIMIT_WRITES` | `30` | Schreibvorgänge je IP und Fenster |
| `RATE_LIMIT_WRITES_WINDOW_S` | `10` | Fenster in Sekunden |
| `RATE_LIMIT_CREATE` | `10` | Neuanlagen je IP und Fenster |
| `RATE_LIMIT_CREATE_WINDOW_S` | `60` | Fenster in Sekunden |
| `UPLOAD_MAX_MB` | `20` | Maximale Größe einer hochgeladenen .xlsx (App); das Gateway erlaubt `UPLOAD_MAX_MB + 1` MB (Multipart-Rahmen) |

### SMTP (Mailversand)

| Variable | Default | Bedeutung |
|---|---|---|
| `SMTP_HOST` | leer | SMTP-Server; leer = Versand deaktiviert (Kampagnen lassen sich anlegen und prüfen) |
| `SMTP_PORT` | `587` | 587 (STARTTLS), 465 (TLS), 25 (intern) |
| `SMTP_SECURITY` | `starttls` | `starttls` (erzwungen, Zertifikat geprüft), `tls` (implizit), `none` (nur interne Relays) |
| `SMTP_USER` / `SMTP_PASSWORD` | leer | Anmeldung; leer = ohne |
| `SMTP_FROM` | leer | Absender (Pflicht für den Versand), z. B. `Notfall-Patch-Tracker <npt@firma.de>` |
| `SMTP_REPLY_TO` | leer | Optionale Antwortadresse, z. B. das Security-Postfach |
| `SMTP_TIMEOUT_S` | `30` | Zeitlimit je Verbindung/Befehl |
| `MAIL_RATE_PER_SEC` | `5` | Drosselung (Mails je Sekunde) |

### Stammdaten-API (Stub, deaktiviert)

| Variable | Default | Bedeutung |
|---|---|---|
| `MASTER_API_ENABLED` | `false` | Abruf der Stammdaten per API (Stub; Anbindung = `ApiMasterDataSource.map_response()` implementieren) |
| `MASTER_API_URL` | leer | nur `https://` |
| `MASTER_API_TOKEN` | leer | Bearer-Token |
| `MASTER_API_SCHEDULE_MINUTES` | `0` | > 0: zeitgesteuert, legt nur eine **Vorschau** an (Übernahme durch Admin) |

Von Compose fest gesetzt und **nicht** in die `.env` gehörend: `ADMIN_BIND_ADDR`,
`ADMIN_GATEWAY_CIDR`, `POSTGRES_HOST`, `POSTGRES_PORT`, `BACKUP_DIR` (`/data/backups`).
`COMPOSE_PROJECT_NAME`/`COMPOSE_FILE` können zusätzlich in der `.env` stehen (Docker-Standard).

## 3. PKI für den Admin-Zugang

Ins Verzeichnis `ADMIN_PKI_DIR` (z. B. `/opt/npt-pki`, Eigentümer root, Dateien lesbar für
den nginx-Benutzer im Container: `0644` für Zertifikate/CRL, Schlüssel `0640` mit
Gruppe 101 oder `0644` auf einem nur für root lesbaren Verzeichnis):

```text
/opt/npt-pki/
  ca-bundle.pem   Root- und ggf. Intermediate-CA, die Admin-Zertifikate ausstellt
  server.crt      Server-Zertifikat des Gateways (Kette: Server, Intermediate)
  server.key      Server-Schlüssel
  crl.pem         optional: CRL der ausstellenden CA (PEM)
```

`.env`: `ADMIN_PKI_DIR=/opt/npt-pki`, bei CRL `ADMIN_CRL_PATH=/etc/npt-gateway/pki/crl.pem`,
`ADMIN_VERIFY_DEPTH` passend zur Kette. Eine neue CRL einfach im Verzeichnis ersetzen
(`mv crl.new crl.pem`): Das Gateway lädt sie innerhalb von `CRL_RELOAD_INTERVAL` Sekunden
ohne Neustart. Dasselbe gilt für ein erneuertes Serverzertifikat samt Schlüssel und ein
geändertes CA-Bundle (erst Schlüssel und Zertifikat gemeinsam ersetzen; `nginx -t` schlägt
bei unpassendem Paar fehl, dann bleibt der alte Stand aktiv). **Wichtig:** nginx lehnt mit einer abgelaufenen CRL alle Zertifikate ab –
die CRL muss regelmäßig erneuert werden.

**Identifier-Art wählen** (`ADMIN_CERT_ID_ATTRIBUTE`):

| Wert | Identifier | Wann |
|---|---|---|
| `subject_dn` (Default) | Subject-DN, normalisiert (Reihenfolge, Leerzeichen, Groß/klein egal) | überlebt Zertifikatserneuerung mit gleichem DN – Normalfall |
| `san_email` | E-Mail-Adresse aus dem SAN | wenn DNs nicht eindeutig sind, Mail-Adressen aber schon |
| `san_upn` | Microsoft-UPN aus dem SAN (OID 1.3.6.1.4.1.311.20.2.3) | AD-/Smartcard-Zertifikate |
| `fingerprint_sha256` | SHA-256 des Zertifikats | maximal streng; jede Erneuerung erfordert neuen Eintrag |

Gespeichert wird der Identifier zusammen mit dem Aussteller-DN (ohne Angabe: jeder
Aussteller der konfigurierten CA).

**Ersten Admin anlegen:** Identifier aus dem Admin-Zertifikat ermitteln:

```bash
openssl x509 -in admin.crt -noout -subject -nameopt RFC2253
# subject=CN=Max Muster,OU=IT-Security,O=Firma GmbH,C=DE
openssl x509 -in admin.crt -noout -issuer -nameopt RFC2253      # Aussteller (optional)
openssl x509 -in admin.crt -noout -ext subjectAltName            # fuer san_email/san_upn
openssl x509 -in admin.crt -noout -fingerprint -sha256 | tr -d : # fuer fingerprint_sha256
```

```dotenv
ADMIN_BOOTSTRAP_IDS=CN=Max Muster,OU=IT-Security,O=Firma GmbH,C=DE
# mehrere: mit ';' trennen; mit Aussteller:
# ADMIN_BOOTSTRAP_IDS=CN=Firma Issuing CA,O=Firma GmbH,C=DE::CN=Max Muster,OU=IT-Security,O=Firma GmbH,C=DE
```

Beim Start wird der Eintrag angelegt, falls er fehlt. Weitere Admins danach in der
Anwendung (*Admin-Benutzer*) hinzufügen oder per Freischaltungsanfrage bestätigen. Ein
in der Anwendung deaktivierter Bootstrap-Admin bleibt deaktiviert; ein entfernter wird
beim nächsten Start wieder angelegt – dauerhaft entfernen heißt, ihn aus der Variable zu
streichen.

Zum Testen erzeugt `scripts/gen-test-pki.sh` eine vollständige Test-PKI (nur Tests!).

## 4. Erststart

```bash
./deploy.sh            # baut, startet, prueft: jeder Dienst laeuft mit dem neuen Abbild und ist healthy
# alternativ: docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz    # {"status":"ok"}       Liveness
curl -fsS http://127.0.0.1:8080/readyz     # {"status":"ready"}    DB erreichbar
docker compose logs -f app                 # JSON-Logs; "Migration abgeschlossen", "Admin-Bootstrap abgeschlossen"
```

Migrationen laufen beim Start automatisch (Advisory Lock, mehrere Instanzen sicher).
Danach im Browser mit dem Admin-Zertifikat `https://<gateway-host>:8443/admin/` öffnen.

**Erster Stammdatenimport:** *Stammdaten* → *Excel-Vorlage herunterladen*, mit den Daten
füllen (oder Export des Quellsystems mit passenden Spaltenköpfen), hochladen, Vorschau
prüfen, *Übernehmen*. Testdaten: `python3 scripts/gen-testdata.py` →
`testdata-out/stammdaten.xlsx` (2.000 erfundene Systeme). Danach erstes Projekt anlegen
(Admin-Handbuch unter `/admin/hilfe`).

## 5. Reverse Proxy vor dem Bearbeiter-Port

Der Proxy terminiert TLS und leitet an `127.0.0.1:8080` weiter. Pflicht für SSE
(`/p/<token>/api/events`): **Buffering aus, Lese-Timeout deutlich über dem Heartbeat
(15 s), keine Kompression des Event-Streams, HTTP/1.1 zum Upstream.** Zusätzlich:
Projekt-Token in den Proxy-Logs maskieren, Upload-Grenze klein (die Bearbeiter-API nimmt
höchstens 64 KB JSON an).

In der `.env`: `PUBLIC_BASE_URL=https://npt.firma.de` und `TRUSTED_PROXY_IPS` = Adresse,
mit der der Proxy bei der App ankommt (bei Proxy auf demselben Host über den
veröffentlichten Port: die Gateway-IP des Docker-Netzes `<projekt>_public_net`, z. B.
`172.24.0.1`).

### Mitgeliefertes HTTPS-Overlay (ohne eigenen Proxy)

Wer keinen Reverse Proxy betreibt, nimmt `docker-compose.tls.yml` (DECISIONS J14): ein
nginx-Container `public-tls` terminiert TLS (HTTP/2), reicht SSE ungepuffert durch,
maskiert Projekt-Token im Access-Log. Klartext-Anfragen an den HTTPS-Port werden per 301
auf `https://` umgeleitet; ein Zusatzport leitet auf den Haupt-Port um. Das Abbild
`notfall-patch-tracker-tls-proxy` wird lokal aus `tls-proxy/Dockerfile` gebaut: das
offizielle nginx-Abbild (per Digest, wie das admin-gateway) ohne die ungenutzten Module
acme/geoip/image-filter/njs/xslt und ohne curl, sonst unverändert (Standard-Entrypoint,
envsubst der Templates); Konfiguration und Zertifikate kommen als Read-only-Mounts.
`./deploy.sh` baut es mit und prüft auch `public-tls` auf aktuelles Abbild und „healthy“.

```bash
mkdir -p ~/npt-tls && chmod 700 ~/npt-tls     # fullchain.pem (Zertifikat + Kette) und server.key ablegen
# in .env:
COMPOSE_FILE=docker-compose.yml:docker-compose.tls.yml
PUBLIC_TLS_DIR=/home/<user>/npt-tls
PUBLIC_TLS_PORT=8444                           # HTTPS
PUBLIC_TLS_HTTP_PORT=8081                      # Zusatzport -> Umleitung
PUBLIC_BIND_ADDR=127.0.0.1                     # eigener App-Port bleibt nur lokal
PUBLIC_BASE_URL=https://npt.firma.de:8444
./deploy.sh                                    # oder: docker compose up -d --build
```

`TRUSTED_PROXY_IPS` setzt das Overlay selbst (feste Proxy-Adresse im internen Netz
`tls_net`). Zertifikat tauschen: Dateien ersetzen, dann
`docker compose exec public-tls nginx -s reload`. Kein HSTS (gilt für den ganzen Hostnamen).

### nginx

```nginx
# Projekt-Token nie ins Access-Log (Auftrag 8.2)
map $request_uri $npt_log_uri {
    ~^(?<pre>/p/)[^/?]+(?<rest>.*)$  $pre***$rest;
    default                          $request_uri;
}
log_format npt '$remote_addr - [$time_local] "$request_method $npt_log_uri" $status '
               '$body_bytes_sent $request_time';

upstream npt_public { server 127.0.0.1:8080; keepalive 32; }

server {
    listen 443 ssl;
    http2 on;
    server_name npt.firma.de;
    ssl_certificate     /etc/nginx/tls/npt.crt;
    ssl_certificate_key /etc/nginx/tls/npt.key;
    access_log /var/log/nginx/npt.log npt;
    client_max_body_size 1m;

    # Live-Updates (SSE)
    location ~ ^/p/[^/]+/api/events$ {
        proxy_pass http://npt_public;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;              # Ereignisse sofort durchreichen
        proxy_cache off;
        gzip off;                         # keine Kompression des Streams
        proxy_read_timeout 1h;            # >> 15 s Heartbeat
        proxy_send_timeout 1h;
    }

    location / {
        proxy_pass http://npt_public;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 60s;
        gzip on;
        gzip_types application/json text/css application/javascript;   # Liste: 1 MB -> ~0,15 MB
    }
}
```

`error_log` von nginx enthält bei Fehlern die Request-Zeile; Level `error` (Default) loggt
normale Upstream-Abbrüche nicht. Wer `info`/`debug` nutzt, muss diese Logs wie die
Access-Logs behandeln.

### Traefik (v2/v3, Docker-Labels oder File-Provider)

```yaml
# dynamische Konfiguration (File-Provider)
http:
  routers:
    npt:
      rule: Host(`npt.firma.de`)
      entryPoints: [websecure]
      tls: {}
      service: npt
      middlewares: [npt-limit]
    npt-sse:
      rule: Host(`npt.firma.de`) && PathRegexp(`^/p/[^/]+/api/events$`)   # Traefik v3
      entryPoints: [websecure]
      tls: {}
      service: npt
      priority: 100
  middlewares:
    npt-limit:
      buffering:
        maxRequestBodyBytes: 1048576     # 1 MB; NICHT am SSE-Router verwenden (puffert)
  services:
    npt:
      loadBalancer:
        servers: [{ url: "http://127.0.0.1:8080" }]
        responseForwarding:
          flushInterval: 100ms           # SSE: Ereignisse sofort weiterreichen
```

```yaml
# statische Konfiguration: Timeouts SSE-tauglich
entryPoints:
  websecure:
    address: ":443"
    transport:
      respondingTimeouts:
        readTimeout: 60s
        writeTimeout: 0s                 # 0 = kein Abbruch langlebiger Streams
        idleTimeout: 180s
accessLog:
  fields:
    headers: { defaultMode: drop }
    names: { RequestPath: drop }         # Pfad enthaelt den Projekt-Token -> nicht loggen
```

Traefik setzt `X-Forwarded-For` selbst. Keine `compress`- und keine `buffering`-Middleware
am SSE-Router (`buffering` puffert auch Antworten – nur am normalen Router verwenden).
Traefik v2 kennt `PathRegexp` nicht; dort `Path(`/p/{token:[^/]+}/api/events`)`.

### Admin-Bereich

Der mTLS-Port des admin-gateway wird **nicht** hinter einen TLS-terminierenden Proxy
gelegt (das Client-Zertifikat muss am Gateway ankommen). Falls nötig: TCP-Passthrough
(nginx `stream`, Traefik `tcp`-Router mit `passthrough: true`).

## 6. SMTP und Testmail

In der `.env` `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, ggf. `SMTP_USER`/`SMTP_PASSWORD`,
`SMTP_FROM` (Pflicht) und optional `SMTP_REPLY_TO` setzen, dann `./deploy.sh` (bzw.
`docker compose up -d app`). Im Log erscheint „Mail-Sender aktiv"; ohne SMTP
„Mailversand deaktiviert".

**Testmail:** Projekt → *Mailversand / Kampagnen* → *Neue Kampagne* (Betreff, Text mit
`[LINK]`) → *Speichern und Vorschau* → *Testmail senden* an die eigene Adresse. Fehler
erscheinen sofort als Meldung (Details im Audit-Log und im App-Log). Prüfen: Absender,
Links zeigen auf `PUBLIC_BASE_URL`, HTML- und Textteil.

Für Entwicklung/Test: Overlay `docker-compose.mailpit.yml` (lokaler Mailpit, stellt nichts
zu) – nie in Produktion.

## 7. Datensicherung und Wiederherstellung

Zu sichern sind zwei Docker-Volumes: `<projekt>_db_data` (PostgreSQL) und
`<projekt>_backups` (Projekt-Backups als JSON/xlsx), dazu `.env` und `ADMIN_PKI_DIR`
(enthalten Geheimnisse – getrennt und geschützt ablegen).

**Nächtlicher DB-Dump** (konsistent im laufenden Betrieb), z. B. per Cron:

```bash
cd /opt/notfall-patch-tracker
ts=$(date +%Y%m%d-%H%M)
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > /srv/backup/npt/npt-$ts.dump
docker run --rm -v notfall-patch-tracker_backups:/b:ro -v /srv/backup/npt:/out alpine \
  tar czf /out/npt-backups-$ts.tgz -C /b .
find /srv/backup/npt -name 'npt-*' -mtime +30 -delete
```

(Volume-Namen: `docker volume ls | grep backups`; Präfix = Compose-Projektname.)

**Wiederherstellung der Datenbank** (z. B. auf neuem Host):

```bash
docker compose up -d db                      # leere DB mit gleichem POSTGRES_PASSWORD
docker compose stop app                      # keine Schreiber
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' \
  < /srv/backup/npt/npt-20261027-0200.dump
docker run --rm -v notfall-patch-tracker_backups:/b -v /srv/backup/npt:/in:ro alpine \
  sh -c 'tar xzf /in/npt-backups-20261027-0200.tgz -C /b && chown -R 10001:10001 /b'
./deploy.sh
```

**Einzelnes Projekt** zurücksetzen: im Admin-Bereich *Backups* → *Wiederherstellen …*
(siehe Admin-Handbuch) – dafür ist kein Eingriff am Host nötig.

## 8. Logs und Überwachung

- Alle Dienste loggen nach stdout (`docker compose logs`); die App als JSON (eine Zeile je
  Ereignis, Felder `ts`, `level`, `logger`, `msg`, Access-Log `npt.access` mit `path`,
  `status`, `duration_ms`, `client_ip`). Projekt-Token sind maskiert (`/p/***`).
- Docker-Log-Rotation: `json-file`, 5 × 20 MB je Dienst.
- Healthchecks: app `GET /healthz` (Container-Healthcheck), Gateway intern `/gw-health`,
  db `pg_isready`. Für externes Monitoring `GET /readyz` am Bearbeiter-Port (503, wenn die
  DB seit > 30 s nicht erreichbar ist).

## 9. Update und Rollback

```bash
cd /opt/notfall-patch-tracker
# 1. Sicherung VOR dem Update (Migrationen sind vorwaerts gerichtet)
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > /srv/backup/npt/vor-update.dump
# 2. Bisheriges Abbild als Rueckfallstand markieren
docker tag notfall-patch-tracker:latest notfall-patch-tracker:vorher
docker tag notfall-patch-tracker-gateway:latest notfall-patch-tracker-gateway:vorher
# nur mit HTTPS-Overlay (docker-compose.tls.yml):
docker tag notfall-patch-tracker-tls-proxy:latest notfall-patch-tracker-tls-proxy:vorher
# 3. Neuen Stand holen und ausrollen
git fetch && git checkout <neuer-tag-oder-main>
./deploy.sh        # baut, tauscht die Container (Abbild-Abgleich), wartet auf healthy
```

Beim Start laufen neue Migrationen automatisch. `deploy.sh` meldet je Dienst „OK … läuft
mit aktuellem Abbild", sonst Exitcode ≠ 0.

**Rollback:**

```bash
git checkout <vorheriger-stand>
# ohne Schemaaenderung im Update: altes Abbild starten
NPT_IMAGE_TAG=vorher docker compose up -d --no-build --force-recreate app admin-gateway
# mit HTTPS-Overlay zusaetzlich public-tls anhaengen
# mit Schemaaenderung: zusaetzlich die DB aus vor-update.dump wiederherstellen (Abschnitt 7)
```

Ob ein Update das Schema ändert, steht in den Release-Notes bzw. zeigt
`git diff <alt>..<neu> --stat -- src/npt/migrations/versions`.

## 10. Troubleshooting

**mTLS-Verifikation schlägt fehl (403 „Client-Zertifikat fehlt oder ist ungültig")**
- `docker compose logs admin-gateway` zeigt je Request `verify` (`NONE` = kein Zertifikat
  gesendet, `FAILED:…` = Grund).
- Browser fragt nicht nach dem Zertifikat: Zertifikat (PKCS#12) im Benutzer-Store
  importiert? Ausstellende CA steht in `ca-bundle.pem`? (Der Server sendet die Liste der
  akzeptierten CAs; passt keine, bietet der Browser nichts an.)
- `unable to get issuer certificate`: Intermediate fehlt im `ca-bundle.pem` oder
  `ADMIN_VERIFY_DEPTH` zu klein.
- `CRL has expired` / `unable to get certificate CRL`: CRL erneuern; bei mehrstufiger
  Kette braucht nginx CRLs für **alle** CAs der Kette in `crl.pem`.
- Test von der Kommandozeile:
  `curl -v --cert admin.crt --key admin.key https://<host>:8443/admin/`
- Zertifikat gültig, aber Seite „Kein Admin-Zugang": Identifier stimmt nicht – die Seite
  zeigt den ermittelten Identifier; mit `ADMIN_BOOTSTRAP_IDS` bzw. *Admin-Benutzer*
  abgleichen (Identifier-Art beachten).
- `403 csrf` bei Formularen: `ADMIN_BASE_URL` muss exakt der Adresse im Browser
  entsprechen (Schema, Host, Port).

**„Vorübergehend nicht verfügbar“ / 503 `datenbank`**
- Die App erreicht PostgreSQL nicht (Container gestoppt, Neustart, Netz): `docker compose ps db`,
  `docker compose logs db`. Die App antwortet solange mit 503 und `Retry-After: 5`, loggt
  `datenbank_nicht_erreichbar` (ohne Traceback) und arbeitet nach dem Start der DB ohne
  Neustart weiter. Die Liste wiederholt ein Speichern bis zu fünfmal im Abstand von
  `Retry-After` (≈ 25 s); dauert der Ausfall länger, zeigt die Zelle einen Fehler und der
  Wert muss erneut eingegeben werden.

**SSE bricht hinter dem Proxy ab / Status „Abgleich alle 5 s"**
- Proxy puffert: `proxy_buffering off` (nginx) bzw. `flushInterval` (Traefik) für
  `/p/<token>/api/events`; keine Kompression des Streams.
- Verbindung endet nach 60 s: Proxy-Lese-Timeout erhöhen (≥ 1 h); Heartbeat kommt alle
  15 s.
- Prüfen: `curl -N https://npt.firma.de/p/<token>/api/events` muss sofort `retry: 3000`
  und alle 15 s `: hb` zeigen.
- 503 `ueberlastet` auf `/api/events`: mehr als 300 Streams je Worker – `WEB_WORKERS`
  erhöhen.
- Ohne SSE funktioniert die Liste weiter (Polling alle 5 s), nur langsamer.

**Mails kommen nicht an**
- Log: „Mailversand deaktiviert" → `SMTP_HOST`/`SMTP_FROM` fehlen.
- Versandstatus je Empfänger zeigt den Fehlertext: 5xx (z. B. 535 Anmeldung, 550
  Relay verweigert) = endgültig; 4xx/Timeout = automatische Wiederholung (bis 5 Versuche).
- STARTTLS-Zertifikatsfehler: der SMTP-Server muss ein von System-CAs vertrauenswürdiges
  Zertifikat haben; interne Relays ggf. mit `SMTP_SECURITY=none` auf Port 25 (nur im
  internen Netz).
- Mails im Spam: SPF/DKIM für die Absender-Domain von `SMTP_FROM` prüfen.

**Sonstiges**
- App startet nicht („Start abgebrochen"): `CSRF_SECRET` < 32 Zeichen, DB nicht erreichbar
  (Passwort geändert, nachdem das Volume schon initialisiert war – das Passwort gilt nur
  beim ersten Start).
- Alle Bearbeiter landen gemeinsam im Rate-Limit (429): `TRUSTED_PROXY_IPS` fehlt, alle
  Requests kommen mit der Proxy-Adresse.
- Upload 413 am Gateway: Datei größer als `UPLOAD_MAX_MB`.
- `Netzwerk … overlaps`: `ADMIN_NET_SUBNET`, `APP_ADMIN_IP`, `GATEWAY_ADMIN_IP` auf ein
  freies Subnetz ändern.
