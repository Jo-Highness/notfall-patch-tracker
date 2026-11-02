#!/bin/sh
# Reload-Schleife (NPT-06, NPT-12 K3): prueft alle CRL_RELOAD_INTERVAL Sekunden (Default 60)
# den Inhalt von Serverzertifikat, Schluessel, CA-Bundle und optional CRL (sha256 ueber
# alle Dateien statt mtime: Kubernetes tauscht Secret-Inhalte per Symlink-Wechsel). Bei
# Aenderung `nginx -t && nginx -s reload`; fehlende/unlesbare Datei -> kein Reload,
# nginx arbeitet mit dem zuletzt geladenen Stand weiter (fail closed).
# Aufruf: crl-reload.sh <datei> [<datei> ...]
set -u
interval="${CRL_RELOAD_INTERVAL:-60}"

digest() {
  for f in "$@"; do
    [ -r "$f" ] || return 1
  done
  cat "$@" | sha256sum | cut -d' ' -f1
}

last="$(digest "$@" || echo unreadable)"
while sleep "$interval"; do
  if ! cur="$(digest "$@")"; then
    echo '{"component":"admin-gateway","level":"WARNING","msg":"Zertifikat/CA/CRL nicht lesbar, kein Reload"}' >&2
    continue
  fi
  if [ "$cur" != "$last" ]; then
    if nginx -t -q; then
      nginx -s reload && last="$cur" && \
        echo '{"component":"admin-gateway","level":"INFO","msg":"PKI-Dateien geaendert, nginx neu geladen"}' >&2
    else
      echo '{"component":"admin-gateway","level":"ERROR","msg":"nginx -t fehlgeschlagen, kein Reload"}' >&2
    fi
  fi
done
