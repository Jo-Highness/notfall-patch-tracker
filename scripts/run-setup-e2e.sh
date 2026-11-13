#!/usr/bin/env bash
# E2E der Erstinbetriebnahme (NPT-15) gegen einen FRISCHEN Wegwerf-Stack OHNE Datei-CA,
# ohne Serverzertifikat und ohne ADMIN_BOOTSTRAP_IDS:
#   1. Test-PKI erzeugen (nur fuer Client-Zertifikate, CA und CRL zum Hochladen)
#   2. Stack starten; Gateway muss im Einrichtungsmodus (503) mit selbstsigniertem
#      Zertifikat laufen; Einrichtungscode aus dem App-Log lesen
#   3. tests.e2e.setup_smoke (Playwright) fuehrt die Einrichtung im Browser durch und
#      prueft die Uebernahme durch das Gateway ohne Neustart
#   4. Stack samt Volumes entfernen (KEEP=1 behaelt ihn)
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"

PROJECT="${E2E_PROJECT:-npt-setup-e2e}"
PUBLIC_PORT="${E2E_PUBLIC_PORT:-8492}"
GATEWAY_PORT="${E2E_GATEWAY_PORT:-8493}"
SUBNET="${E2E_SUBNET:-172.31.250.48/28}"
APP_IP="${E2E_APP_IP:-172.31.250.58}"
GW_IP="${E2E_GW_IP:-172.31.250.59}"
E2E_IMAGE="${E2E_IMAGE:-npt-e2e}"

WORK="$(mktemp -d /tmp/npt-setup-e2e.XXXXXX)"
chmod 755 "$WORK"
ENV_FILE="$WORK/e2e.env"
COMPOSE=(docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f docker-compose.yml)

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

echo "== Test-PKI (nur zum Hochladen) und Konfiguration ($WORK)"
scripts/gen-test-pki.sh "$WORK/pki" >/dev/null
chmod -R a+rX "$WORK/pki"
mkdir -p "$WORK/empty-pki"
cat > "$ENV_FILE" <<ENV
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
ADMIN_PKI_DIR=$WORK/empty-pki
NPT_IMAGE_TAG=e2e
WEB_WORKERS=2
CRL_RELOAD_INTERVAL=10
ENV

echo "== Frischer Stack $PROJECT (Ports $PUBLIC_PORT/$GATEWAY_PORT), ohne CA und Admin"
"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
if ! "${COMPOSE[@]}" up -d --build --wait --wait-timeout 240 >"$WORK/up.log" 2>&1; then
  tail -40 "$WORK/up.log"; "${COMPOSE[@]}" ps; "${COMPOSE[@]}" logs --tail 30 admin-gateway app; exit 1
fi
CODE="$("${COMPOSE[@]}" logs --no-color app | grep -o 'Einrichtungscode [0-9A-Z-]*' | tail -1 | cut -d' ' -f2)"
[[ -n "$CODE" ]] || { echo "Kein Einrichtungscode im Log"; exit 1; }
echo "   Einrichtungscode gefunden"

docker build -q -t "$E2E_IMAGE" tests/e2e >/dev/null
docker run --rm --network host -v "$REPO:/w:ro" -v "$WORK/pki:/pki:ro" -w /w \
  -e PYTHONDONTWRITEBYTECODE=1 "$E2E_IMAGE" python -m tests.e2e.setup_smoke \
  --base "http://127.0.0.1:$PUBLIC_PORT" --admin "https://localhost:$GATEWAY_PORT" \
  --pki /pki --code "$CODE"

echo "== Gateway-Log"
"${COMPOSE[@]}" logs --no-color admin-gateway | grep -E '"level":"(INFO|WARNING|ERROR)"' | grep -v '"component":"admin-gateway","remote"' | tail -8
