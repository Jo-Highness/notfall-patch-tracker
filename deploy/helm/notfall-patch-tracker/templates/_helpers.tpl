{{/* Namen und Labels */}}
{{- define "npt.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "npt.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "npt.selectorLabels" -}}
app.kubernetes.io/name: {{ include "npt.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "npt.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{ include "npt.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: notfall-patch-tracker
{{- end -}}

{{- define "npt.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "npt.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* Abbild: registry/repository[:tag][@digest]; Tag-Default appVersion, nie latest. */}}
{{- define "npt.image" -}}
{{- $img := index .root.Values.image .kind -}}
{{- if not $img.repository -}}
{{- fail (printf "image.%s.repository fehlt" .kind) -}}
{{- end -}}
{{- $tag := default .root.Chart.AppVersion $img.tag | toString -}}
{{- if eq $tag "latest" -}}
{{- fail (printf "image.%s.tag: latest ist nicht erlaubt (SemVer-Tag bzw. Digest)" .kind) -}}
{{- end -}}
{{- $repo := ternary (printf "%s/%s" $img.registry $img.repository) $img.repository (ne $img.registry "") -}}
{{- if $img.digest -}}
{{- if not (hasPrefix "sha256:" $img.digest) -}}
{{- fail (printf "image.%s.digest muss mit sha256: beginnen" .kind) -}}
{{- end -}}
{{- printf "%s:%s@%s" $repo $tag $img.digest -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{/* Secrets */}}
{{- define "npt.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-app" (include "npt.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "npt.dbSecretName" -}}
{{- default (include "npt.secretName" .) .Values.database.existingSecret -}}
{{- end -}}

{{/* Maximale Zahl gleichzeitiger public-Pods (HPA-Obergrenze bzw. feste Replikas). */}}
{{- define "npt.publicMax" -}}
{{- if .Values.public.hpa.enabled -}}
{{- int .Values.public.hpa.maxReplicas -}}
{{- else -}}
{{- int .Values.public.replicas -}}
{{- end -}}
{{- end -}}

{{/* RollingUpdate der public-Deployment (fest, K5); eine Quelle fuer Strategie und Budget. */}}
{{- define "npt.publicMaxSurge" -}}0{{- end -}}
{{- define "npt.publicMaxUnavailable" -}}1{{- end -}}

{{/*
Zusaetzliche public-Pods mit DB-Verbindungen waehrend eines Rollouts (K5): ein auslaufender
Pod haelt seine Verbindungen bis zum Ende von terminationGracePeriodSeconds, waehrend der
Ersatz-Pod schon startet. minReadySeconds (> Grace) verhindert, dass sich mehrere auslaufende
Pods ueberlagern; damit sind es hoechstens maxSurge + maxUnavailable, mindestens 1.
*/}}
{{- define "npt.publicRolloutExtra" -}}
{{- max 1 (add (int (include "npt.publicMaxSurge" .)) (int (include "npt.publicMaxUnavailable" .))) -}}
{{- end -}}

{{/* minReadySeconds der public-Deployment: Grace + 5 s (siehe npt.publicRolloutExtra). */}}
{{- define "npt.publicMinReadySeconds" -}}
{{- add (int .Values.public.terminationGracePeriodSeconds) 5 -}}
{{- end -}}

{{/* DB-Verbindungsbudget (K5): (P_max + Rollout-Zusatz) x webWorkers x 18 + ... */}}
{{- define "npt.dbBudget" -}}
{{- $extra := int (include "npt.publicRolloutExtra" .) -}}
{{- $public := mul (add (int (include "npt.publicMax" .)) $extra) (int .Values.public.webWorkers) 18 -}}
{{- $admin := mul (int .Values.backend.adminWorkers) 16 -}}
{{- add $public $admin 6 2 (int .Values.database.budgetReserve) -}}
{{- end -}}

{{/* Pflichtwerte und Querbeziehungen; bricht helm template/install mit Klartext ab. */}}
{{- define "npt.validate" -}}
{{- $v := .Values -}}
{{- if not $v.publicBaseUrl }}{{ fail "publicBaseUrl fehlt (z. B. https://npt.example.org)" }}{{ end -}}
{{- if not $v.adminBaseUrl }}{{ fail "adminBaseUrl fehlt (z. B. https://npt-admin.example.org)" }}{{ end -}}
{{- if and (not $v.secrets.existingSecret) (not $v.secrets.create) }}{{ fail "secrets.existingSecret setzen (oder secrets.create=true nur fuer Dev/Test)" }}{{ end -}}
{{- if and $v.secrets.existingSecret $v.secrets.create }}{{ fail "secrets.existingSecret und secrets.create schliessen sich aus" }}{{ end -}}
{{- if and (not $v.database.host) (not $v.database.urlKey) }}{{ fail "database.host fehlt (oder database.urlKey fuer DATABASE_URL)" }}{{ end -}}
{{- if not $v.admin.tls.secretName }}{{ fail "admin.tls.secretName fehlt (Serverzertifikat des Gateways)" }}{{ end -}}
{{- if or (and $v.admin.clientCa.configMapName $v.admin.clientCa.secretName) (and (not $v.admin.clientCa.configMapName) (not $v.admin.clientCa.secretName)) }}{{ fail "admin.clientCa: genau eines von configMapName/secretName setzen" }}{{ end -}}
{{- if $v.admin.crl.enabled -}}
{{- if or (and $v.admin.crl.configMapName $v.admin.crl.secretName) (and (not $v.admin.crl.configMapName) (not $v.admin.crl.secretName)) }}{{ fail "admin.crl: genau eines von configMapName/secretName setzen" }}{{ end -}}
{{- end -}}
{{- if and $v.ingress.enabled $v.httpRoute.enabled }}{{ fail "ingress und httpRoute schliessen sich aus" }}{{ end -}}
{{- if and (or $v.ingress.enabled $v.httpRoute.enabled) (not $v.public.trustedProxyCIDRs) }}{{ fail "public.trustedProxyCIDRs fehlt (Pflicht mit ingress/httpRoute, Quell-CIDRs der Controller-Pods)" }}{{ end -}}
{{- if $v.public.hpa.enabled -}}
{{- $min := int (default $v.public.replicas $v.public.hpa.minReplicas) -}}
{{- if gt $min (int $v.public.hpa.maxReplicas) }}{{ fail "public.hpa.minReplicas groesser als maxReplicas" }}{{ end -}}
{{- end -}}
{{- if $v.database.budgetCheck -}}
{{- $budget := int (include "npt.dbBudget" .) -}}
{{- if gt $budget (int $v.database.maxConnections) -}}
{{- fail (printf "DB-Budget %d > database.maxConnections %d: (P_max %s + Rollout %s) x webWorkers %d x 18 + adminWorkers %d x 16 + 8 + budgetReserve %d; Replikas/Worker senken oder max_connections der Datenbank erhoehen" $budget (int $v.database.maxConnections) (include "npt.publicMax" .) (include "npt.publicRolloutExtra" .) (int $v.public.webWorkers) (int $v.backend.adminWorkers) (int $v.database.budgetReserve)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Gemeinsame Umgebung aller App-Container (initContainer, public, backend). */}}
{{- define "npt.appEnv" -}}
- name: CSRF_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "npt.secretName" . }}
      key: {{ .Values.secrets.keys.csrfSecret }}
{{- if .Values.database.urlKey }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "npt.dbSecretName" . }}
      key: {{ .Values.database.urlKey }}
{{- else }}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "npt.dbSecretName" . }}
      key: {{ default .Values.secrets.keys.dbPassword .Values.database.passwordKey }}
{{- end }}
- name: SMTP_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "npt.secretName" . }}
      key: {{ .Values.secrets.keys.smtpPassword }}
      optional: true
- name: MASTER_API_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "npt.secretName" . }}
      key: {{ .Values.secrets.keys.masterApiToken }}
      optional: true
{{- end -}}

{{- define "npt.appEnvFrom" -}}
- configMapRef:
    name: {{ include "npt.fullname" . }}
{{- end -}}

{{/* Volumes/Mounts fuer DB-TLS (optional). */}}
{{- define "npt.dbTlsVolume" -}}
{{- if .Values.database.tls.secretName }}
- name: db-tls
  secret:
    secretName: {{ .Values.database.tls.secretName }}
    defaultMode: 0440
{{- end }}
{{- end -}}

{{- define "npt.dbTlsMount" -}}
{{- if .Values.database.tls.secretName }}
- name: db-tls
  mountPath: /etc/npt-db
  readOnly: true
{{- end }}
{{- end -}}

{{/* initContainer: Migration + Bootstrap unter Advisory Lock (K2). */}}
{{- define "npt.migrateInitContainer" -}}
- name: migrate
  image: {{ include "npt.image" (dict "root" . "kind" "app") }}
  imagePullPolicy: {{ .Values.image.app.pullPolicy }}
  envFrom:
    {{- include "npt.appEnvFrom" . | nindent 4 }}
  env:
    - name: NPT_MIGRATE_ONLY
      value: "1"
    {{- include "npt.appEnv" . | nindent 4 }}
  securityContext:
    {{- toYaml .Values.securityContext.app | nindent 4 }}
  resources:
    {{- toYaml .Values.migrations.resources | nindent 4 }}
  volumeMounts:
    - name: tmp
      mountPath: /tmp
    {{- include "npt.dbTlsMount" . | nindent 4 }}
{{- end -}}

{{/* Standard-Verteilung der public-Pods ueber Knoten. */}}
{{- define "npt.publicSpread" -}}
{{- if .Values.public.topologySpreadConstraints -}}
{{- toYaml .Values.public.topologySpreadConstraints -}}
{{- else -}}
- maxSkew: 1
  topologyKey: kubernetes.io/hostname
  whenUnsatisfiable: ScheduleAnyway
  labelSelector:
    matchLabels:
      {{- include "npt.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: public
{{- end -}}
{{- end -}}

{{/* NetworkPolicy: Peers aus cidrs + optionalen Selektoren (leer = keine Einschraenkung). */}}
{{- define "npt.npPeers" -}}
{{- range .cidrs }}
- ipBlock:
    cidr: {{ . }}
{{- end }}
{{- if or .namespaceSelector .podSelector }}
- {{- with .namespaceSelector }}
  namespaceSelector:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with .podSelector }}
  podSelector:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end }}
{{- end -}}

{{/* NetworkPolicy: Egress-Regel zu einem Port, optional auf Peers beschraenkt. */}}
{{- define "npt.npEgressRule" -}}
{{- $peers := include "npt.npPeers" .peers | trim -}}
- ports:
    - { protocol: TCP, port: {{ .port }} }
  {{- if $peers }}
  to:
    {{- $peers | nindent 4 }}
  {{- end }}
{{- end -}}

{{/* NetworkPolicy: Egress aller App-Pods (DNS, Datenbank, extraEgress). */}}
{{- define "npt.npEgressCommon" -}}
{{- $np := .Values.networkPolicy -}}
- to:
    - namespaceSelector:
        {{- toYaml (default (dict) $np.dns.namespaceSelector) | nindent 8 }}
      {{- with $np.dns.podSelector }}
      podSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
  ports:
    {{- range $np.dns.ports }}
    - { protocol: UDP, port: {{ . }} }
    - { protocol: TCP, port: {{ . }} }
    {{- end }}
{{ include "npt.npEgressRule" (dict "port" $np.database.port "peers" $np.database) }}
{{- with $np.extraEgress }}
{{ toYaml . }}
{{- end }}
{{- end -}}
