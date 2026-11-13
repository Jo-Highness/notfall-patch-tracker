#!/bin/sh
# Reload-Schleife (NPT-06, NPT-12 K3, NPT-15): prueft den Inhalt aller Eingabedateien
# (Serverzertifikat, Schluessel, Datei-CA, Datei-CRL und die web-verwalteten Dateien in
# TRUST_DIR). Bei Aenderung: render.sh, `nginx -t`, `nginx -s reload`. Scheitert eines
# davon, arbeitet nginx mit dem zuletzt geladenen Stand weiter (fail closed).
# Intervall: CRL_RELOAD_INTERVAL (Default 60 s), im Einrichtungsmodus 5 s.
set -u
interval="${CRL_RELOAD_INTERVAL:-60}"
: "${TRUST_DIR:=/etc/npt-gateway/trust}"
: "${NPT_GW_STATE:=/run/nginx/npt}"
: "${ADMIN_CA_BUNDLE_PATH:=/etc/npt-gateway/pki/ca-bundle.pem}"
: "${ADMIN_CRL_PATH:=}"
: "${GATEWAY_TLS_CERT_PATH:=/etc/npt-gateway/pki/server.crt}"
: "${GATEWAY_TLS_KEY_PATH:=/etc/npt-gateway/pki/server.key}"

log() { echo "{\"component\":\"admin-gateway\",\"level\":\"$1\",\"msg\":\"$2\"}" >&2; }

digest() {
  for f in "$GATEWAY_TLS_CERT_PATH" "$GATEWAY_TLS_KEY_PATH" "$ADMIN_CA_BUNDLE_PATH" \
           $ADMIN_CRL_PATH "$TRUST_DIR/ca-bundle.pem" "$TRUST_DIR/crl.pem" \
           "$TRUST_DIR/server.crt" "$TRUST_DIR/server.key"; do
    if [ -r "$f" ]; then echo "== $f"; cat "$f"; else echo "-- $f"; fi
  done | sha256sum | cut -d' ' -f1
}

last="$(digest)"
while :; do
  if [ "$(cat "$NPT_GW_STATE/mode" 2>/dev/null)" = setup ]; then sleep 5; else sleep "$interval"; fi
  cur="$(digest)"
  [ "$cur" = "$last" ] && continue
  /usr/local/bin/render.sh
  rc=$?
  if [ "$rc" -eq 2 ]; then
    log WARNING "Serverzertifikat fehlt, kein Reload"; continue
  elif [ "$rc" -ne 0 ]; then
    log ERROR "Konfiguration nicht erzeugbar, kein Reload"; continue
  fi
  if nginx -t -q; then
    nginx -s reload && last="$cur" && \
      log INFO "PKI-Dateien geaendert, nginx neu geladen (Modus $(cat "$NPT_GW_STATE/mode"))"
  else
    log ERROR "nginx -t fehlgeschlagen, kein Reload"
  fi
done
