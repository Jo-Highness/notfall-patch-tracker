# Lasttest – Notfall-Patch-Tracker

Auftrag 5.5/10: 500 gleichzeitige Nutzer mit offenen SSE-Verbindungen, realistischer
Schreibmix, ca. 2.000 Systeme je Projekt. Zielwerte: **Initialladen der Liste < 2 s**,
**Schreib-Latenz p95 < 300 ms**, **Live-Änderungen ≤ 2 s**.

## Ergebnis auf einen Blick (Lauf 4, 2026-10-27, maßgeblich)

| Zielwert | Gemessen | Bewertung |
|---|---|---|
| Initialladen der Hauptliste < 2 s | Browser (Chromium, kalter Cache, unter Last): **Median 366 ms, max 496 ms** bis Liste bedienbar; HTTP (HTML + JSON 2.080–2.236 Zeilen, 1,2 MB): p50 93 ms, p95 160 ms, p99 390 ms, max 501 ms | ✅ erreicht |
| Schreib-Latenz p95 < 300 ms | PATCH *-Feld: **p50 20 ms, p95 64 ms, p99 110 ms**, max 345 ms (5.015 Schreibvorgänge, 17,9/s); Neuanlage p95 59 ms | ✅ erreicht |
| Live ≤ 2 s (Schreiben → Empfang) | unabhängige Probe: **p50 178 ms, p95 981 ms, p99 1.156 ms, max 1.195 ms** (343 von 343 Änderungen angekommen); Locust-Stichprobe (83.745 Messwerte): p50 190 ms, p95 1.000 ms, p99 1.200 ms, max 1.294 ms | ✅ erreicht (max < 1,3 s) |
| Fehlerquote | **0 Fehler** bei 103.675 Requests/Messungen (409-Konflikte sind erwartete Antworten, s. u.); 0 SSE-Abweisungen | ✅ |
| Ressourcen | app: CPU Ø 79 % (≈ 0,8 Kern), Spitze 168 %; RAM 826–844 MB. db: CPU Ø 20 %, Spitze 37 %; RAM 182 MB. Gateway 8–15 MB | Reserve auf 4 Kernen |

## Aufbau

| Punkt | Wert |
|---|---|
| Host | Testhost: AMD Ryzen 9 PRO 6950H, 6 vCPU, 32 GB RAM, Docker 29.9.2 |
| Stack | Dev-Stack `npt-dev` (Stand dieses Commits), `WEB_WORKERS=4`, `ADMIN_WORKERS=2`, Bearbeiter-Port 127.0.0.1:8480 |
| CPU-Trennung | app, db, gateway per `docker update --cpuset-cpus 0-3`; Locust und Probes auf CPUs 4,5 (`LT_CPUSET=4,5`) |
| Daten | ein Projekt aus 2.000 generierten Stammdaten (`scripts/gen-testdata.py`); durch die Neuanlagen der Läufe wuchs es auf 2.236 Zeilen |
| Werkzeug | Locust 2.47.0, 2 Prozesse (`--processes 2`), `loadtest/locustfile.py`; Steuerung `loadtest/run-loadtest.sh`; Auswertung `loadtest/summarize.py` |
| Dauer | Hochlauf 25 Nutzer/s (20 s), Messung 5 min insgesamt, Statistik nach dem Hochlauf zurückgesetzt (`--reset-stats`) |
| Vertrauen XFF | für den Test `TRUSTED_PROXY_IPS=172.24.0.1` (Docker-Gateway des Dev-Netzes), damit jeder simulierte Nutzer mit eigener `X-Forwarded-For`-Adresse zählt (Rate-Limit je IP realistisch); danach zurückgesetzt |

### Lastprofil (500 Nutzer, alle mit offenem SSE-Stream)

| Nutzerart | Anzahl | Verhalten |
|---|---|---|
| Viewer | 430 | Hauptliste laden (HTML + `/api/systems`), SSE-Stream offen; nach 60–240 s (zufällig) **neu laden** – dadurch ständig Initialladen unter Last (≈ 2,3 Listen/s) |
| PivotViewer | 50 | Übersichtsseite + `/api/pivot`, SSE-Stream offen, Pivot nach Änderungen höchstens 1/s neu (wie der Browser) |
| Editor | 20 | eigener SSE-Stream + alle 0,5–2 s ein Schreibvorgang: Bemerkung 40 %, System betroffen 30 %, Aktion 15 %, Version 15 %; 409 → wie die UI mit aktuellem Wert erneut; etwa jede Minute eine Neuanlage |

Gleichzeitig offene SSE-Streams: 520. Requestrate gesamt ≈ 70/s plus ≈ 300 Live-
Messungen/s; Fan-out ≈ 3,4 Delta-Frames/s je Worker an je ≈ 130 Streams.

### Messverfahren

- **Initialladen (HTTP):** Pseudo-Request „Initialladen Liste (HTML+JSON)“ = HTML-Seite +
  vollständiges JSON aller Zeilen, gemessen in Locust bei jedem Neuladen eines Viewers.
- **Initialladen (Browser):** `loadtest/browser_probe.py` (Playwright/Chromium, E2E-Image)
  ruft 60 s nach dem Hochlauf zehnmal die Projekt-URL mit kaltem Cache auf und misst bis
  „x von y Systemen angezeigt“ und erste Zeile gerendert.
- **Schreib-Latenz:** Antwortzeit `PATCH /p/<token>/api/systems/<id>` in Locust.
- **Live-Latenz:**
  1. `loadtest/live_probe.py` (stdlib, ein Prozess, kaum CPU): schreibt alle 0,5 s die
     Bemerkung eines festen Systems und misst bis zum Eingang des ersten Delta-Frames mit
     diesem **oder einem neueren** Wert dieser Zeile (J11 in `DECISIONS.md`). 53 der 343
     Änderungen kamen direkt mit dem nächsten Wert (Frame länger unterwegs als das
     Schreibintervall) – der Nutzer sieht dann sofort den aktuellen Stand.
  2. Locust: jeder 10. Viewer wertet die Zeitstempel in den Bemerkungen der Editoren aus
     (gleiche Uhr, gleicher Host).
- **CPU/RAM:** `docker stats` alle 5 s für alle Container.

## Ergebnisse im Detail (Lauf 4)

| Request | Anzahl | Fehler | p50 | p95 | p99 | max | Ø Größe |
|---|---:|---:|---:|---:|---:|---:|---:|
| Initialladen Liste (HTML+JSON) | 658 | 0 | 93 | 160 | 390 | 501 | 1,24 MB |
| GET /api/systems (voll) | 658 | 0 | 62 | 120 | 290 | 339 | 1,24 MB |
| GET Hauptliste (HTML) | 652 | 0 | 9 | 49 | 120 | 198 | 13,7 KB |
| PATCH *-Feld | 5.015 | 0 | 20 | 64 | 110 | 345 | 0,6 KB |
| POST /api/systems (Neuanlage) | 59 | 0 | 24 | 59 | 100 | 101 | 0,4 KB |
| GET /api/pivot | 10.809 | 0 | 47 | 110 | 150 | 242 | 12,7 KB |
| SSE /api/events (bis Header) | 1.235 | 0 | 15 | 180 | 290 | 659 | – |
| SSE live (Locust-Stichprobe) | 83.745 | 0 | 190 | 1.000 | 1.200 | 1.294 | – |
| Live-Probe (unabhängig) | 343 | 0 | 178 | 981 | 1.156 | 1.195 | – |

Alle Zeiten in ms. 842 Schreibvorgänge (≈ 17 %) wurden mit 409 beantwortet: Zufällige
Schreiber treffen bei 2.000 Zeilen immer wieder dieselbe Zeile, deren zuletzt bekannter
Wert inzwischen veraltet ist (die Editoren lesen ihren eigenen Stream nicht aus) – genau
die Konflikterkennung aus 5.5; die Wiederholung mit aktuellem Wert gelang jeweils.

Rohdaten: `loadtest/results/2026-10-27-run4-summary.json`,
`loadtest/results/2026-10-27-run4-locust-stats.csv` (Container-Werte aller Container,
Browser- und Live-Probe).

## Verlauf und Befunde

| Lauf | Änderung | Befund |
|---|---|---|
| 1 | Erstlauf | Ziele erreicht; **7 SSE-Abweisungen (503)** trotz 800 Plätzen: neue Verbindungen verteilen sich ungleich auf die 4 Worker, einzelne erreichten die Grenze 200. Betroffene Clients verbanden nach 5 s neu (Locust-Live-Maximum 5,7 s durch dieses Nachholen) |
| 2 | Live-Probe ergänzt | 29 Abweisungen (gleiche Ursache), Live-Maximum 6,8 s |
| 3 | `SSE_MAX_PER_WORKER` 200 → 300, `UVICORN_LIMIT_CONCURRENCY` 400 → 500 (J10) | **0 Abweisungen**, Live-Maximum 1,4 s. Erste Probe-Fassung zählte überholte Werte als „verloren“ (Messfehler der Probe, J11) |
| 4 | Probe korrigiert (überholte Werte = beim nächsten Frame angekommen) | maßgeblich, siehe oben |
| 5 | Kapazitätsprüfung nach QA-Hinweis: **`WEB_WORKERS=6`, 1.000 Nutzer** (doppelte Last, J12) | 0 Fehler, 0 SSE-Abweisungen; Details unten |

### Lauf 5: 6 Worker, 1.000 Nutzer (Kapazitätsprüfung, J12)

Anlass: 500 Nutzer mit Liste **und** Übersicht in je eigenem Tab ergeben ≈ 1.000 Streams,
mit 4 Workern blieben nur 1.200 Plätze. Gleicher Aufbau wie oben, aber `WEB_WORKERS=6`
(neuer Default), `LT_USERS=1000` mit den Standardgewichten (860 Listen, 100 Übersichten,
40 Schreiber – also auch doppelt so viele Schreibvorgänge wie Lauf 4), ≈ 1.000 gleichzeitig
offene Streams, Messung 3 min nach dem Hochlauf, Projekt mit 2.001–2.071 Zeilen.

| Messgröße | Ergebnis |
|---|---|
| Fehler / SSE-Abweisungen | **0 / 0** bei 177.343 Requests/Messungen (1.013 erwartete 409) |
| Initialladen HTTP (HTML + JSON) | p50 95 ms, p95 290 ms, p99 490 ms, max 570 ms; Browser-Probe Median 449 ms, max 1.008 ms |
| PATCH *-Feld (36/s) | p50 24 ms, p95 170 ms, p99 330 ms, max 660 ms; Neuanlage p95 220 ms |
| Live (unabhängige Probe) | p50 268 ms, p95 1.179 ms, p99 1.400 ms, max 1.534 ms; 166 von 166 angekommen |
| Live (Locust-Stichprobe) | p50 300 ms, p95 1.200 ms, max 2.006 ms (Lastgenerator bis 95 % CPU, enthält dessen Wartezeit, J11) |
| app | CPU Ø 135 %, Spitze 181 %; RAM 1.007–1.066 MB (Limit 1.536 MB) |
| db | CPU Ø 32 %, Spitze 48 %; RAM 220–231 MB |

Bewertung: Mit 6 Workern trägt der Stack die doppelte Nutzerzahl ohne Abweisung; die
Schreib-Latenz bleibt mit p95 170 ms unter dem Ziel 300 ms, Live p95 ≈ 1,2 s unter 2 s. Der
RAM-Bedarf steigt gegenüber Lauf 4 um ≈ 0,2 GB und bleibt mit ≈ 70 % des Limits im Rahmen.
Rohdaten: `loadtest/results/2026-10-27-run5-6w-1000u-summary.json`,
`loadtest/results/2026-10-27-run5-6w-1000u-locust-stats.csv`.

**Woher kommt das Live-p95 von ≈ 1 s?** Ohne Last liegt die Live-Latenz bei 172–178 ms
(Bündelfenster 150 ms + Abfrage + Versand; gemessen mit derselben Probe). Unter Last
dauert ein Bündelzyklus je Worker im Mittel ≈ 290 ms (≈ 3,4 Frames/s): Die Delta-Abfrage
des Brokers läuft in einem eigenen Thread und konkurriert unter dem GIL mit den
Request-Threads, die gleichzeitig Listen (1,2 MB JSON, ≈ 2,3/s) und Pivot-Abrufe (38/s)
erzeugen. Bei gleichzeitigem Neuladen mehrerer Listen auf demselben Worker verlängern
sich einzelne Zyklen auf bis zu ≈ 1 s. Das Ziel (≤ 2 s) hält mit Abstand; Spielraum
bei Bedarf: mehr `WEB_WORKERS` (verteilt Listen-Last und Streams), Kompression am Proxy
verringert nur die Übertragungszeit, nicht die CPU-Zeit.

## Einschränkungen

- **Lastgenerator auf demselben Host wie die App** (Testhost), getrennt über CPU-Pinning
  (App/DB auf 4 Kernen, Locust und Probes auf 2 Kernen; Locust nutzte Ø 0,39, Spitze 0,74
  Kerne, also keine Sättigung). Alternativen geprüft und verworfen: ein zweiter Host hatte 0,9 GB
  freien Speicher, Swap voll und Load ≈ 7 auf 2 Kernen (hätte Messung und Host gefährdet);
  andere Hosts hätten den Dev-Stack nur über einen zusätzlich veröffentlichten Host-Port
  oder SSH-Port-Forwarding erreicht (auf dem Testhost `AllowTcpForwarding no`, zusätzliche
  Host-Ports widersprechen der Betriebsvorgabe). Folge: keine Netzwerklatenz zwischen Client und
  Server (im LAN typischerweise < 1 ms) und kein TLS-Proxy im Pfad – beides fällt gegen die
  gemessenen Werte nicht ins Gewicht; die Übertragung der 1,2 MB-Liste über ein langsames
  Netz kommt hinzu (mit gzip am Proxy ≈ 0,15 MB).
- Die Verbindungen laufen über den Docker-Userland-Proxy des veröffentlichten
  loopback-Ports (wie im Betrieb hinter einem Reverse Proxy auf demselben Host).
- Benachbarte produktive Container (open-webui, parts-inventory-*, wmmi-datalayer-postgis)
  liefen weiter (CPU je < 11 %); vor, während und nach den Läufen blieben alle healthy.
- Gemessen wurde ein Projekt; mehrere gleichzeitig aktive Projekte teilen sich die
  Kapazität (jede Delta-Abfrage ist je Projekt eine Abfrage je Worker).

## Wiederholen

```bash
# Projekt mit 2.000 Systemen anlegen (Admin-Bereich), Projektpfad /p/<token> notieren
# App muss der Absenderadresse fuer X-Forwarded-For vertrauen (nur fuer den Test):
#   TRUSTED_PROXY_IPS=<Gateway-IP von <projekt>_public_net> in .env, ./deploy.sh
docker update --cpuset-cpus 0-3 <projekt>-app-1 <projekt>-db-1 <projekt>-admin-gateway-1
LT_CPUSET=4,5 LT_HOST=http://127.0.0.1:8480 loadtest/run-loadtest.sh /p/<token> /tmp/lt
# danach: TRUSTED_PROXY_IPS zuruecksetzen, ./deploy.sh (setzt auch das CPU-Pinning der App zurueck),
#         docker update --cpuset-cpus 0-<n> fuer db/gateway
```

Variablen: `LT_USERS` (500), `LT_SPAWN` (25), `LT_TIME` (5m), `LT_PROCESSES` (2),
`LT_PROBE` (Browser-Probe, 1), `LT_LIVE_PROBE` (1), `LT_PROBE_DURATION` (120 s); Gewichte
und Intervalle der Nutzerarten über `NPT_W_*`, `NPT_RELOAD_*`, `NPT_CREATE_EVERY_S`
(siehe Kopf von `loadtest/locustfile.py`).
