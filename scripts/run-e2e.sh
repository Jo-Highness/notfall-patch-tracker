#!/usr/bin/env bash
# E2E-Suite (Auftrag 10) gegen einen FRISCHEN Wegwerf-Stack:
#   1. Test-PKI + .env in einem Temp-Verzeichnis erzeugen (nie im Repo)
#   2. eigenes Compose-Projekt (Default npt-e2e) mit eigenen Ports/Netzen + Mailpit starten
#   3. Stammdaten (2.000 Testsysteme) ueber den Admin-Bereich importieren (tests.e2e.seed)
#   4. Playwright-Suiten nacheinander ausfuehren:
#        inline_smoke  Inline-Bearbeitung (+ XSS-Probe, Tooltip, Hilfe)
#        live_smoke    Live-Update zwischen zwei Browser-Kontexten (+ Fokus, Konflikt, Polling)
#        pivot_smoke   Pivot-Drilldown (+ Live-Aktualisierung)
#        upload_smoke  Admin-Upload mit Konfliktbestaetigung (+ Backup/Restore)
#        mail_smoke    Mailversand an Mailpit (optional, E2E_MAIL=0 schaltet ab)
#   5. Stack samt Volumes dieses Projekts entfernen (KEEP=1 behaelt ihn)
# Exitcode 0 = alle Suiten gruen. Es werden nur Mails an den lokalen Mailpit zugestellt.
#
#   scripts/run-e2e.sh                     # alles
#   E2E_SUITES="inline_smoke live_smoke" scripts/run-e2e.sh
#   KEEP=1 scripts/run-e2e.sh              # Stack danach stehen lassen (Fehlersuche)
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"

PROJECT="${E2E_PROJECT:-npt-e2e}"
PUBLIC_PORT="${E2E_PUBLIC_PORT:-8490}"
GATEWAY_PORT="${E2E_GATEWAY_PORT:-8491}"
SUBNET="${E2E_SUBNET:-172.31.250.32/28}"
APP_IP="${E2E_APP_IP:-172.31.250.42}"
GW_IP="${E2E_GW_IP:-172.31.250.43}"
SUITES="${E2E_SUITES:-inline_smoke live_smoke pivot_smoke upload_smoke}"
[[ "${E2E_MAIL:-1}" == "1" && -z "${E2E_SUITES:-}" ]] && SUITES="$SUITES mail_smoke"
E2E_IMAGE="${E2E_IMAGE:-npt-e2e}"

WORK="$(mktemp -d /tmp/npt-e2e.XXXXXX)"
chmod 755 "$WORK"
ENV_FILE="$WORK/e2e.env"
COMPOSE=(docker compose -p "$PROJECT" --env-file "$ENV_FILE"
         -f docker-compose.yml -f docker-compose.mailpit.yml)

cleanup() {
  local rc=$?
  if [[ "${KEEP:-0}" == "1" ]]; then
    echo "KEEP=1: Stack $PROJECT bleibt stehen (Arbeitsverzeichnis $WORK)."
  else
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$WORK"
  fi
  exit "$rc"
}
trap cleanup EXIT

echo "== Test-PKI und Konfiguration ($WORK)"
scripts/gen-test-pki.sh "$WORK/pki" >/dev/null
chmod -R a+rX "$WORK/pki"
cat > "$ENV_FILE" <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 24)
CSRF_SECRET=$(openssl rand -hex 32)
PUBLIC_BIND_ADDR=127.0.0.1
PUBLIC_PORT=$PUBLIC_PORT
GATEWAY_BIND_ADDR=127.0.0.1
GATEWAY_PORT=$GATEWAY_PORT
ADMIN_NET_SUBNET=$SUBNET
APP_ADMIN_IP=$APP_IP
GATEWAY_ADMIN_IP=$GW_IP
PUBLIC_BASE_URL=http://127.0.0.1:$PUBLIC_PORT
ADMIN_BASE_URL=https://localhost:$GATEWAY_PORT
ADMIN_PKI_DIR=$WORK/pki/gateway
ADMIN_CRL_PATH=/etc/npt-gateway/pki/crl.pem
ADMIN_BOOTSTRAP_IDS=$(cat "$WORK/pki/admin-bootstrap-id.txt")
NPT_IMAGE_TAG=e2e
WEB_WORKERS=2
EOF

echo "== Frischer Stack $PROJECT (Ports $PUBLIC_PORT/$GATEWAY_PORT)"
"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
if ! "${COMPOSE[@]}" up -d --build --wait --wait-timeout 180 >"$WORK/up.log" 2>&1; then
  tail -40 "$WORK/up.log"; "${COMPOSE[@]}" ps; exit 1
fi
curl -fsS "http://127.0.0.1:$PUBLIC_PORT/readyz" >/dev/null

echo "== E2E-Image"
docker build -q -t "$E2E_IMAGE" tests/e2e >/dev/null
run_e2e() {
  docker run --rm --network host -v "$REPO:/w:ro" -v "$WORK/pki/clients:/pki:ro" -w /w \
    -e PYTHONDONTWRITEBYTECODE=1 "$E2E_IMAGE" python -m "$@"
}
COMMON=(--base "http://127.0.0.1:$PUBLIC_PORT" --admin "https://localhost:$GATEWAY_PORT" --pki /pki)

echo "== Stammdaten importieren"
run_e2e tests.e2e.seed --admin "https://localhost:$GATEWAY_PORT" --pki /pki

declare -A RESULT
rc=0
for suite in $SUITES; do
  echo "== $suite"
  extra=()
  if [[ "$suite" == "mail_smoke" ]]; then
    mp="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$PROJECT-mailpit-1")"
    extra=(--mailpit "http://$mp:8025")
  fi
  start=$SECONDS
  if run_e2e "tests.e2e.$suite" "${COMMON[@]}" "${extra[@]}"; then
    RESULT[$suite]="OK     ($((SECONDS - start)) s)"
  else
    RESULT[$suite]="FEHLER ($((SECONDS - start)) s)"
    rc=1
  fi
done

echo
echo "== Ergebnis"
for suite in $SUITES; do echo "  $suite: ${RESULT[$suite]}"; done
# Kein Projekt-Token im Log des Stacks (Auftrag 8.2)
if "${COMPOSE[@]}" logs --no-color 2>&1 | grep -Eq '/p/[A-Za-z0-9_-]{20,}'; then
  echo "  FEHLER: unmaskierter Projekt-Token in Container-Logs"; rc=1
else
  echo "  Logs: kein Projekt-Token im Klartext"
fi
exit $rc
