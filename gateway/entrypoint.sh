#!/bin/sh
# admin-gateway: Konfiguration rendern, CRL einbinden, Reload-Schleife starten, nginx im
# Vordergrund. Laeuft als root (Compose: Master root, Worker nginx, cap_add) oder komplett
# als uid 101 (Kubernetes, readOnlyRootFilesystem, ohne Capabilities, NPT-12 K3).
set -eu

: "${ADMIN_CA_BUNDLE_PATH:=/etc/npt-gateway/pki/ca-bundle.pem}"
: "${ADMIN_CRL_PATH:=}"
: "${ADMIN_VERIFY_DEPTH:=2}"
: "${GATEWAY_TLS_CERT_PATH:=/etc/npt-gateway/pki/server.crt}"
: "${GATEWAY_TLS_KEY_PATH:=/etc/npt-gateway/pki/server.key}"
: "${ADMIN_UPSTREAM:=app:8001}"
: "${UPLOAD_MAX_MB:=20}"
# auto = ein Worker je CPU des Knotens; in Kubernetes ohne CPU-Limit fest begrenzen.
: "${GATEWAY_WORKER_PROCESSES:=auto}"
case "$GATEWAY_WORKER_PROCESSES" in auto|[1-9]|[1-9][0-9]) ;; *) echo '{"component":"admin-gateway","level":"ERROR","msg":"GATEWAY_WORKER_PROCESSES muss auto oder 1-99 sein"}' >&2; exit 1;; esac
case "$UPLOAD_MAX_MB" in ''|*[!0-9]*) echo '{"component":"admin-gateway","level":"ERROR","msg":"UPLOAD_MAX_MB muss eine Zahl sein"}' >&2; exit 1;; esac
# Upload-Grenze der App plus 1 MB fuer Multipart-Rahmen und weitere Formularfelder.
: "${GATEWAY_MAX_BODY:=$((UPLOAD_MAX_MB + 1))m}"
export ADMIN_CA_BUNDLE_PATH ADMIN_CRL_PATH ADMIN_VERIFY_DEPTH GATEWAY_TLS_CERT_PATH \
       GATEWAY_TLS_KEY_PATH ADMIN_UPSTREAM GATEWAY_MAX_BODY

fail() { echo "{\"component\":\"admin-gateway\",\"level\":\"ERROR\",\"msg\":\"$1\"}" >&2; exit 1; }

for f in "$ADMIN_CA_BUNDLE_PATH" "$GATEWAY_TLS_CERT_PATH" "$GATEWAY_TLS_KEY_PATH"; do
  [ -r "$f" ] || fail "Datei fehlt oder nicht lesbar: $f"
done
case "$ADMIN_VERIFY_DEPTH" in ''|*[!0-9]*) fail "ADMIN_VERIFY_DEPTH muss eine Zahl sein";; esac

mkdir -p /etc/nginx/conf.d /run/nginx /var/cache/nginx
cp /etc/npt-gateway/security-headers.inc /etc/nginx/conf.d/npt-security-headers.inc
printf 'worker_processes %s;\n' "$GATEWAY_WORKER_PROCESSES" > /etc/nginx/conf.d/npt-main.inc
envsubst '${ADMIN_CA_BUNDLE_PATH} ${ADMIN_VERIFY_DEPTH} ${GATEWAY_TLS_CERT_PATH} ${GATEWAY_TLS_KEY_PATH} ${ADMIN_UPSTREAM} ${GATEWAY_MAX_BODY}' \
  < /etc/npt-gateway/admin.conf.template > /etc/nginx/conf.d/admin.conf

if [ -n "$ADMIN_CRL_PATH" ]; then
  # Fail closed: konfigurierte, aber fehlende CRL verhindert den Start.
  [ -r "$ADMIN_CRL_PATH" ] || fail "ADMIN_CRL_PATH gesetzt, Datei fehlt: $ADMIN_CRL_PATH"
  printf 'ssl_crl %s;\n' "$ADMIN_CRL_PATH" > /etc/nginx/conf.d/npt-crl.inc
else
  : > /etc/nginx/conf.d/npt-crl.inc
fi

nginx -t -q
# Reload-Schleife immer: Zertifikats-/CA-Rotation (z. B. cert-manager) wirkt ohne Neustart.
set -- "$GATEWAY_TLS_CERT_PATH" "$GATEWAY_TLS_KEY_PATH" "$ADMIN_CA_BUNDLE_PATH"
[ -n "$ADMIN_CRL_PATH" ] && set -- "$@" "$ADMIN_CRL_PATH"
/usr/local/bin/crl-reload.sh "$@" &
exec nginx -g 'daemon off;'
