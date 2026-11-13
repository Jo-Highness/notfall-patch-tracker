#!/bin/sh
# admin-gateway: Konfiguration rendern (render.sh), Reload-Schleife starten, nginx im
# Vordergrund. Laeuft als root (Compose: Master root, Worker nginx, cap_add) oder komplett
# als uid 101 (Kubernetes, readOnlyRootFilesystem, ohne Capabilities, NPT-12 K3).
# Ohne Client-CA startet das Gateway im Einrichtungsmodus (503, NPT-15); ohne
# Serverzertifikat wartet es, bis der Jobs-Prozess eines in TRUST_DIR abgelegt hat.
set -eu

: "${ADMIN_VERIFY_DEPTH:=2}"
: "${UPLOAD_MAX_MB:=20}"
# auto = ein Worker je CPU des Knotens; in Kubernetes ohne CPU-Limit fest begrenzen.
: "${GATEWAY_WORKER_PROCESSES:=auto}"
: "${TRUST_DIR:=/etc/npt-gateway/trust}"

fail() { echo "{\"component\":\"admin-gateway\",\"level\":\"ERROR\",\"msg\":\"$1\"}" >&2; exit 1; }

case "$GATEWAY_WORKER_PROCESSES" in auto|[1-9]|[1-9][0-9]) ;; *) fail "GATEWAY_WORKER_PROCESSES muss auto oder 1-99 sein";; esac
case "$UPLOAD_MAX_MB" in ''|*[!0-9]*) fail "UPLOAD_MAX_MB muss eine Zahl sein";; esac
case "$ADMIN_VERIFY_DEPTH" in ''|*[!0-9]*) fail "ADMIN_VERIFY_DEPTH muss eine Zahl sein";; esac
# Upload-Grenze der App plus 1 MB fuer Multipart-Rahmen und weitere Formularfelder.
: "${GATEWAY_MAX_BODY:=$((UPLOAD_MAX_MB + 1))m}"
export GATEWAY_MAX_BODY ADMIN_VERIFY_DEPTH TRUST_DIR

mkdir -p /etc/nginx/conf.d /run/nginx /var/cache/nginx
cp /etc/npt-gateway/security-headers.inc /etc/nginx/conf.d/npt-security-headers.inc
printf 'worker_processes %s;\n' "$GATEWAY_WORKER_PROCESSES" > /etc/nginx/conf.d/npt-main.inc

waited=0
while :; do
  rc=0
  /usr/local/bin/render.sh || rc=$?
  [ "$rc" -eq 0 ] && break
  [ "$rc" -eq 2 ] || fail "Konfiguration nicht erzeugbar"
  if [ $((waited % 30)) -eq 0 ]; then
    echo '{"component":"admin-gateway","level":"WARNING","msg":"Kein Serverzertifikat (ADMIN_PKI_DIR oder TRUST_DIR) - warte"}' >&2
  fi
  sleep 2; waited=$((waited + 2))
done
[ "$(cat /run/nginx/npt/mode)" = setup ] && \
  echo '{"component":"admin-gateway","level":"WARNING","msg":"Keine Client-CA - Einrichtungsmodus (503) bis zur Erstinbetriebnahme"}' >&2

nginx -t -q
/usr/local/bin/crl-reload.sh &
exec nginx -g 'daemon off;'
