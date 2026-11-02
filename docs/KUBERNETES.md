# Kubernetes-Betrieb (Helm-Chart)

Der Notfall-Patch-Tracker lässt sich neben Docker Compose auch auf Kubernetes betreiben.
Grundlage ist das Helm-Chart `deploy/helm/notfall-patch-tracker` (Entscheidung NPT-12,
Abschnitt K in `docs/DECISIONS.md`). Das Chart ist generisch: alle Hostnamen, Adressen und
Registry-Pfade in dieser Anleitung sind Beispiele (`npt.example.org`, `192.0.2.0/24`).

## 1. Überblick

```
                 Bearbeiter (Browser)                       Admins (Client-Zertifikat)
                        │ HTTPS                                      │ mTLS (L4 bis zum Pod)
              Ingress / Gateway-Controller                LoadBalancer / TLSRoute (Passthrough)
                        │ HTTP :8000                                 │ :8443
   ┌────────────────────▼─────────────────────┐   ┌─────────────────▼──────────────────────┐
   │ Deployment <release>-public (3 Pods)      │   │ Deployment <release>-backend (1 Pod)   │
   │  initContainer migrate (Advisory Lock)    │   │  initContainer migrate                 │
   │  app: APP_ROLE=public, WEB_WORKERS=2      │   │  gateway (nginx, mTLS) ─► 127.0.0.1:8001│
   └────────────────────┬─────────────────────┘   │  app: APP_ROLE=admin,jobs              │
                        │                         │  PVC backups (/data/backups, RWO)       │
                        │                         └─────────────────┬──────────────────────┘
                        └──────────────► PostgreSQL 16 (extern) ◄───┘
```

| Baustein | Inhalt |
|---|---|
| `public` | Bearbeiter-App, beliebig viele Replikas (Default 3 × 2 Worker = 1.800 SSE-Plätze), RollingUpdate ohne Surge mit `minReadySeconds` (Grace + 5 s), PodDisruptionBudget, optional HPA |
| `backend` | genau **ein** Pod: Admin-App und Hintergrundjobs (Backups, Mailversand, Stammdaten-API) im Container `app`, das mTLS-Gateway als Sidecar `gateway`; Strategie `Recreate` |
| Migration | `initContainer` `migrate` in jedem App-Pod; Advisory-Lock-Schleife (bis 300 s), Admin-Bootstrap im Lock; Hauptcontainer starten mit `NPT_SKIP_MIGRATE=1` |
| Live-Updates | `LISTEN/NOTIFY` wirkt datenbankweit und damit Pod-übergreifend; keine Sticky Sessions nötig |
| Datenbank | extern (eigener Betrieb oder Operator, Beispiel CloudNativePG); kein Subchart |
| Backups | Dateien auf einem PVC (RWO) im backend-Pod; Datenbank-Backup/PITR ist Sache des Betreibers |

Warum kein eigenes Deployment für die Jobs: Backup-Dateien schreiben sowohl die Admin-App
(vor Import, manuell, vor Wiederherstellung) als auch die Jobs (zeitgesteuert); die Admin-App
liest sie (Download, Restore). Mehrere Pods bräuchten ein RWX-Volume, mehrere RWO-Volumes
wären ein Split-Brain. Endet ein Prozess im backend-Pod, endet der Container und kubelet
startet ihn neu (gleiches Supervisor-Verhalten wie unter Compose).

## 2. Voraussetzungen

| Was | Anforderung |
|---|---|
| Kubernetes | ≥ 1.29, CNI mit NetworkPolicy-Unterstützung (getestet: kind 0.33 / Kubernetes 1.37 mit kindnet) |
| Helm | 3.x oder 4.x (getestet: 4.3.1, Lint zusätzlich mit 3.22.1) |
| PostgreSQL | 16, erreichbar aus dem Namespace, `max_connections` nach Budget (Abschnitt 8), Log-Härtung (Abschnitt 7) |
| Bearbeiter-Zugang | Ingress-Controller oder Gateway-API-Implementierung mit SSE-tauglicher Konfiguration (Abschnitt 5) |
| Admin-Zugang | L4-Weg bis zum Pod: Service `LoadBalancer` (MetalLB, Cloud-LB) oder Gateway-API `TLSRoute` mit Passthrough (Abschnitt 6) |
| PKI | Serverzertifikat des Gateways (z. B. cert-manager), CA der Admin-Client-Zertifikate, optional CRL |
| Abbilder | App- und Gateway-Abbild in einer Registry (Abschnitt 3) |

## 3. Abbilder

```bash
VERSION=0.2.0
REG=ghcr.io/<owner>
docker build --target runtime -t $REG/notfall-patch-tracker:$VERSION \
  --build-arg NPT_REVISION="$(git rev-parse HEAD)" .
docker build -t $REG/notfall-patch-tracker-gateway:$VERSION gateway
docker push $REG/notfall-patch-tracker:$VERSION
docker push $REG/notfall-patch-tracker-gateway:$VERSION
# Digests für die Werte-Datei (image.*.digest)
docker buildx imagetools inspect $REG/notfall-patch-tracker:$VERSION --format '{{json .Manifest.Digest}}'
```

- Tags immer SemVer, nie `latest` (das Chart bricht mit `latest` ab).
- Im Betrieb mit Digest ausrollen (`image.app.digest`, `image.gateway.digest`): das Abbild
  ist dann unveränderlich, `deploy.sh` prüft nach dem Rollout je Pod, dass genau dieser
  Digest läuft.
- Die SBOM (`docs/SBOM.md`) aus genau dem veröffentlichten Abbild erzeugen.

## 4. Installation

### 4.1 Namespace

```bash
kubectl create namespace npt
kubectl label namespace npt pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/warn=restricted
```

Alle Pods des Charts erfüllen PodSecurity `restricted` (non-root, read-only Root-Dateisystem,
keine Capabilities, seccomp `RuntimeDefault`, kein Service-Account-Token).

### 4.2 Secrets (Anwendung)

Ein Secret mit diesen Schlüsseln (Namen über `secrets.keys.*` änderbar), angelegt aus dem
eigenen Secret-Store (External Secrets, Sealed Secrets, Vault-Agent o. ä.) – nie im Git:

| Schlüssel | Pflicht | Inhalt |
|---|---|---|
| `CSRF_SECRET` | ja | ≥ 32 Zeichen Zufall (`openssl rand -hex 32`) |
| `POSTGRES_PASSWORD` | ja, außer `DATABASE_URL` | Passwort des DB-Benutzers |
| `DATABASE_URL` | alternativ | vollständige URL (`postgres://`, `postgresql://` oder `postgresql+psycopg://`) |
| `SMTP_PASSWORD` | nein | SMTP-Passwort |
| `MASTER_API_TOKEN` | nein | Token der Stammdaten-API |

```bash
kubectl -n npt create secret generic npt-app \
  --from-literal=CSRF_SECRET="$(openssl rand -hex 32)" \
  --from-literal=POSTGRES_PASSWORD='<aus dem Secret-Store>'
```

Werte: `secrets.existingSecret: npt-app`. Liegt der DB-Zugang in einem eigenen Secret
(z. B. vom Datenbank-Operator), zeigen `database.existingSecret` und `database.passwordKey`
bzw. `database.urlKey` darauf (`urlKey` hat Vorrang). `secrets.create: true` mit
`secrets.values` ist nur für Dev/Test gedacht: die Werte stehen dann im Helm-Release und mit
`helm template`/GitOps im gerenderten Manifest.

### 4.3 PKI des Admin-Gateways

| Wert | Inhalt |
|---|---|
| `admin.tls.secretName` | Secret mit Serverzertifikat (`tls.crt`, Kette) und Schlüssel (`tls.key`), z. B. von cert-manager |
| `admin.clientCa.configMapName` **oder** `.secretName` | CA-Bundle der Admin-Client-Zertifikate (Schlüssel `ca.crt`) |
| `admin.crl.enabled` + `.secretName`/`.configMapName` | optionale CRL (Schlüssel `crl.pem`); konfiguriert, aber fehlend → Gateway startet nicht |

Beispiel mit cert-manager:

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata: { name: npt-admin-tls, namespace: npt }
spec:
  secretName: npt-admin-tls
  dnsNames: [npt-admin.example.org]
  privateKey: { algorithm: ECDSA, size: 256, rotationPolicy: Always }
  issuerRef: { kind: ClusterIssuer, name: <eigener-issuer> }
```

```bash
kubectl -n npt create configmap npt-client-ca --from-file=ca.crt=admin-client-ca.pem
kubectl -n npt create secret generic npt-crl --from-file=crl.pem=admin-client-ca.crl.pem
```

Die Dateien werden als Verzeichnis (ohne `subPath`) eingehängt. Die Reload-Schleife des
Gateways vergleicht alle `backend.gateway.crlReloadIntervalS` Sekunden (Default 60) den
Inhalt von Zertifikat, Schlüssel, CA-Bundle und CRL und lädt nginx bei Änderung ohne
Neustart neu (`nginx -t` vorher; schlägt er fehl, bleibt der alte Stand aktiv). Zusammen
mit der Verzögerung, mit der kubelet Secret-Änderungen in Pods sichtbar macht (bis etwa
60–90 s), wirkt eine Rotation spätestens nach etwa 3 Minuten (gemessen auf kind: CRL 85 s,
Serverzertifikat 69 s bei 10-s-Intervall).

### 4.4 Werte-Datei und Ausrollen

Minimal (Rest siehe `values.yaml` mit Kommentaren):

```yaml
image:
  app:     { registry: ghcr.io, repository: <owner>/notfall-patch-tracker,         tag: "0.2.0", digest: "sha256:…" }
  gateway: { registry: ghcr.io, repository: <owner>/notfall-patch-tracker-gateway, tag: "0.2.0", digest: "sha256:…" }
publicBaseUrl: https://npt.example.org
adminBaseUrl: https://npt-admin.example.org
config:
  adminBootstrapIds: ["CN=Erste Admin,OU=IT,O=Beispiel"]
  smtp: { host: smtp.example.org, user: npt, from: "Notfall-Patch-Tracker <npt@example.org>" }
secrets:
  existingSecret: npt-app
database:
  host: npt-db-rw
  maxConnections: 200
public:
  trustedProxyCIDRs: [10.244.0.0/16]      # Pod-Netz der Controller-Pods
admin:
  service: { type: LoadBalancer, loadBalancerSourceRanges: [192.0.2.0/24] }
  tls: { secretName: npt-admin-tls }
  clientCa: { configMapName: npt-client-ca }
  crl: { enabled: true, secretName: npt-crl }
ingress:
  enabled: true
  className: nginx
  hosts: [{ host: npt.example.org }]
  tls: [{ secretName: npt-public-tls, hosts: [npt.example.org] }]
networkPolicy:
  ingressController:
    namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: ingress-nginx } }
  adminSourceCIDRs: [192.0.2.0/24]
  database:
    podSelector: { matchLabels: { cnpg.io/cluster: npt-db } }
  smtp: { cidrs: [192.0.2.25/32] }
```

```bash
deploy/helm/deploy.sh -n npt -f values-npt.yaml            # lint, template, Budget, install, Abbild-Abgleich, helm test
deploy/helm/deploy.sh -n npt -f values-npt.yaml --dry-run  # nur Prüfungen
```

`deploy.sh` führt aus: `helm lint --strict`, `helm template` (Pflichtwerte, Schema,
Verbindungsbudget), `kubeconform -strict` (falls installiert; `SKIP_KUBECONFORM=1` ohne
Internet), `helm upgrade --install --wait`, wartet terminierende Pods ab, vergleicht je Pod
und Container das laufende Abbild (`imageID`) mit dem gerenderten (Digest, sonst Gleichheit
über alle Pods) und startet `helm test` (public `/healthz` = 200, Gateway ohne
Client-Zertifikat = 403).

### 4.5 Wichtige Werte

| Wert | Default | Bedeutung |
|---|---|---|
| `publicBaseUrl` / `adminBaseUrl` | – (Pflicht) | Origins für CSRF/Origin-Prüfung und Links in Mails; Admin nur `https://` |
| `public.replicas` / `public.webWorkers` | 3 / 2 | SSE-Plätze = Replikas × Worker × 300 |
| `public.trustedProxyCIDRs` | `[]` | Quell-CIDRs der Controller-Pods (`TRUSTED_PROXY_IPS`); Pflicht mit `ingress`/`httpRoute` |
| `public.hpa.*` | aus | CPU 70 %, `minReplicas` = `replicas`, Abbau-Stabilisierung 300 s; `maxReplicas` zählt ins Budget |
| `backend.adminWorkers` | 2 | Gunicorn-Worker der Admin-App |
| `backend.gateway.workerProcesses` | 2 | nginx-Worker (statt `auto` = ein Worker je Knoten-CPU) |
| `backend.persistence.*` | 5Gi RWO | Backup-PVC; `existingClaim` möglich; bleibt bei `helm uninstall` erhalten |
| `database.tls.*` | leer | `sslmode`, CA/Client-Zertifikat aus einem Secret (`/etc/npt-db`) |
| `database.maxConnections` / `budgetReserve` / `budgetCheck` | 200 / 10 / true | Verbindungsbudget (Abschnitt 8) |
| `admin.service.type` | ClusterIP | `LoadBalancer` (mit `externalTrafficPolicy: Local`), `NodePort` nur für Tests |
| `admin.tlsRoute.*` | aus | Gateway-API-TLSRoute (Passthrough), `apiVersion` einstellbar |
| `ingress.*` / `httpRoute.*` | aus | Bearbeiter-Zugang, genau eines von beiden |
| `networkPolicy.*` | an | Default-Deny plus Freigaben (Abschnitt 9) |

Neu gegenüber Compose sind die Umgebungsvariablen `NPT_SKIP_MIGRATE`, `NPT_SCHEMA_NEWER`
(`fail`|`warn`, Chart: `warn`), `JOBS_HEARTBEAT_FILE`, `POSTGRES_SSLMODE`,
`POSTGRES_SSLROOTCERT`, `POSTGRES_SSLCERT`, `POSTGRES_SSLKEY`, `GATEWAY_WORKER_PROCESSES`
sowie `APP_ROLE` als Komma-Liste (`admin,jobs`). Das Chart setzt sie selbst.

## 5. Bearbeiter-Zugang: Controller-Beispiele

Anforderungen an jeden Controller (K4):

- **SSE** `^/p/[^/]+/api/events$`: keine Pufferung, Timeout ≥ 3600 s, Leerlauf-Timeout
  > 15 s (Heartbeat alle 15 s), keine gzip-Kompression für `text/event-stream`.
  Die App sendet `X-Accel-Buffering: no` und `Cache-Control: no-store`.
- **Body** ≤ 1 MiB genügt (die App begrenzt den Bearbeiter-Port selbst, J3).
- **Log-Maskierung:** Der Projekt-Token steht im Pfad. Das Access-Log des Controllers darf
  den Pfad nicht enthalten (abschalten oder Feld entfernen).
- **Client-IP:** Der Controller muss die echte Client-IP sehen
  (`externalTrafficPolicy: Local` an seinem Service oder PROXY-Protokoll) und als
  `X-Forwarded-For` weitergeben; sonst zählt das Rate-Limit alle Bearbeiter als eine IP.
  `public.trustedProxyCIDRs` = Pod-Netz der Controller-Pods; die NetworkPolicy lässt
  Port 8000 nur aus dem Controller-Namespace zu, damit niemand sonst `X-Forwarded-For`
  setzen kann. Keine Sticky Sessions nötig.

### ingress-nginx

```yaml
ingress:
  enabled: true
  className: nginx
  hosts: [{ host: npt.example.org }]
  tls: [{ secretName: npt-public-tls, hosts: [npt.example.org] }]
networkPolicy:
  ingressController:
    namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: ingress-nginx } }
```

Bei `className: nginx` setzt das Chart selbst `proxy-read-timeout`/`proxy-send-timeout`
= `ingress.sseTimeoutSeconds` (3600), `proxy-body-size: 1m` und
`enable-access-log: "false"`; eigene `ingress.annotations` haben Vorrang. Pufferung schaltet
der `X-Accel-Buffering`-Header der App ab. Controllerweit (ConfigMap des Controllers):
`use-gzip` aus lassen oder `text/event-stream` nicht in `gzip-types` aufnehmen;
`error-log-level: crit` erwägen – das Error-Log nennt bei Upstream-Fehlern die
Request-Zeile samt Token und ist nur controllerweit steuerbar (Restrisiko). Der Service des
Controllers mit `externalTrafficPolicy: Local`. Hinweis: Das Projekt ingress-nginx ist seit
März 2026 eingestellt; für neue Installationen Gateway API oder einen gepflegten Controller
wählen.

### Traefik

```yaml
# Chart-Werte
ingress:
  enabled: true
  className: traefik
  hosts: [{ host: npt.example.org }]
networkPolicy:
  ingressController:
    namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: traefik } }
```

```yaml
# Werte des Traefik-Helm-Charts (v3): Access-Log ohne Pfad und ohne Header
accessLog:
  enabled: true
  format: json
  fields:
    defaultMode: keep
    names: { RequestPath: drop }
    headers: { defaultMode: drop }
service:
  spec: { externalTrafficPolicy: Local }
```

Traefik puffert Antworten nicht und reicht SSE direkt durch (im kind-Test geprüft:
Live-Ereignis in 190 ms, Access-Log ohne Token). **Keine** `buffering`-Middleware
verwenden (sie puffert auch Antworten und hält SSE auf); wer die `compress`-Middleware
nutzt, nimmt `text/event-stream` in `excludedContentTypes` auf. Traefik ersetzt `X-Forwarded-For` von nicht
vertrauten Clients (`forwardedHeaders.trustedIPs` leer lassen bzw. nur echte vorgelagerte
Proxys eintragen).

### Envoy Gateway (Gateway API)

```yaml
# Chart-Werte
httpRoute:
  enabled: true
  parentRefs: [{ name: eg, namespace: envoy-gateway-system }]
  hostnames: [npt.example.org]
  sseTimeout: 3600s          # HTTPRoute timeouts.request; Default 15 s würde SSE kappen
networkPolicy:
  ingressController:
    namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: envoy-gateway-system } }
```

```yaml
# Access-Log ohne %REQ(:PATH)% und Client-IP-Erhalt (Gateway.spec.infrastructure.parametersRef)
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: EnvoyProxy
metadata: { name: npt-proxy, namespace: envoy-gateway-system }
spec:
  provider:
    type: Kubernetes
    kubernetes:
      envoyService: { externalTrafficPolicy: Local }
  telemetry:
    accessLog:
      settings:
        - format:
            type: JSON
            json:
              start_time: "%START_TIME%"
              method: "%REQ(:METHOD)%"
              status: "%RESPONSE_CODE%"
              duration_ms: "%DURATION%"
              upstream: "%UPSTREAM_HOST%"
              client: "%DOWNSTREAM_REMOTE_ADDRESS_WITHOUT_PORT%"
          sinks:
            - type: File
              file: { path: /dev/stdout }
```

Envoy hält Streams standardmäßig bis zu 5 min ohne Daten offen (`stream_idle_timeout`);
der 15-s-Heartbeat bleibt darunter. Mit `ClientTrafficPolicy` lässt sich der
Leerlauf-Timeout bei Bedarf anheben und die XFF-Auswertung (`clientIPDetection`)
festlegen. Envoy komprimiert nur mit eigener Kompressions-Policy; dort `text/event-stream`
ausnehmen. Die Envoy-Beispiele sind nicht auf kind getestet.

## 6. Admin-Zugang (L4 / Passthrough)

TLS (mTLS) endet **nur** im Gateway-Sidecar. Davor darf nichts TLS terminieren – kein
`auth-tls-*` am Ingress, kein Service-Mesh-mTLS dazwischen. Die App vertraut dem
Admin-Port (127.0.0.1:8001) nur aus dem eigenen Pod (`ADMIN_GATEWAY_CIDR=127.0.0.1/32`);
das Chart schaltet die Mesh-Injektion für den backend-Pod deshalb ab
(`sidecar.istio.io/inject: "false"`, `linkerd.io/inject: disabled`) und lässt keine
weiteren Container zu.

| Variante | Werte | Hinweis |
|---|---|---|
| LoadBalancer (empfohlen) | `admin.service.type: LoadBalancer`, `externalTrafficPolicy: Local`, `loadBalancerSourceRanges`, optional `loadBalancerIP` | Client-IP bleibt erhalten (Audit, `networkPolicy.adminSourceCIDRs`) |
| Gateway API TLSRoute | `admin.tlsRoute.enabled`, `parentRefs` (Listener `protocol: TLS`, `tls.mode: Passthrough`), `hostnames` | TLSRoute-CRD aus dem Experimental-Kanal; der Gateway-Pod ist Absender → in `networkPolicy.adminIngressFrom` freigeben; Audit-IP = Gateway-Pod |
| Ingress mit SSL-Passthrough | nicht vorgesehen | ingress-nginx `--enable-ssl-passthrough` verliert die Client-IP |
| NodePort | `admin.service.type: NodePort`, `nodePort` | nur Tests (z. B. kind) |

`adminBaseUrl` muss genau der Origin sein, den Browser der Admins verwenden (inkl. Port,
falls nicht 443).

## 7. Datenbank: Pflichten des Betreibers

- PostgreSQL 16, eigener Benutzer ohne Superuser-Rechte, Eigentümer der Datenbank.
- Log-Härtung (Token und Personendaten nie ins Postgres-Log, wie in `docker-compose.yml`):
  `log_parameter_max_length=0`, `log_parameter_max_length_on_error=0`,
  `log_error_verbosity=terse`. Das Chart kann das bei einer externen DB nicht erzwingen.
- `max_connections` ≥ Verbindungsbudget (Abschnitt 8).
- Backups mit Point-in-Time-Recovery (z. B. CloudNativePG mit Barman-Cloud-Plugin).
- Kein PgBouncer im Transaction-Modus (LISTEN/NOTIFY, Advisory Locks und
  Session-Einstellungen brauchen feste Sitzungen); Session-Modus ist möglich, zählt aber
  ins Budget.
- TLS: `database.tls.sslmode: verify-full` mit der CA aus einem Secret
  (`database.tls.secretName`, `rootCertKey`); optional Client-Zertifikat (`certKey`,
  `keyKey`).
- **Migrationen sind expand/contract:** Jede Version muss mit dem Schema der nächsten
  laufen (N-1-kompatibel). Spalten/Tabellen werden erst eine Version nach dem letzten Lesen
  entfernt. Nur so funktionieren RollingUpdate und `helm rollback`.

Beispiel CloudNativePG: `deploy/helm/notfall-patch-tracker/examples/cnpg-cluster.yaml`
(drei Instanzen, Log-Parameter, `max_connections`, Werte-Ausschnitt im Kopf der Datei).
Das Operator-Secret `<cluster>-app` enthält `uri` (`postgresql://…`); das Chart wandelt
das Schema in `postgresql+psycopg://` um.

## 8. Sizing und Verbindungsbudget

Je Worker-Prozess hält die App einen Pool (10 + 5 Überlauf), eine LISTEN-Verbindung und eine
Gesundheits-Probe. Das Chart rechnet:

```
(P_max + 1) × webWorkers × 18  +  adminWorkers × 16  +  6 (Jobs)  +  2 (Migration)
  + budgetReserve  ≤  database.maxConnections
P_max = public.hpa.maxReplicas (HPA an) bzw. public.replicas
+1    = auslaufender Pod beim Rollout (max(1, maxSurge + maxUnavailable), fest 0/1)
Default: (3 + 1) × 2 × 18 + 2 × 16 + 8 + 10 = 194 ≤ 200
```

Warum `+1`: Beim RollingUpdate (maxSurge 0, maxUnavailable 1) hält ein auslaufender
public-Pod seine Verbindungen bis zum Ende von `terminationGracePeriodSeconds` (Default
40 s inkl. 5 s preStop), während sein Ersatz schon startet. Die public-Deployment setzt
`minReadySeconds` = `terminationGracePeriodSeconds` + 5 s (Default 45 s): Der nächste alte
Pod wird erst beendet, wenn der vorige sicher weg ist; mehr als ein auslaufender Pod
überlagert sich so beim Rollout nicht. Ein Rollout dauert dadurch je Pod etwa 45 s
länger (3 Pods ≈ 2,5 min, bei HPA-Maximum 6 ≈ 5 min; `deploy.sh` wartet bis 10 min).
Ein Knoten-Drain (`kubectl drain`) beachtet nur das PodDisruptionBudget, nicht
`minReadySeconds` – mehrere Evictions kurz hintereinander können kurzzeitig weitere
auslaufende Pods bedeuten; dafür ist `budgetReserve` da (bei knappem Budget Knoten
nacheinander leeren).

Überschreitet das Budget `database.maxConnections`, bricht `helm template/install` mit der
Rechnung ab (`database.budgetCheck: false` schaltet das ab – nur, wenn die Datenbank wirklich
mehr Verbindungen erlaubt; dann besser `maxConnections` anpassen).

| Größe | Faustwert |
|---|---|
| SSE-Plätze | Replikas × Worker × 300 (Default 1.800) |
| public-Pod | 250m / 320Mi Request, 640Mi Limit (2 Worker) |
| backend `app` | 200m / 384Mi, 1Gi Limit (Excel-Importe) |
| backend `gateway` | 20m / 32Mi, 128Mi Limit |
| migrate | 50m / 128Mi, 256Mi Limit |

CPU-Limits setzt das Chart bewusst nicht (Drosselung verlängert Antwortzeiten und
Heartbeats). SSE-Verbindungen sind meist untätig; ein CPU-basierter HPA reagiert auf viele
offene Streams kaum – Replikas eher nach erwarteter Gleichzeitigkeit festlegen.

## 9. Sicherheit

- PodSecurity `restricted` für alle Pods; App als UID 10001, Gateway als UID 101 (nginx
  läuft vollständig ohne root, ohne Capabilities), `fsGroup` 10001 für das Backup-PVC
  (`fsGroupChangePolicy: OnRootMismatch`).
- `automountServiceAccountToken: false`, `enableServiceLinks: false`,
  `shareProcessNamespace: false`.
- NetworkPolicies (Default an): Default-Deny für alle Pods des Releases; public Port 8000 nur
  aus `networkPolicy.ingressController` (und vom `helm test`-Pod); backend Port 8443 nur aus
  `adminSourceCIDRs`/`adminIngressFrom`; Egress nur DNS, Datenbank, beim backend SMTP und
  optional die Stammdaten-API (`extraEgress` für Weiteres). Datenbank-Selektoren beziehen
  sich auf Pods (NetworkPolicies sehen keine Service-IPs).
- Secrets nur per `secretKeyRef`, einzeln je Schlüssel; Validierungsfehler der App nennen
  keine Werte.

## 10. Betrieb

**Rollout.** public: RollingUpdate ohne Surge, jeweils ein Pod weniger (PDB
`maxUnavailable: 1`), `preStop` 5 s, `terminationGracePeriodSeconds` 40 s; offene SSE-Streams
werden beim Beenden nach 5 s geschlossen und verbinden sich mit `Last-Event-ID` neu (kein
Verlust, auf kind geprüft). backend: `Recreate` – während des Rollouts sind Admin-Bereich,
Mailversand und Backups kurz nicht verfügbar; ein laufender Excel-Import bricht ab.

**Migration und Rollback.** Jeder neue Pod migriert im initContainer unter Advisory Lock;
weitere Pods warten bis 300 s auf den Lock statt abzubrechen. `helm rollback` auf eine
ältere Version: deren initContainer finden eine ihnen unbekannte (neuere) Revision und
starten wegen `NPT_SCHEMA_NEWER=warn` ohne Migration und ohne Bootstrap (Log-Warnung
„Datenbank-Revision unbekannt“). Unter Compose bleibt `fail` der Default.

**Backups.** Zwei Ebenen: PostgreSQL (Betreiber, PITR) und die Projekt-Backups der App
auf dem PVC `<release>-backups` (Volume-Snapshots oder Dateisicherung durch den Betreiber).
Das PVC trägt `helm.sh/resource-policy: keep`. Wird der backend-Pod auf einen anderen
Knoten verlegt, dauert das Umhängen eines RWO-Volumes je nach Speicher bis zu etwa 6 Minuten.

**PKI-Rotation.** Server- und CA-Wechsel per Secret/ConfigMap, CRL-Aktualisierung per
Secret: wirkt ohne Neustart nach etwa 1–3 Minuten. Eine abgelaufene CRL sperrt alle Admins
(regelmäßig erneuern).

**Gesundheit.** public: `startupProbe` `/readyz` (bis 120 s), Readiness/Liveness `/healthz`.
backend `app`: `python -m npt.probe` (Admin-Port per TCP, Jobs-Heartbeat ≤ 120 s alt);
`gateway`: `wget http://127.0.0.1:8080/gw-health`.

**Aufräumen.** Der Jobs-Prozess löscht stündlich Gesundheits-Proben (`health_probe`)
beendeter Pods, die älter als 24 h sind.

## 11. Risiken

| Risiko | Einordnung / Gegenmaßnahme |
|---|---|
| backend ist ein Single Point | Rollout oder Knotenausfall unterbricht Admin, Mailversand und Backups; RWO-Reattach bis ~6 min; laufende Excel-Importe brechen ab. Bearbeiter (public) sind nicht betroffen. |
| Token in Controller-Logs | controllerabhängig; Access-Log ohne Pfad konfigurieren, Error-Log (ingress-nginx) nur controllerweit steuerbar |
| XFF-Fälschung ohne NetworkPolicy | ohne Policy kann jeder Pod `X-Forwarded-For` setzen (Rate-Limit/Audit-IP) |
| Client-IP-Erhalt | ohne `externalTrafficPolicy: Local` bzw. PROXY-Protokoll gilt das Rate-Limit global |
| Klartext im Pod-Netz | Controller → public und App → DB ohne TLS, sofern nicht `database.tls` bzw. ein CNI mit Verschlüsselung genutzt wird; TLS am Bearbeiter-Port ist eine Folge-Option |
| expand/contract | dauerhafte Disziplin für jede Migration |
| PKI-Latenz | Sperrung/Rotation wirkt erst nach ~3 min |
| Postgres-Log-Härtung | bei externer DB nicht durch das Chart erzwingbar |

## 12. Test auf kind

Reproduzierbarer Test aller Punkte aus NPT-12 (ohne Host-Ports, ohne sudo):

```bash
tests/k8s/tools.sh                       # kind v0.33.0, helm v4.3.1 (+ v3.22.1), kubectl v1.37.2,
                                         # kubeconform v0.8.0 nach ~/npt-k8s-tools (sha256-geprüft)
docker build --target runtime -t notfall-patch-tracker:k8s .
docker build -t notfall-patch-tracker-gateway:k8s gateway
docker build -t npt-e2e tests/e2e
tests/k8s/kind-test.sh                   # Cluster npt-kind (1+2 Knoten), Phasen 1-11
tests/k8s/kind-test.sh cleanup           # Cluster löschen
```

Das Skript legt die Test-PKI (`scripts/gen-test-pki.sh`) in einem Temp-Verzeichnis an, lädt
die Abbilder per `kind load`, installiert eine Wegwerf-Datenbank (`tests/k8s/postgres.yaml`)
und Traefik als Edge-Proxy (NodePort nur im Docker-Netz `kind`) und prüft: Lint/Schema/Budget,
PodSecurity, gleichzeitige Erstmigration, Bearbeiter-API, SSE über Pod-Grenzen und nach
Pod-Löschung, mTLS inklusive CRL- und Zertifikatstausch ohne Neustart, Isolation des
Admin-Ports, Backups über Pod-Neustart, XFF-Vertrauen, `helm rollback` und Token-freie Logs.
Ergebnis des letzten Laufs: `docs/DECISIONS.md`, K12.
