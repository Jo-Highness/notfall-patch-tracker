#!/bin/sh
# Rendert die nginx-Konfiguration aus dem aktuellen Dateistand (NPT-06, NPT-15).
#
#   Serverzertifikat: GATEWAY_TLS_CERT_PATH/_KEY_PATH (Datei, Vorrang), sonst das vom
#                     Jobs-Prozess erzeugte selbstsignierte aus TRUST_DIR.
#   Client-CA:        Datei-CA (ADMIN_CA_BUNDLE_PATH) UND Web-CA (TRUST_DIR/ca-bundle.pem),
#                     zusammengefuehrt; keine von beiden -> Einrichtungsmodus (503).
#   CRL:              ADMIN_CRL_PATH (gesetzt = Pflicht, fail closed) und/oder
#                     TRUST_DIR/crl.pem, zusammengefuehrt.
#
# Exitcodes: 0 gerendert, 2 Serverzertifikat fehlt (noch), 1 Fehler.
# Schreibt den Modus (mtls|setup) nach $NPT_GW_STATE/mode.
set -eu

: "${ADMIN_CA_BUNDLE_PATH:=/etc/npt-gateway/pki/ca-bundle.pem}"
: "${ADMIN_CRL_PATH:=}"
: "${ADMIN_VERIFY_DEPTH:=2}"
: "${GATEWAY_TLS_CERT_PATH:=/etc/npt-gateway/pki/server.crt}"
: "${GATEWAY_TLS_KEY_PATH:=/etc/npt-gateway/pki/server.key}"
: "${TRUST_DIR:=/etc/npt-gateway/trust}"
: "${NPT_GW_STATE:=/run/nginx/npt}"
: "${ADMIN_UPSTREAM:=app:8001}"
: "${GATEWAY_MAX_BODY:=21m}"

log() { echo "{\"component\":\"admin-gateway\",\"level\":\"$1\",\"msg\":\"$2\"}" >&2; }

mkdir -p "$NPT_GW_STATE"

if [ -r "$GATEWAY_TLS_CERT_PATH" ] && [ -r "$GATEWAY_TLS_KEY_PATH" ]; then
  TLS_CERT="$GATEWAY_TLS_CERT_PATH"; TLS_KEY="$GATEWAY_TLS_KEY_PATH"
elif [ -r "$TRUST_DIR/server.crt" ] && [ -r "$TRUST_DIR/server.key" ]; then
  TLS_CERT="$TRUST_DIR/server.crt"; TLS_KEY="$TRUST_DIR/server.key"
else
  exit 2
fi

# Client-CA zusammenfuehren (Leerzeile zwischen den Dateien: fehlender Zeilenumbruch
# am Dateiende wuerde sonst zwei PEM-Bloecke verkleben).
ca_tmp="$NPT_GW_STATE/.ca.tmp"
: > "$ca_tmp"
for f in "$ADMIN_CA_BUNDLE_PATH" "$TRUST_DIR/ca-bundle.pem"; do
  if [ -r "$f" ] && [ -s "$f" ]; then cat "$f" >> "$ca_tmp"; echo >> "$ca_tmp"; fi
done
if [ -s "$ca_tmp" ]; then
  mode=mtls
  mv "$ca_tmp" "$NPT_GW_STATE/ca.pem"
else
  mode=setup
  rm -f "$ca_tmp" "$NPT_GW_STATE/ca.pem"
fi

crl_tmp="$NPT_GW_STATE/.crl.tmp"
: > "$crl_tmp"
if [ -n "$ADMIN_CRL_PATH" ]; then
  if [ ! -r "$ADMIN_CRL_PATH" ]; then
    rm -f "$crl_tmp"
    log ERROR "ADMIN_CRL_PATH gesetzt, Datei fehlt: $ADMIN_CRL_PATH"
    exit 1
  fi
  cat "$ADMIN_CRL_PATH" >> "$crl_tmp"; echo >> "$crl_tmp"
fi
if [ -r "$TRUST_DIR/crl.pem" ] && [ -s "$TRUST_DIR/crl.pem" ]; then
  cat "$TRUST_DIR/crl.pem" >> "$crl_tmp"; echo >> "$crl_tmp"
fi
if [ "$mode" = mtls ] && [ -s "$crl_tmp" ]; then
  mv "$crl_tmp" "$NPT_GW_STATE/crl.pem"
  printf 'ssl_crl %s;\n' "$NPT_GW_STATE/crl.pem" > /etc/nginx/conf.d/npt-crl.inc
else
  rm -f "$crl_tmp" "$NPT_GW_STATE/crl.pem"
  : > /etc/nginx/conf.d/npt-crl.inc
fi

GATEWAY_TLS_CERT_EFFECTIVE="$TLS_CERT"
GATEWAY_TLS_KEY_EFFECTIVE="$TLS_KEY"
ADMIN_CA_EFFECTIVE="$NPT_GW_STATE/ca.pem"
export GATEWAY_TLS_CERT_EFFECTIVE GATEWAY_TLS_KEY_EFFECTIVE ADMIN_CA_EFFECTIVE \
       ADMIN_VERIFY_DEPTH ADMIN_UPSTREAM GATEWAY_MAX_BODY
if [ "$mode" = mtls ]; then
  template=/etc/npt-gateway/admin.conf.template
else
  template=/etc/npt-gateway/setup.conf.template
fi
envsubst '${ADMIN_CA_EFFECTIVE} ${ADMIN_VERIFY_DEPTH} ${GATEWAY_TLS_CERT_EFFECTIVE} ${GATEWAY_TLS_KEY_EFFECTIVE} ${ADMIN_UPSTREAM} ${GATEWAY_MAX_BODY}' \
  < "$template" > /etc/nginx/conf.d/admin.conf.tmp
mv /etc/nginx/conf.d/admin.conf.tmp /etc/nginx/conf.d/admin.conf
echo "$mode" > "$NPT_GW_STATE/mode"
exit 0
