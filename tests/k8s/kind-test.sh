#!/usr/bin/env bash
# kind-Test des Helm-Charts (NPT-12, Testplan 1-11; 12 = Compose-Regression separat).
#
# Voraussetzungen (Testhost mit Docker, ohne sudo):
#   tests/k8s/tools.sh                      -> ~/npt-k8s-tools/bin (kind, helm, kubectl, kubeconform)
#   docker build --target runtime -t notfall-patch-tracker:k8s .
#   docker build -t notfall-patch-tracker-gateway:k8s gateway
#   docker build -t npt-e2e tests/e2e       (Admin-Schritte im Docker-Netz "kind")
#
#   tests/k8s/kind-test.sh                  alle Phasen, Cluster bleibt stehen
#   PHASES="static" tests/k8s/kind-test.sh  nur einzelne Phasen
#   KIND_WORK=<dir> PHASES="p6 p7" ...      weitere Phasen im selben Arbeitsverzeichnis
#   tests/k8s/kind-test.sh cleanup          Cluster loeschen
#
# Cluster npt-kind: API-Server nur auf 127.0.0.1, keine extraPortMappings (kein Host-Port);
# NodePorts (Admin 30443, Traefik 30080) nur aus dem Docker-Netz "kind". Abbilder per
# `kind load docker-image`. Die Test-PKI entsteht in einem Temp-Verzeichnis (KIND_WORK).
set -uo pipefail
cd "$(dirname "$0")/../.."
REPO="$PWD"

TOOLS="${NPT_K8S_TOOLS:-$HOME/npt-k8s-tools}"
export PATH="$TOOLS/bin:$PATH"
CLUSTER=npt-kind
NS=npt
EDGE=npt-edge
OTHER=npt-other
REL=npt
FULL="$REL-notfall-patch-tracker"
CHART=deploy/helm/notfall-patch-tracker
APP_IMAGE=notfall-patch-tracker:k8s
GW_IMAGE=notfall-patch-tracker-gateway:k8s
PG_IMAGE=postgres:16.15-alpine
TRAEFIK_IMAGE=traefik:v3.7.16
TRAEFIK_CHART="$TOOLS/charts/traefik-42.2.0.tgz"
TRAEFIK_CHART_SHA256=03ec772e0caab39f3d265db14014f56832ab420da98d6210f83d3ef908c077e5
CURL_IMAGE=curlimages/curl:8.17.0
E2E_IMAGE=npt-e2e
ADMIN_HOST=npt-admin.example.org
PUBLIC_HOST=npt.example.org
CRD_SCHEMAS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

WORK="${KIND_WORK:-$(mktemp -d /tmp/npt-kind.XXXXXX)}"
mkdir -p "$WORK"
chmod 755 "$WORK"
export KUBECONFIG="$WORK/kubeconfig"
RESULTS="$WORK/results.txt"
PHASES="${PHASES:-static cluster install p2 p3 seed p4 p5 p6 p7 p8 p9 p10 p11}"
[[ "${1:-}" == "cleanup" ]] && PHASES="cleanup"

k() { kubectl "$@"; }
log() { echo "== $*"; }
record() { # punkt PASS|FAIL text
  printf '%-4s %-4s %s\n' "$1" "$2" "$3" | tee -a "$RESULTS"
}
check() { # punkt text erwartet ist
  if [[ "$4" == "$3" ]]; then record "$1" PASS "$2 ($4)"; else record "$1" FAIL "$2: erwartet $3, erhalten $4"; fi
}
state() { # name [wert] – kleine Zustandsablage im Arbeitsverzeichnis
  if [[ $# -eq 2 ]]; then printf '%s' "$2" > "$WORK/state.$1"; else cat "$WORK/state.$1" 2>/dev/null; fi
}
mask() { sed -E 's#/p/[A-Za-z0-9_-]{20,}#/p/***#g'; }

pods() { k -n "$NS" get pods -l "app.kubernetes.io/instance=$REL,app.kubernetes.io/component=$1" \
  --field-selector=status.phase=Running -o jsonpath='{range .items[*]}{.metadata.name} {.status.podIP} {.status.hostIP}{"\n"}{end}'; }
backend_pod() { pods backend | awk 'NR==1{print $1}'; }
admin_node_ip() { pods backend | awk 'NR==1{print $3}'; }
psql() { k -n "$NS" exec deploy/npt-db -- psql -U npt -d npt -tAc "$1"; }
driver() { k -n "$EDGE" exec npt-driver -- python /checks/k8s_checks.py "$@"; }
intruder() { k -n "$OTHER" exec npt-intruder -- python /checks/k8s_checks.py "$@"; }
e2e() { # Admin-/Edge-Schritte im Docker-Netz kind (NodePorts)
  docker run --rm --network kind --add-host "$ADMIN_HOST:$(admin_node_ip)" \
    --add-host "$PUBLIC_HOST:$(state node_ip)" -v "$REPO:/w:ro" -v "$WORK/pki/clients:/pki:ro" \
    -w /w -e PYTHONDONTWRITEBYTECODE=1 "$E2E_IMAGE" "$@"
}
gw() { # zert|- pfad [curl-args] -> HTTP-Status; Body nach $WORK/body
  local who="$1" path="$2"; shift 2
  local auth=()
  [[ "$who" != "-" ]] && auth=(--cert "/pki/$who.crt" --key "/pki/$who.key")
  docker run --rm --network kind -v "$WORK/pki/clients:/pki:ro" -v "$WORK:/out" --user "$(id -u)" \
    "$CURL_IMAGE" -sS -o /out/body -w '%{http_code}' --max-time 10 --cacert /pki/ca.crt \
    --resolve "$ADMIN_HOST:30443:$(admin_node_ip)" "${auth[@]}" "$@" \
    "https://$ADMIN_HOST:30443$path" 2>/dev/null || :
}
served_cert_sha() {
  docker run --rm --network kind -v "$WORK/pki/clients:/pki:ro" "$CURL_IMAGE" -sS -o /dev/null \
    --max-time 10 --cacert /pki/ca.crt --cert /pki/admin.crt --key /pki/admin.key \
    --resolve "$ADMIN_HOST:30443:$(admin_node_ip)" -w '%{certs}' "https://$ADMIN_HOST:30443/admin/" 2>/dev/null \
    | openssl x509 -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2
}
helm_install() { # [zusaetzliche helm-Argumente]
  helm upgrade --install "$REL" "$CHART" -n "$NS" -f "$CHART/examples/values-kind.yaml" \
    --set-json "config.adminBootstrapIds=[\"$(cat "$WORK/pki/admin-bootstrap-id.txt")\"]" \
    --wait --timeout 10m "$@"
}
load_image() { # Multi-Plattform-Abbilder (containerd-Image-Store) als Ein-Plattform-Archiv laden
  kind load docker-image --name "$CLUSTER" "$1" >/dev/null 2>&1 && return 0
  docker save --platform linux/amd64 "$1" -o "$WORK/image.tar" \
    && kind load image-archive --name "$CLUSTER" "$WORK/image.tar" >/dev/null
  local rc=$?
  rm -f "$WORK/image.tar"
  return $rc
}
wait_rollout() {
  k -n "$NS" rollout status "deploy/$FULL-public" --timeout=600s >/dev/null &&
    k -n "$NS" rollout status "deploy/$FULL-backend" --timeout=600s >/dev/null
}

# ------------------------------------------------------------------ Phasen
phase_static() {
  log "1. lint, template, kubeconform, Budget"
  local vals=(-f "$CHART/examples/values-kind.yaml")
  local all=(-f tests/k8s/values-all.yaml)
  helm lint --strict "$CHART" "${vals[@]}" >"$WORK/lint.txt" 2>&1 \
    && helm lint --strict "$CHART" "${all[@]}" >>"$WORK/lint.txt" 2>&1 \
    && record 1 PASS "helm lint --strict (helm $(helm version --short), kind- und Voll-Werte)" \
    || record 1 FAIL "helm lint: $(tail -3 "$WORK/lint.txt")"
  if command -v helm3 >/dev/null; then
    helm3 lint --strict "$CHART" "${vals[@]}" >/dev/null 2>&1 && helm3 lint --strict "$CHART" "${all[@]}" >/dev/null 2>&1 \
      && record 1 PASS "helm3 lint --strict ($(helm3 version --short))" || record 1 FAIL "helm3 lint"
  fi
  for v in "$CHART/examples/values-kind.yaml" tests/k8s/values-all.yaml; do
    if helm template "$REL" "$CHART" -n "$NS" -f "$v" >"$WORK/render.yaml" 2>"$WORK/render.err" \
      && kubeconform -strict -summary -kubernetes-version 1.37.0 -schema-location default \
        -schema-location "$CRD_SCHEMAS" "$WORK/render.yaml" >"$WORK/kubeconform.txt" 2>&1; then
      record 1 PASS "template | kubeconform -strict ($(basename "$v"): $(tail -1 "$WORK/kubeconform.txt"))"
    else
      record 1 FAIL "kubeconform $(basename "$v"): $(tail -3 "$WORK/kubeconform.txt" "$WORK/render.err")"
    fi
  done
  expect_fail() { # text muster [helm-args]
    local text="$1" pattern="$2"; shift 2
    if helm template "$REL" "$CHART" "${vals[@]}" "$@" >/dev/null 2>"$WORK/fail.err"; then
      record 1 FAIL "$text: kein Abbruch"
    elif grep -q -- "$pattern" "$WORK/fail.err"; then
      record 1 PASS "$text: Abbruch ($(grep -o -- "$pattern[^:]*" "$WORK/fail.err" | head -1))"
    else
      record 1 FAIL "$text: falscher Fehler $(tail -1 "$WORK/fail.err")"
    fi
  }
  expect_fail "Budget replicas=6" "DB-Budget" --set public.replicas=6
  expect_fail "Budget HPA max 5" "DB-Budget" --set public.hpa.enabled=true --set public.hpa.maxReplicas=5
  expect_fail "Budget webWorkers=4" "DB-Budget" --set public.webWorkers=4
  expect_fail "Tag latest" "/image/app/tag" --set image.app.tag=latest
  expect_fail "ohne publicBaseUrl" "publicBaseUrl" --set publicBaseUrl=
  expect_fail "Ingress ohne trustedProxyCIDRs" "trustedProxyCIDRs" --set-json 'public.trustedProxyCIDRs=[]'
  expect_fail "clientCa doppelt" "clientCa" --set admin.clientCa.secretName=x
  helm template "$REL" "$CHART" "${vals[@]}" --set public.replicas=3 >/dev/null 2>&1 \
    && record 1 PASS "Budget Default (3+1)x2: 144+32+8+10=194 <= 200 (kein Abbruch)" \
    || record 1 FAIL "Budget Default bricht ab"
}

phase_cluster() {
  log "Cluster $CLUSTER, Abbilder, Namespaces, Test-PKI, Datenbank, Traefik"
  if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
    kind create cluster --config tests/k8s/kind-config.yaml --kubeconfig "$KUBECONFIG" --wait 120s || exit 1
  else
    kind export kubeconfig --name "$CLUSTER" --kubeconfig "$KUBECONFIG"
  fi
  docker inspect "$CLUSTER-control-plane" --format '{{range $p, $b := .HostConfig.PortBindings}}{{$p}}->{{(index $b 0).HostIp}}:{{(index $b 0).HostPort}} {{end}}' \
    | tee "$WORK/portbindings.txt"
  for n in $(kind get nodes --name "$CLUSTER"); do
    [[ -n "$(docker inspect "$n" --format '{{json .HostConfig.PortBindings}}' | grep -v -e '^{}$' -e 6443)" ]] \
      && echo "WARNUNG: Port-Binding an $n"
  done
  echo "$TRAEFIK_CHART_SHA256  $TRAEFIK_CHART" | sha256sum -c --quiet - || exit 1
  for img in "$APP_IMAGE" "$GW_IMAGE" "$PG_IMAGE" "$TRAEFIK_IMAGE"; do
    docker image inspect "$img" >/dev/null 2>&1 || docker pull -q "$img" >/dev/null || exit 1
    load_image "$img" || exit 1
  done
  docker image inspect "$CURL_IMAGE" >/dev/null 2>&1 || docker pull -q "$CURL_IMAGE" >/dev/null
  state node_ip "$(docker inspect "$CLUSTER-worker" --format '{{.NetworkSettings.Networks.kind.IPAddress}}')"

  k create namespace "$NS" --dry-run=client -o yaml | k apply -f - >/dev/null
  k label --overwrite namespace "$NS" pod-security.kubernetes.io/enforce=restricted \
    pod-security.kubernetes.io/warn=restricted pod-security.kubernetes.io/audit=restricted >/dev/null
  for n in "$EDGE" "$OTHER"; do k create namespace "$n" --dry-run=client -o yaml | k apply -f - >/dev/null; done

  if [[ ! -f "$WORK/pki/admin-bootstrap-id.txt" ]]; then
    GATEWAY_SANS="DNS:$ADMIN_HOST" scripts/gen-test-pki.sh "$WORK/pki" >/dev/null || exit 1
    chmod -R a+rX "$WORK/pki"
  fi
  k -n "$NS" get secret npt-app >/dev/null 2>&1 \
    || k -n "$NS" create secret generic npt-app --from-literal=CSRF_SECRET="$(openssl rand -hex 32)" \
      --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 24)" >/dev/null || exit 1
  k -n "$NS" create secret tls npt-gw-tls --cert "$WORK/pki/gateway/server.crt" \
    --key "$WORK/pki/gateway/server.key" --dry-run=client -o yaml | k apply -f - >/dev/null
  k -n "$NS" create configmap npt-client-ca --from-file=ca.crt="$WORK/pki/gateway/ca-bundle.pem" \
    --dry-run=client -o yaml | k apply -f - >/dev/null
  k -n "$NS" create secret generic npt-gw-crl --from-file=crl.pem="$WORK/pki/gateway/crl.pem" \
    --dry-run=client -o yaml | k apply -f - >/dev/null

  k -n "$NS" apply -f tests/k8s/postgres.yaml >/dev/null
  k -n "$NS" rollout status deploy/npt-db --timeout=180s >/dev/null || exit 1

  helm upgrade --install traefik "$TRAEFIK_CHART" -n "$EDGE" -f tests/k8s/traefik-values.yaml \
    --set image.tag="${TRAEFIK_IMAGE#*:}" --wait --timeout 5m >/dev/null || exit 1

  k -n "$EDGE" create configmap k8s-checks --from-file=k8s_checks.py=tests/k8s/k8s_checks.py \
    --dry-run=client -o yaml | k apply -f - >/dev/null
  k -n "$OTHER" create configmap k8s-checks --from-file=k8s_checks.py=tests/k8s/k8s_checks.py \
    --dry-run=client -o yaml | k apply -f - >/dev/null
  for pair in "$EDGE:npt-driver" "$OTHER:npt-intruder"; do
    k -n "${pair%%:*}" apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata: { name: ${pair#*:}, labels: { app: ${pair#*:} } }
spec:
  terminationGracePeriodSeconds: 1
  automountServiceAccountToken: false
  securityContext: { runAsNonRoot: true, runAsUser: 10001, seccompProfile: { type: RuntimeDefault } }
  containers:
    - name: c
      image: $APP_IMAGE
      imagePullPolicy: Never
      command: ["sleep", "infinity"]
      securityContext: { allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: [ALL] } }
      volumeMounts: [{ name: checks, mountPath: /checks }]
  volumes: [{ name: checks, configMap: { name: k8s-checks } }]
EOF
  done
  k -n "$EDGE" wait --for=condition=Ready pod/npt-driver --timeout=120s >/dev/null
  k -n "$OTHER" wait --for=condition=Ready pod/npt-intruder --timeout=120s >/dev/null
  kubectl version 2>/dev/null | tee "$WORK/versions.txt"
}

phase_install() {
  log "helm install (3 public + backend starten gleichzeitig)"
  local t0=$SECONDS
  if helm_install >"$WORK/install.txt" 2>&1; then
    record 3 PASS "helm upgrade --install --wait in $((SECONDS - t0)) s"
  else
    record 3 FAIL "helm install: $(tail -5 "$WORK/install.txt")"
  fi
  helm test "$REL" -n "$NS" --logs >"$WORK/helmtest.txt" 2>&1 \
    && record 3 PASS "helm test: $(grep -E 'healthz|Zertifikat' "$WORK/helmtest.txt" | tr '\n' ' ')" \
    || record 3 FAIL "helm test: $(tail -8 "$WORK/helmtest.txt")"
}

phase_p2() {
  log "2. PodSecurity restricted"
  local events warn
  events="$(k -n "$NS" get events -o json | grep -c -i 'violates PodSecurity' || true)"
  warn="$(k label --dry-run=server --overwrite namespace "$NS" pod-security.kubernetes.io/enforce=restricted 2>&1 | grep -i -c warning || true)"
  local notready
  notready="$(k -n "$NS" get pods -l "app.kubernetes.io/instance=$REL" -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}' | grep -v -c -e Running -e Succeeded || true)"
  check 2 "Namespace enforce=restricted: abgewiesene Pods (Events)" 0 "$events"
  check 2 "server-seitiger Dry-Run restricted: Warnungen fuer laufende Pods" 0 "$warn"
  check 2 "Chart-Pods nicht Running" 0 "$notready"
}

phase_p3() {
  log "3. Erstmigration: genau eine, kein Crash-Loop, Bootstrap ohne Unique-Verletzung"
  local p migrated=0 waited=0 restarts=0 created=0 n
  for p in $(k -n "$NS" get pods -l "app.kubernetes.io/instance=$REL" -o jsonpath='{.items[*].metadata.name}'); do
    [[ "$p" == *smoke* ]] && continue
    k -n "$NS" logs "$p" -c migrate >"$WORK/migrate-$p.log" 2>/dev/null || continue
    n="$(grep -c 'Running upgrade' "$WORK/migrate-$p.log" || true)"
    [[ "$n" -gt 0 ]] && migrated=$((migrated + 1))
    grep -q 'warte auf Lock' "$WORK/migrate-$p.log" && waited=$((waited + 1))
    created=$((created + $(sed -n 's/.*"neu": *\([0-9]*\).*/\1/p' "$WORK/migrate-$p.log" | head -1 | grep . || echo 0)))
  done
  restarts="$(k -n "$NS" get pods -l "app.kubernetes.io/instance=$REL" -o jsonpath='{range .items[*]}{range .status.initContainerStatuses[*]}{.restartCount}{"\n"}{end}{range .status.containerStatuses[*]}{.restartCount}{"\n"}{end}{end}' | awk '{s+=$1} END {print s+0}')"
  check 3 "Pods mit ausgefuehrter Migration (Running upgrade)" 1 "$migrated"
  record 3 INFO "initContainer, die auf den Lock gewartet haben: $waited"
  check 3 "Neustarts aller Container (inkl. init)" 0 "$restarts"
  check 3 "Admins aus Bootstrap (Summe 'neu' aller Pods)" 1 "$created"
  check 3 "admin_users in der DB" 1 "$(psql 'SELECT count(*) FROM admin_users')"
  check 3 "Unique-Verletzungen in App-/DB-Logs" 0 "$(cat "$WORK"/migrate-*.log <(k -n "$NS" logs deploy/npt-db 2>&1) | grep -c -i -e 'duplicate key' -e UniqueViolation || true)"
  check 3 "alembic_version" 0003 "$(psql 'SELECT version_num FROM alembic_version')"
}

phase_seed() {
  log "Stammdaten + Projekt ueber das Admin-Gateway (NodePort)"
  e2e python -m tests.e2e.seed --admin "https://$ADMIN_HOST:30443" --pki /pki --systems 60 >"$WORK/seed.txt" 2>&1 \
    || { record S FAIL "seed: $(tail -3 "$WORK/seed.txt")"; return; }
  local out
  out="$(e2e python tests/k8s/k8s_checks.py create-project --admin "https://$ADMIN_HOST:30443" --pki /pki --name "kind $(date +%H%M%S)")"
  state pid "$(sed -n 's/.*"pid": \([0-9]*\).*/\1/p' <<<"$out")"
  state path "$(sed -n 's/.*"path": "\([^"]*\)".*/\1/p' <<<"$out")"
  [[ -n "$(state path)" ]] && record S PASS "Projekt $(state pid) angelegt ($(state path | mask))" || record S FAIL "create-project: $out"
}

phase_p4() {
  log "4. Bearbeiter: Liste, api/systems, PATCH 200, 409, CSRF 403"
  local out
  out="$(driver public --base "http://$FULL-public.$NS:8000" --path "$(state path)")"
  [[ "$out" == *'"ok": true'* ]] && record 4 PASS "Service direkt (aus $EDGE): $(mask <<<"$out")" || record 4 FAIL "Service: $(mask <<<"$out")"
  out="$(e2e python tests/k8s/k8s_checks.py public --base "http://$PUBLIC_HOST:30080" --path "$(state path)" 2>&1)"
  [[ "$out" == *'"ok": true'* ]] && record 4 PASS "ueber Traefik-Ingress: $(mask <<<"$out")" || record 4 FAIL "Traefik: $(mask <<<"$out" | tail -3)"
}

phase_p5() {
  log "5. SSE pod-uebergreifend, Reconnect nach Pod-Loeschung"
  mapfile -t P < <(pods public)
  local a b out
  a="$(awk '{print $2}' <<<"${P[0]}")"; b="$(awk '{print $2}' <<<"${P[1]}")"
  out="$(driver sse --a "http://$a:8000" --b "http://$b:8000" --path "$(state path)")"
  [[ "$out" == *'"ok": true'* ]] && record 5 PASS "Stream Pod A, PATCH Pod B: $out" || record 5 FAIL "SSE: $out"
  out="$(e2e python tests/k8s/k8s_checks.py sse --a "http://$PUBLIC_HOST:30080" --b "http://$PUBLIC_HOST:30080" --path "$(state path)" 2>&1)"
  [[ "$out" == *'"ok": true'* ]] && record 5 PASS "SSE ueber Traefik: $out" || record 5 FAIL "SSE Traefik: $(tail -3 <<<"$out")"
  local podA
  podA="$(awk '{print $1}' <<<"${P[0]}")"
  driver sse-reconnect --a "http://$a:8000" --b "http://$b:8000" --svc "http://$FULL-public.$NS:8000" \
    --path "$(state path)" >"$WORK/reconnect.txt" 2>&1 &
  local bg=$!
  for _ in $(seq 1 50); do grep -q LISTENING "$WORK/reconnect.txt" && break; sleep 0.2; done
  k -n "$NS" delete pod "$podA" --wait=false >/dev/null
  wait "$bg"
  out="$(tail -1 "$WORK/reconnect.txt")"
  [[ "$out" == *'"ok": true'* ]] && record 5 PASS "Pod A geloescht, Aenderung waehrend Trennung nachgeliefert: $out" || record 5 FAIL "Reconnect: $out"
  wait_rollout
}

phase_p6() {
  log "6. Admin mTLS ueber NodePort"
  check 6 "Admin-Zertifikat /admin/" 200 "$(gw admin /admin/)"
  check 6 "ohne Zertifikat" 403 "$(gw - /admin/)"
  local c
  c="$(gw foreign /admin/)"; [[ "$c" == 403 || "$c" == 000 ]] && record 6 PASS "fremde CA, gleicher DN ($c)" || record 6 FAIL "fremde CA: $c"
  c="$(gw revoked /admin/)"; [[ "$c" == 403 || "$c" == 000 ]] && record 6 PASS "gesperrtes Zertifikat ($c)" || record 6 FAIL "gesperrt: $c"
  check 6 "Nicht-Admin vor CRL-Tausch (App-Anfrageseite)" 403 "$(gw nonadmin /admin/)"
  grep -q "Freischaltung anfragen\|ist offen" "$WORK/body" && record 6 PASS "Nicht-Admin sieht Anfrageseite (App)" || record 6 FAIL "Anfrageseite fehlt"

  local pod uid0 r0
  pod="$(backend_pod)"
  uid0="$(k -n "$NS" get pod "$pod" -o jsonpath='{.metadata.uid}')"
  r0="$(k -n "$NS" get pod "$pod" -o jsonpath='{.status.containerStatuses[?(@.name=="gateway")].restartCount}')"
  # Neue CRL: zusaetzlich nonadmin gesperrt (CA-Datenbank im Temp-Verzeichnis).
  local ca="$WORK/ca2"
  mkdir -p "$ca"; : > "$ca/index.txt"; echo 2000 > "$ca/crlnumber"
  cat > "$ca/ca.cnf" <<EOF
[ ca ]
default_ca = t
[ t ]
database = $ca/index.txt
crlnumber = $ca/crlnumber
certificate = $WORK/pki/ca/test-ca.crt
private_key = $WORK/pki/ca/test-ca.key
default_md = sha256
default_crl_days = 30
EOF
  openssl ca -config "$ca/ca.cnf" -revoke "$WORK/pki/clients/revoked.crt" 2>/dev/null
  openssl ca -config "$ca/ca.cnf" -revoke "$WORK/pki/clients/nonadmin.crt" 2>/dev/null
  openssl ca -config "$ca/ca.cnf" -gencrl -out "$ca/crl.pem" 2>/dev/null
  k -n "$NS" create secret generic npt-gw-crl --from-file=crl.pem="$ca/crl.pem" --dry-run=client -o yaml | k apply -f - >/dev/null
  local t0=$SECONDS c2=""
  while (( SECONDS - t0 < 300 )); do
    c2="$(gw nonadmin /admin/)"
    if grep -q "Client-Zertifikat fehlt" "$WORK/body" 2>/dev/null || [[ "$c2" == 000 ]]; then break; fi
    sleep 5
  done
  if grep -q "Client-Zertifikat fehlt" "$WORK/body" 2>/dev/null || [[ "$c2" == 000 ]]; then
    record 6 PASS "CRL-Secret getauscht: Nicht-Admin vom Gateway gesperrt nach $((SECONDS - t0)) s ($c2)"
  else
    record 6 FAIL "CRL-Tausch wirkt nicht innerhalb 300 s ($c2)"
  fi
  check 6 "Admin nach CRL-Tausch" 200 "$(gw admin /admin/)"

  # Neues Serverzertifikat (neuer Schluessel) derselben CA.
  local sha0 sha1="" s="$WORK/srv2"
  mkdir -p "$s"
  sha0="$(served_cert_sha)"
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$s/server.key" 2>/dev/null
  printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:%s,DNS:localhost\n' "$ADMIN_HOST" > "$s/ext"
  openssl req -new -key "$s/server.key" -subj "/C=DE/O=NPT Test/CN=$ADMIN_HOST" -out "$s/req.csr" 2>/dev/null
  openssl x509 -req -in "$s/req.csr" -CA "$WORK/pki/ca/test-ca.crt" -CAkey "$WORK/pki/ca/test-ca.key" \
    -CAcreateserial -CAserial "$s/ca.srl" -days 30 -sha256 -extfile "$s/ext" -out "$s/server.crt" 2>/dev/null
  k -n "$NS" create secret tls npt-gw-tls --cert "$s/server.crt" --key "$s/server.key" --dry-run=client -o yaml | k apply -f - >/dev/null
  t0=$SECONDS
  while (( SECONDS - t0 < 300 )); do
    sha1="$(served_cert_sha)"
    [[ -n "$sha1" && "$sha1" != "$sha0" ]] && break
    sleep 5
  done
  local expect
  expect="$(openssl x509 -in "$s/server.crt" -noout -fingerprint -sha256 | cut -d= -f2)"
  check 6 "TLS-Secret getauscht: neues Serverzertifikat ausgeliefert nach $((SECONDS - t0)) s" "$expect" "$sha1"
  check 6 "Admin mit neuem Serverzertifikat" 200 "$(gw admin /admin/)"
  check 6 "backend-Pod unveraendert (UID)" "$uid0" "$(k -n "$NS" get pod "$pod" -o jsonpath='{.metadata.uid}')"
  check 6 "Gateway-Neustarts" "$r0" "$(k -n "$NS" get pod "$pod" -o jsonpath='{.status.containerStatuses[?(@.name=="gateway")].restartCount}')"
  record 6 INFO "Gateway-Log: $(k -n "$NS" logs "$pod" -c gateway 2>/dev/null | grep -c 'neu geladen') Reloads ('PKI-Dateien geaendert')"
}

phase_p7() {
  log "7. App-Admin-Port nur loopback, gespoofte Header am Bearbeiter-Port"
  local pod ip out
  pod="$(backend_pod)"; ip="$(pods backend | awk 'NR==1{print $2}')"
  out="$(driver tcp --host "$ip" --port 8001)"
  [[ "$out" == *'"state": "timeout"'* || "$out" == *'"state": "refused"'* ]] \
    && record 7 PASS "Pod-IP:8001 aus $EDGE: $(sed -n 's/.*"state": "\([a-z]*\)".*/\1/p' <<<"$out")" || record 7 FAIL "8001 aus $EDGE: $out"
  out="$(intruder tcp --host "$ip" --port 8001)"
  [[ "$out" == *'"state": "timeout"'* || "$out" == *'"state": "refused"'* ]] \
    && record 7 PASS "Pod-IP:8001 aus $OTHER: $(sed -n 's/.*"state": "\([a-z]*\)".*/\1/p' <<<"$out")" || record 7 FAIL "8001 aus $OTHER: $out"
  # Ohne NetworkPolicy-Einfluss: aus dem Pod selbst ueber die Pod-IP -> abgelehnt (nur 127.0.0.1 gebunden).
  out="$(k -n "$NS" exec "$pod" -c app -- python -c "import socket,sys
for h in ('$ip','127.0.0.1'):
    s=socket.socket(); s.settimeout(3)
    try: s.connect((h,8001)); print(h,'open')
    except OSError as e: print(h,type(e).__name__)
    finally: s.close()")"
  [[ "$out" == *"$ip ConnectionRefusedError"* && "$out" == *"127.0.0.1 open"* ]] \
    && record 7 PASS "Admin-Port bindet nur 127.0.0.1 (Pod-IP refused, loopback open)" || record 7 FAIL "Bindung: $out"
  out="$(driver spoof-admin --base "http://$FULL-public.$NS:8000")"
  [[ "$out" == *'"ok": true'* ]] && record 7 PASS "X-SSL-*-Header an public /admin/ -> 404" || record 7 FAIL "spoof: $out"
}

phase_p8() {
  log "8. Backups: manuell, geplant, Download, Restore, Pod-Loeschung"
  local pid out
  pid="$(state pid)"
  out="$(e2e python tests/k8s/k8s_checks.py backups --admin "https://$ADMIN_HOST:30443" --pki /pki --pid "$pid" --restore 2>&1)"
  [[ "$out" == *'"ok": true'* ]] && record 8 PASS "manuell + Download json/xlsx + Restore: $out" || record 8 FAIL "backups: $(tail -3 <<<"$out")"
  # Geplant: Datenaenderung + faelliges Intervall (last_backup_at zurueckgesetzt).
  driver xff --base "http://$FULL-public.$NS:8000" --path "$(state path)" --xff 198.51.100.1 >/dev/null
  local q="SELECT count(*) FROM backups WHERE project_id = $pid AND trigger = 'zeitgesteuert'"
  local t0=$SECONDS n0 n=0
  n0="$(psql "$q")"
  psql "UPDATE projects SET last_backup_at = now() - interval '1 day' WHERE id = $pid" >/dev/null
  while (( SECONDS - t0 < 90 )); do
    n="$(psql "$q")"
    [[ "$n" -gt "$n0" ]] && break
    sleep 3
  done
  [[ "$n" -gt "$n0" ]] && record 8 PASS "zeitgesteuertes Backup nach $((SECONDS - t0)) s" || record 8 FAIL "kein zeitgesteuertes Backup"
  record 8 INFO "Backups je Ausloeser: $(psql "SELECT string_agg(trigger || '=' || c, ' ') FROM (SELECT trigger, count(*) c FROM backups WHERE project_id = $pid GROUP BY 1) x")"
  local pod missing cnt0 cnt1
  pod="$(backend_pod)"
  k -n "$NS" exec "$pod" -c app -- find /data/backups -type f | sort > "$WORK/files-before.txt"
  cnt0="$(wc -l < "$WORK/files-before.txt")"
  k -n "$NS" delete pod "$pod" --wait=true >/dev/null
  wait_rollout
  sleep 2
  pod="$(backend_pod)"
  k -n "$NS" exec "$pod" -c app -- find /data/backups -type f | sort > "$WORK/files-after.txt"
  cnt1="$(wc -l < "$WORK/files-after.txt")"
  missing="$(comm -23 "$WORK/files-before.txt" "$WORK/files-after.txt" | wc -l)"
  check 8 "Backup-Dateien nach Loeschen des backend-Pods fehlend (vorher $cnt0, nachher $cnt1)" 0 "$missing"
  out="$(e2e python tests/k8s/k8s_checks.py download --admin "https://$ADMIN_HOST:30443" --pki /pki --pid "$pid" 2>&1)"
  [[ "$out" == *'"ok": true'* ]] && record 8 PASS "Download nach Pod-Neustart: $out" || record 8 FAIL "Download: $out"
}

phase_p9() {
  log "9. X-Forwarded-For: nur aus dem Controller-Namespace vertraut"
  local out
  out="$(intruder xff --base "http://$FULL-public.$NS:8000" --path "$(state path)" --xff 203.0.113.66 2>&1 | grep '"check"')"
  [[ "$out" != *'"ok": true'* ]] && record 9 PASS "aus $OTHER blockiert ($out)" \
    || record 9 FAIL "aus $OTHER erreichbar: $out"
  driver xff --base "http://$FULL-public.$NS:8000" --path "$(state path)" --xff 203.0.113.77 >/dev/null
  check 9 "aus $EDGE (vertrauter Proxy): Client-IP im Aenderungsprotokoll" 203.0.113.77 \
    "$(psql 'SELECT client_ip FROM change_log ORDER BY id DESC LIMIT 1')"
  e2e python tests/k8s/k8s_checks.py xff --base "http://$PUBLIC_HOST:30080" --path "$(state path)" --xff 203.0.113.99 >/dev/null 2>&1
  local ip
  ip="$(psql 'SELECT client_ip FROM change_log ORDER BY id DESC LIMIT 1')"
  [[ -n "$ip" && "$ip" != 203.0.113.99 ]] && record 9 PASS "ueber Traefik gespoofte XFF wirkungslos (protokolliert: $ip)" \
    || record 9 FAIL "XFF-Spoof ueber Traefik wirksam ($ip)"
}

phase_p10() {
  log "10. helm rollback nach Test-Migration (NPT_SCHEMA_NEWER=warn)"
  docker build -q -t notfall-patch-tracker:k8s-mig --build-arg BASE="$APP_IMAGE" tests/k8s/rollback >/dev/null || return
  load_image notfall-patch-tracker:k8s-mig
  local rev
  rev="$(helm history "$REL" -n "$NS" -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)[-1]["revision"])')"
  helm_install --set image.app.tag=k8s-mig >"$WORK/upgrade.txt" 2>&1 \
    && record 10 PASS "Upgrade auf Abbild mit Migration 0004 (Revision $((rev + 1)))" || record 10 FAIL "Upgrade: $(tail -3 "$WORK/upgrade.txt")"
  check 10 "alembic_version nach Upgrade" 0004_k8s_rollback_test "$(psql 'SELECT version_num FROM alembic_version')"
  helm rollback "$REL" "$rev" -n "$NS" --wait --timeout 10m >"$WORK/rollback.txt" 2>&1 \
    && record 10 PASS "helm rollback auf Revision $rev" || record 10 FAIL "rollback: $(tail -3 "$WORK/rollback.txt")"
  wait_rollout
  local img notready warns t0=$SECONDS
  while (( SECONDS - t0 < 120 )); do # alte Pods (Terminating) abwarten
    img="$(k -n "$NS" get pods -l "app.kubernetes.io/instance=$REL" -o jsonpath='{range .items[*]}{.spec.containers[0].image}{"\n"}{end}' | grep -v smoke | sort -u | tr '\n' ' ')"
    [[ "$img" == "$APP_IMAGE " ]] && break
    sleep 3
  done
  check 10 "Abbild aller App-Pods nach Rollback" "$APP_IMAGE " "$img"
  notready="$(k -n "$NS" get pods -l "app.kubernetes.io/instance=$REL" --field-selector=status.phase!=Running -o name | grep -v smoke | wc -l)"
  check 10 "Pods nicht Running nach Rollback" 0 "$notready"
  warns=0
  for p in $(k -n "$NS" get pods -l "app.kubernetes.io/instance=$REL" -o jsonpath='{.items[*].metadata.name}'); do
    k -n "$NS" logs "$p" -c migrate 2>/dev/null | grep -q 'Datenbank-Revision unbekannt' && warns=$((warns + 1))
  done
  [[ "$warns" -gt 0 ]] && record 10 PASS "initContainer warnen 'Datenbank-Revision unbekannt' ($warns Pods) und starten" || record 10 FAIL "keine Warnung im migrate-Log"
  check 10 "alembic_version bleibt (expand/contract)" 0004_k8s_rollback_test "$(psql 'SELECT version_num FROM alembic_version')"
  local out
  out="$(driver public --base "http://$FULL-public.$NS:8000" --path "$(state path)")"
  [[ "$out" == *'"ok": true'* ]] && record 10 PASS "Bearbeiter-Funktion nach Rollback" || record 10 FAIL "nach Rollback: $(mask <<<"$out")"
}

phase_p11() {
  log "11. Kein Projekt-Token in Pod-/Controller-Logs"
  : > "$WORK/all-logs.txt"
  local ns p
  for ns in "$NS" "$EDGE" kube-system; do
    for p in $(k -n "$ns" get pods -o jsonpath='{.items[*].metadata.name}'); do
      k -n "$ns" logs "$p" --all-containers --prefix >>"$WORK/all-logs.txt" 2>/dev/null
      k -n "$ns" logs "$p" --all-containers --prefix --previous >>"$WORK/all-logs.txt" 2>/dev/null
    done
  done
  local lines hits traefik
  lines="$(wc -l < "$WORK/all-logs.txt")"
  hits="$(grep -c -E '/p/[A-Za-z0-9_-]{20,}' "$WORK/all-logs.txt" || true)"
  traefik="$(grep -c '"RequestMethod"' "$WORK/all-logs.txt" || true)"
  check 11 "Treffer /p/<20+ Zeichen> in $lines Logzeilen (inkl. $traefik Traefik-Access-Log-Zeilen)" 0 "$hits"
  rm -f "$WORK/all-logs.txt"
}

phase_cleanup() {
  log "Cluster $CLUSTER loeschen"
  kind delete cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG"
}

echo "Arbeitsverzeichnis: $WORK"
for ph in $PHASES; do
  "phase_$ph"
done
echo
echo "== Ergebnisse ($RESULTS)"
cat "$RESULTS" 2>/dev/null
! grep -q ' FAIL ' "$RESULTS" 2>/dev/null
